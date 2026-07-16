"""Run a fixed, table-focused extraction benchmark slice."""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.schemas import OCRLine, OCRResult
from app.services.ocr_engine import OCREngine
from app.services.pipeline_runner import process_document_file, process_loaded_document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--reuse-ocr", action="store_true", help="Reuse OCR blocks from the slice prediction JSON and rerun extraction only.")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    output = ROOT / "dataset" / "reports" / "multi_dataset_benchmark" / "table_heavy"
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "results.csv"
    existing = {} if args.force or not result_path.exists() else {row["file_path"]: row for row in csv.DictReader(result_path.open(encoding="utf-8"))}
    engine = OCREngine()
    rows = []
    documents = manifest["documents"][:args.limit] if args.limit else manifest["documents"]
    for item in documents:
        path = Path(item["file_path"])
        if not args.force and str(path) in existing:
            rows.append(existing[str(path)])
            continue
        start = time.perf_counter()
        row = {"dataset": item.get("dataset"), "filename": path.name, "file_path": str(path)}
        prediction_path = output / "predictions" / f"{path.stem}.json"
        try:
            if not path.exists() and not (args.reuse_ocr and prediction_path.exists()):
                raise FileNotFoundError(path)
            if args.reuse_ocr and prediction_path.exists():
                cached = json.loads(prediction_path.read_text(encoding="utf-8"))
                cached_blocks = [OCRLine.model_validate(block) for block in cached.get("ocr_blocks", [])]
                cached_result = OCRResult(raw_text=cached.get("extracted_text", ""), lines=cached_blocks, confidence=cached.get("erp_json", {}).get("metadata", {}).get("confidence"), engine="cached OCR")
                cached_engine = SimpleNamespace(last_timings={"ocr_cache_hits": len(cached_blocks), "ocr_cache_misses": 0}, run=lambda _images, _text: cached_result)
                document = SimpleNamespace(images=[], embedded_text="", source_file=str(path))
                response = process_loaded_document(document=document, ocr_engine=cached_engine, include_preview=False)
            else:
                response = process_document_file(path, original_filename=path.name, ocr_engine=engine, include_preview=False)
            table_debug = response.extraction_debug.get("table_extraction_debug", {})
            tables = response.table_candidates or []
            table_rows = len(table_debug.get("raw_candidate_rows", []))
            validated = len(response.line_items_validated)
            review = len(response.line_items_needs_review)
            row.update({
                "status": "success", "products_table_detected": bool(tables),
                "table_anchor_candidates": len(table_debug.get("table_anchor_candidates", [])),
                "candidate_rows": table_rows, "validated_rows": validated,
                "review_rows": review, "any_rows": bool(validated or review),
                "amount_ttc_found": response.detected_fields.amount_ttc is not None,
                "processing_time_seconds": round(time.perf_counter() - start, 3),
                "stage_timings": json.dumps(response.extraction_debug.get("stage_timings", {})),
            })
            prediction_path.parent.mkdir(parents=True, exist_ok=True)
            prediction_path.write_text(response.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:
            row.update({"status": "error", "error": str(exc), "products_table_detected": False, "table_anchor_candidates": 0, "candidate_rows": 0, "validated_rows": 0, "review_rows": 0, "any_rows": False, "amount_ttc_found": False, "processing_time_seconds": round(time.perf_counter() - start, 3), "stage_timings": "{}"})
        rows.append(row)
        write_rows(result_path, rows)
        print(f"{path.name}: {row['status']} any_rows={row.get('any_rows')}")
    success = [row for row in rows if row.get("status") == "success"]
    summary = {
        "documents_tested": len(rows),
        "products_table_detected_pct": pct(success, "products_table_detected"),
        "table_anchor_found_pct": pct(success, "table_anchor_candidates"),
        "candidate_rows_found_pct": pct(success, "candidate_rows"),
        "validated_rows_found_pct": pct(success, "validated_rows"),
        "review_rows_found_pct": pct(success, "review_rows"),
        "any_rows_found_pct": pct(success, "any_rows"),
        "ttc_found_pct": pct(success, "amount_ttc_found"),
        "average_processing_time_seconds": round(sum(float(row.get("processing_time_seconds", 0)) for row in success) / len(success), 3) if success else None,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def pct(rows, key):
    if not rows:
        return 0.0
    return round(sum(_truthy(row.get(key)) for row in rows) / len(rows) * 100, 2)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in {"1", "true", "yes", "y"}:
        return True
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def write_rows(path: Path, rows: list[dict]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
