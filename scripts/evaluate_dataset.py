from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.schemas import OCRResult
from app.services.document_classifier import classify_document
from app.services.document_layout import analyze_document_layout
from app.services.dynamic_tables import build_dynamic_review_payload
from app.services.erp_mapper import build_erp_json, map_to_flat_erp
from app.services.extraction_quality import apply_extraction_quality_gate, build_validated_erp_json
from app.services.field_enricher import build_expanded_fields, build_field_boxes
from app.services.field_extractor import extract_with_candidates
from app.services.file_loader import SUPPORTED_EXTENSIONS, load_document
from app.services.layout_analyzer import LayoutAnalyzer
from app.services.ocr_engine import OCREngine
from app.services.validation_explainer import build_validation_explanation
from app.services.validator import validate_invoice

DEFAULT_SOURCE = Path(r"D:\Stage_mr_f\sources")
EVALUATION_ROOT = ROOT / "outputs" / "evaluation"
RUNS_ROOT = EVALUATION_ROOT / "runs"
OCR_CACHE_DIR = ROOT / "outputs" / "cache" / "ocr"
LAYOUT_CACHE_DIR = ROOT / "outputs" / "cache" / "layout"
SUPPORTED = {suffix.lower() for suffix in SUPPORTED_EXTENSIONS}
BATCHES = ("batch_1", "batch_2", "batch_3")
CHECKPOINT_EVERY = 25
CRITICAL_ERROR_LIMIT = 10

RESULT_FIELDS = [
    "index", "batch", "filename", "file_path", "file_hash", "status", "validation_status",
    "document_type", "processing_time_seconds", "ocr_cache_hit", "layout_cache_hit", "ocr_confidence",
    "supplier_name", "customer_name", "invoice_number", "invoice_date", "due_date", "currency",
    "amount_ht", "tva_amount", "amount_ttc", "tax_rate", "line_items_validated",
    "line_items_needs_review", "totals_consistent", "missing_fields", "rejected_values_count",
    "prediction_json", "error_message",
]

ERROR_FIELDS = ["index", "batch", "filename", "file_path", "file_hash", "error_type", "message", "elapsed_seconds"]
REJECTED_FIELDS = ["index", "batch", "filename", "field", "value", "reason", "confidence", "source"]


def main() -> None:
    args = parse_args()
    source = Path(args.source).resolve()
    run_dir = build_run_dir(args)
    args.run_id = run_dir.name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "predictions").mkdir(exist_ok=True)
    OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LAYOUT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    files_by_batch = scan_files_by_batch(source)
    checkpoint = load_checkpoint(run_dir) if args.resume else None
    selected_files = checkpoint_selected_files(checkpoint) if checkpoint else []
    if not selected_files:
        selected_files = select_files(files_by_batch, args.mode, args.seed, args.limit)
    if not selected_files:
        raise SystemExit(f"No supported documents found under {source}")
    processed_paths = set(checkpoint.get("processed_paths", [])) if checkpoint else set()
    rows = load_csv_rows(run_dir / "results.csv") if args.resume else []
    errors = load_csv_rows(run_dir / "errors.csv") if args.resume else []
    rejected_rows = load_csv_rows(run_dir / "rejected_candidates.csv") if args.resume else []
    critical_errors = len(errors)

    engine = OCREngine()
    started_at = time.perf_counter()
    print(f"Evaluation mode={args.mode} run_id={run_dir.name} docs={len(selected_files)} cache={not args.no_ocr_cache}")

    for index, (batch, path) in enumerate(selected_files, start=1):
        resolved = str(path.resolve())
        if resolved in processed_paths:
            continue
        doc_start = time.perf_counter()
        try:
            prediction, row, rejected = process_document(index, batch, path, engine, run_dir, use_cache=not args.no_ocr_cache)
            rows = upsert_row(rows, row, key="file_path")
            rejected_rows.extend(rejected)
            write_json(run_dir / "predictions" / f"{index:05d}_{path.stem}.json", prediction)
            processed_paths.add(resolved)
        except Exception as exc:
            elapsed = round(time.perf_counter() - doc_start, 3)
            err = error_row(index, batch, path, safe_hash(path), type(exc).__name__, str(exc), elapsed)
            errors.append(err)
            processed_paths.add(resolved)
            critical_errors += 1
            print(f"ERROR {index}/{len(selected_files)} {path.name}: {exc}")
            if should_stop_after_error(args, critical_errors):
                write_progress(run_dir, rows, errors, rejected_rows, selected_files, processed_paths, started_at, args)
                print(f"Stopping after {critical_errors} critical errors")
                break
        write_progress(run_dir, rows, errors, rejected_rows, selected_files, processed_paths, started_at, args)
        if index % CHECKPOINT_EVERY == 0:
            save_checkpoint(run_dir, selected_files, processed_paths, args)
        print_progress(index, len(selected_files), rows, errors, started_at)

    save_checkpoint(run_dir, selected_files, processed_paths, args)
    write_progress(run_dir, rows, errors, rejected_rows, selected_files, processed_paths, started_at, args)
    write_html_report(run_dir)
    print(json.dumps(load_json(run_dir / "summary.json"), indent=2, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tiered OCR-to-ERP dataset evaluation runner.")
    parser.add_argument("--mode", choices=["smoke", "medium", "full", "cached", "fail-fast"], default="smoke")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Root folder containing batch_1, batch_2, batch_3.")
    parser.add_argument("--run-id", default=None, help="Custom run id. Defaults to timestamped mode name.")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for smoke/medium sampling.")
    parser.add_argument("--resume", action="store_true", help="Resume this run from checkpoint if present.")
    parser.add_argument("--limit", type=int, default=None, help="Optional hard limit after mode sampling.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after first critical error instead of 10.")
    parser.add_argument("--no-ocr-cache", nargs="?", const=True, default=False, type=parse_bool, help="Disable OCR/layout cache. Accepts true/false.")
    return parser.parse_args()


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_run_dir(args: argparse.Namespace) -> Path:
    if args.run_id:
        return RUNS_ROOT / args.run_id
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return RUNS_ROOT / f"{args.mode}_{stamp}"


def scan_files_by_batch(source: Path) -> dict[str, list[Path]]:
    by_batch: dict[str, list[Path]] = {}
    for batch in BATCHES:
        batch_dir = source / batch
        files = sorted([path for path in batch_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED], key=lambda item: str(item).lower()) if batch_dir.exists() else []
        by_batch[batch] = files
    return by_batch


def select_files(files_by_batch: dict[str, list[Path]], mode: str, seed: int | None, limit: int | None) -> list[tuple[str, Path]]:
    rng = random.Random(seed)
    if mode in {"smoke", "fail-fast"}:
        selected = balanced_sample(files_by_batch, 30, rng)
    elif mode == "medium":
        selected = balanced_sample(files_by_batch, 300, rng)
    else:
        selected = [(batch, path) for batch in BATCHES for path in files_by_batch.get(batch, [])]
        if mode == "cached":
            rng.shuffle(selected)
    if limit is not None:
        selected = selected[:limit]
    return selected


def checkpoint_selected_files(checkpoint: dict[str, Any] | None) -> list[tuple[str, Path]]:
    if not checkpoint:
        return []
    selected = []
    for item in checkpoint.get("selected_files", []):
        batch = item.get("batch")
        path = item.get("path")
        if batch and path:
            selected.append((str(batch), Path(path)))
    return selected


def should_stop_after_error(args: argparse.Namespace, critical_errors: int) -> bool:
    if args.fail_fast:
        return True
    return args.mode == "fail-fast" and critical_errors >= CRITICAL_ERROR_LIMIT

def balanced_sample(files_by_batch: dict[str, list[Path]], total: int, rng: random.Random) -> list[tuple[str, Path]]:
    per_batch = max(1, total // len(BATCHES))
    remainder = total % len(BATCHES)
    selected: list[tuple[str, Path]] = []
    for idx, batch in enumerate(BATCHES):
        batch_files = list(files_by_batch.get(batch, []))
        rng.shuffle(batch_files)
        count = per_batch + (1 if idx < remainder else 0)
        selected.extend((batch, path) for path in batch_files[:count])
    rng.shuffle(selected)
    return selected


def process_document(index: int, batch: str, path: Path, engine: OCREngine, run_dir: Path, use_cache: bool) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    start = time.perf_counter()
    file_hash = compute_file_hash(path)
    document = load_document(path, path.name)
    ocr_result, ocr_cache_hit = get_ocr_result(file_hash, document, engine, use_cache)
    layout_debug, layout_cache_hit = get_layout_result(file_hash, ocr_result, use_cache)
    layout_blocks = LayoutAnalyzer(ocr_result.lines).detect_layout_blocks()
    classification = classify_document(ocr_result.raw_text, ocr_result.lines)
    fields, candidates, field_confidences, extraction_debug = extract_with_candidates(ocr_result.raw_text, ocr_result.lines, classification)
    quality_gate = apply_extraction_quality_gate(fields, candidates, field_confidences)
    fields = quality_gate.sanitized_fields
    expanded_fields = build_expanded_fields(fields, candidates, field_confidences, ocr_result.raw_text)
    field_boxes = build_field_boxes(expanded_fields)
    extraction_debug["layout_analysis"] = layout_debug
    validation = validate_invoice(fields, ocr_result, classification)
    validation.warnings.extend(quality_gate.validation_report.get("warnings", []))
    if quality_gate.validation_report.get("extraction_status") == "needs_review" and validation.status == "valid":
        validation.status = "needs_review"
        validation.is_valid = False
    validation_explanation = build_validation_explanation(validation)
    erp_json = build_erp_json(
        fields=fields,
        validation=validation,
        source_file=document.source_file,
        ocr_engine=ocr_result.engine,
        confidence=ocr_result.confidence,
        document_type=classification.document_type,
        field_confidences=field_confidences,
        languages=["fr", "en", "ar"],
        expanded_fields=expanded_fields,
    )
    erp_json.quality["validation_explanation"] = validation_explanation.model_dump(mode="json")
    dynamic_tables, extraction_layer, erp_layer = build_dynamic_review_payload(
        fields=fields,
        expanded_fields=expanded_fields,
        layout_blocks=layout_blocks,
        ocr_blocks=ocr_result.lines,
        validation=validation,
        erp_json=erp_json,
    )
    validated_erp_json = build_validated_erp_json(erp_json, quality_gate.validation_report)
    erp_export = map_to_flat_erp(erp_json)
    erp_export.source_payload = validated_erp_json
    elapsed = round(time.perf_counter() - start, 3)
    prediction = {
        "index": index,
        "source_file": path.name,
        "source_path": str(path.resolve()),
        "batch": batch,
        "file_hash": file_hash,
        "extracted_text": ocr_result.raw_text,
        "document_classification": classification.model_dump(mode="json"),
        "detected_fields": fields.model_dump(mode="json"),
        "field_confidences": field_confidences,
        "field_boxes": [box.model_dump(mode="json") for box in field_boxes],
        "layout_blocks": [block.model_dump(mode="json") for block in layout_blocks],
        "layout_analysis": layout_debug,
        "ocr_blocks": [line.model_dump(mode="json") for line in ocr_result.lines],
        "dynamic_tables": [table.model_dump(mode="json") for table in dynamic_tables],
        "extraction_layer": extraction_layer.model_dump(mode="json"),
        "erp_layer": erp_layer.model_dump(mode="json"),
        "review_candidates": quality_gate.review_candidates,
        "rejected_candidates": quality_gate.rejected_candidates,
        "line_items_validated": [item.model_dump(mode="json") for item in quality_gate.line_items_validated],
        "line_items_needs_review": [item.model_dump(mode="json") for item in quality_gate.line_items_needs_review],
        "validation": validation.model_dump(mode="json"),
        "validation_explanation": validation_explanation.model_dump(mode="json"),
        "validation_report": quality_gate.validation_report,
        "erp_json": erp_json.model_dump(mode="json"),
        "erp_export": erp_export.model_dump(mode="json"),
        "validated_erp_json": validated_erp_json,
        "metadata": {
            "ocr_engine": ocr_result.engine,
            "ocr_confidence": ocr_result.confidence,
            "page_count": ocr_result.page_count,
            "ocr_cache_hit": ocr_cache_hit,
            "layout_cache_hit": layout_cache_hit,
            "processing_time_seconds": elapsed,
        },
    }
    prediction_path = run_dir / "predictions" / f"{index:05d}_{safe_name(path.stem)}.json"
    row = result_row(index, batch, path, file_hash, prediction_path, prediction)
    rejected = rejected_candidate_rows(index, batch, path, quality_gate.rejected_candidates)
    return prediction, row, rejected


def get_ocr_result(file_hash: str, document: Any, engine: OCREngine, use_cache: bool) -> tuple[OCRResult, bool]:
    cache_path = OCR_CACHE_DIR / f"{file_hash}.json"
    if use_cache and cache_path.exists():
        return OCRResult.model_validate(load_json(cache_path)), True
    ocr_result = engine.run(document.images, document.embedded_text)
    if not ocr_result.raw_text:
        raise ValueError("No text could be extracted")
    write_json(cache_path, ocr_result.model_dump(mode="json"))
    return ocr_result, False


def get_layout_result(file_hash: str, ocr_result: OCRResult, use_cache: bool) -> tuple[dict[str, Any], bool]:
    cache_path = LAYOUT_CACHE_DIR / f"{file_hash}.json"
    if use_cache and cache_path.exists():
        return load_json(cache_path), True
    layout = analyze_document_layout(ocr_result.lines)
    write_json(cache_path, layout)
    return layout, False


def result_row(index: int, batch: str, path: Path, file_hash: str, prediction_path: Path, prediction: dict[str, Any]) -> dict[str, Any]:
    fields = prediction["detected_fields"]
    validation = prediction["validation"]
    metadata = prediction["metadata"]
    validation_report = prediction.get("validation_report", {})
    line_valid = len(prediction.get("line_items_validated", []))
    line_review = len(prediction.get("line_items_needs_review", []))
    missing = missing_fields(fields)
    return {
        "index": index,
        "batch": batch,
        "filename": path.name,
        "file_path": str(path.resolve()),
        "file_hash": file_hash,
        "status": "success",
        "validation_status": validation.get("status"),
        "document_type": prediction.get("document_classification", {}).get("document_type"),
        "processing_time_seconds": metadata.get("processing_time_seconds"),
        "ocr_cache_hit": metadata.get("ocr_cache_hit"),
        "layout_cache_hit": metadata.get("layout_cache_hit"),
        "ocr_confidence": metadata.get("ocr_confidence"),
        "supplier_name": fields.get("supplier_name"),
        "customer_name": fields.get("customer_name"),
        "invoice_number": fields.get("invoice_number"),
        "invoice_date": fields.get("invoice_date"),
        "due_date": fields.get("due_date"),
        "currency": fields.get("currency"),
        "amount_ht": fields.get("amount_ht"),
        "tva_amount": fields.get("tva_amount"),
        "amount_ttc": fields.get("amount_ttc"),
        "tax_rate": fields.get("tax_rate"),
        "line_items_validated": line_valid,
        "line_items_needs_review": line_review,
        "totals_consistent": validation_report.get("totals", {}).get("accepted"),
        "missing_fields": ";".join(missing),
        "rejected_values_count": sum(len(values) for values in prediction.get("rejected_candidates", {}).values()),
        "prediction_json": str(prediction_path.resolve()),
        "error_message": "",
    }


def error_row(index: int, batch: str, path: Path, file_hash: str, error_type: str, message: str, elapsed: float) -> dict[str, Any]:
    return {
        "index": index,
        "batch": batch,
        "filename": path.name,
        "file_path": str(path.resolve()),
        "file_hash": file_hash,
        "error_type": error_type,
        "message": message,
        "elapsed_seconds": elapsed,
    }


def rejected_candidate_rows(index: int, batch: str, path: Path, rejected: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for field, candidates in rejected.items():
        for candidate in candidates:
            rows.append({
                "index": index,
                "batch": batch,
                "filename": path.name,
                "field": field,
                "value": candidate.get("value"),
                "reason": candidate.get("rejection_reason"),
                "confidence": candidate.get("confidence"),
                "source": candidate.get("source"),
            })
    return rows


def missing_fields(fields: dict[str, Any]) -> list[str]:
    required = ["supplier_name", "invoice_number", "invoice_date", "amount_ttc", "currency"]
    return [field for field in required if fields.get(field) in (None, "", [])]


def write_progress(run_dir: Path, rows: list[dict[str, Any]], errors: list[dict[str, Any]], rejected_rows: list[dict[str, Any]], selected_files: list[tuple[str, Path]], processed_paths: set[str], started_at: float, args: argparse.Namespace) -> None:
    write_csv(run_dir / "results.csv", rows, RESULT_FIELDS)
    write_csv(run_dir / "errors.csv", errors, ERROR_FIELDS)
    write_csv(run_dir / "rejected_candidates.csv", rejected_rows, REJECTED_FIELDS)
    summary = compute_summary(rows, errors, rejected_rows, selected_files, processed_paths, started_at, args)
    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "needs_review_samples.json", needs_review_samples(rows))
    write_json(run_dir / "worst_20_documents.json", worst_documents(rows, errors))


def compute_summary(rows: list[dict[str, Any]], errors: list[dict[str, Any]], rejected_rows: list[dict[str, Any]], selected_files: list[tuple[str, Path]], processed_paths: set[str], started_at: float, args: argparse.Namespace) -> dict[str, Any]:
    processed = len(rows) + len(errors)
    elapsed = max(time.perf_counter() - started_at, 0.001)
    times = [as_float(row.get("processing_time_seconds")) for row in rows]
    times = [value for value in times if value is not None]
    average_time = statistics.mean(times) if times else None
    validation_counts = Counter(row.get("validation_status") or "error" for row in rows)
    missing_counter = Counter(field for row in rows for field in str(row.get("missing_fields") or "").split(";") if field)
    rejected_counter = Counter(str(row.get("value")) for row in rejected_rows if row.get("value") not in (None, ""))
    cache_hits = sum(1 for row in rows if str(row.get("ocr_cache_hit")).lower() == "true")
    layout_hits = sum(1 for row in rows if str(row.get("layout_cache_hit")).lower() == "true")
    total_line_valid = sum(int(as_float(row.get("line_items_validated")) or 0) for row in rows)
    total_line_review = sum(int(as_float(row.get("line_items_needs_review")) or 0) for row in rows)
    totals_known = [row for row in rows if str(row.get("totals_consistent")).lower() in {"true", "false"}]
    totals_ok = sum(1 for row in totals_known if str(row.get("totals_consistent")).lower() == "true")
    return {
        "run_id": args.run_id,
        "mode": args.mode,
        "seed": args.seed,
        "selected_documents": len(selected_files),
        "docs_processed": processed,
        "docs_remaining": max(0, len(selected_files) - len(processed_paths)),
        "success_count": len(rows),
        "error_count": len(errors),
        "average_time_per_doc": round(average_time, 3) if average_time is not None else None,
        "estimated_time_for_full_8000_seconds": round((average_time or 0) * 8000, 1) if average_time else None,
        "estimated_time_for_full_8000_hours": round(((average_time or 0) * 8000) / 3600, 2) if average_time else None,
        "elapsed_seconds": round(elapsed, 1),
        "throughput_docs_per_hour": round((processed / elapsed) * 3600, 2) if processed else None,
        "validation_distribution": dict(validation_counts),
        "valid": validation_counts.get("valid", 0),
        "needs_review": validation_counts.get("needs_review", 0),
        "invalid": validation_counts.get("invalid", 0),
        "top_missing_fields": missing_counter.most_common(20),
        "top_rejected_values": rejected_counter.most_common(20),
        "line_items_validated": total_line_valid,
        "line_items_needs_review": total_line_review,
        "totals_consistency_rate": round(totals_ok / len(totals_known), 3) if totals_known else None,
        "ocr_cache_hit_rate": round(cache_hits / len(rows), 3) if rows else 0.0,
        "layout_cache_hit_rate": round(layout_hits / len(rows), 3) if rows else 0.0,
    }


def needs_review_samples(rows: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    samples = [row for row in rows if row.get("validation_status") == "needs_review"]
    return samples[:limit]


def worst_documents(rows: list[dict[str, Any]], errors: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    scored = []
    for row in rows:
        score = 0
        score += len(str(row.get("missing_fields") or "").split(";")) if row.get("missing_fields") else 0
        score += int(as_float(row.get("rejected_values_count")) or 0)
        score += 5 if row.get("validation_status") == "invalid" else 2 if row.get("validation_status") == "needs_review" else 0
        scored.append((score, row))
    for error in errors:
        scored.append((99, error))
    return [row for _score, row in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]


def write_html_report(run_dir: Path) -> None:
    summary = load_json(run_dir / "summary.json") if (run_dir / "summary.json").exists() else {}
    rows = load_csv_rows(run_dir / "results.csv")[:100]
    html_rows = "\n".join(
        f"<tr><td>{html.escape(str(row.get('filename','')))}</td><td>{html.escape(str(row.get('validation_status','')))}</td><td>{html.escape(str(row.get('missing_fields','')))}</td><td>{html.escape(str(row.get('processing_time_seconds','')))}</td></tr>"
        for row in rows
    )
    content = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Evaluation Report</title>
<style>body{{font-family:Arial;margin:24px;color:#172033}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d9e0ea;padding:8px;text-align:left}}pre{{background:#f6f8fb;padding:12px;border:1px solid #d9e0ea}}</style></head>
<body><h1>OCR-to-ERP Evaluation Report</h1><pre>{html.escape(json.dumps(summary, indent=2, ensure_ascii=False))}</pre>
<h2>First 100 Results</h2><table><thead><tr><th>File</th><th>Status</th><th>Missing fields</th><th>Seconds</th></tr></thead><tbody>{html_rows}</tbody></table></body></html>"""
    (run_dir / "report.html").write_text(content, encoding="utf-8")


def save_checkpoint(run_dir: Path, selected_files: list[tuple[str, Path]], processed_paths: set[str], args: argparse.Namespace) -> None:
    payload = {
        "mode": args.mode,
        "seed": args.seed,
        "selected_files": [{"batch": batch, "path": str(path.resolve())} for batch, path in selected_files],
        "processed_paths": sorted(processed_paths),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(run_dir / "checkpoint.json", payload)


def load_checkpoint(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "checkpoint.json"
    return load_json(path) if path.exists() else {}


def print_progress(index: int, total: int, rows: list[dict[str, Any]], errors: list[dict[str, Any]], started_at: float) -> None:
    elapsed = max(time.perf_counter() - started_at, 0.001)
    avg = elapsed / max(1, len(rows) + len(errors))
    remaining = max(0, total - index)
    print(f"Progress {index}/{total} avg={avg:.2f}s/doc eta={remaining * avg / 60:.1f}m errors={len(errors)}")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def upsert_row(rows: list[dict[str, Any]], row: dict[str, Any], key: str) -> list[dict[str, Any]]:
    by_key = {existing.get(key): existing for existing in rows}
    by_key[row.get(key)] = row
    return list(by_key.values())


def compute_file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_hash(path: Path) -> str:
    try:
        return compute_file_hash(path)
    except Exception:
        return ""


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)[:120]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
