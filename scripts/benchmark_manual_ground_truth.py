from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ocr_engine import OCREngine
from app.services.pipeline_runner import process_document_file
from scripts.manual_benchmark_utils import (
    DEFAULT_BENCHMARK_ROOT,
    FIELD_NAMES,
    compare_prediction_to_label,
    html_table,
    line_items_from_response,
    load_manifest_documents,
    markdown_table,
    prediction_fields_from_response,
    read_csv,
    safe_json_default,
    summarize_results,
    validate_verified_label,
    write_csv,
    write_json,
)


RESULT_COLUMNS = [
    "run_id",
    "dataset",
    "filename",
    "document_type_hint",
    "status",
    "error_message",
    "processing_time_seconds",
    "validation_status",
    "erp_ready_status",
    "erp_export_allowed",
    "document_type_true",
    "document_type_pred",
    "supplier_name_true",
    "supplier_name_pred",
    "customer_name_true",
    "customer_name_pred",
    "invoice_number_true",
    "invoice_number_pred",
    "invoice_date_true",
    "invoice_date_pred",
    "currency_true",
    "currency_pred",
    "amount_ht_true",
    "amount_ht_pred",
    "tax_amount_true",
    "tax_amount_pred",
    "amount_ttc_true",
    "amount_ttc_pred",
    "line_items_truth_count",
    "line_items_pred_count",
    "line_item_precision",
    "line_item_recall",
    "line_item_f1",
    "all_required_fields_correct",
    "all_financial_fields_correct",
    "fully_correct_document",
    "erp_ready_and_actually_correct",
    "false_erp_ready",
    "safely_routed_to_review",
    "incorrect_prediction_count",
    "missing_prediction_count",
    "label_path",
    "prediction_path",
    "source_path",
]

for field in FIELD_NAMES:
    RESULT_COLUMNS.extend([
        f"{field}_applicable",
        f"{field}_correct",
        f"{field}_prediction_missing",
    ])

RESULT_COLUMNS.extend([
    "line_items_applicable",
    "line_item_count_correct",
    "line_description_accuracy",
    "line_quantity_accuracy",
    "line_unit_price_accuracy",
    "line_total_accuracy",
])


def main() -> None:
    args = parse_args()
    benchmark_root = Path(args.benchmark_root).resolve()
    run_id = build_run_id(args.run_name)
    run_root = benchmark_root / "reports" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    checkpoint_path = run_root / "checkpoint.json"
    checkpoint = load_checkpoint(checkpoint_path) if args.resume else {"processed": []}
    processed = set(checkpoint.get("processed", []))
    documents = load_manifest_documents(benchmark_root)
    if not documents:
        raise SystemExit("No benchmark documents found. Run manual_label_helper.py --prepare first.")
    unverified = []
    for document in documents:
        try:
            validate_verified_label(document.label_path)
        except Exception as exc:
            unverified.append(f"{document.label_path}: {exc}")
    if unverified:
        raise SystemExit("Unverified labels refused. Verify labels before benchmarking:\n" + "\n".join(unverified))
    engine = OCREngine()
    rows_by_filename = {row["filename"]: row for row in read_csv(run_root / "results.csv")} if args.resume else {}

    for document in documents:
        if document.filename in processed and not args.force:
            continue
        prediction_path = run_root / "predictions" / f"{Path(document.filename).stem}.json"
        start = time.perf_counter()
        try:
            label = validate_verified_label(document.label_path)
            response = process_document_file(
                document.image_path,
                original_filename=document.filename,
                ocr_engine=engine,
                include_preview=False,
                persist_erp_json=False,
            )
            prediction_payload = response.model_dump(mode="json")
            write_json(prediction_path, prediction_payload)
            row = build_result_row(run_id, document, label, response, prediction_path, round(time.perf_counter() - start, 4))
        except Exception as exc:
            row = build_error_row(run_id, document, exc, prediction_path, round(time.perf_counter() - start, 4))
        rows_by_filename[document.filename] = row
        processed.add(document.filename)
        checkpoint["processed"] = sorted(processed)
        write_json(checkpoint_path, checkpoint)
        write_csv(run_root / "results.csv", list(rows_by_filename.values()), RESULT_COLUMNS)

    rows = list(rows_by_filename.values())
    write_csv(run_root / "results.csv", rows, RESULT_COLUMNS)
    write_errors(run_root / "errors.csv", rows)
    summary = summarize_results(rows)
    summary["run_id"] = run_id
    summary["run_name"] = args.run_name
    summary["benchmark_root"] = str(benchmark_root)
    write_json(run_root / "summary.json", summary)
    write_reports(run_root, rows, summary)
    mirror_latest_reports(benchmark_root, run_root)
    print(f"Run ID: {run_id}")
    print(f"Results: {run_root / 'results.csv'}")
    print(f"Summary: {run_root / 'summary.json'}")
    print(f"Report: {run_root / 'report.html'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the manually verified OCR-to-ERP ground-truth benchmark.")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT), help="Manual benchmark root folder.")
    parser.add_argument("--run-name", default="baseline", help="Human-readable run name. Also used as run ID when safe.")
    parser.add_argument("--force", action="store_true", help="Re-run even if predictions already exist.")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted run using checkpoint.json.")
    return parser.parse_args()


def build_run_id(run_name: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in run_name).strip("_")
    return safe or datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed": []}
    return json.loads(path.read_text(encoding="utf-8"))


def build_result_row(run_id: str, document, label: dict[str, Any], response, prediction_path: Path, elapsed: float) -> dict[str, Any]:
    pred_fields = prediction_fields_from_response(response)
    pred_lines = line_items_from_response(response)
    comparison = compare_prediction_to_label(pred_fields, pred_lines, label)
    field_results = comparison["fields"]
    line_results = comparison["line_items"]
    erp_ready_status = response.erp_readiness.get("erp_ready_status") if response.erp_readiness else response.validation.status
    erp_allowed = bool(response.erp_readiness.get("ready")) if response.erp_readiness else response.validation.status == "valid"
    incorrect = sum(result["applicable"] and result["correct"] is False for result in field_results.values())
    missing = sum(result["applicable"] and pred_fields.get(field) in (None, "") for field, result in field_results.items())
    fully_correct = comparison["fully_correct_document"]
    row = {
        "run_id": run_id,
        "dataset": document.dataset,
        "filename": document.filename,
        "document_type_hint": document.document_type_hint,
        "status": "success",
        "error_message": "",
        "processing_time_seconds": elapsed,
        "validation_status": response.validation.status,
        "erp_ready_status": erp_ready_status,
        "erp_export_allowed": erp_allowed,
        "document_type_true": label.get("document_type"),
        "document_type_pred": pred_fields.get("document_type"),
        "supplier_name_true": label.get("supplier_name"),
        "supplier_name_pred": pred_fields.get("supplier_name"),
        "customer_name_true": label.get("customer_name"),
        "customer_name_pred": pred_fields.get("customer_name"),
        "invoice_number_true": label.get("invoice_number"),
        "invoice_number_pred": pred_fields.get("invoice_number"),
        "invoice_date_true": label.get("invoice_date"),
        "invoice_date_pred": pred_fields.get("invoice_date"),
        "currency_true": label.get("currency"),
        "currency_pred": pred_fields.get("currency"),
        "amount_ht_true": label.get("amount_ht"),
        "amount_ht_pred": pred_fields.get("amount_ht"),
        "tax_amount_true": label.get("tax_amount"),
        "tax_amount_pred": pred_fields.get("tax_amount"),
        "amount_ttc_true": label.get("amount_ttc"),
        "amount_ttc_pred": pred_fields.get("amount_ttc"),
        "line_items_truth_count": line_results["truth_count"],
        "line_items_pred_count": line_results["predicted_count"],
        "line_item_precision": line_results["precision"],
        "line_item_recall": line_results["recall"],
        "line_item_f1": line_results["f1"],
        "line_items_applicable": line_results["applicable"],
        "line_item_count_correct": line_results["correct_count"],
        "line_description_accuracy": line_results["description_accuracy"],
        "line_quantity_accuracy": line_results["quantity_accuracy"],
        "line_unit_price_accuracy": line_results["unit_price_accuracy"],
        "line_total_accuracy": line_results["line_total_accuracy"],
        "all_required_fields_correct": comparison["all_required_fields_correct"],
        "all_financial_fields_correct": comparison["all_financial_fields_correct"],
        "fully_correct_document": fully_correct,
        "erp_ready_and_actually_correct": erp_allowed and fully_correct,
        "false_erp_ready": erp_allowed and not fully_correct,
        "safely_routed_to_review": (not erp_allowed) and not fully_correct,
        "incorrect_prediction_count": incorrect,
        "missing_prediction_count": missing,
        "label_path": str(document.label_path.resolve()),
        "prediction_path": str(prediction_path.resolve()),
        "source_path": str(document.source_path.resolve()),
    }
    for field in FIELD_NAMES:
        result = field_results[field]
        row[f"{field}_applicable"] = result["applicable"]
        row[f"{field}_correct"] = result["correct"]
        row[f"{field}_prediction_missing"] = result["applicable"] and pred_fields.get(field) in (None, "")
    return row


def build_error_row(run_id: str, document, exc: Exception, prediction_path: Path, elapsed: float) -> dict[str, Any]:
    row = {column: "" for column in RESULT_COLUMNS}
    row.update({
        "run_id": run_id,
        "dataset": document.dataset,
        "filename": document.filename,
        "document_type_hint": document.document_type_hint,
        "status": "error",
        "error_message": str(exc),
        "processing_time_seconds": elapsed,
        "label_path": str(document.label_path.resolve()),
        "prediction_path": str(prediction_path.resolve()),
        "source_path": str(document.source_path.resolve()),
    })
    return row


def write_errors(path: Path, rows: list[dict[str, Any]]) -> None:
    error_rows = [row for row in rows if row.get("status") == "error" or row.get("incorrect_prediction_count")]
    columns = ["filename", "status", "error_message", "incorrect_prediction_count", "missing_prediction_count", "erp_ready_status", "false_erp_ready", "label_path", "prediction_path"]
    write_csv(path, error_rows, columns)


def write_reports(run_root: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    columns = [
        "filename",
        "status",
        "erp_ready_status",
        "fully_correct_document",
        "false_erp_ready",
        "supplier_name_correct",
        "invoice_number_correct",
        "amount_ttc_correct",
        "line_item_f1",
        "processing_time_seconds",
    ]
    for row in rows:
        for field in ("supplier_name", "invoice_number", "amount_ttc"):
            row[f"{field}_correct"] = row.get(f"{field}_correct")
    field_rows = [
        {"field": field, **metrics}
        for field, metrics in summary["field_metrics"].items()
    ]
    markdown = [
        f"# Manual Ground-Truth Benchmark: {summary['run_id']}",
        "",
        "This report measures true extraction accuracy only for manually verified labels.",
        "OCR confidence is not used as accuracy.",
        "",
        "## Summary",
        f"- Documents: {summary['documents_total']}",
        f"- Success: {summary['documents_success']}",
        f"- Errors: {summary['documents_error']}",
        f"- False ERP Ready count: {summary['document_metrics']['false_erp_ready_count']}",
        f"- Fully correct document rate: {summary['document_metrics']['fully_correct_document_rate']}",
        "",
        "## Field Accuracy",
        markdown_table(field_rows, ["field", "correct", "applicable", "accuracy"]),
        "",
        "## Document Comparison",
        markdown_table(rows, columns),
    ]
    (run_root / "report.md").write_text("\n".join(markdown), encoding="utf-8")

    html_content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Manual Ground-Truth Benchmark {summary['run_id']}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; margin: 14px 0 28px; }}
    th, td {{ border: 1px solid #d9e0ea; padding: 8px; text-align: left; font-size: 13px; }}
    th {{ background: #f6f8fb; }}
    .metric {{ display: inline-block; border: 1px solid #d9e0ea; border-radius: 8px; padding: 12px; margin: 6px; }}
  </style>
</head>
<body>
  <h1>Manual Ground-Truth Benchmark: {summary['run_id']}</h1>
  <p>This report separates true accuracy from OCR confidence. Missing ground-truth fields are excluded from denominators.</p>
  <div class="metric">Documents: {summary['documents_total']}</div>
  <div class="metric">Success: {summary['documents_success']}</div>
  <div class="metric">Errors: {summary['documents_error']}</div>
  <div class="metric">False ERP Ready: {summary['document_metrics']['false_erp_ready_count']}</div>
  <h2>Field Accuracy</h2>
  {html_table(field_rows, ["field", "correct", "applicable", "accuracy"])}
  <h2>Line Item Metrics</h2>
  {html_table([summary["line_item_metrics"]], list(summary["line_item_metrics"].keys()))}
  <h2>Document Metrics</h2>
  {html_table([summary["document_metrics"]], list(summary["document_metrics"].keys()))}
  <h2>Document-by-document comparison</h2>
  {html_table(rows, columns + ["label_path", "prediction_path", "source_path"])}
</body>
</html>
"""
    (run_root / "report.html").write_text(html_content, encoding="utf-8")
    write_manual_review_html(run_root / "manual_review.html", rows)


def write_manual_review_html(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = ["filename", "erp_ready_status", "fully_correct_document", "false_erp_ready", "label_path", "prediction_path", "source_path"]
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Manual Review</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px}}</style>
</head><body><h1>Manual Review Queue</h1>{html_table(rows, columns)}</body></html>"""
    path.write_text(content, encoding="utf-8")


def mirror_latest_reports(benchmark_root: Path, run_root: Path) -> None:
    reports_root = benchmark_root / "reports"
    for name in ("results.csv", "summary.json", "report.md", "report.html", "errors.csv", "manual_review.html"):
        source = run_root / name
        if source.exists():
            target = reports_root / name
            target.write_bytes(source.read_bytes())


if __name__ == "__main__":
    main()
