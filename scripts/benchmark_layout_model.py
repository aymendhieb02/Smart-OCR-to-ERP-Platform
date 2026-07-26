from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.services.dynamic_tables import compute_unmapped_ocr_ratio
from app.services.file_loader import SUPPORTED_EXTENSIONS, load_document
from app.services.layout_analyzer import LayoutAnalyzer
from app.services.layout_model.layout_model_router import detect_layout_blocks_with_model
from app.services.ocr_engine import OCREngine


REPORT_DIR = Path("dataset/reports/layout_model_benchmark")


def main() -> None:
    args = parse_args()
    dataset_root = Path(args.input_dir)
    if not dataset_root.exists():
        dataset_root = Path("dataset/samples")
    files = sorted(path for path in dataset_root.rglob("*") if path.suffix.lower() in SUPPORTED_EXTENSIONS)
    if args.limit:
        files = files[: args.limit]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    engine = OCREngine(use_disk_cache=True)
    original_flag = settings.enable_layout_model
    settings.enable_layout_model = True
    try:
        for path in files:
            rows.append(benchmark_one(path, engine))
    finally:
        settings.enable_layout_model = original_flag
    write_outputs(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare heuristic layout blocks with optional DocLayout-YOLO layout model.")
    parser.add_argument("--input-dir", default="dataset/unseen_format_test/raw")
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args()


def benchmark_one(path: Path, engine: OCREngine) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {"filename": path.name, "file_path": str(path), "status": "success", "error": ""}
    try:
        document = load_document(path, path.name)
        ocr = engine.run(document.images, document.embedded_text)
        heuristic_started = time.perf_counter()
        heuristic_blocks = LayoutAnalyzer(ocr.lines).detect_layout_blocks()
        heuristic_latency = time.perf_counter() - heuristic_started
        model_started = time.perf_counter()
        model_blocks, model_debug = detect_layout_blocks_with_model(document.images, ocr.lines)
        model_latency = time.perf_counter() - model_started
        row.update({
            "ocr_blocks": len(ocr.lines),
            "heuristic_block_count": len(heuristic_blocks),
            "model_block_count": len(model_blocks),
            "heuristic_unmapped_ratio": compute_unmapped_ocr_ratio(heuristic_blocks, ocr.lines),
            "model_unmapped_ratio": compute_unmapped_ocr_ratio(model_blocks, ocr.lines) if model_blocks else None,
            "heuristic_latency_seconds": round(heuristic_latency, 4),
            "model_latency_seconds": round(model_latency, 4),
            "layout_model_available": bool(model_debug.get("available")),
            "layout_model_reason": model_debug.get("reason"),
        })
    except Exception as exc:
        row.update({"status": "error", "error": str(exc)})
    row["total_seconds"] = round(time.perf_counter() - started, 4)
    return row


def write_outputs(rows: list[dict[str, Any]]) -> None:
    csv_path = REPORT_DIR / "layout_model_benchmark.csv"
    json_path = REPORT_DIR / "layout_model_benchmark.json"
    md_path = Path("docs/layout_model_benchmark.md")
    fieldnames = sorted({key for row in rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    successes = [row for row in rows if row.get("status") == "success"]
    heuristic = [row["heuristic_unmapped_ratio"] for row in successes if row.get("heuristic_unmapped_ratio") is not None]
    model = [row["model_unmapped_ratio"] for row in successes if row.get("model_unmapped_ratio") is not None]
    model_available = any(row.get("layout_model_available") for row in successes)
    md_path.write_text(build_report(rows, heuristic, model, model_available), encoding="utf-8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


def build_report(rows: list[dict[str, Any]], heuristic: list[float], model: list[float], model_available: bool) -> str:
    heuristic_avg = round(mean(heuristic), 4) if heuristic else None
    model_avg = round(mean(model), 4) if model else None
    recommendation = "Keep optional: no model detections were available in this environment."
    if model_available and heuristic_avg is not None and model_avg is not None:
        recommendation = "Candidate for further review." if model_avg < heuristic_avg else "Keep optional: no unmapped-text improvement over heuristic baseline yet."
    return f"""# Layout Model Benchmark

This benchmark compares the current heuristic layout analyzer with the optional DocLayout-YOLO visual layout model.

Headline metric: unmapped OCR text ratio, where lower is better.

## Summary

- Documents attempted: {len(rows)}
- Successful documents: {sum(1 for row in rows if row.get('status') == 'success')}
- Heuristic average unmapped ratio: {heuristic_avg}
- Layout model average unmapped ratio: {model_avg}
- Layout model available: {model_available}

## Recommendation Gate

{recommendation}

The pretrained model remains optional and disabled by default. Do not enable `INVOICE_OCR_ENABLE_LAYOUT_MODEL` in production until repeated benchmarks show a clear unmapped-text-ratio improvement without regressions.

## Notes

DocLayout-YOLO uses `juliozhao/DocLayout-YOLO-DocStructBench` with `doclayout_yolo`/`YOLOv10`. If dependencies or local weights are missing, the application falls back to the heuristic analyzer.
"""


if __name__ == "__main__":
    main()
