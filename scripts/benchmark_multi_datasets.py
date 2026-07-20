from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import logging
import os
import random
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.schemas import ProcessInvoiceResponse
from app.services.ocr_engine import OCREngine
from app.services.ocr_profiles import PROFILES
from app.services.party_name_normalizer import compare_party_names
from app.services.pipeline_runner import process_document_file
from scripts.dataset_label_adapter import load_ground_truth
from scripts.generate_multi_dataset_report import generate_reports

DATASETS_ROOT_DEFAULT = ROOT.parent / "sources" / "datasets"
OUTPUT_ROOT = ROOT / "dataset" / "reports" / "multi_dataset_benchmark"
PREDICTIONS_ROOT = OUTPUT_ROOT / "predictions"
CHECKPOINT_PATH = OUTPUT_ROOT / "checkpoint.json"
LOG_PATH = OUTPUT_ROOT / "benchmark.log"
ENVIRONMENT_PATH = OUTPUT_ROOT / "environment.json"
SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".pdf"}
LABEL_DIR_NAMES = {"labels", "ground_truth", "annotations", "json"}
RESULT_FIELDS = [
    "dataset_name",
    "split",
    "filename",
    "file_path",
    "label_path",
    "has_ground_truth",
    "status",
    "error_message",
    "error_category",
    "processing_time_seconds",
    "document_type_pred",
    "validation_status",
    "erp_export_allowed",
    "ocr_confidence",
    "overall_confidence",
    "ocr_engine_used",
    "ocr_mode",
    "total_paddle_calls",
    "fallback_region_count",
    "disk_cache_hit",
    "full_page_ocr_inference",
    "fallback_ocr_inference",
    "supplier_name_pred",
    "customer_name_pred",
    "invoice_number_pred",
    "invoice_date_pred",
    "due_date_pred",
    "currency_pred",
    "amount_ht_pred",
    "tva_amount_pred",
    "amount_ttc_pred",
    "tax_rate_pred",
    "line_items_count_pred",
    "validated_line_items_count_pred",
    "review_line_items_count_pred",
    "any_line_items_pred",
    "has_supplier_pred",
    "has_customer_pred",
    "has_invoice_number_pred",
    "has_invoice_date_pred",
    "has_amount_ttc_pred",
    "has_line_items_pred",
    "has_validated_line_items_pred",
    "has_review_line_items_pred",
    "supplier_name_true",
    "customer_name_true",
    "invoice_number_true",
    "invoice_date_true",
    "amount_ttc_true",
    "document_type_true",
    "supplier_name_correct",
    "customer_name_correct",
    "invoice_number_correct",
    "invoice_date_correct",
    "amount_ttc_correct",
    "document_type_correct",
    "prediction_path",
]
MANUAL_REVIEW_FIELDS = [
    "dataset_name",
    "validation_status",
    "filename",
    "file_path",
    "label_path",
    "prediction_path",
    "supplier_name_pred",
    "supplier_name_true",
    "invoice_number_pred",
    "invoice_number_true",
    "amount_ttc_pred",
    "amount_ttc_true",
    "review_notes",
    "verified_by",
]


@dataclass(frozen=True)
class DatasetDocument:
    dataset_name: str
    split: str
    file_path: Path
    label_path: Path | None


def main() -> None:
    args = parse_args()
    if uses_p1_benchmark(args):
        from scripts import large_benchmark_runner

        raise SystemExit(large_benchmark_runner.run(args, sys.modules[__name__]))

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    environment = collect_environment_status()
    write_json(ENVIRONMENT_PATH, environment)

    if args.check_env:
        print(format_environment_status(environment))
        if not environment["ready"]:
            raise SystemExit(1)
        return

    if not environment["ready"]:
        print(format_environment_status(environment))
        if previous_failed_predictions_exist():
            print("Previous failed predictions were found. After fixing OCR, rerun with --force.")
        print("BENCHMARK ABORTED: no OCR engine is available.")
        raise SystemExit("OCR engine not available. Install PaddleOCR or Tesseract before running benchmark.")

    setup_logging()
    if previous_failed_predictions_exist() and not args.force:
        print("Warning: previous failed predictions exist. Rerun with --force after fixing OCR if you want a clean benchmark.")

    rng = random.Random(args.seed)
    datasets = discover_datasets(Path(args.datasets_root).resolve(), dataset_filter=args.dataset)
    selected = sample_documents(datasets, limit_per_dataset=args.limit_per_dataset, rng=rng)
    checkpoint = load_checkpoint()
    results = read_csv(OUTPUT_ROOT / "results.csv")
    existing_rows = {(row.get("dataset_name"), row.get("file_path")): row for row in results}
    engine = OCREngine(mode=args.ocr_mode, use_disk_cache=not args.no_ocr_cache, refresh_cache=args.refresh_ocr_cache)

    iterator = with_progress(selected, "Benchmark")
    for document in iterator:
        prediction_path = build_prediction_path(document)
        key = (document.dataset_name, str(document.file_path.resolve()))
        if not args.force and key in checkpoint.get("processed", {}) and prediction_path.exists():
            continue
        start = time.perf_counter()
        ground_truth = load_ground_truth(document.label_path) if document.label_path else load_ground_truth(None)
        try:
            response = process_document_file(
                document.file_path,
                original_filename=document.file_path.name,
                ocr_engine=engine,
                include_preview=False,
                persist_erp_json=False,
                ocr_mode=args.ocr_mode,
                use_ocr_cache=not args.no_ocr_cache,
                refresh_ocr_cache=args.refresh_ocr_cache,
            )
            prediction = build_prediction_payload(document, response, ground_truth)
            write_json(prediction_path, prediction)
            row = build_result_row(document, response, ground_truth, prediction_path, round(time.perf_counter() - start, 3))
        except Exception as exc:
            prediction = build_error_prediction_payload(document, exc, ground_truth)
            write_json(prediction_path, prediction)
            row = build_error_row(document, exc, ground_truth, round(time.perf_counter() - start, 3), prediction_path)
            logging.exception("Failed processing %s", document.file_path)
        existing_rows[key] = row
        checkpoint.setdefault("processed", {})[str(document.file_path.resolve())] = {
            "dataset_name": document.dataset_name,
            "prediction_path": str(prediction_path),
            "updated_at": datetime.utcnow().isoformat(),
        }
        write_rows(list(existing_rows.values()))
        save_checkpoint(checkpoint)

    write_rows(list(existing_rows.values()))
    write_manual_review_sample(list(existing_rows.values()), rng)
    generate_reports(OUTPUT_ROOT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the OCR-to-ERP pipeline across multiple datasets.")
    parser.add_argument("--datasets-root", default=str(DATASETS_ROOT_DEFAULT), help="Root folder containing multiple datasets.")
    parser.add_argument("--dataset", default=None, help="Optional single dataset name to benchmark.")
    parser.add_argument("--datasets", nargs="*", default=None, help="Optional list of dataset names to benchmark.")
    parser.add_argument("--limit-per-dataset", type=int, default=50, help="Maximum sampled documents per dataset.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic sampling.")
    parser.add_argument("--force", action="store_true", help="Reprocess even if prediction JSON already exists.")
    parser.add_argument("--check-env", action="store_true", help="Check benchmark OCR environment and exit.")
    parser.add_argument("--ocr-mode", choices=["fast", "balanced", "accurate"], default="balanced", help="OCR mode used by the benchmark.")
    parser.add_argument("--no-ocr-cache", action="store_true", help="Disable disk OCR cache.")
    parser.add_argument("--refresh-ocr-cache", action="store_true", help="Ignore existing OCR cache and rewrite entries.")
    parser.add_argument("--run-id", default=None, help="P1 isolated benchmark run ID.")
    parser.add_argument("--resume", action="store_true", help="Resume a previous P1 run.")
    parser.add_argument("--restart", action="store_true", help="Restart an existing P1 run ID after clearing run state files.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry documents marked failed in the checkpoint.")
    parser.add_argument("--retry-timeouts", action="store_true", help="Retry timeout documents from an existing P1 run.")
    parser.add_argument("--retry-errors", action="store_true", help="Retry error documents from an existing P1 run.")
    parser.add_argument("--force-reprocess", action="store_true", help="Reprocess selected documents even if previous result rows exist.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip documents already present in results.jsonl.")
    parser.add_argument("--limit", type=int, default=None, help="Global document limit after deterministic selection.")
    parser.add_argument("--offset", type=int, default=0, help="Global document offset after deterministic selection.")
    parser.add_argument("--document-types", nargs="*", default=None, help="Reserved metadata filter for future labeled datasets.")
    parser.add_argument("--languages", nargs="*", default=None, help="Reserved metadata filter for future labeled datasets.")
    parser.add_argument("--workers", type=int, default=1, help="P1 worker count. Current safe mode runs one worker.")
    parser.add_argument("--document-timeout", type=float, default=None, help="Per-document timeout budget in seconds.")
    parser.add_argument("--ocr-profile", choices=sorted(PROFILES), default=None, help="OCR profile used by the benchmark.")
    parser.add_argument("--disable-cache", action="store_true", help="Disable OCR disk cache in the P1 runner.")
    parser.add_argument("--refresh-cache", action="store_true", help="Refresh OCR cache entries in the P1 runner.")
    parser.add_argument("--reuse-ocr", action="store_true", help="Record intent to reuse OCR/layout outputs where available.")
    parser.add_argument("--checkpoint-every", type=int, default=1, help="Checkpoint every N documents.")
    parser.add_argument("--report-only", action="store_true", help="Generate reports from an existing P1 results.jsonl without processing.")
    parser.add_argument("--size", choices=["smoke", "small", "medium", "large", "full"], default=None, help="P1 deterministic benchmark size.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after 10 critical document errors.")
    return parser.parse_args()


def uses_p1_benchmark(args: argparse.Namespace) -> bool:
    return any(
        [
            args.run_id,
            args.resume,
            args.restart,
            args.retry_failed,
            args.retry_timeouts,
            args.retry_errors,
            args.force_reprocess,
            args.skip_existing,
            args.limit is not None,
            args.offset,
            args.document_types,
            args.languages,
            args.workers != 1,
            args.document_timeout is not None,
            args.ocr_profile,
            args.disable_cache,
            args.refresh_cache,
            args.reuse_ocr,
            args.report_only,
            args.size,
            args.fail_fast,
        ]
    )


def collect_environment_status() -> dict[str, Any]:
    python_executable = sys.executable
    virtualenv = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX") or ""
    modules = {
        "PaddleOCR": module_status("paddleocr"),
        "PaddlePaddle": module_status("paddle"),
        "pytesseract": module_status("pytesseract"),
        "OpenCV": module_status("cv2"),
        "Pillow": module_status("PIL"),
        "PyMuPDF": module_status("fitz"),
        "pyarrow": module_status("pyarrow"),
        "pandas": module_status("pandas"),
        "matplotlib": module_status("matplotlib"),
        "rapidfuzz": module_status("rapidfuzz"),
        "tqdm": module_status("tqdm"),
    }
    tesseract_path = detect_tesseract_path() if modules["pytesseract"]["available"] else ""
    tesseract_available = bool(tesseract_path)
    ocr_engines = available_ocr_engines(modules, tesseract_available)
    ready = bool(ocr_engines) and modules["OpenCV"]["available"] and modules["Pillow"]["available"]
    return {
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "python_executable": python_executable,
        "virtualenv": virtualenv,
        "modules": modules,
        "tesseract_executable_path": tesseract_path,
        "tesseract_available": tesseract_available,
        "ocr_engines_available": ocr_engines,
        "ready": ready,
        "status": "READY" if ready else "BROKEN",
        "failure_message": "" if ready else "BENCHMARK ABORTED: no OCR engine is available.",
    }


def module_status(module_name: str) -> dict[str, Any]:
    try:
        importlib.import_module(module_name)
        return {"available": True, "error": ""}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def detect_tesseract_path() -> str:
    path = shutil.which("tesseract")
    if path:
        return path
    try:
        import pytesseract  # type: ignore

        configured = getattr(pytesseract.pytesseract, "tesseract_cmd", "")
        if configured and Path(configured).exists():
            return str(Path(configured))
    except Exception:
        return ""
    return ""


def available_ocr_engines(modules: dict[str, dict[str, Any]], tesseract_available: bool) -> list[str]:
    engines = []
    if modules["PaddleOCR"]["available"]:
        engines.append("PaddleOCR")
    if modules["pytesseract"]["available"] and tesseract_available:
        engines.append("Tesseract")
    return engines


def format_environment_status(environment: dict[str, Any]) -> str:
    modules = environment["modules"]
    return "\n".join([
        f"Python executable: {environment['python_executable']}",
        f"Virtualenv: {environment['virtualenv'] or 'not detected'}",
        f"PaddleOCR available: {yes_no(modules['PaddleOCR']['available'])}",
        f"PaddlePaddle available: {yes_no(modules['PaddlePaddle']['available'])}",
        f"pytesseract available: {yes_no(modules['pytesseract']['available'])}",
        f"Tesseract executable path: {environment['tesseract_executable_path'] or 'not found'}",
        f"OpenCV available: {yes_no(modules['OpenCV']['available'])}",
        f"Pillow available: {yes_no(modules['Pillow']['available'])}",
        f"PyMuPDF available: {yes_no(modules['PyMuPDF']['available'])}",
        f"pyarrow available: {yes_no(modules['pyarrow']['available'])}",
        f"pandas available: {yes_no(modules['pandas']['available'])}",
        f"matplotlib available: {yes_no(modules['matplotlib']['available'])}",
        f"rapidfuzz available: {yes_no(modules['rapidfuzz']['available'])}",
        f"tqdm available: {yes_no(modules['tqdm']['available'])}",
        f"Supported image libraries available: {yes_no(modules['OpenCV']['available'] and modules['Pillow']['available'])}",
        f"Environment status: {environment['status']}",
        *([environment["failure_message"]] if environment["failure_message"] else []),
    ])


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def previous_failed_predictions_exist() -> bool:
    results_path = OUTPUT_ROOT / "results.csv"
    if not results_path.exists():
        return False
    return any(row.get("status") == "error" for row in read_csv(results_path))


def discover_datasets(datasets_root: Path, dataset_filter: str | None = None) -> dict[str, list[DatasetDocument]]:
    grouped: dict[str, list[DatasetDocument]] = {}
    dataset_roots = [path for path in datasets_root.iterdir() if path.is_dir()]
    for dataset_root in sorted(dataset_roots, key=lambda item: item.name.lower()):
        dataset_name = dataset_root.name
        if dataset_filter and dataset_name != dataset_filter:
            continue
        label_index = build_label_index(dataset_root)
        images = sorted([path for path in dataset_root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED], key=lambda item: str(item).lower())
        for path in images:
            label_path = match_label_for_image(path, dataset_root, label_index)
            grouped.setdefault(dataset_name, []).append(
                DatasetDocument(
                    dataset_name=dataset_name,
                    split=detect_split(path),
                    file_path=path,
                    label_path=label_path,
                )
            )
    return grouped


def detect_split(path: Path) -> str:
    joined = " ".join(part.lower() for part in path.parts)
    if "train" in joined:
        return "train"
    if "test" in joined:
        return "test"
    if "valid" in joined or "val" in joined:
        return "valid"
    return "unknown"


def build_label_index(dataset_root: Path) -> dict[str, Any]:
    candidate_dirs = [
        path
        for path in dataset_root.rglob("*")
        if path.is_dir()
        and (
            path.name.lower() in LABEL_DIR_NAMES
            or str(path).lower().endswith("exported\\labels")
            or str(path).lower().endswith("exported_parquet\\labels")
        )
    ]
    if not candidate_dirs:
        candidate_dirs = [dataset_root]
    label_files: list[Path] = []
    for label_dir in candidate_dirs:
        label_files.extend(label_dir.glob("*.json"))
    exact: dict[str, Path] = {}
    normalized: dict[str, Path] = {}
    for label_path in sorted(set(label_files), key=lambda item: str(item).lower()):
        exact.setdefault(label_path.stem, label_path)
        normalized.setdefault(normalize_id(label_path.stem), label_path)
    return {"label_files": sorted(set(label_files), key=lambda item: str(item).lower()), "exact": exact, "normalized": normalized}


def match_label_for_image(image_path: Path, dataset_root: Path, label_index: dict[str, Any] | None = None) -> Path | None:
    stem = image_path.stem
    label_index = label_index or build_label_index(dataset_root)
    exact = label_index["exact"].get(stem)
    if exact:
        return exact

    compact_stem = normalize_id(stem)
    normalized = label_index["normalized"].get(compact_stem)
    if normalized:
        return normalized
    prefix_match = next(
        (
            path
            for key, path in label_index["normalized"].items()
            if key.endswith(compact_stem) or compact_stem.endswith(key)
        ),
        None,
    )
    if prefix_match:
        return prefix_match

    for path in label_index["label_files"][:500]:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if image_path.name in content or stem in content:
            return path
    return None


def sample_documents(datasets: dict[str, list[DatasetDocument]], *, limit_per_dataset: int, rng: random.Random) -> list[DatasetDocument]:
    selected = []
    for dataset_name in sorted(datasets):
        docs = list(datasets[dataset_name])
        rng.shuffle(docs)
        selected.extend(sorted(docs[:limit_per_dataset], key=lambda item: str(item.file_path).lower()))
    return selected


def build_prediction_payload(document: DatasetDocument, response: ProcessInvoiceResponse, ground_truth: dict[str, Any]) -> dict[str, Any]:
    comparison = compare_prediction_to_ground_truth(response, ground_truth)
    return {
        "dataset_name": document.dataset_name,
        "split": document.split,
        "file_path": str(document.file_path.resolve()),
        "label_path": str(document.label_path.resolve()) if document.label_path else None,
        "has_ground_truth": bool(document.label_path),
        "ground_truth": ground_truth,
        "label_comparison": comparison,
        "response": response.model_dump(mode="json"),
    }


def build_error_prediction_payload(document: DatasetDocument, exc: Exception, ground_truth: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_name": document.dataset_name,
        "split": document.split,
        "file_path": str(document.file_path.resolve()),
        "label_path": str(document.label_path.resolve()) if document.label_path else None,
        "has_ground_truth": bool(document.label_path),
        "ground_truth": ground_truth,
        "error": {
            "message": str(exc),
            "category": categorize_error(str(exc)),
        },
    }


def build_result_row(document: DatasetDocument, response: ProcessInvoiceResponse, ground_truth: dict[str, Any], prediction_path: Path, elapsed: float) -> dict[str, Any]:
    fields = response.detected_fields
    timings = response.extraction_debug.get("stage_timings", {}) if response.extraction_debug else {}
    validated_items = response.line_items_validated or fields.line_items
    review_items = response.line_items_needs_review
    all_items = response.all_line_items or (validated_items + review_items)
    comparison = compare_prediction_to_ground_truth(response, ground_truth)
    return {
        "dataset_name": document.dataset_name,
        "split": document.split,
        "filename": document.file_path.name,
        "file_path": str(document.file_path.resolve()),
        "label_path": str(document.label_path.resolve()) if document.label_path else "",
        "has_ground_truth": bool(document.label_path),
        "status": "success",
        "error_message": "",
        "error_category": categorize_success(response),
        "processing_time_seconds": elapsed,
        "document_type_pred": response.document_classification.document_type if response.document_classification else None,
        "validation_status": response.validation.status,
        "erp_export_allowed": response.validation.status == "valid",
        "ocr_confidence": response.erp_json.metadata.confidence,
        "overall_confidence": overall_confidence(response),
        "ocr_engine_used": timings.get("ocr_engine_used") or response.erp_json.metadata.ocr_engine,
        "ocr_mode": timings.get("ocr_mode"),
        "total_paddle_calls": timings.get("total_paddle_calls"),
        "fallback_region_count": timings.get("fallback_region_count"),
        "disk_cache_hit": timings.get("disk_cache_hit"),
        "full_page_ocr_inference": timings.get("full_page_ocr_inference"),
        "fallback_ocr_inference": timings.get("fallback_ocr_inference"),
        "supplier_name_pred": fields.supplier_name,
        "customer_name_pred": fields.customer_name,
        "invoice_number_pred": fields.invoice_number,
        "invoice_date_pred": fields.invoice_date.isoformat() if fields.invoice_date else "",
        "due_date_pred": fields.due_date.isoformat() if fields.due_date else "",
        "currency_pred": fields.currency,
        "amount_ht_pred": fields.amount_ht,
        "tva_amount_pred": fields.tva_amount,
        "amount_ttc_pred": fields.amount_ttc,
        "tax_rate_pred": fields.tax_rate,
        "line_items_count_pred": len(all_items),
        "validated_line_items_count_pred": len(validated_items),
        "review_line_items_count_pred": len(review_items),
        "any_line_items_pred": bool(all_items),
        "has_supplier_pred": bool(fields.supplier_name),
        "has_customer_pred": bool(fields.customer_name),
        "has_invoice_number_pred": bool(fields.invoice_number),
        "has_invoice_date_pred": bool(fields.invoice_date),
        "has_amount_ttc_pred": fields.amount_ttc is not None,
        "has_line_items_pred": bool(all_items),
        "has_validated_line_items_pred": bool(validated_items),
        "has_review_line_items_pred": bool(review_items),
        "supplier_name_true": ground_truth.get("supplier_name"),
        "customer_name_true": ground_truth.get("customer_name"),
        "invoice_number_true": ground_truth.get("invoice_number"),
        "invoice_date_true": ground_truth.get("invoice_date"),
        "amount_ttc_true": ground_truth.get("amount_ttc"),
        "document_type_true": ground_truth.get("document_type"),
        "supplier_name_correct": comparison.get("supplier_name_correct"),
        "customer_name_correct": comparison.get("customer_name_correct"),
        "invoice_number_correct": comparison.get("invoice_number_correct"),
        "invoice_date_correct": comparison.get("invoice_date_correct"),
        "amount_ttc_correct": comparison.get("amount_ttc_correct"),
        "document_type_correct": comparison.get("document_type_correct"),
        "prediction_path": str(prediction_path.resolve()),
    }


def build_error_row(document: DatasetDocument, exc: Exception, ground_truth: dict[str, Any], elapsed: float, prediction_path: Path) -> dict[str, Any]:
    return {
        "dataset_name": document.dataset_name,
        "split": document.split,
        "filename": document.file_path.name,
        "file_path": str(document.file_path.resolve()),
        "label_path": str(document.label_path.resolve()) if document.label_path else "",
        "has_ground_truth": bool(document.label_path),
        "status": "error",
        "error_message": str(exc),
        "error_category": categorize_error(str(exc)),
        "processing_time_seconds": elapsed,
        "document_type_pred": "",
        "validation_status": "",
        "erp_export_allowed": False,
        "ocr_confidence": "",
        "overall_confidence": "",
        "ocr_engine_used": "",
        "ocr_mode": "",
        "total_paddle_calls": "",
        "fallback_region_count": "",
        "disk_cache_hit": "",
        "full_page_ocr_inference": "",
        "fallback_ocr_inference": "",
        "supplier_name_pred": "",
        "customer_name_pred": "",
        "invoice_number_pred": "",
        "invoice_date_pred": "",
        "due_date_pred": "",
        "currency_pred": "",
        "amount_ht_pred": "",
        "tva_amount_pred": "",
        "amount_ttc_pred": "",
        "tax_rate_pred": "",
        "line_items_count_pred": "",
        "has_supplier_pred": False,
        "has_customer_pred": False,
        "has_invoice_number_pred": False,
        "has_invoice_date_pred": False,
        "has_amount_ttc_pred": False,
        "has_line_items_pred": False,
        "supplier_name_true": ground_truth.get("supplier_name"),
        "customer_name_true": ground_truth.get("customer_name"),
        "invoice_number_true": ground_truth.get("invoice_number"),
        "invoice_date_true": ground_truth.get("invoice_date"),
        "amount_ttc_true": ground_truth.get("amount_ttc"),
        "document_type_true": ground_truth.get("document_type"),
        "supplier_name_correct": "",
        "customer_name_correct": "",
        "invoice_number_correct": "",
        "invoice_date_correct": "",
        "amount_ttc_correct": "",
        "document_type_correct": "",
        "prediction_path": str(prediction_path.resolve()),
    }


def compare_prediction_to_ground_truth(response: ProcessInvoiceResponse, ground_truth: dict[str, Any]) -> dict[str, Any]:
    if not any(ground_truth.get(key) not in (None, "", []) for key in ("supplier_name", "customer_name", "invoice_number", "invoice_date", "amount_ttc", "document_type")):
        return {}
    fields = response.detected_fields
    pred_date = fields.invoice_date.isoformat() if fields.invoice_date else None
    return {
        "supplier_name_correct": compare_names(fields.supplier_name, ground_truth.get("supplier_name")),
        "customer_name_correct": compare_names(fields.customer_name, ground_truth.get("customer_name")),
        "invoice_number_correct": compare_invoice_numbers(fields.invoice_number, ground_truth.get("invoice_number")),
        "invoice_date_correct": compare_dates(pred_date, ground_truth.get("invoice_date")),
        "amount_ttc_correct": compare_amounts(fields.amount_ttc, ground_truth.get("amount_ttc")),
        "document_type_correct": compare_simple(predicted=(response.document_classification.document_type if response.document_classification else None), truth=ground_truth.get("document_type")),
    }


def compare_simple(predicted: Any = None, truth: Any = None) -> bool | None:
    if predicted in (None, "") or truth in (None, ""):
        return None
    return normalize_id(str(predicted)) == normalize_id(str(truth))


def compare_invoice_numbers(predicted: Any, truth: Any) -> bool | None:
    return compare_simple(predicted=predicted, truth=truth)


def compare_dates(predicted: Any, truth: Any) -> bool | None:
    pred_candidates = normalize_date_candidates(predicted)
    actual_candidates = normalize_date_candidates(truth)
    if not pred_candidates or not actual_candidates:
        return None
    return bool(pred_candidates & actual_candidates)


def compare_amounts(predicted: Any, truth: Any) -> bool | None:
    pred = normalize_amount(predicted)
    actual = normalize_amount(truth)
    if pred is None or actual is None:
        return None
    diff = abs(pred - actual)
    return diff <= 0.01 or diff <= abs(actual) * 0.005


def compare_names(predicted: Any, truth: Any) -> bool | None:
    if predicted in (None, "") or truth in (None, ""):
        return None
    comparison = compare_party_names(predicted, truth)
    return comparison.final_match is True


def normalize_date(value: Any) -> str | None:
    candidates = normalize_date_candidates(value)
    return sorted(candidates)[0] if candidates else None


def normalize_date_candidates(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, date):
        return {value.isoformat()}
    candidates: set[str] = set()
    for dayfirst in (False, True):
        try:
            candidates.add(date_parser.parse(str(value), fuzzy=True, dayfirst=dayfirst).date().isoformat())
        except Exception:
            continue
    return candidates


def normalize_amount(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = "".join(char for char in str(value) if char.isdigit() or char in ",.-")
    if not text:
        return None
    if text.count(",") and text.count("."):
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(",") and not text.count("."):
        text = text.replace(",", ".")
    try:
        return float(text)
    except Exception:
        return None


def normalize_text(value: str) -> str:
    return " ".join("".join(char.lower() if char.isalnum() else " " for char in value).split())


def normalize_id(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def categorize_success(response: ProcessInvoiceResponse) -> str:
    fields = response.detected_fields
    if response.validation.status == "invalid":
        return "validation failed"
    if not fields.supplier_name:
        return "missing supplier"
    if not fields.customer_name:
        return "missing customer"
    if not fields.invoice_number:
        return "missing invoice number"
    if not fields.invoice_date:
        return "missing invoice date"
    if fields.amount_ttc is None:
        return "missing total TTC"
    if not fields.line_items:
        return "missing line items"
    if overall_confidence(response) is not None and overall_confidence(response) < 0.5:
        return "low confidence"
    return "ok"


def categorize_error(message: str) -> str:
    lowered = message.lower()
    if "paddleocr" in lowered or "tesseract" in lowered or "ocr" in lowered:
        return "OCR failure"
    if "unsupported" in lowered:
        return "unsupported format"
    if "unreadable" in lowered or "file" in lowered:
        return "file loading error"
    if "no text" in lowered:
        return "no text extracted"
    return "exception"


def overall_confidence(response: ProcessInvoiceResponse) -> float | None:
    values = [value for value in response.field_confidences.values() if value is not None]
    if not values:
        return response.erp_json.metadata.confidence
    return round(sum(values) / len(values), 4)


def build_prediction_path(document: DatasetDocument) -> Path:
    digest = compute_file_hash(document.file_path)[:12]
    dataset_dir = PREDICTIONS_ROOT / safe_name(document.dataset_name)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    return dataset_dir / f"{document.file_path.stem}_{digest}.json"


def write_rows(rows: list[dict[str, Any]]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT_ROOT / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (row.get("dataset_name") or "", row.get("filename") or "")))


def write_manual_review_sample(rows: list[dict[str, Any]], rng: random.Random) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("dataset_name") or "unknown", []).append(row)
    samples = []
    for dataset_rows in grouped.values():
        by_status = {"valid": [], "needs_review": [], "invalid": [], "error": []}
        for row in dataset_rows:
            by_status.setdefault(row.get("validation_status") or row.get("status") or "unknown", []).append(row)
        chosen = []
        for bucket in ("valid", "needs_review", "invalid", "error"):
            bucket_rows = list(by_status.get(bucket, []))
            rng.shuffle(bucket_rows)
            chosen.extend(bucket_rows[:5])
        if len(chosen) < 20:
            remainder = [row for row in dataset_rows if row not in chosen]
            rng.shuffle(remainder)
            chosen.extend(remainder[: 20 - len(chosen)])
        samples.extend(chosen[:20])
    with (OUTPUT_ROOT / "manual_review_sample.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANUAL_REVIEW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(samples)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_checkpoint() -> dict[str, Any]:
    if not CHECKPOINT_PATH.exists():
        return {"processed": {}}
    return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))


def save_checkpoint(payload: dict[str, Any]) -> None:
    write_json(CHECKPOINT_PATH, payload)


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=json_default), encoding="utf-8")


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, np.generic):
        return value.item()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def compute_file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def with_progress(items: list[DatasetDocument], label: str):
    try:
        from tqdm import tqdm  # type: ignore

        return tqdm(items, desc=label)
    except Exception:
        return items


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value).strip("_") or "dataset"


if __name__ == "__main__":
    main()
