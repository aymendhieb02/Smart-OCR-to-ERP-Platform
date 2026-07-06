from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.document_classifier import classify_document
from app.services.dynamic_tables import build_dynamic_review_payload
from app.services.erp_mapper import build_erp_json, map_to_flat_erp
from app.services.field_enricher import build_expanded_fields, build_field_boxes
from app.services.field_extractor import extract_with_candidates
from app.services.file_loader import SUPPORTED_EXTENSIONS, load_document
from app.services.layout_analyzer import LayoutAnalyzer
from app.services.ocr_engine import OCREngine
from app.services.validation_explainer import build_validation_explanation
from app.services.validator import validate_invoice
from app.utils.helpers import normalize_text, parse_amount, parse_date


DEFAULT_SOURCE = Path(r"D:\Stage_mr_f\sources")
DEFAULT_REPORT_DIR = ROOT / "dataset" / "reports" / "benchmark_8000"
DEFAULT_PREDICTION_DIR = ROOT / "dataset" / "predictions" / "benchmark_8000"
DEFAULT_LABEL_DIR = ROOT / "dataset" / "labels" / "benchmark_8000"
SUPPORTED = {suffix.lower() for suffix in SUPPORTED_EXTENSIONS}

CSV_FIELDS = [
    "batch",
    "filename",
    "file_path",
    "status",
    "document_type",
    "validation_status",
    "erp_decision",
    "ocr_engine",
    "ocr_confidence",
    "overall_confidence",
    "processing_time_seconds",
    "page_count",
    "extracted_supplier_name",
    "extracted_customer_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "amount_ht",
    "tva_amount",
    "amount_ttc",
    "tax_rate",
    "line_items_count",
    "has_invoice_number",
    "has_invoice_date",
    "has_amount_ttc",
    "has_supplier",
    "has_customer",
    "has_line_items",
    "error_message",
]

LABEL_FIELDS = [
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "amount_ttc",
]


def main() -> None:
    args = parse_args()
    source = Path(args.source).resolve()
    report_dir = Path(args.output).resolve()
    prediction_dir = Path(args.predictions).resolve()
    label_dir = Path(args.labels).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(report_dir / "benchmark.log")
    files = scan_files(source, args.batch)
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise SystemExit(f"No supported files found under {source}")

    existing_rows = load_existing_rows(report_dir / "results.csv") if not args.force else {}
    engine = OCREngine()
    iterator = progress(files)
    for index, path in enumerate(iterator, start=1):
        batch = infer_batch(source, path)
        prediction_path = prediction_dir / safe_prediction_name(batch, path)
        if prediction_path.exists() and not args.force:
            row = existing_rows.get(str(path.resolve()))
            if row:
                logging.info("Skipping existing prediction %s", path)
                append_or_update_csv(report_dir / "results.csv", row)
                continue

        start = time.perf_counter()
        logging.info("Processing %s/%s: %s", index, len(files), path)
        try:
            prediction, row = process_file(path, batch, engine, prediction_path)
            label = load_label(label_dir, batch, path)
            if label:
                prediction["label_comparison"] = compare_label(label, prediction)
                prediction_path.write_text(json.dumps(prediction, indent=2, ensure_ascii=False), encoding="utf-8")
            row["processing_time_seconds"] = round(time.perf_counter() - start, 3)
            append_or_update_csv(report_dir / "results.csv", row)
            logging.info(
                "SUCCESS %s %.3fs validation=%s",
                path.name,
                row["processing_time_seconds"],
                row["validation_status"],
            )
        except Exception as exc:
            row = error_row(path, batch, round(time.perf_counter() - start, 3), str(exc))
            append_or_update_csv(report_dir / "results.csv", row)
            logging.exception("ERROR %s: %s", path, exc)

        write_error_analysis(report_dir / "results.csv", report_dir / "error_analysis.csv")
        write_manual_review_sample(report_dir / "results.csv", report_dir / "manual_review_sample.csv", prediction_dir)

    write_error_analysis(report_dir / "results.csv", report_dir / "error_analysis.csv")
    write_manual_review_sample(report_dir / "results.csv", report_dir / "manual_review_sample.csv", prediction_dir)
    metrics = compute_metrics(read_rows(report_dir / "results.csv"))
    (report_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Large-scale OCR-to-ERP benchmark runner.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Root folder containing batch_1, batch_2, batch_3.")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_DIR), help="Benchmark report output directory.")
    parser.add_argument("--predictions", default=str(DEFAULT_PREDICTION_DIR), help="Prediction JSON output directory.")
    parser.add_argument("--labels", default=str(DEFAULT_LABEL_DIR), help="Optional ground-truth label directory.")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N files.")
    parser.add_argument("--force", action="store_true", help="Reprocess files even when predictions already exist.")
    parser.add_argument("--batch", choices=["batch_1", "batch_2", "batch_3"], help="Process one batch only.")
    return parser.parse_args()


def setup_logging(log_path: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def scan_files(source: Path, batch: str | None = None) -> list[Path]:
    batches = [batch] if batch else ["batch_1", "batch_2", "batch_3"]
    files: list[Path] = []
    for batch_name in batches:
        batch_dir = source / batch_name
        if not batch_dir.exists():
            logging.warning("Batch folder missing: %s", batch_dir)
            continue
        files.extend(path for path in batch_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED)
    return sorted(files, key=lambda item: str(item).lower())


def progress(files: list[Path]):
    try:
        from tqdm import tqdm

        return tqdm(files, desc="Benchmark", unit="doc")
    except ImportError:
        return files


def process_file(path: Path, batch: str, engine: OCREngine, prediction_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    document = load_document(path, path.name)
    ocr_result = engine.run(document.images, document.embedded_text)
    if not ocr_result.raw_text:
        raise ValueError("No text could be extracted from the invoice")

    layout_blocks = LayoutAnalyzer(ocr_result.lines).detect_layout_blocks()
    classification = classify_document(ocr_result.raw_text, ocr_result.lines)
    fields, candidates, field_confidences, extraction_debug = extract_with_candidates(
        ocr_result.raw_text,
        ocr_result.lines,
        classification,
    )
    expanded_fields = build_expanded_fields(fields, candidates, field_confidences, ocr_result.raw_text)
    field_boxes = build_field_boxes(expanded_fields)
    validation = validate_invoice(fields, ocr_result, classification)
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
    erp_export = map_to_flat_erp(erp_json)
    prediction = {
        "source_file": path.name,
        "source_path": str(path),
        "batch": batch,
        "extracted_text": ocr_result.raw_text,
        "document_classification": classification.model_dump(mode="json"),
        "detected_fields": fields.model_dump(mode="json"),
        "expanded_fields": {key: value.model_dump(mode="json") for key, value in expanded_fields.items()},
        "field_confidences": field_confidences,
        "field_boxes": [box.model_dump(mode="json") for box in field_boxes],
        "layout_blocks": [block.model_dump(mode="json") for block in layout_blocks],
        "ocr_blocks": [line.model_dump(mode="json") for line in ocr_result.lines],
        "dynamic_tables": [table.model_dump(mode="json") for table in dynamic_tables],
        "extraction_layer": extraction_layer.model_dump(mode="json"),
        "erp_layer": erp_layer.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
        "validation_explanation": validation_explanation.model_dump(mode="json"),
        "erp_json": erp_json.model_dump(mode="json"),
        "erp_export": erp_export.model_dump(mode="json"),
        "metadata": {
            "ocr_engine": ocr_result.engine,
            "ocr_confidence": ocr_result.confidence,
            "page_count": ocr_result.page_count,
        },
    }
    prediction_path.write_text(json.dumps(prediction, indent=2, ensure_ascii=False), encoding="utf-8")
    row = summary_row(path, batch, prediction, prediction_path)
    return prediction, row


def summary_row(path: Path, batch: str, prediction: dict[str, Any], prediction_path: Path) -> dict[str, Any]:
    fields = prediction["detected_fields"]
    validation = prediction["validation"]
    classification = prediction["document_classification"]
    metadata = prediction["metadata"]
    line_items = fields.get("line_items") or []
    erp_decision = "export_allowed" if validation.get("status") == "valid" else "review_required" if validation.get("status") == "needs_review" else "blocked"
    return {
        "batch": batch,
        "filename": path.name,
        "file_path": str(path.resolve()),
        "status": "success",
        "document_type": classification.get("document_type"),
        "validation_status": validation.get("status"),
        "erp_decision": erp_decision,
        "ocr_engine": metadata.get("ocr_engine"),
        "ocr_confidence": metadata.get("ocr_confidence"),
        "overall_confidence": prediction.get("erp_json", {}).get("quality", {}).get("overall_confidence"),
        "processing_time_seconds": "",
        "page_count": metadata.get("page_count"),
        "extracted_supplier_name": fields.get("supplier_name"),
        "extracted_customer_name": fields.get("customer_name"),
        "invoice_number": fields.get("invoice_number"),
        "invoice_date": fields.get("invoice_date"),
        "due_date": fields.get("due_date"),
        "currency": fields.get("currency"),
        "amount_ht": fields.get("amount_ht"),
        "tva_amount": fields.get("tva_amount"),
        "amount_ttc": fields.get("amount_ttc"),
        "tax_rate": fields.get("tax_rate"),
        "line_items_count": len(line_items),
        "has_invoice_number": bool(fields.get("invoice_number")),
        "has_invoice_date": bool(fields.get("invoice_date")),
        "has_amount_ttc": fields.get("amount_ttc") is not None,
        "has_supplier": bool(fields.get("supplier_name") or fields.get("supplier_tax_id")),
        "has_customer": bool(fields.get("customer_name") or fields.get("customer_tax_id")),
        "has_line_items": bool(line_items),
        "error_message": "",
        "prediction_json": str(prediction_path.resolve()),
    }


def error_row(path: Path, batch: str, elapsed: float, message: str) -> dict[str, Any]:
    return {
        **{field: "" for field in CSV_FIELDS},
        "batch": batch,
        "filename": path.name,
        "file_path": str(path.resolve()),
        "status": "error",
        "processing_time_seconds": elapsed,
        "error_message": message,
    }


def append_or_update_csv(csv_path: Path, row: dict[str, Any]) -> None:
    rows = {existing["file_path"]: existing for existing in read_rows(csv_path)}
    rows[row["file_path"]] = {field: row.get(field, "") for field in CSV_FIELDS}
    rows[row["file_path"]]["prediction_json"] = row.get("prediction_json", "")
    fieldnames = CSV_FIELDS + ["prediction_json"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows.values())


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_existing_rows(csv_path: Path) -> dict[str, dict[str, str]]:
    return {row["file_path"]: row for row in read_rows(csv_path)}


def infer_batch(source: Path, path: Path) -> str:
    try:
        return path.relative_to(source).parts[0]
    except ValueError:
        return path.parent.name


def safe_prediction_name(batch: str, path: Path) -> str:
    safe_stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in path.stem)
    return f"{batch}__{safe_stem}.json"


def load_label(label_dir: Path, batch: str, path: Path) -> dict[str, Any] | None:
    candidates = [
        label_dir / batch / f"{path.stem}.json",
        label_dir / f"{batch}__{path.stem}.json",
        label_dir / f"{path.stem}.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def compare_label(label: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    fields = prediction.get("detected_fields", {})
    comparison = {}
    for field in LABEL_FIELDS:
        expected = label.get(field)
        actual = fields.get(field)
        comparison[field] = {
            "expected": expected,
            "actual": actual,
            "match": values_match(expected, actual, field),
        }
    available = [item["match"] for item in comparison.values() if item["expected"] not in (None, "")]
    comparison["field_accuracy"] = round(sum(available) / len(available), 3) if available else None
    return comparison


def values_match(expected: Any, actual: Any, field: str) -> bool:
    if expected in (None, "") and actual in (None, ""):
        return True
    if field == "amount_ttc":
        e, a = parse_amount(str(expected)), parse_amount(str(actual))
        return e is not None and a is not None and abs(e - a) <= 0.01
    if field == "invoice_date":
        e, a = parse_date(str(expected)), parse_date(str(actual))
        return e is not None and a is not None and e == a
    return normalize_text(str(expected)).lower() == normalize_text(str(actual)).lower()


def compute_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    total = len(rows)
    successes = [row for row in rows if row.get("status") == "success"]
    failures = [row for row in rows if row.get("status") == "error"]
    times = [float(row["processing_time_seconds"]) for row in rows if as_float(row.get("processing_time_seconds")) is not None]
    ocr_conf = [float(row["ocr_confidence"]) for row in successes if as_float(row.get("ocr_confidence")) is not None]
    overall_conf = [float(row["overall_confidence"]) for row in successes if as_float(row.get("overall_confidence")) is not None]
    validation_counts = Counter(row.get("validation_status") or "error" for row in rows)
    document_counts = Counter(row.get("document_type") or "unknown" for row in successes)
    batch_counts = Counter(row.get("batch") or "unknown" for row in rows)
    return {
        "total_files": total,
        "successful_files": len(successes),
        "failed_files": len(failures),
        "average_processing_time": round(statistics.mean(times), 3) if times else None,
        "median_processing_time": round(statistics.median(times), 3) if times else None,
        "fastest_file": min(rows, key=lambda row: as_float(row.get("processing_time_seconds")) or 10**9).get("filename") if rows else None,
        "slowest_file": max(rows, key=lambda row: as_float(row.get("processing_time_seconds")) or 0).get("filename") if rows else None,
        "throughput_documents_per_hour": round(3600 / statistics.mean(times), 2) if times and statistics.mean(times) else None,
        "average_ocr_confidence": round(statistics.mean(ocr_conf), 3) if ocr_conf else None,
        "average_overall_confidence": round(statistics.mean(overall_conf), 3) if overall_conf else None,
        "validation_distribution": dict(validation_counts),
        "document_type_distribution": dict(document_counts),
        "batch_distribution": dict(batch_counts),
        "missing_field_rates": missing_field_rates(successes),
    }


def missing_field_rates(rows: list[dict[str, str]]) -> dict[str, float]:
    checks = {
        "invoice_number_missing_pct": "has_invoice_number",
        "invoice_date_missing_pct": "has_invoice_date",
        "amount_ttc_missing_pct": "has_amount_ttc",
        "supplier_missing_pct": "has_supplier",
        "customer_missing_pct": "has_customer",
        "line_items_missing_pct": "has_line_items",
    }
    result = {}
    for output, column in checks.items():
        missing = sum(1 for row in rows if str(row.get(column)).lower() not in {"true", "1"})
        result[output] = round((missing / len(rows)) * 100, 2) if rows else 0.0
    return result


def write_error_analysis(results_csv: Path, output_csv: Path) -> None:
    rows = read_rows(results_csv)
    categories = Counter()
    details: list[dict[str, Any]] = []
    for row in rows:
        for category in categorize_row(row):
            categories[category] += 1
            details.append({
                "category": category,
                "batch": row.get("batch"),
                "filename": row.get("filename"),
                "file_path": row.get("file_path"),
                "validation_status": row.get("validation_status"),
                "error_message": row.get("error_message"),
            })
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["category", "count", "batch", "filename", "file_path", "validation_status", "error_message"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for category, count in categories.most_common():
            writer.writerow({"category": category, "count": count})
        for detail in details:
            writer.writerow({"count": "", **detail})


def categorize_row(row: dict[str, str]) -> list[str]:
    categories = []
    message = (row.get("error_message") or "").lower()
    if row.get("status") == "error":
        if "unsupported file format" in message:
            categories.append("unsupported file")
        elif "unreadable" in message or "cannot open" in message:
            categories.append("file loading error")
        elif "no text" in message:
            categories.append("no text extracted")
        elif "ocr" in message:
            categories.append("OCR failure")
        else:
            categories.append("unknown error")
        return categories
    if str(row.get("has_invoice_date")).lower() != "true":
        categories.append("missing invoice date")
    if str(row.get("has_amount_ttc")).lower() != "true":
        categories.append("missing total TTC")
    if str(row.get("has_supplier")).lower() != "true":
        categories.append("missing supplier")
    if str(row.get("has_customer")).lower() != "true":
        categories.append("missing customer")
    if str(row.get("has_line_items")).lower() != "true":
        categories.append("product table detected but no line items parsed")
    confidence = as_float(row.get("ocr_confidence"))
    if confidence is not None and confidence < 0.65:
        categories.append("low OCR confidence")
    if row.get("validation_status") == "invalid":
        categories.append("validation failed")
    return categories or ["ok"]


def write_manual_review_sample(results_csv: Path, output_csv: Path, prediction_dir: Path) -> None:
    rows = [row for row in read_rows(results_csv) if row.get("status") == "success"]
    selected = (
        select_by_status(rows, "valid", 40)
        + select_by_status(rows, "needs_review", 40)
        + select_by_status(rows, "invalid", 20)
    )
    fieldnames = [
        "filename",
        "file_path",
        "prediction_json",
        "document_type",
        "validation_status",
        "supplier_name_expected",
        "supplier_name_predicted",
        "invoice_number_expected",
        "invoice_number_predicted",
        "invoice_date_expected",
        "invoice_date_predicted",
        "amount_ttc_expected",
        "amount_ttc_predicted",
        "manual_correct_supplier",
        "manual_correct_invoice_number",
        "manual_correct_invoice_date",
        "manual_correct_amount_ttc",
        "notes",
    ]
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in selected:
            writer.writerow({
                "filename": row.get("filename"),
                "file_path": row.get("file_path"),
                "prediction_json": row.get("prediction_json") or str(prediction_dir / f"{row.get('batch')}__{Path(row.get('filename', '')).stem}.json"),
                "document_type": row.get("document_type"),
                "validation_status": row.get("validation_status"),
                "supplier_name_expected": "",
                "supplier_name_predicted": row.get("extracted_supplier_name"),
                "invoice_number_expected": "",
                "invoice_number_predicted": row.get("invoice_number"),
                "invoice_date_expected": "",
                "invoice_date_predicted": row.get("invoice_date"),
                "amount_ttc_expected": "",
                "amount_ttc_predicted": row.get("amount_ttc"),
                "manual_correct_supplier": "",
                "manual_correct_invoice_number": "",
                "manual_correct_invoice_date": "",
                "manual_correct_amount_ttc": "",
                "notes": "",
            })


def select_by_status(rows: list[dict[str, str]], status: str, limit: int) -> list[dict[str, str]]:
    return sorted(
        [row for row in rows if row.get("validation_status") == status],
        key=lambda row: row.get("overall_confidence") or "",
        reverse=True,
    )[:limit]


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
