from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import time
import traceback
import difflib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.extraction_failure_taxonomy import analyze_failure, failure_summary
from app.services.ocr_engine import OCREngine
from app.services.party_name_normalizer import adapt_party_ground_truth, compare_party_names
from app.services.ocr_profiles import effective_ocr_config, ocr_configuration_hash, selected_profile
from app.services.pipeline_runner import process_document_file
from scripts.dataset_label_adapter import load_ground_truth
from scripts.table_ground_truth_adapter import adapt_table_ground_truth, compare_line_items


RUNS_ROOT = Path(__file__).resolve().parents[1] / "dataset" / "reports" / "benchmark_runs"
CHECKPOINT_SCHEMA_VERSION = 1
RESULT_FIELDNAMES = [
    "attempt_id",
    "attempt_number",
    "run_id",
    "document_id",
    "dataset_name",
    "split",
    "relative_path",
    "filename",
    "has_ground_truth",
    "ground_truth_supported",
    "status",
    "status_definition",
    "execution_status",
    "extraction_status",
    "erp_status",
    "processing_completed",
    "execution_error_type",
    "execution_error_stage",
    "execution_error_message",
    "traceback_path",
    "retryable",
    "is_retry",
    "retry_reason",
    "previous_attempt_id",
    "previous_execution_status",
    "started_at",
    "completed_at",
    "duration_seconds",
    "processing_time_seconds",
    "timeout_mode",
    "timeout_limit_seconds",
    "exceeded_timeout_budget",
    "completed_after_timeout_budget",
    "hard_terminated",
    "performance_violation",
    "ocr_profile",
    "ocr_mode",
    "ocr_engine_used",
    "ocr_cache_source",
    "disk_cache_hit",
    "memory_cache_hit",
    "total_paddle_calls",
    "reuse_ocr",
    "fresh_ocr",
    "selected_as_latest_result",
    "ocr_blocks",
    "ocr_blocks_with_bbox",
    "layout_blocks",
    "candidate_count",
    "validation_status",
    "validation_failure_reasons",
    "missing_required_fields",
    "totals_consistent",
    "row_validation_failures",
    "erp_readiness_status",
    "erp_blocking_reasons",
    "erp_export_allowed",
    "extraction_warning_codes",
    "suspicious_field_codes",
    "suspicious_fields",
    "confidence_warning",
    "confidence_warning_codes",
    "failure_codes",
    "failure_categories",
    "failure_details",
    "primary_failure_code",
    "failure_count",
    "document_type_pred",
    "supplier_name_pred",
    "customer_name_pred",
    "invoice_number_pred",
    "invoice_date_pred",
    "currency_pred",
    "amount_ht_pred",
    "tva_amount_pred",
    "amount_ttc_pred",
    "tax_rate_pred",
    "line_items_count_pred",
    "validated_line_items_count_pred",
    "review_line_items_count_pred",
    "ocr_confidence",
    "overall_confidence",
    "supplier_name_true",
    "customer_name_true",
    "invoice_number_true",
    "invoice_date_true",
    "currency_true",
    "amount_ht_true",
    "tva_amount_true",
    "amount_ttc_true",
    "line_items_count_true",
    "line_items_count_true_canonical",
    "table_truth_status",
    "table_truth_source_schema",
]


@dataclass(frozen=True)
class BenchmarkDocument:
    document_id: str
    dataset_name: str
    split: str
    file_path: Path
    label_path: Path | None
    relative_path: str
    file_size: int
    file_hash: str


def run(args: Any, legacy_module: Any) -> int:
    _apply_cli_profile(args)
    profile = selected_profile()
    run_id = args.run_id or f"{args.size or 'smoke'}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    run_dir = RUNS_ROOT / _safe_name(run_id)
    paths = _prepare_run_paths(run_dir)
    _setup_run_logging(paths["run_log"])

    environment = legacy_module.collect_environment_status()
    environment.update({
        "ocr_profile": profile.name,
        "effective_ocr_config": effective_ocr_config(),
        "ocr_configuration_hash": ocr_configuration_hash(),
    })

    if args.check_env:
        _atomic_json(paths["environment"], environment)
        print(legacy_module.format_environment_status(environment))
        print(f"OCR profile: {profile.name}")
        print(f"Effective OCR config: {json.dumps(effective_ocr_config(), sort_keys=True)}")
        return 0 if environment.get("ready") else 1

    if not environment.get("ready"):
        print(legacy_module.format_environment_status(environment))
        raise SystemExit("OCR engine not available. Install PaddleOCR or Tesseract before running benchmark.")

    if run_dir.exists() and not args.resume and not args.restart and not args.report_only and (paths["checkpoint"].exists() or paths["results_jsonl"].exists()):
        raise SystemExit(f"Run '{run_id}' already exists. Use --resume, --restart, or choose a new --run-id.")
    if args.restart:
        _reset_run_files(paths)

    configuration = _resolve_run_configuration(args, paths, profile.name)
    _apply_configuration_to_args(args, configuration)
    environment.update({
        "ocr_profile": configuration.get("ocr_profile") or profile.name,
        "effective_ocr_config": configuration.get("effective_ocr_config") or effective_ocr_config(),
    })
    _atomic_json(paths["environment"], environment)
    if not (args.resume or args.report_only) or not paths["configuration"].exists():
        _atomic_json(paths["configuration"], configuration)

    if args.report_only:
        _write_reports(run_dir, paths, _read_jsonl(paths["results_jsonl"]), configuration)
        return 0

    datasets_root = Path(args.datasets_root).resolve()
    grouped = legacy_module.discover_datasets(datasets_root, dataset_filter=None)
    grouped = _filter_grouped_datasets(grouped, args)
    selected = _select_documents(grouped, datasets_root, args, legacy_module)
    manifest = {
        "run_id": run_id,
        "created_at": _utc_now(),
        "datasets_root_name": datasets_root.name,
        "document_count": len(selected),
        "documents": [asdict(doc) | {"file_path": _safe_display_path(doc.file_path), "label_path": _safe_display_path(doc.label_path) if doc.label_path else None} for doc in selected],
    }
    _atomic_json(paths["manifest"], manifest)

    checkpoint = _load_or_create_checkpoint(paths["checkpoint"], run_id, configuration, environment, selected, args.resume)
    existing_attempts = _normalize_attempts(_read_jsonl(paths["results_jsonl"]), run_id=run_id)
    attempt_state = _attempt_state(existing_attempts)
    completed = set(checkpoint.get("completed_document_ids", []))
    failed = set(checkpoint.get("failed_document_ids", []))
    selected_ids = {doc.document_id for doc in selected}
    if args.retry_failed:
        pending = [doc for doc in selected if doc.document_id in failed]
    elif args.retry_timeouts:
        existing = _latest_by_document(existing_attempts)
        pending = [doc for doc in selected if existing.get(doc.document_id, {}).get("execution_status") == "timeout"]
    elif args.retry_errors:
        existing = _latest_by_document(existing_attempts)
        pending = [doc for doc in selected if existing.get(doc.document_id, {}).get("execution_status") == "failed"]
    elif args.skip_existing or args.resume:
        pending = [doc for doc in selected if doc.document_id not in completed]
    else:
        pending = selected
    if args.force_reprocess:
        pending = selected
        completed.clear()

    engine = OCREngine(
        mode=args.ocr_mode,
        use_disk_cache=not _disable_cache(args),
        refresh_cache=_refresh_cache(args),
    )
    result_ids_seen = _existing_result_ids(paths["results_jsonl"])
    counts = checkpoint.setdefault("counts", {})
    counts.update({
        "selected": len(selected),
        "pending_at_start": len(pending),
        "completed": len(completed),
        "failed": len(failed),
        "skipped": len(checkpoint.get("skipped_document_ids", [])),
    })
    checkpoint["benchmark_status"] = "running"
    checkpoint["selected_document_ids"] = sorted(selected_ids)
    _save_checkpoint(paths["checkpoint"], checkpoint)

    try:
        for index, document in enumerate(_with_progress(pending, "P1 benchmark"), start=1):
            if args.skip_existing and document.document_id in result_ids_seen:
                _record_skipped(paths, checkpoint, document, "existing_result")
                continue
            attempt_context = _next_attempt_context(document.document_id, run_id, attempt_state, args)
            result = _process_one(document, paths, engine, args, legacy_module, attempt_context)
            attempt_state[document.document_id] = result
            if result["execution_status"] == "completed":
                completed.add(document.document_id)
                failed.discard(document.document_id)
                checkpoint["last_completed_document_id"] = document.document_id
            else:
                failed.add(document.document_id)
            checkpoint["current_document_id"] = document.document_id
            checkpoint["completed_document_ids"] = sorted(completed)
            checkpoint["failed_document_ids"] = sorted(failed)
            checkpoint["counts"] = {
                "selected": len(selected),
                "completed": len(completed),
                "failed": len(failed),
                "skipped": len(checkpoint.get("skipped_document_ids", [])),
                "remaining": max(0, len(selected) - len(completed) - len(failed)),
            }
            _append_jsonl(paths["results_jsonl"], result)
            _append_partial_csv(paths, result)
            if index % max(1, args.checkpoint_every) == 0:
                _save_checkpoint(paths["checkpoint"], checkpoint)
            if _critical_error_count(paths["results_jsonl"]) >= 10 and args.fail_fast:
                checkpoint["benchmark_status"] = "stopped_fail_fast"
                _save_checkpoint(paths["checkpoint"], checkpoint)
                break
    except KeyboardInterrupt:
        checkpoint["benchmark_status"] = "interrupted"
        checkpoint["interrupted_at"] = _utc_now()
        _save_checkpoint(paths["checkpoint"], checkpoint)
        print(f"\nInterrupted safely. Resume with: python scripts/benchmark_multi_datasets.py --run-id {run_id} --resume")
        return 130

    checkpoint["benchmark_status"] = "completed"
    checkpoint["completed_at"] = _utc_now()
    _save_checkpoint(paths["checkpoint"], checkpoint)
    results = _read_jsonl(paths["results_jsonl"])
    _write_reports(run_dir, paths, results, configuration)
    return 0


def _apply_cli_profile(args: Any) -> None:
    if getattr(args, "ocr_profile", None):
        os.environ["INVOICE_OCR_OCR_PROFILE"] = args.ocr_profile
        settings.ocr_profile = args.ocr_profile


def _build_configuration(args: Any, profile_name: str) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": args.run_id,
        "size": args.size,
        "seed": args.seed,
        "limit": args.limit,
        "offset": args.offset,
        "datasets": args.datasets or ([args.dataset] if args.dataset else []),
        "document_types": args.document_types or [],
        "languages": args.languages or [],
        "workers": args.workers,
        "document_timeout": args.document_timeout,
        "ocr_profile": profile_name,
        "table_reconstruction_profile": settings.table_reconstruction_profile,
        "ocr_mode": args.ocr_mode,
        "disable_cache": _disable_cache(args),
        "refresh_cache": _refresh_cache(args),
        "reuse_ocr": args.reuse_ocr,
        "checkpoint_every": args.checkpoint_every,
        "configuration_hash": _configuration_hash(args, profile_name),
        "effective_ocr_config": effective_ocr_config(),
    }


def _configuration_hash(args: Any, profile_name: str) -> str:
    payload = {
        "datasets_root_name": Path(args.datasets_root).name,
        "size": args.size,
        "seed": args.seed,
        "limit": args.limit,
        "offset": args.offset,
        "datasets": args.datasets or ([args.dataset] if args.dataset else []),
        "document_types": args.document_types or [],
        "languages": args.languages or [],
        "workers": args.workers,
        "document_timeout": args.document_timeout,
        "ocr_profile": profile_name,
        "ocr_mode": args.ocr_mode,
        "disable_cache": _disable_cache(args),
        "refresh_cache": _refresh_cache(args),
        "reuse_ocr": args.reuse_ocr,
        "effective_ocr_config": effective_ocr_config(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _resolve_run_configuration(args: Any, paths: dict[str, Path], profile_name: str) -> dict[str, Any]:
    if args.restart or not (args.resume or args.report_only or paths["configuration"].exists()):
        return _build_configuration(args, profile_name)
    original = _load_original_configuration(paths)
    _validate_resume_configuration(args, original)
    return original


def _load_original_configuration(paths: dict[str, Path]) -> dict[str, Any]:
    checkpoint_config = {}
    if paths["checkpoint"].exists():
        try:
            checkpoint = json.loads(paths["checkpoint"].read_text(encoding="utf-8"))
            checkpoint_config = checkpoint.get("benchmark_configuration") or {}
        except Exception:
            checkpoint_config = {}
    file_config = {}
    if paths["configuration"].exists():
        try:
            file_config = json.loads(paths["configuration"].read_text(encoding="utf-8"))
        except Exception:
            file_config = {}
    # Checkpoint is authoritative for older runs because report-only previously overwrote configuration.json.
    if checkpoint_config:
        return checkpoint_config
    if file_config:
        return file_config
    raise SystemExit("Cannot resume/report: original configuration is missing.")


def _validate_resume_configuration(args: Any, original: dict[str, Any]) -> None:
    supplied = _supplied_cli_options()
    checks = {
        "--size": ("size", args.size),
        "--seed": ("seed", args.seed),
        "--limit": ("limit", args.limit),
        "--offset": ("offset", args.offset),
        "--workers": ("workers", args.workers),
        "--document-timeout": ("document_timeout", args.document_timeout),
        "--ocr-profile": ("ocr_profile", args.ocr_profile),
        "--ocr-mode": ("ocr_mode", args.ocr_mode),
    }
    for flag, (key, requested) in checks.items():
        if flag in supplied and requested != original.get(key):
            raise SystemExit(f"Run configuration mismatch: {key} was {original.get(key)} but resume requested {requested}. Use a new run ID or --restart.")
    if "--disable-cache" in supplied and not original.get("disable_cache"):
        raise SystemExit("Run configuration mismatch: cache mode was reuse but resume requested disabled. Use a new run ID or --restart.")
    if "--refresh-cache" in supplied and not original.get("refresh_cache"):
        raise SystemExit("Run configuration mismatch: cache mode was not refresh but resume requested refresh. Use a new run ID or --restart.")
    if "--datasets" in supplied or "--dataset" in supplied:
        requested = args.datasets or ([args.dataset] if args.dataset else [])
        if requested != (original.get("datasets") or []):
            raise SystemExit(f"Run configuration mismatch: datasets were {original.get('datasets') or []} but resume requested {requested}. Use a new run ID or --restart.")


def _supplied_cli_options() -> set[str]:
    return {item for item in sys.argv[1:] if item.startswith("--")}


def _apply_configuration_to_args(args: Any, configuration: dict[str, Any]) -> None:
    for key in ("size", "seed", "limit", "offset", "workers", "document_timeout", "ocr_mode", "reuse_ocr"):
        if key in configuration:
            setattr(args, key, configuration[key])
    args.disable_cache = bool(configuration.get("disable_cache"))
    args.refresh_cache = bool(configuration.get("refresh_cache"))
    args.no_ocr_cache = bool(configuration.get("disable_cache"))
    args.refresh_ocr_cache = bool(configuration.get("refresh_cache"))
    datasets = configuration.get("datasets") or []
    args.datasets = datasets
    args.dataset = datasets[0] if len(datasets) == 1 else None
    profile = configuration.get("ocr_profile")
    if profile:
        args.ocr_profile = profile
        os.environ["INVOICE_OCR_OCR_PROFILE"] = str(profile)
        settings.ocr_profile = str(profile)


def _filter_grouped_datasets(grouped: dict[str, list[Any]], args: Any) -> dict[str, list[Any]]:
    names = set(args.datasets or [])
    if args.dataset:
        names.add(args.dataset)
    if not names:
        return grouped
    return {name: docs for name, docs in grouped.items() if name in names}


def _select_documents(grouped: dict[str, list[Any]], datasets_root: Path, args: Any, legacy_module: Any) -> list[BenchmarkDocument]:
    rng = random.Random(args.seed)
    size_limit = _size_to_total_limit(args.size)
    per_dataset_limit = args.limit_per_dataset
    if size_limit is not None and grouped:
        per_dataset_limit = max(1, size_limit // len(grouped))
    selected_raw = legacy_module.sample_documents(grouped, limit_per_dataset=per_dataset_limit, rng=rng)
    if size_limit is not None and len(selected_raw) < size_limit:
        selected_keys = {str(Path(doc.file_path).resolve()) for doc in selected_raw}
        remaining = [doc for dataset in sorted(grouped) for doc in grouped[dataset] if str(Path(doc.file_path).resolve()) not in selected_keys]
        rng.shuffle(remaining)
        selected_raw.extend(sorted(remaining[: size_limit - len(selected_raw)], key=lambda item: str(item.file_path).lower()))
    if args.offset:
        selected_raw = selected_raw[args.offset :]
    if args.limit:
        selected_raw = selected_raw[: args.limit]
    documents = [_to_benchmark_document(doc, datasets_root) for doc in selected_raw]
    return sorted(documents, key=lambda doc: doc.document_id)


def _size_to_total_limit(size: str | None) -> int | None:
    return {
        "smoke": 12,
        "small": 50,
        "medium": 300,
        "large": 1000,
        "full": None,
    }.get(size or "smoke", 12)


def _to_benchmark_document(document: Any, datasets_root: Path) -> BenchmarkDocument:
    file_path = Path(document.file_path).resolve()
    file_hash = _compute_file_hash(file_path)
    try:
        relative_path = file_path.relative_to(datasets_root).as_posix()
    except ValueError:
        relative_path = file_path.name
    file_size = file_path.stat().st_size
    document_id = _stable_document_id(document.dataset_name, relative_path, file_size, file_hash)
    return BenchmarkDocument(
        document_id=document_id,
        dataset_name=document.dataset_name,
        split=document.split,
        file_path=file_path,
        label_path=document.label_path,
        relative_path=relative_path,
        file_size=file_size,
        file_hash=file_hash,
    )


def _stable_document_id(dataset_name: str, relative_path: str, file_size: int, file_hash: str) -> str:
    payload = f"{dataset_name}|{relative_path.replace(os.sep, '/').lower()}|{file_size}|{file_hash}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{_safe_name(dataset_name)}_{digest}"


def _process_one(document: BenchmarkDocument, paths: dict[str, Path], engine: OCREngine, args: Any, legacy_module: Any, attempt_context: dict[str, Any]) -> dict[str, Any]:
    started_at = _utc_now()
    started = time.perf_counter()
    ground_truth = load_ground_truth(document.label_path) if document.label_path else load_ground_truth(None)
    prediction_path = paths["artifacts"] / f"{document.document_id}.json"
    base = _base_result(document, args) | attempt_context | {
        "started_at": started_at,
        "timeout_mode": "soft",
        "timeout_limit_seconds": args.document_timeout,
        "hard_terminated": False,
    }
    try:
        response = process_document_file(
            document.file_path,
            original_filename=document.file_path.name,
            ocr_engine=engine,
            include_preview=False,
            persist_erp_json=False,
            ocr_mode=args.ocr_mode,
            use_ocr_cache=not _disable_cache(args),
            refresh_ocr_cache=_refresh_cache(args),
        )
        payload = {
            "document_id": document.document_id,
            "document": _document_payload(document),
            "ground_truth": ground_truth,
            "response": response.model_dump(mode="json"),
        }
        _atomic_json(prediction_path, payload)
        duration = round(time.perf_counter() - started, 3)
        result = base | _success_fields(response, legacy_module, args)
        result.update({
            "status": "completed",
            "status_definition": "legacy alias for execution_status",
            "execution_status": "completed",
            "processing_completed": True,
            "execution_error_type": "",
            "execution_error_stage": "",
            "execution_error_message": "",
            "traceback_path": "",
            "retryable": False,
            "duration_seconds": duration,
            "processing_time_seconds": duration,
            "completed_at": _utc_now(),
            "prediction_path": _safe_display_path(prediction_path),
        })
        result.update(_ground_truth_columns(ground_truth))
        exceeded = bool(args.document_timeout and duration > args.document_timeout)
        result["exceeded_timeout_budget"] = exceeded
        result["completed_after_timeout_budget"] = exceeded
        result["performance_violation"] = exceeded
        result = _attach_failure_analysis(result)
        if exceeded:
            _append_csv(paths["timeouts_csv"], result, RESULT_FIELDNAMES)
        return result
    except Exception as exc:
        error_log = paths["errors"] / f"{document.document_id}.log"
        error_log.write_text(traceback.format_exc(), encoding="utf-8")
        duration = round(time.perf_counter() - started, 3)
        result = base | {
            "status": "failed",
            "status_definition": "legacy alias for execution_status",
            "execution_status": "failed",
            "extraction_status": "unavailable",
            "erp_status": "unavailable",
            "processing_completed": False,
            "execution_error_type": type(exc).__name__,
            "execution_error_stage": legacy_module.categorize_error(str(exc)),
            "execution_error_message": str(exc),
            "traceback_path": _safe_display_path(error_log),
            "retryable": True,
            "validation_status": "unavailable",
            "validation_failure_reasons": [],
            "missing_required_fields": [],
            "totals_consistent": None,
            "row_validation_failures": [],
            "erp_readiness_status": "unavailable",
            "erp_blocking_reasons": ["execution failed"],
            "erp_export_allowed": False,
            "extraction_warning_codes": [],
            "suspicious_field_codes": [],
            "suspicious_fields": {},
            "confidence_warning": False,
            "confidence_warning_codes": [],
            "duration_seconds": duration,
            "processing_time_seconds": duration,
            "completed_at": _utc_now(),
            "exceeded_timeout_budget": bool(args.document_timeout and duration > args.document_timeout),
            "completed_after_timeout_budget": False,
            "performance_violation": bool(args.document_timeout and duration > args.document_timeout),
            "prediction_path": "",
        }
        result.update(_ground_truth_columns(ground_truth))
        result = _attach_failure_analysis(result)
        _append_csv(paths["errors_csv"], result, RESULT_FIELDNAMES)
        logging.exception("Document failed: %s", document.document_id)
        return result


def _base_result(document: BenchmarkDocument, args: Any) -> dict[str, Any]:
    return {
        "document_id": document.document_id,
        "dataset_name": document.dataset_name,
        "split": document.split,
        "relative_path": document.relative_path,
        "filename": document.file_path.name,
        "file_size": document.file_size,
        "file_hash": document.file_hash,
        "has_ground_truth": bool(document.label_path),
        "ground_truth_supported": False,
        "ocr_profile": args.ocr_profile or settings.ocr_profile,
        "ocr_mode": args.ocr_mode,
        "status": "",
        "status_definition": "legacy alias for execution_status",
    }


def _ground_truth_columns(ground_truth: dict[str, Any]) -> dict[str, Any]:
    has_truth = any(ground_truth.get(key) not in (None, "", []) for key in ("supplier_name", "customer_name", "invoice_number", "invoice_date", "amount_ttc", "document_type", "line_items"))
    table_truth = ground_truth.get("table_ground_truth") if isinstance(ground_truth.get("table_ground_truth"), dict) else {}
    return {
        "ground_truth_supported": has_truth,
        "supplier_name_true": ground_truth.get("supplier_name"),
        "customer_name_true": ground_truth.get("customer_name"),
        "invoice_number_true": ground_truth.get("invoice_number"),
        "invoice_date_true": ground_truth.get("invoice_date"),
        "currency_true": ground_truth.get("currency"),
        "amount_ht_true": ground_truth.get("amount_ht"),
        "tva_amount_true": ground_truth.get("tax_amount") or ground_truth.get("tva_amount"),
        "amount_ttc_true": ground_truth.get("amount_ttc"),
        "line_items_count_true": len(ground_truth.get("line_items") or []),
        "line_items_count_true_canonical": table_truth.get("canonical_item_count"),
        "table_truth_status": table_truth.get("truth_status"),
        "table_truth_source_schema": table_truth.get("source_schema"),
    }


def _attach_failure_analysis(row: dict[str, Any]) -> dict[str, Any]:
    analysis = analyze_failure(row)
    row["failure_codes"] = analysis.failure_codes
    row["failure_categories"] = analysis.failure_categories
    row["failure_details"] = analysis.failure_details
    row["primary_failure_code"] = analysis.primary_failure_code or ""
    row["failure_count"] = analysis.failure_count
    if row.get("extraction_status") in {"invalid", "needs_review"} and not row.get("validation_failure_reasons"):
        row["validation_failure_reasons"] = analysis.validation_failure_reasons
    elif analysis.validation_failure_reasons:
        existing = _coerce_list(row.get("validation_failure_reasons"))
        row["validation_failure_reasons"] = sorted(set(existing + analysis.validation_failure_reasons))
    return row


def _success_fields(response: Any, legacy_module: Any, args: Any) -> dict[str, Any]:
    fields = response.detected_fields
    timings = response.extraction_debug.get("stage_timings", {}) if response.extraction_debug else {}
    validated = response.line_items_validated or fields.line_items
    review = response.line_items_needs_review
    all_items = response.all_line_items or (validated + review)
    validation_reasons = list(response.validation.errors or []) + list(response.validation.warnings or [])
    missing_required = _missing_required_fields(response)
    suspicious = _suspicious_diagnostics(response, legacy_module)
    table_diagnostics = _table_diagnostics(response)
    erp_allowed = response.validation.status == "valid"
    total_paddle_calls = int(timings.get("total_paddle_calls") or 0)
    disk_cache_hit = bool(timings.get("disk_cache_hit"))
    memory_cache_hit = bool(timings.get("memory_cache_hits") or 0) and total_paddle_calls == 0
    fresh_ocr = bool(
        response.validation.status
        and total_paddle_calls >= 1
        and not disk_cache_hit
        and not memory_cache_hit
        and not args.reuse_ocr
    )
    return {
        "extraction_status": response.validation.status or "unavailable",
        "erp_status": "ready" if erp_allowed else "blocked",
        "validation_status": response.validation.status,
        "validation_failure_reasons": validation_reasons,
        "missing_required_fields": missing_required,
        "totals_consistent": _totals_consistent(response),
        "row_validation_failures": _row_validation_failures(response),
        "erp_readiness_status": "ready" if erp_allowed else "blocked",
        "erp_blocking_reasons": [] if erp_allowed else (validation_reasons or ["validation status is not valid"]),
        "erp_export_allowed": response.validation.status == "valid",
        "extraction_warning_codes": suspicious["warning_codes"],
        "suspicious_field_codes": suspicious["field_codes"],
        "suspicious_fields": suspicious["fields"],
        "confidence_warning": suspicious["confidence_warning"],
        "confidence_warning_codes": suspicious["confidence_warning_codes"],
        "document_type_pred": response.document_classification.document_type if response.document_classification else "",
        "supplier_name_pred": fields.supplier_name or "",
        "customer_name_pred": fields.customer_name or "",
        "invoice_number_pred": fields.invoice_number or "",
        "invoice_date_pred": fields.invoice_date.isoformat() if fields.invoice_date else "",
        "currency_pred": fields.currency or "",
        "amount_ht_pred": fields.amount_ht,
        "tva_amount_pred": fields.tva_amount,
        "amount_ttc_pred": fields.amount_ttc,
        "tax_rate_pred": fields.tax_rate,
        "line_items_count_pred": len(all_items),
        "validated_line_items_count_pred": len(validated),
        "review_line_items_count_pred": len(review),
        "ocr_confidence": response.erp_json.metadata.confidence,
        "overall_confidence": legacy_module.overall_confidence(response),
        "ocr_engine_used": timings.get("ocr_engine_used") or response.erp_json.metadata.ocr_engine,
        "ocr_cache_source": timings.get("ocr_cache_source"),
        "disk_cache_hit": disk_cache_hit,
        "memory_cache_hit": memory_cache_hit,
        "total_paddle_calls": total_paddle_calls,
        "reuse_ocr": bool(args.reuse_ocr),
        "fresh_ocr": fresh_ocr,
        "ocr_blocks": len(response.ocr_blocks or []),
        "ocr_blocks_with_bbox": sum(1 for block in (response.ocr_blocks or []) if block.bbox),
        "layout_blocks": len(response.layout_blocks or []),
        "candidate_count": _candidate_count(response),
        "party_candidate_ranking": _party_candidate_ranking(response),
        "party_confidence": _party_confidence_payload(response),
        "timings": timings,
        "table_diagnostics": table_diagnostics,
    }


def _party_candidate_ranking(response: Any) -> list[dict[str, Any]]:
    debug = response.extraction_debug or {}
    ranking = debug.get("party_candidate_ranking") or (debug.get("party_resolver") or {}).get("all_ranked_candidates") or []
    return ranking if isinstance(ranking, list) else []


def _party_confidence_payload(response: Any) -> dict[str, Any]:
    fields = response.detected_fields
    ranking = _party_candidate_ranking(response)
    supplier_candidates = [item for item in ranking if item.get("role") == "supplier"]
    customer_candidates = [item for item in ranking if item.get("role") == "customer"]
    return {
        "supplier_name": fields.supplier_name,
        "customer_name": fields.customer_name,
        "supplier_top_score": supplier_candidates[0].get("score") if supplier_candidates else None,
        "customer_top_score": customer_candidates[0].get("score") if customer_candidates else None,
        "supplier_candidate_count": len(supplier_candidates),
        "customer_candidate_count": len(customer_candidates),
        "supplier_selected_reason": supplier_candidates[0].get("selected_reason") if supplier_candidates else "",
        "customer_selected_reason": customer_candidates[0].get("selected_reason") if customer_candidates else "",
    }


def _table_diagnostics(response: Any) -> dict[str, Any]:
    debug = response.extraction_debug or {}
    table_debug = debug.get("table_extraction_debug") or {}
    p3 = table_debug.get("p3_table_reconstruction") or {}
    diagnostics = p3.get("diagnostics") or {}
    counts = table_debug.get("counts") or {}
    return {
        "selected_strategy": p3.get("selected_strategy") or diagnostics.get("selected_strategy") or "",
        "strategy_scores": p3.get("strategy_scores") or diagnostics.get("strategy_scores") or {},
        "selection_explanation": p3.get("selection_explanation") or diagnostics.get("selection_explanation") or "",
        "header_candidate_found": bool(diagnostics.get("header_candidate_found")),
        "header_confirmed": bool(diagnostics.get("header_confirmed")),
        "table_region_detected": bool(diagnostics.get("table_region_detected")),
        "table_body_detected": bool(diagnostics.get("table_body_detected")),
        "row_anchor_detected": bool(diagnostics.get("row_anchor_detected")),
        "rows_reconstructed": bool(diagnostics.get("rows_reconstructed")),
        "numeric_anchor_count": int(diagnostics.get("numeric_anchors") or diagnostics.get("numeric_anchor_count") or 0),
        "description_anchor_count": int(diagnostics.get("description_anchors") or diagnostics.get("description_anchor_count") or 0),
        "unresolved_fragment_count": int(diagnostics.get("unresolved_fragment_count") or counts.get("p3_unresolved_fragments") or 0),
        "over_merge_detected": bool(diagnostics.get("over_merge_indicators")),
        "under_merge_detected": bool(diagnostics.get("under_merge_indicators")),
        "candidate_row_count": int(diagnostics.get("candidate_row_count") or counts.get("p3_candidate_rows") or 0),
        "reconstructed_row_count": int(diagnostics.get("reconstructed_row_count") or counts.get("p3_reconstructed_rows") or 0),
        "validated_row_count": int(diagnostics.get("validated_row_count") or counts.get("p3_validated_rows") or 0),
        "review_row_count": int(diagnostics.get("review_row_count") or counts.get("p3_review_rows") or 0),
    }


def _candidate_count(response: Any) -> int:
    debug = response.extraction_debug or {}
    candidates = debug.get("candidates") or debug.get("field_candidates") or {}
    if isinstance(candidates, dict):
        return sum(len(value) for value in candidates.values() if isinstance(value, list))
    if isinstance(candidates, list):
        return len(candidates)
    return 0


def _next_attempt_context(document_id: str, run_id: str, attempt_state: dict[str, dict[str, Any]], args: Any) -> dict[str, Any]:
    previous = attempt_state.get(document_id)
    previous_number = int(previous.get("attempt_number") or 0) if previous else 0
    attempt_number = previous_number + 1
    retry_reason = ""
    if previous:
        retry_reason = "retry_failed" if args.retry_failed else "retry_timeout" if args.retry_timeouts else "retry_error" if args.retry_errors else "resume_or_reprocess"
    return {
        "attempt_id": f"{document_id}_attempt_{attempt_number}",
        "attempt_number": attempt_number,
        "run_id": run_id,
        "is_retry": bool(previous),
        "retry_reason": retry_reason,
        "previous_attempt_id": previous.get("attempt_id", "") if previous else "",
        "previous_execution_status": previous.get("execution_status", "") if previous else "",
        "selected_as_latest_result": False,
        "attempt_metadata_inferred": False,
    }


def _attempt_state(attempts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        document_id = attempt.get("document_id")
        if document_id:
            state[str(document_id)] = attempt
    return state


def _latest_by_document(attempts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        document_id = attempt.get("document_id")
        if document_id:
            latest[str(document_id)] = attempt
    for attempt in latest.values():
        attempt["selected_as_latest_result"] = True
    return latest


def _normalize_attempts(rows: list[dict[str, Any]], *, run_id: str) -> list[dict[str, Any]]:
    counters: dict[str, int] = {}
    previous_by_doc: dict[str, dict[str, Any]] = {}
    normalized = []
    for row in rows:
        item = dict(row)
        document_id = str(item.get("document_id") or "")
        counters[document_id] = counters.get(document_id, 0) + 1
        if not item.get("attempt_number"):
            item["attempt_number"] = counters[document_id]
            item["attempt_metadata_inferred"] = True
        else:
            item["attempt_metadata_inferred"] = bool(item.get("attempt_metadata_inferred"))
        item.setdefault("attempt_id", f"{document_id}_attempt_{item['attempt_number']}")
        item.setdefault("run_id", run_id)
        old_status = item.get("status")
        if not item.get("execution_status"):
            item["execution_status"] = "completed" if old_status in ("success", "completed") else "timeout" if old_status == "timeout" else "failed" if old_status in ("error", "failed") else old_status or "completed"
        item["status"] = item["execution_status"]
        item.setdefault("status_definition", "legacy alias for execution_status")
        item.setdefault("extraction_status", item.get("validation_status") or ("unavailable" if item["execution_status"] != "completed" else "invalid"))
        item.setdefault("erp_status", "ready" if _truthy(item.get("erp_export_allowed")) else "blocked" if item["execution_status"] == "completed" else "unavailable")
        item.setdefault("erp_readiness_status", item["erp_status"])
        item.setdefault("execution_error_type", "" if item["execution_status"] == "completed" else item.get("error_type") or item.get("execution_error_stage") or "exception")
        item.setdefault("execution_error_stage", item.get("error_type") if item["execution_status"] != "completed" else "")
        item.setdefault("execution_error_message", item.get("error_message") if item["execution_status"] != "completed" else "")
        item.setdefault("processing_completed", item["execution_status"] == "completed")
        item.setdefault("duration_seconds", item.get("processing_time_seconds"))
        item.setdefault("timeout_mode", "soft")
        item.setdefault("timeout_limit_seconds", None)
        item.setdefault("hard_terminated", False)
        item.setdefault("exceeded_timeout_budget", False)
        item.setdefault("completed_after_timeout_budget", False)
        item.setdefault("performance_violation", False)
        item.setdefault("validation_failure_reasons", [])
        item.setdefault("missing_required_fields", _missing_required_fields_from_row(item))
        item.setdefault("erp_blocking_reasons", [] if item["erp_status"] == "ready" else ["validation status is not valid"])
        row_suspicious = _suspicious_diagnostics_from_row(item)
        item.setdefault("suspicious_field_codes", row_suspicious["field_codes"])
        item.setdefault("suspicious_fields", row_suspicious["fields"])
        item.setdefault("confidence_warning", row_suspicious["confidence_warning"])
        item.setdefault("confidence_warning_codes", row_suspicious["confidence_warning_codes"])
        timings = item.get("timings") if isinstance(item.get("timings"), dict) else {}
        item.setdefault("memory_cache_hit", bool(timings.get("memory_cache_hits") or 0) and int(timings.get("total_paddle_calls") or 0) == 0)
        item.setdefault("total_paddle_calls", int(timings.get("total_paddle_calls") or 0))
        item.setdefault("reuse_ocr", False)
        item.setdefault("disk_cache_hit", _truthy(item.get("disk_cache_hit")) or _truthy(timings.get("disk_cache_hit")))
        if not item.get("ocr_cache_source") and timings.get("ocr_cache_source"):
            item["ocr_cache_source"] = timings.get("ocr_cache_source")
        item.setdefault("fresh_ocr", _is_fresh_ocr_attempt(item))
        item.setdefault("ground_truth_supported", bool(item.get("has_ground_truth")))
        item = _attach_failure_analysis(item)
        previous = previous_by_doc.get(document_id)
        item.setdefault("is_retry", counters[document_id] > 1)
        item.setdefault("previous_attempt_id", previous.get("attempt_id", "") if previous else "")
        item.setdefault("previous_execution_status", previous.get("execution_status", "") if previous else "")
        previous_by_doc[document_id] = item
        normalized.append(item)
    latest = _latest_by_document(normalized)
    for item in normalized:
        item["selected_as_latest_result"] = latest.get(str(item.get("document_id"))) is item
    return normalized


def _is_fresh_ocr_attempt(row: dict[str, Any]) -> bool:
    return (
        row.get("execution_status") == "completed"
        and int(row.get("total_paddle_calls") or 0) >= 1
        and not _truthy(row.get("disk_cache_hit"))
        and not _truthy(row.get("memory_cache_hit"))
        and not _truthy(row.get("reuse_ocr"))
    )


def _apply_report_configuration_defaults(attempts: list[dict[str, Any]], configuration: dict[str, Any]) -> None:
    timeout = configuration.get("document_timeout")
    for attempt in attempts:
        if attempt.get("timeout_limit_seconds") in (None, ""):
            attempt["timeout_limit_seconds"] = timeout


def _missing_required_fields_from_row(row: dict[str, Any]) -> list[str]:
    missing = []
    checks = {
        "supplier_name": row.get("supplier_name_pred"),
        "customer_name": row.get("customer_name_pred"),
        "invoice_number": row.get("invoice_number_pred"),
        "invoice_date": row.get("invoice_date_pred"),
        "currency": row.get("currency_pred"),
        "amount_ttc": row.get("amount_ttc_pred"),
    }
    for key, value in checks.items():
        if value in (None, "", []):
            missing.append(key)
    if int(row.get("line_items_count_pred") or 0) <= 0:
        missing.append("line_items")
    return missing


def _suspicious_diagnostics_from_row(row: dict[str, Any]) -> dict[str, Any]:
    suspicious_fields: dict[str, list[str]] = {}
    codes: list[str] = []
    for field_name, key in (("supplier_name", "supplier_name_pred"), ("customer_name", "customer_name_pred")):
        party_codes = _party_suspicious_codes(row.get(key))
        if party_codes:
            suspicious_fields[field_name] = party_codes
            codes.extend(party_codes)
    fields = type("Fields", (), {
        "amount_ttc": _float_or_none(row.get("amount_ttc_pred")),
        "tva_amount": _float_or_none(row.get("tva_amount_pred")),
        "amount_ht": _float_or_none(row.get("amount_ht_pred")),
        "tax_rate": _float_or_none(row.get("tax_rate_pred")),
    })()
    amount_codes = _amount_suspicious_codes(fields)
    if amount_codes:
        suspicious_fields["amount_ttc"] = amount_codes
        codes.extend(amount_codes)
    confidence_codes = []
    if _missing_required_fields_from_row(row):
        confidence_codes.append("REQUIRED_FIELDS_MISSING")
    if row.get("extraction_status") == "invalid" or row.get("validation_status") == "invalid":
        confidence_codes.append("HIGH_CONFIDENCE_INVALID_EXTRACTION")
    if codes:
        confidence_codes.append("OCR_CONFIDENCE_NOT_SEMANTIC_CONFIDENCE")
    confidence = _float_or_none(row.get("overall_confidence"))
    warning = bool(confidence is not None and confidence >= 0.85 and confidence_codes)
    return {
        "field_codes": sorted(set(codes)),
        "fields": suspicious_fields,
        "confidence_warning": warning,
        "confidence_warning_codes": sorted(set(confidence_codes)) if warning else [],
    }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _int_or_none(value: Any) -> int | None:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return int(parsed)


def _normalize_date_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        from dateutil import parser as date_parser

        return date_parser.parse(str(value), fuzzy=True, dayfirst=False).date().isoformat()
    except Exception:
        try:
            from dateutil import parser as date_parser

            return date_parser.parse(str(value), fuzzy=True, dayfirst=True).date().isoformat()
        except Exception:
            return _norm_text_extended(value)


def _normalize_currency(value: Any) -> str:
    raw = str(value or "").strip()
    text = _norm_text_extended(raw).upper()
    mapping = {
        "$": "USD",
        "US": "USD",
        "DOLLAR": "USD",
        "€": "EUR",
        "EURO": "EUR",
        "TND": "TND",
        "DT": "TND",
        "DINAR": "TND",
        "GBP": "GBP",
        "£": "GBP",
    }
    return mapping.get(raw, mapping.get(text, text))


def _norm_text_extended(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).lower()
    replacements = {"s.a.r.l": "sarl", "s.a": "sa", "l.l.c": "llc", "inc.": "inc", "ltd.": "ltd"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def _coerce_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            import ast

            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [part.strip() for part in text.split(";") if part.strip()]
    return [value]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _missing_required_fields(response: Any) -> list[str]:
    fields = response.detected_fields
    missing = []
    for name in ("supplier_name", "customer_name", "invoice_number", "invoice_date", "currency", "amount_ttc"):
        if getattr(fields, name, None) in (None, ""):
            missing.append(name)
    if not (response.all_line_items or fields.line_items):
        missing.append("line_items")
    return missing


def _totals_consistent(response: Any) -> bool | None:
    fields = response.detected_fields
    if fields.amount_ht is None or fields.tva_amount is None or fields.amount_ttc is None:
        return None
    return abs((float(fields.amount_ht) + float(fields.tva_amount)) - float(fields.amount_ttc)) <= 0.05


def _row_validation_failures(response: Any) -> list[str]:
    failures = []
    for item in response.line_items_needs_review or []:
        reason = getattr(item, "review_reason", None)
        if reason:
            failures.append(str(reason))
    return failures


def _suspicious_diagnostics(response: Any, legacy_module: Any) -> dict[str, Any]:
    fields = response.detected_fields
    suspicious_fields: dict[str, list[str]] = {}
    codes: list[str] = []
    for field_name in ("supplier_name", "customer_name"):
        value = getattr(fields, field_name, None)
        party_codes = _party_suspicious_codes(value)
        if party_codes:
            suspicious_fields[field_name] = party_codes
            codes.extend(party_codes)
    amount_codes = _amount_suspicious_codes(fields)
    if amount_codes:
        suspicious_fields["amount_ttc"] = amount_codes
        codes.extend(amount_codes)
    missing = _missing_required_fields(response)
    confidence_warning_codes = []
    overall = legacy_module.overall_confidence(response)
    if missing:
        confidence_warning_codes.append("REQUIRED_FIELDS_MISSING")
    if response.validation.status == "invalid":
        confidence_warning_codes.append("HIGH_CONFIDENCE_INVALID_EXTRACTION")
    if codes:
        confidence_warning_codes.append("OCR_CONFIDENCE_NOT_SEMANTIC_CONFIDENCE")
    confidence_warning = bool(overall is not None and overall >= 0.85 and confidence_warning_codes)
    return {
        "field_codes": sorted(set(codes)),
        "warning_codes": sorted(set(codes + confidence_warning_codes)),
        "fields": suspicious_fields,
        "confidence_warning": confidence_warning,
        "confidence_warning_codes": sorted(set(confidence_warning_codes)) if confidence_warning else [],
    }


def _party_suspicious_codes(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    text = str(value).strip()
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text).strip()
    compact = " ".join(normalized.split())
    labels = {"ship to", "ship_to", "bill to", "bill_to", "customer", "supplier", "unit price", "quantity", "description", "unit prico"}
    table_words = {"quantity", "description", "unit", "price", "total", "amount", "qty", "vat", "tva"}
    metadata_words = {"invoice", "facture", "date", "number", "numero", "total", "subtotal"}
    codes = []
    if compact in labels:
        codes.append("PARTY_IS_LABEL_ONLY")
    if any(word in compact for word in table_words):
        codes.append("PARTY_IS_TABLE_HEADER")
    if any(word in compact for word in metadata_words):
        codes.append("PARTY_CONTAINS_METADATA_LABEL")
    if len(text) > 80:
        codes.append("PARTY_NAME_TOO_LONG")
    if text.count(" ") > 12 or any(mark in text for mark in (". ", ": ", "; ")):
        codes.append("PARTY_LOOKS_LIKE_SENTENCE")
    digits = sum(char.isdigit() for char in text)
    if digits > 0 and digits >= max(3, len(text) // 3):
        codes.append("PARTY_EXCESSIVE_NUMERIC_CONTENT")
    company_tokens = ("inc", "ltd", "llc", "sarl", "sa", "sas", "corp", "company", "distribution", "pharma", "medical")
    if not any(token in compact.split() for token in company_tokens) and len(compact.split()) <= 2:
        codes.append("PARTY_LOW_COMPANY_PLAUSIBILITY")
    return codes


def _amount_suspicious_codes(fields: Any) -> list[str]:
    ttc = fields.amount_ttc
    codes = []
    if ttc is None:
        return codes
    try:
        amount = float(ttc)
    except Exception:
        return ["TTC_NOT_NUMERIC"]
    if amount <= 0:
        codes.append("TTC_NON_POSITIVE")
    if 1900 <= amount <= 2100 and float(amount).is_integer():
        codes.append("TTC_LOOKS_LIKE_YEAR")
    if 0 < amount <= 100 and fields.tax_rate is not None and abs(amount - float(fields.tax_rate)) < 0.001:
        codes.append("TTC_LOOKS_LIKE_PERCENTAGE")
    if len(str(int(abs(amount)))) >= 8:
        codes.append("TTC_IMPLAUSIBLY_LARGE")
    if fields.tva_amount is not None and float(fields.tva_amount) > amount:
        codes.append("TVA_GREATER_THAN_TTC")
    if fields.amount_ht is not None and float(fields.amount_ht) > amount:
        codes.append("HT_GREATER_THAN_TTC")
    return codes


def _write_reports(run_dir: Path, paths: dict[str, Path], results: list[dict[str, Any]], configuration: dict[str, Any]) -> None:
    attempts = _normalize_attempts(results, run_id=str(configuration.get("run_id") or "run"))
    _apply_report_configuration_defaults(attempts, configuration)
    latest = _dedupe_latest_results(attempts)
    _enrich_latest_with_canonical_table_truth(latest, paths)
    _ensure_event_csv(paths["errors_csv"], RESULT_FIELDNAMES)
    _ensure_event_csv(paths["timeouts_csv"], RESULT_FIELDNAMES)
    _ensure_event_csv(paths["skipped_csv"], ["document_id", "dataset_name", "relative_path", "filename", "skip_reason"])
    _write_results_csv(paths["attempts_csv"], attempts)
    _write_results_csv(paths["document_latest_results_csv"], latest)
    _write_results_csv(paths["results_csv"], latest)
    _write_timings_csv(paths["timings_csv"], attempts)
    performance = _performance_payloads(attempts, latest)
    quality = _quality_payloads(latest)
    failures = failure_summary(latest)
    failures["party_comparison_code_distribution"] = _counts(row.get("taxonomy_code") or "unknown" for row in quality["party_rows"])
    failures["party_true_failure_code_distribution"] = _counts(
        row.get("taxonomy_code") or "unknown"
        for row in quality["party_rows"]
        if row.get("taxonomy_code") in {"PARTY_AMBIGUOUS_MATCH", "PARTY_TRUE_MISMATCH", "PARTY_GROUND_TRUTH_UNSUPPORTED", "PARTY_PREDICTION_MISSING", "PARTY_TRUTH_MISSING"}
    )
    _atomic_json(paths["performance_fresh_ocr"], performance["fresh_ocr"])
    _atomic_json(paths["performance_cached"], performance["cached"])
    _atomic_json(paths["performance_all_attempts"], performance["all_attempts"])
    _write_failure_matrix(paths["failure_matrix"], latest)
    _atomic_json(paths["failure_summary"], failures)
    _write_field_quality(paths["field_quality"], quality["field_rows"])
    _write_party_comparison(paths["party_comparison"], quality["party_rows"])
    _write_party_candidate_ranking(paths["party_candidate_ranking"], latest)
    _write_party_candidate_debug(paths["party_candidate_debug"], latest)
    _write_party_confidence_report(paths["party_confidence_report"], latest)
    _write_table_quality(paths["table_quality"], quality["table_rows"])
    _write_table_failure_matrix(paths["table_failure_matrix"], quality["table_rows"])
    _write_line_item_comparison(paths["line_item_comparison"], quality["line_item_rows"])
    _write_line_item_pair_comparison(paths["line_item_pair_comparison"], quality["line_item_pair_rows"])
    _write_table_ground_truth_manual_review(paths["table_ground_truth_manual_review"], quality["line_item_rows"])
    _write_schema_audit(paths["schema_audit"], quality["schema_rows"])
    _write_p3_vs_p3_1_comparison(paths["p3_vs_p3_1_table_comparison"])
    _write_dataset_table_failure_analysis(paths["donut_table_failure_analysis"], quality["table_rows"], "donut")
    _write_dataset_table_failure_analysis(paths["invoicexpert_table_failure_analysis"], quality["table_rows"], "invoicexpert")
    _atomic_json(paths["row_reconstruction_summary"], quality["table_quality"])
    _write_dataset_quality_summary(paths["dataset_quality_summary"], latest, quality["field_rows"], quality.get("party_quality", {}))
    summary = _summary(attempts, latest, configuration, performance, quality, failures)
    _atomic_json(paths["summary"], summary)
    md = _render_markdown(summary)
    paths["report_md"].write_text(md, encoding="utf-8")
    paths["report_html"].write_text(_render_html(summary), encoding="utf-8")
    _atomic_json(paths["slowest"], sorted(attempts, key=lambda row: float(row.get("duration_seconds") or row.get("processing_time_seconds") or 0), reverse=True)[:20])
    _atomic_json(paths["worst"], _worst_results(latest))
    _write_manual_review(paths["manual_review"], latest)


def _dedupe_latest_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for row in results:
        document_id = row.get("document_id")
        if document_id:
            latest[str(document_id)] = row
        else:
            anonymous.append(row)
    for row in latest.values():
        row["selected_as_latest_result"] = True
    return anonymous + list(latest.values())


def _enrich_latest_with_canonical_table_truth(latest: list[dict[str, Any]], paths: dict[str, Path]) -> None:
    manifest_docs = _manifest_documents_by_id(paths.get("manifest"))
    label_cache: dict[str, dict[str, Any]] = {}
    for row in latest:
        document_id = str(row.get("document_id") or "")
        artifact = _load_prediction_artifact(paths, row)
        if artifact:
            response = artifact.get("response") or {}
            row["_predicted_line_items"] = response.get("all_line_items") or response.get("detected_fields", {}).get("line_items") or []
            ground_truth = artifact.get("ground_truth") or {}
            table_truth = ground_truth.get("table_ground_truth") if isinstance(ground_truth, dict) else None
            if isinstance(table_truth, dict):
                row["_table_ground_truth"] = table_truth
        if row.get("_table_ground_truth"):
            table_truth = row["_table_ground_truth"]
        else:
            doc = manifest_docs.get(document_id, {})
            label_path = _resolve_label_path(doc, row)
            cache_key = str(label_path) if label_path else f"missing:{document_id}"
            if cache_key not in label_cache:
                label_cache[cache_key] = adapt_table_ground_truth(label_path, dataset_name=str(row.get("dataset_name") or "")).to_dict()
            table_truth = label_cache[cache_key]
            row["_table_ground_truth"] = table_truth
        if isinstance(table_truth, dict):
            row["line_items_count_true_canonical"] = table_truth.get("canonical_item_count")
            row["table_truth_status"] = table_truth.get("truth_status")
            row["table_truth_source_schema"] = table_truth.get("source_schema")


def _manifest_documents_by_id(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(doc.get("document_id")): doc for doc in manifest.get("documents", []) if doc.get("document_id")}


def _load_prediction_artifact(paths: dict[str, Path], row: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    if row.get("prediction_path"):
        candidates.append(Path(str(row["prediction_path"])))
        candidates.append(Path(__file__).resolve().parents[1] / str(row["prediction_path"]))
    if row.get("document_id"):
        candidates.append(paths["artifacts"] / f"{row['document_id']}.json")
    for path in candidates:
        try:
            resolved = path if path.is_absolute() else Path(__file__).resolve().parents[1] / path
            if resolved.exists():
                return json.loads(resolved.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
    return None


def _resolve_label_path(doc: dict[str, Any], row: dict[str, Any]) -> Path | None:
    label = doc.get("label_path") or row.get("label_path")
    if not label:
        return None
    label_path = Path(str(label))
    if label_path.is_absolute() and label_path.exists():
        return label_path
    dataset_name = str(row.get("dataset_name") or doc.get("dataset_name") or "")
    roots = [
        Path(__file__).resolve().parents[2] / "sources" / "datasets",
        Path("D:/Stage_udgroup/sources/datasets"),
        Path("D:/Stage_mr_f/sources/datasets"),
    ]
    for root in roots:
        scoped = root / dataset_name if dataset_name else root
        if scoped.exists():
            matches = list(scoped.rglob(label_path.name))
            if matches:
                return matches[0]
        candidate = root / str(label)
        if candidate.exists():
            return candidate
    return None


def _performance_payloads(attempts: list[dict[str, Any]], latest: list[dict[str, Any]]) -> dict[str, Any]:
    fresh = [row for row in attempts if _is_fresh_ocr_attempt(row)]
    disk = [row for row in attempts if row.get("execution_status") == "completed" and _truthy(row.get("disk_cache_hit"))]
    memory = [row for row in attempts if row.get("execution_status") == "completed" and _truthy(row.get("memory_cache_hit"))]
    reuse = [row for row in attempts if row.get("execution_status") == "completed" and _truthy(row.get("reuse_ocr"))]
    completed = [row for row in attempts if row.get("execution_status") == "completed"]
    latest_completed = [row for row in latest if row.get("execution_status") == "completed"]
    return {
        "fresh_ocr": _performance_stats(fresh, label="fresh OCR", include_thresholds=True),
        "cached": _performance_stats(disk + memory, label="cached"),
        "disk_cache": _performance_stats(disk, label="disk cache"),
        "memory_cache": _performance_stats(memory, label="memory cache"),
        "reuse_ocr": _performance_stats(reuse, label="reuse OCR"),
        "all_attempts": _performance_stats(completed, label="all completed attempts"),
        "latest_completed": _performance_stats(latest_completed, label="latest completed attempts"),
        "slowest_fresh_documents": [_compact_result(row) for row in sorted(fresh, key=lambda row: float(row.get("duration_seconds") or 0), reverse=True)[:10]],
        "documents_retried_from_cache": [_compact_result(row) for row in attempts if _truthy(row.get("is_retry")) and _truthy(row.get("disk_cache_hit"))],
    }


def _performance_stats(rows: list[dict[str, Any]], *, label: str, include_thresholds: bool = False) -> dict[str, Any]:
    durations = [float(row.get("duration_seconds") or row.get("processing_time_seconds") or 0) for row in rows if row.get("duration_seconds") not in (None, "")]
    payload = {
        "count": len(durations),
        "median": _percentile(durations, 50),
        "mean": round(sum(durations) / len(durations), 3) if durations else None,
        "p90": _percentile(durations, 90),
        "p95": _percentile(durations, 95) if len(durations) >= 2 else None,
        "max": max(durations) if durations else None,
        "percentile_note": f"Not enough {label} attempts for stable percentiles." if len(durations) < 5 else "",
    }
    if include_thresholds:
        payload["under_30_seconds"] = sum(1 for value in durations if value <= 30)
        payload["over_30_seconds"] = sum(1 for value in durations if value > 30)
    return payload


def _quality_payloads(latest: list[dict[str, Any]]) -> dict[str, Any]:
    field_rows: list[dict[str, Any]] = []
    party_rows: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []
    line_item_rows: list[dict[str, Any]] = []
    line_item_pair_rows: list[dict[str, Any]] = []
    schema_rows: list[dict[str, Any]] = []
    fields = [
        ("supplier_name", "supplier_name_pred", "supplier_name_true", "party"),
        ("customer_name", "customer_name_pred", "customer_name_true", "party"),
        ("invoice_number", "invoice_number_pred", "invoice_number_true", "id"),
        ("invoice_date", "invoice_date_pred", "invoice_date_true", "date"),
        ("currency", "currency_pred", "currency_true", "currency"),
        ("amount_ht", "amount_ht_pred", "amount_ht_true", "amount"),
        ("tax_amount", "tva_amount_pred", "tva_amount_true", "amount"),
        ("amount_ttc", "amount_ttc_pred", "amount_ttc_true", "amount"),
        ("line_items_presence", "line_items_count_pred", "line_items_count_true", "presence"),
        ("line_items_count", "line_items_count_pred", "line_items_count_true", "count"),
    ]
    gt_docs = 0
    no_gt = 0
    unsupported = 0
    for row in latest:
        if not _truthy(row.get("has_ground_truth")):
            no_gt += 1
            continue
        if not _truthy(row.get("ground_truth_supported")):
            unsupported += 1
            continue
        gt_docs += 1
        table_rows.append(_table_quality_row(row))
        comparison_row, pair_rows = _line_item_comparison_rows(row)
        line_item_rows.append(comparison_row)
        line_item_pair_rows.extend(pair_rows)
        schema_rows.append(_schema_audit_row(row))
        for field_name, pred_key, true_key, kind in fields:
            truth = row.get(true_key)
            if truth in (None, "", []) and field_name not in {"line_items_presence", "line_items_count"}:
                continue
            pred = row.get(pred_key)
            comparison = _compare_field(pred, truth, kind)
            party_comparison = _compare_party_field(pred, truth) if kind == "party" else {}
            if kind == "party":
                party_rows.append(_party_comparison_row(row, field_name, pred, truth, party_comparison))
            field_rows.append({
                "dataset_name": row.get("dataset_name"),
                "document_id": row.get("document_id"),
                "field_name": field_name,
                "expected_value": truth,
                "predicted_value": pred,
                "exact_match": comparison["exact_match"],
                "normalized_match": comparison["normalized_match"],
                "absolute_error": comparison["absolute_error"],
                "relative_error": comparison["relative_error"],
                "missing_prediction": comparison["missing_prediction"],
                "confidence": row.get("overall_confidence"),
                "failure_codes": ";".join(_coerce_list(row.get("failure_codes"))),
                "party_strict_match": party_comparison.get("strict_match"),
                "party_normalized_full_match": party_comparison.get("normalized_full_match"),
                "party_canonical_match": party_comparison.get("canonical_match"),
                "party_suffix_insensitive_match": party_comparison.get("suffix_insensitive_match"),
                "party_similarity_score": party_comparison.get("similarity_score"),
                "party_match_classification": party_comparison.get("match_classification"),
            })
    return {
        "field_rows": field_rows,
        "party_rows": party_rows,
        "ground_truth": {
            "ground_truth_evaluated_count": gt_docs,
            "no_ground_truth_count": no_gt,
            "unsupported_ground_truth_schema_count": unsupported,
        },
        "field_accuracy": _field_accuracy_summary(field_rows),
        "party_quality": _party_quality_summary(party_rows),
        "line_item_quality": _line_item_quality_summary(latest),
        "canonical_line_item_quality": _canonical_line_item_quality_summary(line_item_rows),
        "table_rows": table_rows,
        "table_quality": _table_quality_summary(table_rows),
        "line_item_rows": line_item_rows,
        "line_item_pair_rows": line_item_pair_rows,
        "schema_rows": schema_rows,
    }


def _compare_field(predicted: Any, truth: Any, kind: str) -> dict[str, Any]:
    missing = predicted in (None, "", [])
    exact = (str(predicted).strip() == str(truth).strip()) if not missing and truth not in (None, "") else None
    abs_error = None
    rel_error = None
    if kind == "amount":
        pred_amount = _float_or_none(predicted)
        truth_amount = _float_or_none(truth)
        normalized = None if pred_amount is None or truth_amount is None else abs(pred_amount - truth_amount) <= max(0.01, abs(truth_amount) * 0.005)
        if pred_amount is not None and truth_amount is not None:
            abs_error = round(abs(pred_amount - truth_amount), 4)
            rel_error = round(abs_error / abs(truth_amount), 6) if truth_amount else None
    elif kind == "date":
        normalized = _normalize_date_text(predicted) == _normalize_date_text(truth) if not missing else False
    elif kind == "currency":
        normalized = _normalize_currency(predicted) == _normalize_currency(truth) if not missing else False
    elif kind == "presence":
        normalized = (int(_float_or_none(predicted) or 0) > 0) == (int(_float_or_none(truth) or 0) > 0)
        exact = normalized
    elif kind == "count":
        pred_count = _int_or_none(predicted)
        truth_count = _int_or_none(truth)
        normalized = pred_count == truth_count if pred_count is not None and truth_count is not None else False
        exact = normalized
    elif kind == "party":
        party = _compare_party_field(predicted, truth)
        normalized = party["canonical_match"] if party["canonical_match"] is not None else False
        exact = party["strict_match"]
    else:
        normalized = _norm_text_extended(predicted) == _norm_text_extended(truth) if not missing else False
    return {
        "exact_match": exact,
        "normalized_match": normalized,
        "absolute_error": abs_error,
        "relative_error": rel_error,
        "missing_prediction": missing,
    }


def _compare_party_field(predicted: Any, truth: Any) -> dict[str, Any]:
    comparison = compare_party_names(predicted, truth)
    payload = comparison.to_dict()
    truth_norm = payload.get("truth") or {}
    pred_norm = payload.get("prediction") or {}
    adapted_truth = adapt_party_ground_truth(truth)
    classification = str(payload.get("match_classification") or "unavailable")
    similarity = payload.get("token_set_similarity")
    if similarity is None:
        similarity = payload.get("character_similarity")
    taxonomy_code = _party_taxonomy_code(classification, predicted, truth, truth_norm, pred_norm)
    return {
        "strict_match": payload.get("strict_exact_match"),
        "normalized_full_match": payload.get("normalized_full_exact_match"),
        "canonical_match": payload.get("final_match") is True,
        "canonical_exact_match": payload.get("canonical_exact_match"),
        "suffix_insensitive_match": payload.get("canonical_without_suffix_exact_match"),
        "similarity_score": similarity,
        "token_sort_similarity": payload.get("token_sort_similarity"),
        "character_similarity": payload.get("character_similarity"),
        "match_classification": classification,
        "mismatch_reason": payload.get("mismatch_reason"),
        "truth_canonical": truth_norm.get("canonical_name") or "",
        "prediction_canonical": pred_norm.get("canonical_name") or "",
        "adapter_schema": adapted_truth.source_schema,
        "normalization_warnings": ";".join(adapted_truth.normalization_warnings),
        "taxonomy_code": taxonomy_code,
    }


def _party_taxonomy_code(classification: str, predicted: Any, truth: Any, truth_norm: dict[str, Any], pred_norm: dict[str, Any]) -> str:
    if truth in (None, ""):
        return "PARTY_TRUTH_MISSING"
    if predicted in (None, ""):
        return "PARTY_PREDICTION_MISSING"
    if classification == "unavailable":
        return "PARTY_GROUND_TRUTH_UNSUPPORTED"
    if classification in {"exact", "canonical_exact", "strong_fuzzy"}:
        if truth_norm.get("address_removed"):
            return "PARTY_ADDRESS_INCLUDED_IN_GROUND_TRUTH"
        if truth_norm.get("canonical_name") != pred_norm.get("canonical_name") and truth_norm.get("canonical_without_legal_suffix") == pred_norm.get("canonical_without_legal_suffix"):
            return "PARTY_LEGAL_SUFFIX_ONLY_DIFFERENCE"
        return "PARTY_CANONICAL_MATCH"
    if classification == "partial":
        return "PARTY_PARTIAL_NAME_MATCH"
    if classification == "ambiguous":
        return "PARTY_AMBIGUOUS_MATCH"
    return "PARTY_TRUE_MISMATCH"


def _party_comparison_row(row: dict[str, Any], field_name: str, predicted: Any, truth: Any, comparison: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": row.get("document_id"),
        "dataset_name": row.get("dataset_name"),
        "field_name": field_name,
        "truth_raw": truth,
        "prediction_raw": predicted,
        "truth_canonical": comparison.get("truth_canonical"),
        "prediction_canonical": comparison.get("prediction_canonical"),
        "strict_match": comparison.get("strict_match"),
        "normalized_full_match": comparison.get("normalized_full_match"),
        "canonical_match": comparison.get("canonical_match"),
        "suffix_insensitive_match": comparison.get("suffix_insensitive_match"),
        "similarity_score": comparison.get("similarity_score"),
        "match_classification": comparison.get("match_classification"),
        "mismatch_reason": comparison.get("mismatch_reason"),
        "adapter_schema": comparison.get("adapter_schema"),
        "normalization_warnings": comparison.get("normalization_warnings"),
        "taxonomy_code": comparison.get("taxonomy_code"),
    }


def _party_quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("field_name")), []).append(row)
    summary: dict[str, Any] = {}
    for field_name, items in grouped.items():
        evaluated = len(items)
        classes = _counts(row.get("match_classification") or "unavailable" for row in items)
        taxonomy = _counts(row.get("taxonomy_code") or "unknown" for row in items)
        summary[field_name] = {
            "evaluated_count": evaluated,
            "strict_accuracy": _ratio(items, "strict_match"),
            "normalized_full_accuracy": _ratio(items, "normalized_full_match"),
            "canonical_accuracy": _ratio(items, "canonical_match"),
            "suffix_insensitive_accuracy": _ratio(items, "suffix_insensitive_match"),
            "strong_fuzzy_accuracy": round(sum(1 for item in items if item.get("match_classification") in {"exact", "canonical_exact", "strong_fuzzy"}) / evaluated, 4) if evaluated else None,
            "partial_match_count": classes.get("partial", 0),
            "ambiguous_count": classes.get("ambiguous", 0),
            "mismatch_count": classes.get("mismatch", 0),
            "unsupported_count": classes.get("unavailable", 0),
            "classification_distribution": classes,
            "taxonomy_distribution": taxonomy,
        }
    return summary


def _ratio(rows: list[dict[str, Any]], key: str) -> float | None:
    relevant = [row for row in rows if row.get(key) is not None]
    if not relevant:
        return None
    return round(sum(1 for row in relevant if _truthy(row.get(key))) / len(relevant), 4)


def _field_accuracy_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["field_name"]), []).append(row)
    summary = {}
    for field, items in grouped.items():
        evaluated = len(items)
        normalized_correct = sum(1 for item in items if _truthy(item.get("normalized_match")))
        exact_correct = sum(1 for item in items if _truthy(item.get("exact_match")))
        missing = sum(1 for item in items if _truthy(item.get("missing_prediction")))
        false_positive = sum(1 for item in items if item.get("expected_value") in (None, "") and item.get("predicted_value") not in (None, ""))
        summary[field] = {
            "evaluated_document_count": evaluated,
            "exact_match_accuracy": round(exact_correct / evaluated, 4) if evaluated else None,
            "normalized_match_accuracy": round(normalized_correct / evaluated, 4) if evaluated else None,
            "missing_prediction_rate": round(missing / evaluated, 4) if evaluated else None,
            "false_positive_rate": round(false_positive / evaluated, 4) if evaluated else None,
        }
    return summary


def _line_item_quality_summary(latest: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [row for row in latest if _truthy(row.get("ground_truth_supported")) and int(_float_or_none(row.get("line_items_count_true")) or 0) > 0]
    if not applicable:
        return {"evaluated_document_count": 0, "matching_algorithm": "reference if available, otherwise difflib description similarity plus numeric compatibility"}
    exact_counts = sum(1 for row in applicable if int(_float_or_none(row.get("line_items_count_pred")) or 0) == int(_float_or_none(row.get("line_items_count_true")) or 0))
    pred_total = sum(int(_float_or_none(row.get("line_items_count_pred")) or 0) for row in applicable)
    truth_total = sum(int(_float_or_none(row.get("line_items_count_true")) or 0) for row in applicable)
    matched = sum(min(int(_float_or_none(row.get("line_items_count_pred")) or 0), int(_float_or_none(row.get("line_items_count_true")) or 0)) for row in applicable)
    return {
        "evaluated_document_count": len(applicable),
        "row_count_accuracy": round(exact_counts / len(applicable), 4),
        "row_count_precision": round(matched / pred_total, 4) if pred_total else 0,
        "row_count_recall": round(matched / truth_total, 4) if truth_total else 0,
        "matched_row_count": matched,
        "arithmetic_consistency_rate": None,
        "matching_algorithm": "reference if available, otherwise difflib description similarity plus numeric compatibility",
    }


def _table_quality_row(row: dict[str, Any]) -> dict[str, Any]:
    pred_count = int(_float_or_none(row.get("line_items_count_pred")) or 0)
    validated_count = int(_float_or_none(row.get("validated_line_items_count_pred")) or 0)
    review_count = int(_float_or_none(row.get("review_line_items_count_pred")) or 0)
    truth_count = int(_float_or_none(row.get("line_items_count_true")) or 0)
    diff = pred_count - truth_count
    failure_codes = _coerce_list(row.get("failure_codes"))
    table_diag = row.get("table_diagnostics") if isinstance(row.get("table_diagnostics"), dict) else {}
    return {
        "document_id": row.get("document_id"),
        "dataset_name": row.get("dataset_name"),
        "table_detected": bool(table_diag.get("table_region_detected") or pred_count > 0),
        "header_detected": bool(table_diag.get("header_confirmed")),
        "header_candidate_found": bool(table_diag.get("header_candidate_found")),
        "header_confirmed": bool(table_diag.get("header_confirmed")),
        "table_region_detected": bool(table_diag.get("table_region_detected") or pred_count > 0),
        "table_body_detected": bool(table_diag.get("table_body_detected") or pred_count > 0),
        "row_anchor_detected": bool(table_diag.get("row_anchor_detected") or pred_count > 0),
        "rows_reconstructed": bool(table_diag.get("rows_reconstructed") or pred_count > 0),
        "selected_strategy": table_diag.get("selected_strategy") or "",
        "strategy_scores": json.dumps(table_diag.get("strategy_scores") or {}, ensure_ascii=False, default=str),
        "selection_explanation": table_diag.get("selection_explanation") or "",
        "numeric_anchor_count": table_diag.get("numeric_anchor_count") or "",
        "description_anchor_count": table_diag.get("description_anchor_count") or "",
        "unresolved_fragment_count": table_diag.get("unresolved_fragment_count") or "",
        "over_merge_detected": bool(table_diag.get("over_merge_detected")),
        "under_merge_detected": bool(table_diag.get("under_merge_detected")),
        "inferred_column_count": "",
        "candidate_row_count": table_diag.get("candidate_row_count") or pred_count,
        "reconstructed_row_count": table_diag.get("reconstructed_row_count") or pred_count,
        "validated_row_count": table_diag.get("validated_row_count") if table_diag else validated_count,
        "review_row_count": table_diag.get("review_row_count") if table_diag else review_count,
        "invalid_row_count": max(0, pred_count - validated_count - review_count),
        "ground_truth_row_count": truth_count,
        "exact_count_match": pred_count == truth_count,
        "count_difference": diff,
        "line_items_presence_match": (pred_count > 0) == (truth_count > 0),
        "count_within_one": abs(diff) <= 1,
        "table_reconciliation_status": "not_compared",
        "top_failure_codes": ";".join(str(code) for code in failure_codes if str(code).startswith(("NO_", "TABLE_", "LINE_", "MISSING_LINE_ITEMS"))),
    }


def _line_item_comparison_rows(row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    table_truth = row.get("_table_ground_truth") if isinstance(row.get("_table_ground_truth"), dict) else {}
    truth_items = table_truth.get("items") or []
    truth_objects = []
    for item in truth_items:
        try:
            from scripts.table_ground_truth_adapter import CanonicalGroundTruthLineItem

            truth_objects.append(CanonicalGroundTruthLineItem(**{key: item.get(key) for key in CanonicalGroundTruthLineItem.__dataclass_fields__}))
        except Exception:
            continue
    pred_items = row.get("_predicted_line_items") or []
    comparison = compare_line_items(pred_items, truth_objects)
    strict_truth = _int_or_none(row.get("line_items_count_true"))
    canonical_truth = _int_or_none(table_truth.get("canonical_item_count"))
    pred_count = int(_float_or_none(row.get("line_items_count_pred")) or len(pred_items) or 0)
    truth_status = table_truth.get("truth_status") or ("missing" if not row.get("has_ground_truth") else "unsupported")
    canonical_evaluated = truth_status in {"supported", "explicit_zero"}
    canonical_exact = pred_count == canonical_truth if canonical_evaluated and canonical_truth is not None else None
    canonical_within_one = abs(pred_count - canonical_truth) <= 1 if canonical_evaluated and canonical_truth is not None else None
    presence = (pred_count > 0) == ((canonical_truth or 0) > 0) if canonical_evaluated and canonical_truth is not None else None
    warnings = _coerce_list(table_truth.get("adapter_warnings"))
    row_payload = {
        "document_id": row.get("document_id"),
        "dataset_name": row.get("dataset_name"),
        "source_schema": table_truth.get("source_schema"),
        "truth_status": truth_status,
        "truth_raw_count": table_truth.get("raw_item_count"),
        "truth_canonical_count": canonical_truth,
        "prediction_count": pred_count,
        "strict_exact_count_match": pred_count == strict_truth if strict_truth is not None else None,
        "canonical_exact_count_match": canonical_exact,
        "canonical_within_one": canonical_within_one,
        "absolute_count_error": abs(pred_count - canonical_truth) if canonical_evaluated and canonical_truth is not None else None,
        "truth_has_items": (canonical_truth or 0) > 0 if canonical_evaluated and canonical_truth is not None else None,
        "prediction_has_items": pred_count > 0,
        "presence_match": presence,
        "item_match_count": comparison["item_match_count"],
        "item_match_rate": comparison["item_match_rate"],
        "amount_aware_item_match_rate": comparison["amount_aware_item_match_rate"],
        "order_independent_row_match_rate": comparison["order_independent_row_match_rate"],
        "granularity_class": comparison["granularity_class"],
        "excluded_truth_records": table_truth.get("excluded_record_count"),
        "duplicate_truth_records": table_truth.get("duplicate_record_count"),
        "unsupported_truth_records": table_truth.get("unsupported_record_count"),
        "adapter_warnings": ";".join(str(item) for item in warnings),
        "primary_mismatch_reason": _line_item_mismatch_reason(pred_count, canonical_truth, truth_status, warnings, comparison["granularity_class"]),
        "manual_review_required": _manual_review_required(row, table_truth, comparison["granularity_class"]),
        "ground_truth_changed_by_adapter": strict_truth is not None and canonical_truth is not None and strict_truth != canonical_truth,
    }
    pair_rows = []
    for pair in comparison.get("pairs", []):
        pair_rows.append({
            "document_id": row.get("document_id"),
            "dataset_name": row.get("dataset_name"),
            "prediction_index": pair.get("prediction_index"),
            "truth_index": pair.get("truth_index"),
            "score": pair.get("score"),
            "status": pair.get("status"),
        })
    return row_payload, pair_rows


def _schema_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    table_truth = row.get("_table_ground_truth") if isinstance(row.get("_table_ground_truth"), dict) else {}
    strict_count = _int_or_none(row.get("line_items_count_true"))
    canonical_count = _int_or_none(table_truth.get("canonical_item_count"))
    warnings = _coerce_list(table_truth.get("adapter_warnings"))
    return {
        "document_id": row.get("document_id"),
        "dataset_name": row.get("dataset_name"),
        "annotation_source": row.get("prediction_path") or row.get("relative_path"),
        "detected_schema": table_truth.get("source_schema"),
        "raw_container_type": "list/dict/string",
        "raw_record_count": table_truth.get("raw_item_count"),
        "previous_truth_count": strict_count,
        "canonical_truth_count": canonical_count,
        "empty_record_count": sum(1 for item in warnings if item == "TABLE_GT_EMPTY_RECORD_REMOVED"),
        "duplicate_record_count": table_truth.get("duplicate_record_count"),
        "excluded_total_count": sum(1 for item in warnings if item == "TABLE_GT_TOTAL_ROW_REMOVED"),
        "unsupported_record_count": table_truth.get("unsupported_record_count"),
        "parse_warning_codes": ";".join(str(item) for item in warnings),
        "adapter_status": table_truth.get("truth_status"),
        "manual_review_required": _manual_review_required(row, table_truth, "ambiguous_granularity"),
        "audit_conclusion": _audit_conclusion(strict_count, canonical_count, table_truth),
    }


def _canonical_line_item_quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in rows if row.get("canonical_exact_count_match") is not None]
    if not evaluated:
        return {
            "evaluated_document_count": 0,
            "canonical_presence_accuracy": None,
            "canonical_exact_count_accuracy": None,
            "canonical_count_within_one_accuracy": None,
            "canonical_mae": None,
            "item_description_match_rate": None,
            "amount_aware_item_match_rate": None,
            "order_independent_row_match_rate": None,
            "split_merge_compatible_document_rate": None,
            "unsupported_label_count": sum(1 for row in rows if row.get("truth_status") == "unsupported"),
            "adapter_failure_count": sum(1 for row in rows if row.get("truth_status") == "adapter_failed"),
        }
    errors = [float(row.get("absolute_count_error") or 0) for row in evaluated]
    split_merge = [row for row in evaluated if row.get("granularity_class") in {"prediction_split", "prediction_merged"}]
    return {
        "evaluated_document_count": len(evaluated),
        "canonical_presence_accuracy": _ratio(evaluated, "presence_match"),
        "canonical_exact_count_accuracy": _ratio(evaluated, "canonical_exact_count_match"),
        "canonical_count_within_one_accuracy": _ratio(evaluated, "canonical_within_one"),
        "canonical_mae": round(sum(errors) / len(errors), 3) if errors else None,
        "item_description_match_rate": _avg_non_null([row.get("item_match_rate") for row in evaluated]),
        "amount_aware_item_match_rate": _avg_non_null([row.get("amount_aware_item_match_rate") for row in evaluated]),
        "order_independent_row_match_rate": _avg_non_null([row.get("order_independent_row_match_rate") for row in evaluated]),
        "split_merge_compatible_document_rate": round(len(split_merge) / len(evaluated), 4),
        "unsupported_label_count": sum(1 for row in rows if row.get("truth_status") == "unsupported"),
        "adapter_failure_count": sum(1 for row in rows if row.get("truth_status") == "adapter_failed"),
        "explicit_zero_item_documents": sum(1 for row in rows if row.get("truth_status") == "explicit_zero"),
        "truth_counts_changed_by_adapter": sum(1 for row in rows if _truthy(row.get("ground_truth_changed_by_adapter"))),
        "empty_records_removed": sum(int(row.get("excluded_truth_records") or 0) for row in rows),
        "duplicate_records_removed": sum(int(row.get("duplicate_truth_records") or 0) for row in rows),
    }


def _line_item_mismatch_reason(pred_count: int, truth_count: int | None, truth_status: str, warnings: list[Any], granularity: str) -> str:
    if truth_status not in {"supported", "explicit_zero"}:
        return f"truth_{truth_status}"
    if truth_count is None:
        return "truth_count_unavailable"
    if pred_count == truth_count:
        return "count_match"
    if warnings:
        return str(warnings[0])
    if granularity != "same_granularity":
        return granularity
    return "count_mismatch"


def _manual_review_required(row: dict[str, Any], table_truth: dict[str, Any], granularity: str) -> bool:
    warnings = _coerce_list(table_truth.get("adapter_warnings"))
    return (
        "donut" in str(row.get("dataset_name") or "").lower()
        or "invoicexpert" in str(row.get("dataset_name") or "").lower()
        or any("MANUAL" in str(item) or "UNSUPPORTED" in str(item) for item in warnings)
        or granularity in {"prediction_split", "prediction_merged", "ambiguous_granularity"}
    )


def _audit_conclusion(strict_count: int | None, canonical_count: int | None, table_truth: dict[str, Any]) -> str:
    status = table_truth.get("truth_status")
    if status not in {"supported", "explicit_zero"}:
        return f"not_evaluated_{status}"
    if strict_count != canonical_count:
        return "truth_count_changed_by_adapter"
    if canonical_count == 0:
        return "zero_items_confirmed"
    return "canonical_items_supported"


def _avg_non_null(values: list[Any]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    return round(sum(numeric) / len(numeric), 4) if numeric else None


def _table_quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "evaluated_document_count": 0,
            "table_detection_accuracy": None,
            "header_detection_rate": None,
            "header_candidate_rate": None,
            "header_confirmed_rate": None,
            "table_region_detection_rate": None,
            "table_body_detection_rate": None,
            "row_anchor_detection_rate": None,
            "rows_reconstructed_rate": None,
            "line_items_presence_accuracy": None,
            "exact_line_item_count_accuracy": None,
            "count_within_one_accuracy": None,
            "count_mean_absolute_error": None,
            "validated_row_ratio": None,
            "table_reconciliation_rate": None,
            "unresolved_fragment_rate": None,
            "by_dataset": {},
        }
    diffs = [abs(int(row.get("count_difference") or 0)) for row in rows]
    total_pred = sum(int(_float_or_none(row.get("reconstructed_row_count")) or 0) for row in rows)
    validated = sum(int(_float_or_none(row.get("validated_row_count")) or 0) for row in rows)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("dataset_name") or "unknown"), []).append(row)
    return {
        "evaluated_document_count": len(rows),
        "table_detection_accuracy": _ratio(rows, "line_items_presence_match"),
        "header_detection_rate": _ratio(rows, "header_confirmed"),
        "header_candidate_rate": _ratio(rows, "header_candidate_found"),
        "header_confirmed_rate": _ratio(rows, "header_confirmed"),
        "table_region_detection_rate": _ratio(rows, "table_region_detected"),
        "table_body_detection_rate": _ratio(rows, "table_body_detected"),
        "row_anchor_detection_rate": _ratio(rows, "row_anchor_detected"),
        "rows_reconstructed_rate": _ratio(rows, "rows_reconstructed"),
        "line_items_presence_accuracy": _ratio(rows, "line_items_presence_match"),
        "exact_line_item_count_accuracy": _ratio(rows, "exact_count_match"),
        "count_within_one_accuracy": _ratio(rows, "count_within_one"),
        "count_mean_absolute_error": round(sum(diffs) / len(diffs), 3),
        "validated_row_ratio": round(validated / total_pred, 4) if total_pred else 0.0,
        "table_reconciliation_rate": None,
        "unresolved_fragment_rate": None,
        "by_dataset": {dataset: _table_quality_summary_shallow(items) for dataset, items in sorted(grouped.items())},
    }


def _table_quality_summary_shallow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    diffs = [abs(int(row.get("count_difference") or 0)) for row in rows]
    return {
        "documents": len(rows),
        "presence_accuracy": _ratio(rows, "line_items_presence_match"),
        "exact_count_accuracy": _ratio(rows, "exact_count_match"),
        "count_within_one_accuracy": _ratio(rows, "count_within_one"),
        "header_candidate_rate": _ratio(rows, "header_candidate_found"),
        "header_confirmed_rate": _ratio(rows, "header_confirmed"),
        "table_region_detection_rate": _ratio(rows, "table_region_detected"),
        "mean_absolute_error": round(sum(diffs) / len(diffs), 3) if diffs else None,
    }


def _benchmark_valid(latest: list[dict[str, Any]], performance: dict[str, Any]) -> bool:
    if len({row.get("document_id") for row in latest}) != len(latest):
        return False
    if performance["fresh_ocr"]["count"] == 0 and len(latest) > 0:
        return False
    return True


def _benchmark_warnings(latest: list[dict[str, Any]], performance: dict[str, Any], quality: dict[str, Any]) -> list[str]:
    warnings = []
    if not any(row.get("extraction_status") == "valid" for row in latest):
        warnings.append("NO_VALID_EXTRACTIONS")
    if not any(row.get("erp_status") == "ready" for row in latest):
        warnings.append("NO_ERP_READY_DOCUMENTS")
    gt = quality["ground_truth"]
    if gt["ground_truth_evaluated_count"] < max(5, len(latest) * 0.25):
        warnings.append("GROUND_TRUTH_COVERAGE_LOW")
    invalid_high = sum(1 for row in latest if row.get("extraction_status") == "invalid" and _truthy(row.get("confidence_warning")))
    if latest and invalid_high / len(latest) >= 0.5:
        warnings.append("HIGH_CONFIDENCE_INVALID_RATE_HIGH")
    completeness = _field_completeness(latest)
    if completeness.get("currency_present", {}).get("count") == 0:
        warnings.append("CURRENCY_COMPLETENESS_ZERO")
    if performance["fresh_ocr"].get("p90") and performance["fresh_ocr"]["p90"] > 30:
        warnings.append("P90_OCR_TOO_HIGH")
    return warnings


def _benchmark_failures(attempts: list[dict[str, Any]], latest: list[dict[str, Any]], performance: dict[str, Any]) -> list[str]:
    failures = []
    if len({row.get("document_id") for row in latest}) != len(latest):
        failures.append("DUPLICATE_LATEST_DOCUMENT_SELECTION")
    if any(int(row.get("attempt_number") or 0) < 1 for row in attempts):
        failures.append("IMPOSSIBLE_ATTEMPT_HISTORY")
    if any(_truthy(row.get("fresh_ocr")) and _truthy(row.get("disk_cache_hit")) for row in attempts):
        failures.append("CACHED_ATTEMPT_MARKED_FRESH")
    return failures


def _summary(attempts: list[dict[str, Any]], latest: list[dict[str, Any]], configuration: dict[str, Any], performance: dict[str, Any], quality: dict[str, Any], failures: dict[str, Any]) -> dict[str, Any]:
    total_attempts = len(attempts)
    completed_attempts = [row for row in attempts if row.get("execution_status") == "completed"]
    failed_attempts = [row for row in attempts if row.get("execution_status") == "failed"]
    timeout_attempts = [row for row in attempts if row.get("execution_status") == "timeout"]
    by_dataset: dict[str, dict[str, Any]] = {}
    for row in latest:
        dataset = row.get("dataset_name") or "unknown"
        bucket = by_dataset.setdefault(dataset, {"documents": 0, "execution_completed": 0, "execution_failed": 0, "valid": 0, "needs_review": 0, "invalid": 0, "erp_ready": 0, "erp_blocked": 0})
        bucket["documents"] += 1
        if row.get("execution_status") == "completed":
            bucket["execution_completed"] += 1
        else:
            bucket["execution_failed"] += 1
        status = row.get("extraction_status") or row.get("validation_status")
        if status in ("valid", "needs_review", "invalid"):
            bucket[status] += 1
        if row.get("erp_status") == "ready":
            bucket["erp_ready"] += 1
        if row.get("erp_status") == "blocked":
            bucket["erp_blocked"] += 1
    unique_document_count = len({row.get("document_id") for row in attempts if row.get("document_id")})
    retry_count = sum(1 for row in attempts if _truthy(row.get("is_retry")))
    no_success_global = total_attempts > 0 and not completed_attempts
    validation_distribution = _counts(row.get("extraction_status") or row.get("validation_status") or "unavailable" for row in latest)
    erp_distribution = _counts(row.get("erp_status") or "unavailable" for row in latest)
    field_completeness = _field_completeness(latest)
    suspicious = [row for row in latest if row.get("suspicious_field_codes") or _truthy(row.get("confidence_warning"))]
    return {
        "run_id": configuration.get("run_id"),
        "generated_at": _utc_now(),
        "configuration": configuration,
        "run_identity": _run_identity(configuration),
        "unique_document_count": unique_document_count,
        "attempt_count": total_attempts,
        "retry_count": retry_count,
        "execution_completed_count": len(completed_attempts),
        "execution_failed_count": len(failed_attempts),
        "execution_timeout_count": len(timeout_attempts),
        "validation_valid_count": validation_distribution.get("valid", 0),
        "validation_needs_review_count": validation_distribution.get("needs_review", 0),
        "validation_invalid_count": validation_distribution.get("invalid", 0),
        "ERP_ready_count": erp_distribution.get("ready", 0),
        "ERP_blocked_count": erp_distribution.get("blocked", 0),
        "fresh_ocr_attempt_count": performance["fresh_ocr"]["count"],
        "cached_attempt_count": performance["cached"]["count"],
        "fresh_ocr_median_seconds": performance["fresh_ocr"]["median"],
        "fresh_ocr_p90_seconds": performance["fresh_ocr"]["p90"],
        "fresh_ocr_max_seconds": performance["fresh_ocr"]["max"],
        "success_count": len(completed_attempts),
        "success_count_definition": "execution completed without uncaught exception",
        "validation_distribution": validation_distribution,
        "erp_distribution": erp_distribution,
        "erp_readiness_distribution": erp_distribution,
        "execution_distribution": _counts(row.get("execution_status") or "unknown" for row in attempts),
        "execution_error_distribution": _counts(row.get("execution_error_stage") or row.get("execution_error_type") or "none" for row in attempts if row.get("execution_status") != "completed"),
        "dataset_metrics": by_dataset,
        "field_completeness": field_completeness,
        "suspicious_prediction_count": len(suspicious),
        "suspicious_predictions": [_compact_result(row) for row in suspicious[:25]],
        "retry_history": [_compact_result(row) for row in _retry_history(attempts)],
        "performance": performance,
        "ground_truth": quality["ground_truth"],
        "field_accuracy": quality["field_accuracy"],
        "party_quality": quality.get("party_quality", {}),
        "line_item_quality": quality["line_item_quality"],
        "canonical_line_item_quality": quality.get("canonical_line_item_quality", {}),
        "table_quality": quality.get("table_quality", {}),
        "failure_taxonomy": failures,
        "benchmark_valid": _benchmark_valid(latest, performance),
        "benchmark_warning_codes": _benchmark_warnings(latest, performance, quality),
        "benchmark_failure_codes": _benchmark_failures(attempts, latest, performance),
        "ocr_profile": configuration.get("ocr_profile"),
        "benchmark_invalid": no_success_global,
        "invalid_reason": "Benchmark invalid: OCR engine unavailable or OCR extraction failed globally." if no_success_global else "",
    }


def _render_markdown(summary: dict[str, Any]) -> str:
    fresh = summary.get("performance", {}).get("fresh_ocr", {})
    cached = summary.get("performance", {}).get("cached", {})
    lines = [
        f"# Benchmark Run {summary.get('run_id')}",
        "",
        "## Run Identity",
        f"- OCR profile: `{summary.get('ocr_profile')}`",
        f"- Configuration hash: `{summary.get('configuration', {}).get('configuration_hash')}`",
        f"- Git commit: `{summary.get('run_identity', {}).get('git', {}).get('commit')}`",
        f"- Dirty worktree: {summary.get('run_identity', {}).get('git', {}).get('dirty')}",
        "",
        "## Execution Summary",
        f"- Unique documents: {summary.get('unique_document_count')}",
        f"- Attempts: {summary.get('attempt_count')}",
        f"- Completed attempts: {summary.get('execution_completed_count')}",
        f"- Failed attempts: {summary.get('execution_failed_count')}",
        f"- Retry attempts: {summary.get('retry_count')}",
        f"- Benchmark warnings: {summary.get('benchmark_warning_codes')}",
        "",
        "## Extraction Quality Summary",
        f"- Valid: {summary.get('validation_valid_count')}",
        f"- Needs review: {summary.get('validation_needs_review_count')}",
        f"- Invalid: {summary.get('validation_invalid_count')}",
        f"- Suspicious predictions: {summary.get('suspicious_prediction_count')}",
        "",
        "## ERP Readiness",
        f"- Ready: {summary.get('ERP_ready_count')}",
        f"- Blocked: {summary.get('ERP_blocked_count')}",
        "",
        "## Ground Truth Coverage",
    ]
    for key, value in summary.get("ground_truth", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Field Accuracy",
    ])
    for key, value in summary.get("field_accuracy", {}).items():
        lines.append(f"- `{key}`: {value}")
    lines.extend([
        "",
        "## Party Canonical Comparison",
    ])
    for key, value in summary.get("party_quality", {}).items():
        lines.append(f"- `{key}`: {value}")
    lines.extend([
        "",
        "## Table Reconstruction Quality",
    ])
    for key, value in summary.get("table_quality", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Canonical Line-Item Quality",
    ])
    for key, value in summary.get("canonical_line_item_quality", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Fresh OCR Performance",
        f"- Fresh OCR count: {fresh.get('count')}",
        f"- Median: {fresh.get('median')}",
        f"- Mean: {fresh.get('mean')}",
        f"- P90: {fresh.get('p90')}",
        f"- P95: {fresh.get('p95')}",
        f"- Max: {fresh.get('max')}",
        f"- Under 30 seconds: {fresh.get('under_30_seconds')}",
        f"- Over 30 seconds: {fresh.get('over_30_seconds')}",
        "",
        "## Cache Performance",
        f"- Cached attempt count: {cached.get('count')}",
        f"- Cached median: {cached.get('median')}",
        f"- Cached mean: {cached.get('mean')}",
        f"- Cached P90: {cached.get('p90')}",
        "",
        "## Field Completeness",
    ])
    for key, value in summary.get("field_completeness", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Failure Taxonomy",
    ])
    for key, value in list(summary.get("failure_taxonomy", {}).get("count_by_failure_code", {}).items())[:20]:
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Party Evaluation Taxonomy"])
    for key, value in summary.get("failure_taxonomy", {}).get("party_comparison_code_distribution", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Validation Distribution",
    ])
    for key, value in summary.get("validation_distribution", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## ERP Distribution"])
    for key, value in summary.get("erp_distribution", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Suspicious Predictions"])
    lines.append(f"- Suspicious latest documents: {summary.get('suspicious_prediction_count')}")
    for row in summary.get("suspicious_predictions", [])[:10]:
        lines.append(f"- `{row.get('document_id')}`: {row.get('suspicious_field_codes') or row.get('confidence_warning_codes')}")
    lines.extend(["", "## Retry History"])
    for row in summary.get("retry_history", [])[:10]:
        lines.append(f"- `{row.get('document_id')}` attempt {row.get('attempt_number')}: {row.get('previous_execution_status')} -> {row.get('execution_status')}, cache={row.get('ocr_cache_source')}, duration={row.get('duration_seconds')}")
    lines.extend(["", "## Dataset Breakdown"])
    for dataset, metrics in summary.get("dataset_metrics", {}).items():
        lines.append(f"- `{dataset}`: {metrics}")
    lines.extend(["", "## Remaining Limitations"])
    lines.append("- `document_timeout` is a soft timeout budget; OCR is not hard-terminated mid-call.")
    lines.append("- Confidence is OCR/extraction confidence, not ground-truth accuracy.")
    if summary.get("benchmark_invalid"):
        lines.extend(["", f"**{summary.get('invalid_reason')}**"])
    if summary.get("validation_valid_count") == 0:
        lines.extend(["", "**Warning:** No valid extractions in this run."])
    if summary.get("ERP_ready_count") == 0:
        lines.append("**Warning:** No documents are ERP-ready.")
    return "\n".join(lines) + "\n"


def _render_html(summary: dict[str, Any]) -> str:
    fresh = summary.get("performance", {}).get("fresh_ocr", {})
    cached = summary.get("performance", {}).get("cached", {})
    sections = [
        ("Run Identity", _table(summary.get("run_identity", {}))),
        ("Execution Summary", _table({
            "unique documents": summary.get("unique_document_count"),
            "attempts": summary.get("attempt_count"),
            "completed": summary.get("execution_completed_count"),
            "failed": summary.get("execution_failed_count"),
            "retries": summary.get("retry_count"),
            "benchmark warnings": summary.get("benchmark_warning_codes"),
        })),
        ("Extraction Quality Summary", _table({
            "valid": summary.get("validation_valid_count"),
            "needs_review": summary.get("validation_needs_review_count"),
            "invalid": summary.get("validation_invalid_count"),
            "suspicious predictions": summary.get("suspicious_prediction_count"),
        })),
        ("ERP Readiness", _table({"ready": summary.get("ERP_ready_count"), "blocked": summary.get("ERP_blocked_count")})),
        ("Ground Truth Coverage", _table(summary.get("ground_truth", {}))),
        ("Field Accuracy", _table(summary.get("field_accuracy", {}))),
        ("Party Canonical Comparison", _table(summary.get("party_quality", {}))),
        ("Table Reconstruction Quality", _table(summary.get("table_quality", {}))),
        ("Canonical Line-Item Quality", _table(summary.get("canonical_line_item_quality", {}))),
        ("Fresh OCR Performance", _table(fresh)),
        ("Cache Performance", _table(cached)),
        ("Validation Distribution", _table(summary.get("validation_distribution", {}))),
        ("ERP Distribution", _table(summary.get("erp_distribution", {}))),
        ("Field Completeness", _table(summary.get("field_completeness", {}))),
        ("Failure Taxonomy", _table(summary.get("failure_taxonomy", {}).get("count_by_failure_code", {}))),
        ("Party Evaluation Taxonomy", _table(summary.get("failure_taxonomy", {}).get("party_comparison_code_distribution", {}))),
        ("Dataset Breakdown", _table(summary.get("dataset_metrics", {}))),
        ("Suspicious Predictions", _rows_table(summary.get("suspicious_predictions", [])[:25], ["document_id", "suspicious_field_codes", "confidence_warning_codes", "validation_status", "overall_confidence"])),
        ("Retry History", _rows_table(summary.get("retry_history", []), ["document_id", "attempt_number", "previous_execution_status", "execution_status", "ocr_cache_source", "duration_seconds"])),
        ("Remaining Limitations", "<ul><li>Timeouts are soft budget markers, not hard process termination.</li><li>Confidence is not accuracy.</li></ul>"),
    ]
    body = "\n".join(f"<section><h2>{_html_escape(title)}</h2>{content}</section>" for title, content in sections)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Benchmark Report { _html_escape(str(summary.get('run_id'))) }</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #172033; background: #f8fafc; }}
    section {{ background: white; border: 1px solid #d8e0ea; border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #e5eaf0; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ color: #44546a; background: #f1f5f9; }}
    code {{ background: #eef2f7; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Benchmark Run {_html_escape(str(summary.get('run_id')))}</h1>
  {body}
</body>
</html>
"""


def _table(mapping: dict[str, Any]) -> str:
    rows = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False, default=str)
        rows.append(f"<tr><th>{_html_escape(str(key))}</th><td>{_html_escape(str(value))}</td></tr>")
    return f"<table>{''.join(rows)}</table>"


def _rows_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "<p>None.</p>"
    header = "".join(f"<th>{_html_escape(col)}</th>" for col in columns)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{_html_escape(str(row.get(col, '')))}</td>" for col in columns) + "</tr>")
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _load_or_create_checkpoint(path: Path, run_id: str, configuration: dict[str, Any], environment: dict[str, Any], selected: list[BenchmarkDocument], resume: bool) -> dict[str, Any]:
    if resume and path.exists():
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        old_hash = checkpoint.get("benchmark_configuration", {}).get("configuration_hash")
        if old_hash and old_hash != configuration.get("configuration_hash"):
            raise SystemExit("Checkpoint configuration does not match current CLI/configuration. Use --restart or a new --run-id.")
        checkpoint["resumed_at"] = _utc_now()
        return checkpoint
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": run_id,
        "benchmark_status": "created",
        "created_at": _utc_now(),
        "benchmark_configuration": configuration,
        "configuration_hash": configuration.get("configuration_hash"),
        "ocr_profile": configuration.get("ocr_profile"),
        "detector": environment.get("effective_ocr_config", {}).get("detector"),
        "recognizer": environment.get("effective_ocr_config", {}).get("recognizer"),
        "cpu_threads": environment.get("effective_ocr_config", {}).get("cpu_threads"),
        "maximum_input_side": environment.get("effective_ocr_config", {}).get("input_max_side"),
        "preprocessing_profile": environment.get("effective_ocr_config", {}).get("preprocessing_profile"),
        "cache_mode": _cache_mode(configuration),
        "selected_document_ids": [doc.document_id for doc in selected],
        "completed_document_ids": [],
        "failed_document_ids": [],
        "skipped_document_ids": [],
        "current_document_id": None,
        "last_completed_document_id": None,
        "counts": {"selected": len(selected), "completed": 0, "failed": 0, "skipped": 0},
        "result_locations": {},
        "python": sys.version,
        "git": _git_state(),
    }


def _run_identity(configuration: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": configuration.get("run_id"),
        "ocr_profile": configuration.get("ocr_profile"),
        "configuration_hash": configuration.get("configuration_hash"),
        "git": _git_state(),
        "created_time": configuration.get("created_at", ""),
        "completed_time": configuration.get("completed_at", ""),
    }


def _field_completeness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows) or 1
    fields = {
        "supplier_present": "supplier_name_pred",
        "customer_present": "customer_name_pred",
        "invoice_number_present": "invoice_number_pred",
        "invoice_date_present": "invoice_date_pred",
        "currency_present": "currency_pred",
        "TTC_present": "amount_ttc_pred",
        "line_items_present": "line_items_count_pred",
    }
    payload = {}
    for label, key in fields.items():
        count = 0
        for row in rows:
            value = row.get(key)
            if key == "line_items_count_pred":
                present = int(value or 0) > 0
            else:
                present = value not in (None, "", [])
            count += 1 if present else 0
        payload[label] = {"count": count, "percent": round((count / total) * 100, 2)}
    return payload


def _retry_history(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in attempts
        if _truthy(row.get("is_retry")) or row.get("previous_attempt_id")
    ]


def _compact_result(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "attempt_id",
        "attempt_number",
        "document_id",
        "dataset_name",
        "filename",
        "execution_status",
        "extraction_status",
        "erp_status",
        "validation_status",
        "duration_seconds",
        "fresh_ocr",
        "disk_cache_hit",
        "ocr_cache_source",
        "total_paddle_calls",
        "is_retry",
        "previous_attempt_id",
        "previous_execution_status",
        "supplier_name_pred",
        "customer_name_pred",
        "invoice_number_pred",
        "amount_ttc_pred",
        "overall_confidence",
        "suspicious_field_codes",
        "confidence_warning_codes",
        "execution_error_type",
        "execution_error_message",
    ]
    return {key: row.get(key) for key in keys if key in row}


def _cache_mode(configuration: dict[str, Any]) -> str:
    if configuration.get("disable_cache"):
        return "disabled"
    if configuration.get("refresh_cache"):
        return "refresh"
    return "reuse"


def _disable_cache(args: Any) -> bool:
    return bool(getattr(args, "disable_cache", False) or getattr(args, "no_ocr_cache", False))


def _refresh_cache(args: Any) -> bool:
    return bool(getattr(args, "refresh_cache", False) or getattr(args, "refresh_ocr_cache", False))


def _prepare_run_paths(run_dir: Path) -> dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "root": run_dir,
        "checkpoint": run_dir / "checkpoint.json",
        "manifest": run_dir / "manifest.json",
        "configuration": run_dir / "configuration.json",
        "environment": run_dir / "environment.json",
        "results_jsonl": run_dir / "results.jsonl",
        "results_csv": run_dir / "results.csv",
        "attempts_csv": run_dir / "attempts.csv",
        "document_latest_results_csv": run_dir / "document_latest_results.csv",
        "timings_csv": run_dir / "timings.csv",
        "performance_fresh_ocr": run_dir / "performance_fresh_ocr.json",
        "performance_cached": run_dir / "performance_cached.json",
        "performance_all_attempts": run_dir / "performance_all_attempts.json",
        "failure_matrix": run_dir / "failure_matrix.csv",
        "failure_summary": run_dir / "failure_summary.json",
        "field_quality": run_dir / "field_quality.csv",
        "party_comparison": run_dir / "party_comparison.csv",
        "party_candidate_ranking": run_dir / "party_candidate_ranking.csv",
        "party_candidate_debug": run_dir / "party_candidate_debug.json",
        "party_confidence_report": run_dir / "party_confidence_report.csv",
        "table_quality": run_dir / "table_quality.csv",
        "table_failure_matrix": run_dir / "table_failure_matrix.csv",
        "line_item_comparison": run_dir / "line_item_comparison.csv",
        "line_item_pair_comparison": run_dir / "line_item_pair_comparison.csv",
        "table_ground_truth_manual_review": run_dir / "table_ground_truth_manual_review.csv",
        "schema_audit": run_dir / "table_ground_truth_schema_audit.csv",
        "p3_vs_p3_1_table_comparison": run_dir / "p3_vs_p3_1_table_comparison.csv",
        "donut_table_failure_analysis": run_dir / "donut_table_failure_analysis.csv",
        "invoicexpert_table_failure_analysis": run_dir / "invoicexpert_table_failure_analysis.csv",
        "row_reconstruction_summary": run_dir / "row_reconstruction_summary.json",
        "dataset_quality_summary": run_dir / "dataset_quality_summary.csv",
        "errors_csv": run_dir / "errors.csv",
        "timeouts_csv": run_dir / "timeouts.csv",
        "skipped_csv": run_dir / "skipped.csv",
        "summary": run_dir / "summary.json",
        "report_md": run_dir / "report.md",
        "report_html": run_dir / "report.html",
        "run_log": run_dir / "run.log",
        "slowest": run_dir / "slowest_20_documents.json",
        "worst": run_dir / "worst_20_documents.json",
        "manual_review": run_dir / "manual_review_sample.csv",
        "errors": run_dir / "errors",
        "artifacts": run_dir / "artifacts",
        "partial": run_dir / "partial_results",
    }
    for key in ("errors", "artifacts", "partial"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def _reset_run_files(paths: dict[str, Path]) -> None:
    for key in ("checkpoint", "manifest", "configuration", "environment", "results_jsonl", "results_csv", "attempts_csv", "document_latest_results_csv", "timings_csv", "errors_csv", "timeouts_csv", "skipped_csv", "summary", "report_md", "report_html", "performance_fresh_ocr", "performance_cached", "performance_all_attempts", "party_candidate_ranking", "party_candidate_debug", "party_confidence_report"):
        try:
            paths[key].unlink()
        except FileNotFoundError:
            pass


def _save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    checkpoint["updated_at"] = _utc_now()
    checkpoint.setdefault("result_locations", {}).update({
        "results_jsonl": "results.jsonl",
        "results_csv": "results.csv",
        "attempts_csv": "attempts.csv",
        "document_latest_results_csv": "document_latest_results.csv",
        "errors_csv": "errors.csv",
        "timeouts_csv": "timeouts.csv",
        "summary": "summary.json",
        "report_md": "report.md",
    })
    _atomic_json(path, checkpoint)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _existing_result_ids(path: Path) -> set[str]:
    return {str(row.get("document_id")) for row in _read_jsonl(path) if row.get("document_id")}


def _append_partial_csv(paths: dict[str, Path], result: dict[str, Any]) -> None:
    _append_csv(paths["partial"] / "results_stream.csv", result, RESULT_FIELDNAMES)


def _append_csv(path: Path, row: dict[str, Any], fieldnames: list[str]) -> None:
    exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def _ensure_event_csv(path: Path, fieldnames: list[str]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore").writeheader()


def _write_results_csv(path: Path, results: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


def _write_timings_csv(path: Path, results: list[dict[str, Any]]) -> None:
    stage_names = sorted({key for row in results for key in ((row.get("timings") or {}).keys())})
    fields = ["document_id", "dataset_name", "processing_time_seconds"] + stage_names
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow({"document_id": row.get("document_id"), "dataset_name": row.get("dataset_name"), "processing_time_seconds": row.get("processing_time_seconds"), **(row.get("timings") or {})})


def _write_failure_matrix(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["dataset_name", "document_id", "filename", "execution_status", "extraction_status", "erp_status", "primary_failure_code", "failure_codes", "missing_fields", "suspicious_fields", "overall_confidence", "fresh_ocr", "duration_seconds", "has_ground_truth"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "dataset_name": row.get("dataset_name"),
                "document_id": row.get("document_id"),
                "filename": row.get("filename"),
                "execution_status": row.get("execution_status"),
                "extraction_status": row.get("extraction_status"),
                "erp_status": row.get("erp_status"),
                "primary_failure_code": row.get("primary_failure_code"),
                "failure_codes": ";".join(str(item) for item in _coerce_list(row.get("failure_codes"))),
                "missing_fields": ";".join(str(item) for item in _coerce_list(row.get("missing_required_fields"))),
                "suspicious_fields": json.dumps(row.get("suspicious_fields") or {}, ensure_ascii=False, default=str),
                "overall_confidence": row.get("overall_confidence"),
                "fresh_ocr": row.get("fresh_ocr"),
                "duration_seconds": row.get("duration_seconds"),
                "has_ground_truth": row.get("has_ground_truth"),
            })


def _write_field_quality(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "dataset_name",
        "document_id",
        "field_name",
        "expected_value",
        "predicted_value",
        "exact_match",
        "normalized_match",
        "absolute_error",
        "relative_error",
        "missing_prediction",
        "confidence",
        "failure_codes",
        "party_strict_match",
        "party_normalized_full_match",
        "party_canonical_match",
        "party_suffix_insensitive_match",
        "party_similarity_score",
        "party_match_classification",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_party_comparison(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "document_id",
        "dataset_name",
        "field_name",
        "truth_raw",
        "prediction_raw",
        "truth_canonical",
        "prediction_canonical",
        "strict_match",
        "normalized_full_match",
        "canonical_match",
        "suffix_insensitive_match",
        "similarity_score",
        "match_classification",
        "mismatch_reason",
        "adapter_schema",
        "normalization_warnings",
        "taxonomy_code",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_party_candidate_ranking(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "document_id",
        "dataset_name",
        "field_name",
        "role",
        "rank",
        "candidate",
        "score",
        "selected",
        "selected_reason",
        "source",
        "original_field",
        "page",
        "line_index",
        "score_breakdown",
        "penalties",
        "bbox",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            selected = {
                "supplier": str(row.get("supplier_name_pred") or ""),
                "customer": str(row.get("customer_name_pred") or ""),
            }
            by_role: dict[str, int] = {}
            for item in row.get("party_candidate_ranking") or []:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "")
                by_role[role] = by_role.get(role, 0) + 1
                candidate_value = str(item.get("value") or "")
                writer.writerow({
                    "document_id": row.get("document_id"),
                    "dataset_name": row.get("dataset_name"),
                    "field_name": f"{role}_name" if role else "",
                    "role": role,
                    "rank": by_role[role],
                    "candidate": candidate_value,
                    "score": item.get("score"),
                    "selected": bool(role and candidate_value and candidate_value == selected.get(role)),
                    "selected_reason": item.get("selected_reason"),
                    "source": item.get("source"),
                    "original_field": item.get("original_field"),
                    "page": item.get("page"),
                    "line_index": item.get("line_index"),
                    "score_breakdown": json.dumps(item.get("score_breakdown") or {}, ensure_ascii=False, default=str),
                    "penalties": "; ".join(str(value) for value in _coerce_list(item.get("penalties"))),
                    "bbox": json.dumps(item.get("bbox") or {}, ensure_ascii=False, default=str),
                })


def _write_party_candidate_debug(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = []
    for row in rows:
        payload.append({
            "document_id": row.get("document_id"),
            "dataset_name": row.get("dataset_name"),
            "relative_path": row.get("relative_path"),
            "supplier_name_pred": row.get("supplier_name_pred"),
            "customer_name_pred": row.get("customer_name_pred"),
            "party_candidate_ranking": row.get("party_candidate_ranking") or [],
            "party_confidence": row.get("party_confidence") or {},
        })
    _atomic_json(path, payload)


def _write_party_confidence_report(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "document_id",
        "dataset_name",
        "supplier_name_pred",
        "customer_name_pred",
        "supplier_top_score",
        "customer_top_score",
        "supplier_candidate_count",
        "customer_candidate_count",
        "supplier_selected_reason",
        "customer_selected_reason",
        "supplier_canonical_match",
        "customer_canonical_match",
    ]
    quality = _quality_payloads(rows)
    party_by_doc_field = {
        (row.get("document_id"), row.get("field_name")): row
        for row in quality.get("party_rows", [])
    }
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            confidence = row.get("party_confidence") or {}
            supplier_quality = party_by_doc_field.get((row.get("document_id"), "supplier_name"), {})
            customer_quality = party_by_doc_field.get((row.get("document_id"), "customer_name"), {})
            writer.writerow({
                "document_id": row.get("document_id"),
                "dataset_name": row.get("dataset_name"),
                "supplier_name_pred": row.get("supplier_name_pred"),
                "customer_name_pred": row.get("customer_name_pred"),
                "supplier_top_score": confidence.get("supplier_top_score"),
                "customer_top_score": confidence.get("customer_top_score"),
                "supplier_candidate_count": confidence.get("supplier_candidate_count"),
                "customer_candidate_count": confidence.get("customer_candidate_count"),
                "supplier_selected_reason": confidence.get("supplier_selected_reason"),
                "customer_selected_reason": confidence.get("customer_selected_reason"),
                "supplier_canonical_match": supplier_quality.get("canonical_match"),
                "customer_canonical_match": customer_quality.get("canonical_match"),
            })


def _write_table_quality(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "document_id",
        "dataset_name",
        "table_detected",
        "header_detected",
        "header_candidate_found",
        "header_confirmed",
        "table_region_detected",
        "table_body_detected",
        "row_anchor_detected",
        "rows_reconstructed",
        "selected_strategy",
        "strategy_scores",
        "selection_explanation",
        "numeric_anchor_count",
        "description_anchor_count",
        "unresolved_fragment_count",
        "over_merge_detected",
        "under_merge_detected",
        "inferred_column_count",
        "candidate_row_count",
        "reconstructed_row_count",
        "validated_row_count",
        "review_row_count",
        "invalid_row_count",
        "ground_truth_row_count",
        "exact_count_match",
        "count_difference",
        "line_items_presence_match",
        "count_within_one",
        "table_reconciliation_status",
        "top_failure_codes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_table_failure_matrix(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["dataset_name", "document_id", "top_failure_codes", "count_difference", "table_detected", "header_candidate_found", "header_confirmed", "table_region_detected", "table_body_detected", "row_anchor_detected", "rows_reconstructed", "selected_strategy"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_line_item_comparison(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "document_id", "dataset_name", "source_schema", "truth_status", "truth_raw_count",
        "truth_canonical_count", "prediction_count", "strict_exact_count_match",
        "canonical_exact_count_match", "canonical_within_one", "absolute_count_error",
        "truth_has_items", "prediction_has_items", "presence_match", "item_match_count",
        "item_match_rate", "amount_aware_item_match_rate", "order_independent_row_match_rate",
        "granularity_class", "excluded_truth_records", "duplicate_truth_records",
        "unsupported_truth_records", "adapter_warnings", "primary_mismatch_reason",
        "manual_review_required", "ground_truth_changed_by_adapter",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_line_item_pair_comparison(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["document_id", "dataset_name", "prediction_index", "truth_index", "score", "status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_table_ground_truth_manual_review(path: Path, rows: list[dict[str, Any]]) -> None:
    selected = [row for row in rows if _truthy(row.get("manual_review_required")) or _truthy(row.get("ground_truth_changed_by_adapter"))]
    selected = sorted(selected, key=lambda row: (str(row.get("dataset_name")), -int(row.get("absolute_count_error") or 0), str(row.get("document_id"))))
    _write_line_item_comparison(path, selected[:200])


def _write_schema_audit(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "document_id", "dataset_name", "annotation_source", "detected_schema", "raw_container_type",
        "raw_record_count", "previous_truth_count", "canonical_truth_count", "empty_record_count",
        "duplicate_record_count", "excluded_total_count", "unsupported_record_count",
        "parse_warning_codes", "adapter_status", "manual_review_required", "audit_conclusion",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_p3_vs_p3_1_comparison(path: Path) -> None:
    root = RUNS_ROOT
    p3 = _read_csv_dicts(root / "optimized_p3_table_50doc_01" / "line_item_comparison.csv")
    p31 = _read_csv_dicts(root / "optimized_p3_1_table_50doc_01" / "line_item_comparison.csv")
    if not p3 or not p31:
        path.write_text("document_id,dataset_name,canonical_truth_count,p3_prediction_count,p3_1_prediction_count,p3_absolute_error,p3_1_absolute_error,better_version,p3_presence_match,p3_1_presence_match,ground_truth_changed_by_adapter,mismatch_reason\n", encoding="utf-8")
        return
    by_p31 = {row.get("document_id"): row for row in p31}
    fields = [
        "document_id", "dataset_name", "canonical_truth_count", "p3_prediction_count",
        "p3_1_prediction_count", "p3_absolute_error", "p3_1_absolute_error",
        "better_version", "p3_presence_match", "p3_1_presence_match",
        "ground_truth_changed_by_adapter", "mismatch_reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for old in p3:
            new = by_p31.get(old.get("document_id"))
            if not new:
                continue
            old_error = _float_or_none(old.get("absolute_count_error"))
            new_error = _float_or_none(new.get("absolute_count_error"))
            if old_error is None or new_error is None:
                better = "not_evaluated"
            elif new_error < old_error:
                better = "p3_1"
            elif old_error < new_error:
                better = "p3"
            else:
                better = "tie"
            writer.writerow({
                "document_id": old.get("document_id"),
                "dataset_name": old.get("dataset_name"),
                "canonical_truth_count": old.get("truth_canonical_count"),
                "p3_prediction_count": old.get("prediction_count"),
                "p3_1_prediction_count": new.get("prediction_count"),
                "p3_absolute_error": old.get("absolute_count_error"),
                "p3_1_absolute_error": new.get("absolute_count_error"),
                "better_version": better,
                "p3_presence_match": old.get("presence_match"),
                "p3_1_presence_match": new.get("presence_match"),
                "ground_truth_changed_by_adapter": old.get("ground_truth_changed_by_adapter") or new.get("ground_truth_changed_by_adapter"),
                "mismatch_reason": new.get("primary_mismatch_reason") or old.get("primary_mismatch_reason"),
            })


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_dataset_table_failure_analysis(path: Path, rows: list[dict[str, Any]], dataset_marker: str) -> None:
    fields = [
        "document_id",
        "truth_count",
        "prediction_count_before",
        "prediction_count_after",
        "selected_strategy",
        "header_candidate_found",
        "header_confirmed",
        "numeric_anchor_count",
        "description_anchor_count",
        "unresolved_fragment_count",
        "over_merge_detected",
        "under_merge_detected",
        "root_cause",
        "remaining_failure",
    ]
    selected = [row for row in rows if dataset_marker.lower() in str(row.get("dataset_name") or "").lower()]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in selected:
            diff = int(row.get("count_difference") or 0)
            root_cause = _table_root_cause(row)
            writer.writerow({
                "document_id": row.get("document_id"),
                "truth_count": row.get("ground_truth_row_count"),
                "prediction_count_before": "",
                "prediction_count_after": row.get("reconstructed_row_count"),
                "selected_strategy": row.get("selected_strategy"),
                "header_candidate_found": row.get("header_candidate_found"),
                "header_confirmed": row.get("header_confirmed"),
                "numeric_anchor_count": row.get("numeric_anchor_count"),
                "description_anchor_count": row.get("description_anchor_count"),
                "unresolved_fragment_count": row.get("unresolved_fragment_count"),
                "over_merge_detected": row.get("over_merge_detected"),
                "under_merge_detected": row.get("under_merge_detected"),
                "root_cause": root_cause,
                "remaining_failure": "" if diff == 0 else f"row_count_difference={diff}",
            })


def _table_root_cause(row: dict[str, Any]) -> str:
    if not _truthy(row.get("header_candidate_found")):
        return "header failure"
    if not _truthy(row.get("header_confirmed")) and not _truthy(row.get("table_region_detected")):
        return "header confirmation failure"
    if not _truthy(row.get("table_body_detected")):
        return "body detection failure"
    if not _truthy(row.get("rows_reconstructed")):
        return "row reconstruction failure"
    if int(row.get("validated_row_count") or 0) == 0 and int(row.get("reconstructed_row_count") or 0) > 0:
        return "validation rejection"
    if int(row.get("count_difference") or 0) != 0:
        return "row merge/split or ground-truth mismatch"
    return "resolved"


def _write_dataset_quality_summary(path: Path, latest: list[dict[str, Any]], field_rows: list[dict[str, Any]], party_quality: dict[str, Any] | None = None) -> None:
    datasets = sorted({str(row.get("dataset_name") or "unknown") for row in latest})
    fields = [
        "dataset_name",
        "documents",
        "documents_with_ground_truth",
        "completed",
        "valid",
        "needs_review",
        "invalid",
        "ERP_ready",
        "field_completeness",
        "normalized_field_accuracy",
        "supplier_strict_accuracy",
        "supplier_canonical_accuracy",
        "customer_strict_accuracy",
        "customer_canonical_accuracy",
        "high_confidence_invalid_count",
        "median_fresh_ocr_time",
        "p90_fresh_ocr_time",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for dataset in datasets:
            rows = [row for row in latest if str(row.get("dataset_name") or "unknown") == dataset]
            quality_rows = [row for row in field_rows if str(row.get("dataset_name") or "unknown") == dataset]
            accuracy_items = [row for row in quality_rows if row.get("normalized_match") is not None]
            fresh_durations = [float(row.get("duration_seconds") or 0) for row in rows if _truthy(row.get("fresh_ocr"))]
            supplier_rows = [row for row in quality_rows if row.get("field_name") == "supplier_name"]
            customer_rows = [row for row in quality_rows if row.get("field_name") == "customer_name"]
            writer.writerow({
                "dataset_name": dataset,
                "documents": len(rows),
                "documents_with_ground_truth": sum(1 for row in rows if _truthy(row.get("ground_truth_supported"))),
                "completed": sum(1 for row in rows if row.get("execution_status") == "completed"),
                "valid": sum(1 for row in rows if row.get("extraction_status") == "valid"),
                "needs_review": sum(1 for row in rows if row.get("extraction_status") == "needs_review"),
                "invalid": sum(1 for row in rows if row.get("extraction_status") == "invalid"),
                "ERP_ready": sum(1 for row in rows if row.get("erp_status") == "ready"),
                "field_completeness": json.dumps(_field_completeness(rows), ensure_ascii=False),
                "normalized_field_accuracy": round(sum(1 for row in accuracy_items if _truthy(row.get("normalized_match"))) / len(accuracy_items), 4) if accuracy_items else "",
                "supplier_strict_accuracy": _ratio(supplier_rows, "party_strict_match"),
                "supplier_canonical_accuracy": _ratio(supplier_rows, "party_canonical_match"),
                "customer_strict_accuracy": _ratio(customer_rows, "party_strict_match"),
                "customer_canonical_accuracy": _ratio(customer_rows, "party_canonical_match"),
                "high_confidence_invalid_count": sum(1 for row in rows if row.get("extraction_status") == "invalid" and _truthy(row.get("confidence_warning"))),
                "median_fresh_ocr_time": _percentile(fresh_durations, 50),
                "p90_fresh_ocr_time": _percentile(fresh_durations, 90),
            })


def _write_manual_review(path: Path, results: list[dict[str, Any]]) -> None:
    ranked = sorted(results, key=lambda row: (row.get("validation_status") == "valid", float(row.get("overall_confidence") or 0)))
    fields = ["document_id", "dataset_name", "relative_path", "validation_status", "supplier_name_pred", "invoice_number_pred", "amount_ttc_pred", "manual_status", "notes"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in ranked[:100]:
            writer.writerow(row)


def _record_skipped(paths: dict[str, Path], checkpoint: dict[str, Any], document: BenchmarkDocument, reason: str) -> None:
    checkpoint.setdefault("skipped_document_ids", []).append(document.document_id)
    _append_csv(paths["skipped_csv"], _document_payload(document) | {"skip_reason": reason}, ["document_id", "dataset_name", "relative_path", "filename", "skip_reason"])


def _document_payload(document: BenchmarkDocument) -> dict[str, Any]:
    return {
        "document_id": document.document_id,
        "dataset_name": document.dataset_name,
        "split": document.split,
        "relative_path": document.relative_path,
        "filename": document.file_path.name,
        "file_size": document.file_size,
        "file_hash": document.file_hash,
        "has_ground_truth": bool(document.label_path),
    }


def _critical_error_count(results_path: Path) -> int:
    return sum(1 for row in _normalize_attempts(_read_jsonl(results_path), run_id="run") if row.get("execution_status") in {"failed", "timeout"})


def _worst_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        results,
        key=lambda row: (
            row.get("execution_status") == "completed",
            row.get("extraction_status") == "valid",
            float(row.get("overall_confidence") or 0),
        ),
    )[:20]


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percentile / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _compute_file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _setup_run_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.handlers = []
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def _git_state() -> dict[str, Any]:
    def run_git(*args: str) -> str:
        try:
            return subprocess.check_output(["git", *args], cwd=Path(__file__).resolve().parents[1], text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return ""

    status = run_git("status", "--porcelain")
    return {"commit": run_git("rev-parse", "HEAD"), "dirty": bool(status)}


def _with_progress(items: list[BenchmarkDocument], label: str):
    try:
        from tqdm import tqdm  # type: ignore

        return tqdm(items, desc=label)
    except Exception:
        return items


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value).strip("_") or "run"


def _safe_display_path(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        root = Path(__file__).resolve().parents[1]
        return str(Path(path).resolve().relative_to(root))
    except Exception:
        return Path(path).name


def _html_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
