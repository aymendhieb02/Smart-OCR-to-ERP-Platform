from __future__ import annotations

import argparse
import csv
import json
import time
import sys
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.services.file_loader import load_document
from app.services.ocr_engine import OCREngine
from app.services.table_transformer.table_transformer_router import reconstruct_with_table_transformer
from scripts.manual_benchmark_utils import DEFAULT_BENCHMARK_ROOT, load_manifest_documents


REPORT_ROOT = ROOT / "dataset" / "reports" / "table_transformer"


def main() -> None:
    args = parse_args()
    run_dir = REPORT_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    original_enabled = settings.enable_table_transformer
    settings.enable_table_transformer = True
    rows: list[dict[str, Any]] = []
    try:
        documents = load_manifest_documents(Path(args.benchmark_root))[: args.limit]
        for document in documents:
            rows.append(run_document(document))
    finally:
        settings.enable_table_transformer = original_enabled
    write_outputs(run_dir, rows)
    print(f"Table Transformer benchmark written to {run_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare P3 Stable with optional Table Transformer structure detection.")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT))
    parser.add_argument("--run-id", default="table_transformer_experiment")
    parser.add_argument("--limit", type=int, default=10)
    return parser.parse_args()


def run_document(document) -> dict[str, Any]:
    started = time.perf_counter()
    memory_before = psutil.Process().memory_info().rss
    try:
        loaded = load_document(document.image_path, document.filename)
        ocr = OCREngine(use_disk_cache=True).run(loaded.images, loaded.embedded_text)
        image = loaded.images[0]
        items, debug = reconstruct_with_table_transformer(image, ocr.lines, page=1)
        status = "success"
        error = ""
    except Exception as exc:
        items = []
        debug = {"error": str(exc)}
        status = "error"
        error = str(exc)
    memory_after = psutil.Process().memory_info().rss
    latency = round(time.perf_counter() - started, 4)
    detection = debug.get("detection") or {}
    return {
        "filename": document.filename,
        "dataset": document.dataset,
        "status": status,
        "error": error,
        "p3_stable_row_count": None,
        "table_transformer_row_count": len(items),
        "table_detection_count": len(detection.get("tables", [])),
        "model_loaded": detection.get("model_loaded"),
        "model_error": detection.get("model_load_error"),
        "source": detection.get("source"),
        "latency_seconds": latency,
        "memory_delta_mb": round((memory_after - memory_before) / (1024 * 1024), 3),
        "cache_hit": debug.get("cache_hit", False),
    }


def write_outputs(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    metrics = summarize(rows)
    (run_dir / "table_transformer_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(run_dir / "document_results.csv", rows)
    write_csv(run_dir / "latency.csv", [{"filename": row["filename"], "latency_seconds": row["latency_seconds"], "memory_delta_mb": row["memory_delta_mb"]} for row in rows])
    write_csv(run_dir / "comparison_with_p3.csv", rows)
    (run_dir / "table_transformer_report.md").write_text(report_markdown(metrics, rows), encoding="utf-8")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    successes = [row for row in rows if row["status"] == "success"]
    detections = [row for row in successes if int(row.get("table_detection_count") or 0) > 0]
    return {
        "documents": total,
        "success_count": len(successes),
        "error_count": total - len(successes),
        "table_detection_rate": round(len(detections) / total, 4) if total else 0.0,
        "average_latency_seconds": round(sum(float(row["latency_seconds"]) for row in rows) / total, 4) if total else 0.0,
        "cache_hit_ratio": round(sum(1 for row in rows if row.get("cache_hit")) / total, 4) if total else 0.0,
        "recommendation": "remain optional until verified row-level gains exceed P3 Stable",
        "note": "P3 Stable remains production default; this benchmark never enables auto replacement.",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report_markdown(metrics: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Table Transformer Experimental Benchmark",
        "",
        "The Microsoft Table Transformer path is experimental and off by default.",
        "",
        f"- Documents: {metrics['documents']}",
        f"- Successes: {metrics['success_count']}",
        f"- Table detection rate: {metrics['table_detection_rate']}",
        f"- Average latency seconds: {metrics['average_latency_seconds']}",
        f"- Cache hit ratio: {metrics['cache_hit_ratio']}",
        "",
        "## Recommendation",
        "",
        "B) remain optional. The deterministic P3 Stable engine remains the production default until verified benchmarks show consistent row-level gains.",
        "",
        "## Documents",
        "",
        "| Filename | Status | TATR rows | Tables | Source | Latency |",
        "|---|---|---:|---:|---|---:|",
    ]
    for row in rows:
        lines.append(f"| {row['filename']} | {row['status']} | {row['table_transformer_row_count']} | {row['table_detection_count']} | {row.get('source')} | {row['latency_seconds']} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
