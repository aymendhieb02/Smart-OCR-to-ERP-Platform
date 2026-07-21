from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.services.ocr_engine import OCREngine
from app.services.pipeline_runner import process_document_file
from scripts.manual_benchmark_utils import (
    DEFAULT_BENCHMARK_ROOT,
    accuracy_claims_allowed_for_labels,
    compare_prediction_to_label,
    scalar_field_correct,
    line_items_from_response,
    load_manifest_documents,
    normalize_text,
    prediction_fields_from_response,
    read_csv,
    safe_json_default,
    validate_verified_label,
    validate_verified_label_quality,
    write_csv,
    write_json,
)


OUTPUT_ROOT = ROOT / "dataset" / "reports" / "hybrid_llm_benchmark"
PROMPT_VERSIONS = ("hybrid_prompt_v1", "hybrid_prompt_v2", "hybrid_prompt_v3", "hybrid_prompt_v4")
MANUAL_COLUMNS = [
    "document_id", "dataset", "field", "deterministic_value", "proposed_value",
    "accepted_by_gate", "rejection_reason", "evidence_refs", "model_confidence",
    "ground_truth", "manual_classification", "notes",
]
INVOCATION_COLUMNS = [
    "run_id", "document_id", "dataset", "prompt_version", "mode", "invoked",
    "valid_json", "document_decision", "proposal_count", "accepted_count",
    "rejected_count", "latency_seconds", "cache_source", "fallback_reason",
]
REQUIRED_GT_FIELDS = ["supplier_name", "customer_name", "invoice_number", "invoice_date", "amount_ht", "tax_amount", "amount_ttc", "line_items"]
ACCURACY_FIELDS = ["supplier_name", "customer_name", "invoice_number", "invoice_date", "amount_ht", "tax_amount", "amount_ttc"]
FIELD_ACCURACY_COLUMNS = [
    "document_id", "prompt_version", "field", "deterministic_value", "hybrid_value", "ground_truth",
    "deterministic_correct", "hybrid_correct", "improved", "regressed", "unchanged",
]
CORRECTION_REVIEW_COLUMNS = [
    "document_id", "prompt_version", "field", "operation", "deterministic_value", "proposed_value",
    "ground_truth", "accepted_by_gate", "classification", "rejection_reason", "evidence_refs",
]
ERROR_TAXONOMY_COLUMNS = [
    "document_id", "dataset", "prompt_version", "category", "error_type", "source",
    "severity", "triggered_llm", "hybrid_attempted", "hybrid_succeeded",
    "hybrid_failed", "needs_human_review", "potential_erp_impact", "evidence",
    "ground_truth_status",
]
TRIGGER_ANALYSIS_COLUMNS = [
    "trigger", "category", "invoked_count", "proposal_count", "accepted_count",
    "rejected_count", "correct_count", "wrong_count", "unsupported_count",
    "provisional_success_rate", "recommendation",
]
LATENCY_BREAKDOWN_COLUMNS = [
    "prompt_version", "document_id", "stage", "seconds", "percent_of_llm_duration",
    "instrumented", "note",
]
ROI_CATEGORY_ORDER = ["PARTY", "METADATA", "TOTALS", "TABLES", "OCR"]


def main() -> None:
    args = parse_args()
    startup_log(None, "CLI arguments parsed", vars(args))
    if args.check_env:
        startup_log(None, "Ollama check started")
        status = check_environment(model=args.model, endpoint=args.endpoint, timeout=args.timeout)
        startup_log(None, "Ollama check completed", {"ready": status.get("ready")})
        print(format_environment_status(status))
        write_json(OUTPUT_ROOT / "hybrid_environment.json", status)
        raise SystemExit(0 if status["ready"] else 2)

    run_id = args.run_id or f"hybrid_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    run_root = OUTPUT_ROOT / "runs" / safe_name(run_id)
    run_root.mkdir(parents=True, exist_ok=True)
    startup_log(run_root, "Project paths resolved", {"root": str(ROOT), "run_root": str(run_root)})
    startup_log(run_root, "Ollama check started")
    env_status = check_environment(model=args.model, endpoint=args.endpoint, timeout=args.timeout)
    startup_log(run_root, "Ollama check completed", {"ready": env_status.get("ready")})
    write_json(run_root / "hybrid_environment.json", env_status)
    if not env_status["ready"] and not args.report_only:
        raise SystemExit("Ollama hybrid benchmark refused: environment is not ready. Run --check-env for details.")

    prompt_versions = parse_prompt_versions(args)
    pre_run = validate_verified_10_prerun(args, run_root, prompt_versions, env_status)
    if pre_run.get("phase_2_8_mode"):
        write_json(run_root / "verified_10doc_pre_run_validation.json", pre_run)
        write_verified_10doc_pre_run_report(run_root, pre_run)
        if not pre_run["ready"]:
            raise SystemExit("Verified 10-document benchmark refused. See verified_10doc_pre_run_validation.json and verified_10doc_pre_run_validation.md.")
    if args.report_only:
        startup_log(run_root, "Report-only generation started", {"prompt_versions": prompt_versions})
        generate_reports(run_root, prompt_versions)
        print(f"Report-only complete: {run_root}")
        return

    startup_log(run_root, "Document selection started")
    documents = load_or_select_documents(args, run_root)
    startup_log(run_root, "Documents selected", {"document_ids": [Path(doc.filename).stem for doc in documents]})
    if not documents:
        raise SystemExit("No benchmark documents selected. Check manual benchmark manifest and verified labels.")

    checkpoint_path = run_root / "checkpoint.json"
    checkpoint = load_checkpoint(checkpoint_path) if args.resume else {"processed": []}
    processed = set(checkpoint.get("processed", []))
    startup_log(run_root, "OCR engine initialization started")
    engine = OCREngine()
    startup_log(run_root, "OCR engine initialization completed")
    rows = read_csv(run_root / "hybrid_manual_review.csv") if args.resume else []
    invocation_rows = read_csv(run_root / "hybrid_latency.csv") if args.resume else []

    for document in documents:
        for prompt_version in prompt_versions:
            key = processed_key(document, prompt_version, args.model)
            if key in processed and not args.retry_failures:
                startup_log(run_root, "Combination skipped by checkpoint", {"key": key})
                continue
            if args.retry_failures and key in processed and not was_failed(run_root, prompt_version, document.filename):
                startup_log(run_root, "Combination skipped because latest artifact is successful", {"key": key})
                continue
            startup_log(run_root, "Prompt run started", {"prompt_version": prompt_version, "document": document.filename})
            started = datetime.now(timezone.utc)
            artifact = run_document(document, engine, args, prompt_version, run_root)
            finished = datetime.now(timezone.utc)
            artifact["request_timestamp"] = started.isoformat()
            artifact["response_timestamp"] = finished.isoformat()
            artifact["_run_root"] = str(run_root)
            write_json(artifact_path(run_root, prompt_version, document.filename), artifact)
            append_jsonl(run_root / "calibration_invocations.jsonl", artifact)
            rows.extend(manual_rows_from_artifact(artifact))
            invocation_rows.append(invocation_row_from_artifact(run_id, artifact))
            processed.add(key)
            checkpoint["processed"] = sorted(processed)
            write_json(checkpoint_path, checkpoint)
            write_csv(run_root / "hybrid_manual_review.csv", rows, MANUAL_COLUMNS)
            write_csv(run_root / "hybrid_latency.csv", invocation_rows, INVOCATION_COLUMNS)
            startup_log(run_root, "Artifact saved", {"key": key, "parser_status": artifact.get("parser_status"), "status": artifact.get("status")})

    write_json(run_root / "checkpoint.json", {"processed": sorted(processed), "status": "completed"})
    generate_reports(run_root, prompt_versions)
    print(f"Hybrid benchmark complete: {run_root}")
    print(f"Report: {run_root / 'hybrid_benchmark_report.md'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real Ollama hybrid LLM benchmark without changing deterministic extraction.")
    parser.add_argument("--check-env", action="store_true", help="Check Ollama/model/cache environment and exit.")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT), help="Manual ground-truth benchmark root.")
    parser.add_argument("--mode", choices=["advisory", "validated-apply"], default="advisory")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--max-documents", type=int, default=10)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--baseline-run-id", default=None)
    parser.add_argument("--prompt-version", choices=["hybrid_prompt_v1", "hybrid_prompt_v2", "both"], default="hybrid_prompt_v1")
    parser.add_argument("--prompt-versions", default=None, help="Comma-separated prompt versions, e.g. hybrid_prompt_v1,hybrid_prompt_v3.")
    parser.add_argument("--model", default=settings.llm_resolver_model)
    parser.add_argument("--endpoint", default=settings.llm_resolver_url)
    parser.add_argument("--timeout", type=float, default=max(float(settings.llm_resolver_timeout_seconds or 20.0), 60.0))
    return parser.parse_args()


def parse_prompt_versions(args: argparse.Namespace) -> tuple[str, ...]:
    raw = args.prompt_versions
    if raw:
        versions = tuple(item.strip() for item in str(raw).split(",") if item.strip())
    elif args.prompt_version == "both":
        versions = ("hybrid_prompt_v1", "hybrid_prompt_v2")
    else:
        versions = (args.prompt_version,)
    unknown = [version for version in versions if version not in PROMPT_VERSIONS]
    if unknown:
        raise SystemExit(f"Unsupported prompt version(s): {', '.join(unknown)}")
    return versions


def check_environment(*, model: str, endpoint: str, timeout: float) -> dict[str, Any]:
    status: dict[str, Any] = {
        "python_executable": sys.executable,
        "ollama_executable": shutil.which("ollama"),
        "endpoint": endpoint,
        "model": model,
        "configured_timeout_seconds": timeout,
        "resolver_mode": settings.llm_resolver_mode,
        "auto_apply": settings.llm_resolver_auto_apply_safe_corrections,
        "ollama_reachable": False,
        "model_installed": False,
        "model_metadata": None,
        "warmup_latency_seconds": None,
        "test_response_valid_json": False,
        "cache_directory_writable": False,
        "ready": False,
        "errors": [],
    }
    cache_dir = Path(settings.llm_resolver_cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        status["cache_directory_writable"] = True
    except OSError as exc:
        status["errors"].append(f"cache not writable: {exc}")
    tags_url = endpoint.replace("/api/generate", "/api/tags")
    try:
        tags = http_json(tags_url, {}, timeout=timeout, method="GET")
        status["ollama_reachable"] = True
        models = tags.get("models") or []
        for item in models:
            if item.get("name") == model or item.get("model") == model:
                status["model_installed"] = True
                status["model_metadata"] = item
                break
    except Exception as exc:
        status["errors"].append(f"ollama tags failed: {exc}")
    if status["ollama_reachable"] and status["model_installed"]:
        started = time.perf_counter()
        try:
            body = http_json(endpoint, {
                "model": model,
                "prompt": 'Return exactly {"ok": true} and nothing else.',
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            }, timeout=timeout, method="POST")
            status["warmup_latency_seconds"] = round(time.perf_counter() - started, 4)
            parsed = json.loads(str(body.get("response") or "{}"))
            status["test_response_valid_json"] = parsed.get("ok") is True
        except Exception as exc:
            status["errors"].append(f"warmup failed: {exc}")
    status["ready"] = bool(status["ollama_reachable"] and status["model_installed"] and status["test_response_valid_json"] and status["cache_directory_writable"])
    return status


def http_json(url: str, payload: dict[str, Any], *, timeout: float, method: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=None if method == "GET" else json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def format_environment_status(status: dict[str, Any]) -> str:
    lines = [
        "Hybrid LLM environment check",
        f"- Ollama executable: {'yes' if status.get('ollama_executable') else 'no'}",
        f"- Ollama reachable: {'yes' if status.get('ollama_reachable') else 'no'}",
        f"- Model installed: {'yes' if status.get('model_installed') else 'no'}",
        f"- Model name: {status.get('model')}",
        f"- Warm-up latency: {status.get('warmup_latency_seconds')}",
        f"- Test response valid JSON: {'yes' if status.get('test_response_valid_json') else 'no'}",
        f"- Configured timeout: {status.get('configured_timeout_seconds')}",
        f"- Resolver mode: {status.get('resolver_mode')}",
        f"- Auto-apply: {status.get('auto_apply')}",
        f"- Cache writable: {'yes' if status.get('cache_directory_writable') else 'no'}",
        f"- Ready: {'yes' if status.get('ready') else 'no'}",
    ]
    if status.get("errors"):
        lines.append("- Errors: " + "; ".join(status["errors"]))
    return "\n".join(lines)


def select_difficult_documents(args: argparse.Namespace):
    documents = load_manifest_documents(Path(args.benchmark_root).resolve())
    if args.dataset:
        documents = [doc for doc in documents if doc.dataset == args.dataset]
    scored = []
    for doc in documents:
        label = load_label_or_empty(doc.label_path)
        score = 0
        if label.get("line_items"):
            score += 2
        if not label.get("supplier_name") or not label.get("customer_name"):
            score += 1
        if doc.dataset:
            score += 1
        scored.append((score, doc.filename, doc))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[: max(1, args.max_documents)]]


def is_verified_10_run(args: argparse.Namespace) -> bool:
    run_id = str(getattr(args, "run_id", "") or "").lower()
    return "verified_10" in run_id or "verified-10" in run_id


def selected_verified_10_documents(benchmark_root: Path):
    selected_path = benchmark_root / "selected_verified_10_documents.json"
    if not selected_path.exists():
        return []
    payload = json.loads(selected_path.read_text(encoding="utf-8"))
    all_documents = load_manifest_documents(benchmark_root)
    by_filename = {document.filename: document for document in all_documents}
    return [by_filename[item["filename"]] for item in payload.get("documents", []) if item.get("filename") in by_filename]


def load_or_select_documents(args: argparse.Namespace, run_root: Path):
    selected_path = run_root / "selected_documents.json"
    benchmark_root = Path(args.benchmark_root).resolve()
    all_documents = load_manifest_documents(benchmark_root)
    by_filename = {document.filename: document for document in all_documents}
    if selected_path.exists() and args.resume:
        payload = json.loads(selected_path.read_text(encoding="utf-8"))
        documents = [by_filename[item["filename"]] for item in payload.get("documents", []) if item.get("filename") in by_filename]
        if documents:
            return documents
    documents = selected_verified_10_documents(benchmark_root) if is_verified_10_run(args) else select_difficult_documents(args)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selection_policy": "selected_verified_10_documents" if is_verified_10_run(args) else "fixed_phase_2_3_2_difficult_documents",
        "documents": [
            {
                "document_id": Path(document.filename).stem,
                "filename": document.filename,
                "dataset": document.dataset,
                "label_path": str(document.label_path),
                "image_path": str(document.image_path),
            }
            for document in documents
        ],
    }
    write_json(selected_path, payload)
    return documents


def validate_verified_10_prerun(args: argparse.Namespace, run_root: Path, prompt_versions: tuple[str, ...], env_status: dict[str, Any]) -> dict[str, Any]:
    phase_mode = is_verified_10_run(args)
    if not phase_mode:
        return {"phase_2_8_mode": False, "ready": True}
    benchmark_root = Path(args.benchmark_root).resolve()
    documents = selected_verified_10_documents(benchmark_root)
    labels = [load_label_or_empty(document.label_path) for document in documents]
    quality_rows = [validate_verified_label_quality(label, label_path=Path("labels") / document.label_path.name) for document, label in zip(documents, labels)]
    expected_ids = [Path(document.filename).stem for document in documents]
    checks = {
        "ollama_ready": bool(env_status.get("ready")),
        "model_installed": bool(env_status.get("model_installed")) and args.model == "qwen2.5-coder:7b",
        "cache_writable": bool(env_status.get("cache_directory_writable")),
        "exactly_10_documents": len(documents) == 10,
        "all_10_labels_verified": len(documents) == 10 and accuracy_claims_allowed_for_labels(labels),
        "same_selected_document_ids": len(expected_ids) == 10 and len(set(expected_ids)) == 10,
        "advisory_mode_active": args.mode == "advisory",
        "auto_apply_disabled": settings.llm_resolver_auto_apply_safe_corrections is False,
        "prompt_version_v3": prompt_versions == ("hybrid_prompt_v3",),
    }
    missing_or_incomplete = [
        {
            "document_id": Path(document.filename).stem,
            "filename": document.filename,
            "verification_status": quality.get("verification_status"),
            "eligible_for_accuracy": quality.get("eligible_for_accuracy"),
            "missing_fields": quality.get("missing_fields"),
            "errors": quality.get("errors"),
            "warnings": quality.get("warnings"),
        }
        for document, quality in zip(documents, quality_rows)
        if not quality.get("eligible_for_accuracy")
    ]
    return {
        "phase_2_8_mode": True,
        "run_id": args.run_id,
        "ready": all(checks.values()),
        "checks": checks,
        "selected_document_ids": expected_ids,
        "documents_total": len(documents),
        "documents_complete": len(documents) - len(missing_or_incomplete),
        "incomplete_documents": missing_or_incomplete,
        "refusal_reason": None if all(checks.values()) else "Verified 10-document benchmark requires exactly 10 fully verified labels, advisory mode, hybrid_prompt_v3, Ollama readiness, writable cache, and auto-apply disabled.",
        "reports_that_were_not_generated": [
            "verified_10doc_accuracy_metrics.json",
            "verified_10doc_field_accuracy.csv",
            "verified_10doc_correction_review.csv",
            "verified_10doc_latency.csv",
            "verified_10doc_error_analysis.md",
            "verified_10doc_hybrid_report.md",
            "verified_10doc_deployment_decision.md",
        ] if not all(checks.values()) else [],
    }


def write_verified_10doc_pre_run_report(run_root: Path, pre_run: dict[str, Any]) -> None:
    lines = [
        "# Verified 10-Document Pre-Run Validation",
        "",
        f"- Run ID: {pre_run.get('run_id')}",
        f"- Ready: {pre_run.get('ready')}",
        f"- Documents total: {pre_run.get('documents_total')}",
        f"- Complete documents: {pre_run.get('documents_complete')}",
        "",
        "## Checks",
        "",
        "| Check | Passed |",
        "|---|---:|",
    ]
    for key, value in (pre_run.get("checks") or {}).items():
        lines.append(f"| {key} | {value} |")
    if pre_run.get("refusal_reason"):
        lines.extend(["", f"**Refused:** {pre_run['refusal_reason']}"])
    lines.extend(["", "## Incomplete Documents", "", "| Document | Status | Missing Fields | Errors | Warnings |", "|---|---|---|---|---|"])
    for row in pre_run.get("incomplete_documents") or []:
        lines.append(
            f"| {row['document_id']} | {row.get('verification_status')} | {', '.join(row.get('missing_fields') or [])} | {'; '.join(row.get('errors') or [])} | {'; '.join(row.get('warnings') or [])} |"
        )
    if pre_run.get("reports_that_were_not_generated"):
        lines.extend(["", "## Reports Not Generated", ""])
        lines.extend(f"- `{name}`" for name in pre_run["reports_that_were_not_generated"])
    (run_root / "verified_10doc_pre_run_validation.md").write_text("\n".join(lines), encoding="utf-8")


def run_document(document, engine: OCREngine, args: argparse.Namespace, prompt_version: str, run_root: Path) -> dict[str, Any]:
    previous = snapshot_settings()
    settings.enable_llm_resolver = False
    try:
        startup_log(run_root, "Document started", {"prompt_version": prompt_version, "document": document.filename})
        label = load_label_or_empty(document.label_path)
        deterministic_started = time.perf_counter()
        deterministic = process_document_file(document.image_path, original_filename=document.filename, ocr_engine=engine, include_preview=False)
        deterministic_seconds = round(time.perf_counter() - deterministic_started, 4)
        configure_hybrid(args, prompt_version, run_root)
        startup_log(run_root, "Request sent", {"prompt_version": prompt_version, "document": document.filename, "timeout": args.timeout})
        hybrid_started = time.perf_counter()
        hybrid = process_document_file(document.image_path, original_filename=document.filename, ocr_engine=engine, include_preview=False)
        hybrid_seconds = round(time.perf_counter() - hybrid_started, 4)
        startup_log(run_root, "Response received", {"prompt_version": prompt_version, "document": document.filename})
    except Exception as exc:
        startup_log(run_root, "Document failed", {"prompt_version": prompt_version, "document": document.filename, "error": str(exc)})
        restore_settings(previous)
        return {
            "document_id": Path(document.filename).stem,
            "dataset": document.dataset,
            "filename": document.filename,
            "prompt_version": prompt_version,
            "status": "error",
            "error": str(exc),
        }
    finally:
        restore_settings(previous)
    hybrid_debug = (hybrid.extraction_debug or {}).get("hybrid_llm") or {}
    prompt_stats = prompt_stats_from_debug(hybrid_debug)
    return {
        "document_id": Path(document.filename).stem,
        "dataset": document.dataset,
        "filename": document.filename,
        "label": label,
        "status": "success",
        "prompt_version": prompt_version,
        "model": args.model,
        "mode": args.mode,
        "deterministic_processing_seconds": deterministic_seconds,
        "hybrid_processing_seconds": hybrid_seconds,
        "trigger_reasons": hybrid_debug.get("trigger_reasons") or [],
        "evidence_fingerprint": hybrid_debug.get("fingerprint"),
        "prompt_payload_fingerprint": hybrid_debug.get("fingerprint"),
        "raw_model_response": ((hybrid_debug.get("resolution") or {}).get("raw_response")),
        "parsed_response": hybrid_debug.get("resolution"),
        "parser_status": "parsed" if hybrid_debug.get("resolution") else ("skipped" if not hybrid_debug.get("invoked") else "failed"),
        "proposals": hybrid_debug.get("proposals") or [],
        "accepted_proposals": hybrid_debug.get("accepted_corrections") or [],
        "rejected_proposals": hybrid_debug.get("rejected_corrections") or [],
        "deterministic_result": compact_result(deterministic),
        "hybrid_candidate": hybrid_debug.get("hybrid_candidate_result"),
        "final_selected_result": compact_result(hybrid),
        "hybrid_debug": hybrid_debug,
        "prompt_stats": prompt_stats,
        "deterministic_comparison": compare_response(deterministic, label),
        "hybrid_comparison": compare_response(hybrid, label),
    }


def configure_hybrid(args: argparse.Namespace, prompt_version: str, run_root: Path) -> None:
    settings.enable_llm_resolver = True
    settings.llm_resolver_mode = "validated_apply" if args.mode == "validated-apply" else "advisory"
    settings.llm_resolver_auto_apply_safe_corrections = False
    settings.llm_resolver_prompt_version = prompt_version
    settings.llm_resolver_model = args.model
    settings.llm_resolver_url = args.endpoint
    settings.llm_resolver_timeout_seconds = args.timeout
    settings.llm_resolver_cache_dir = run_root / "llm_cache"


def compact_result(response: Any) -> dict[str, Any]:
    fields = prediction_fields_from_response(response)
    return {
        "fields": fields,
        "line_items_count": len(line_items_from_response(response)),
        "validation_status": response.validation.status,
        "erp_readiness": response.erp_readiness,
        "overall_confidence": (response.confidence_breakdown or {}).get("overall_confidence"),
    }


def compare_response(response: Any, label: dict[str, Any]) -> dict[str, Any]:
    return compare_prediction_to_label(prediction_fields_from_response(response), line_items_from_response(response), label)


def load_label_or_empty(label_path: Path) -> dict[str, Any]:
    try:
        return validate_verified_label(label_path)
    except Exception:
        try:
            return json.loads(label_path.read_text(encoding="utf-8"))
        except Exception:
            return {}


def manual_rows_from_artifact(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    if artifact.get("status") != "success":
        return [{
            "document_id": artifact.get("document_id"),
            "dataset": artifact.get("dataset"),
            "field": "document",
            "deterministic_value": "",
            "proposed_value": "",
            "accepted_by_gate": False,
            "rejection_reason": artifact.get("error"),
            "evidence_refs": "",
            "model_confidence": "",
            "ground_truth": "",
            "manual_classification": "benchmark_error",
            "notes": "",
        }]
    rows = []
    truth = artifact.get("label") or {}
    for item in artifact.get("accepted_proposals", []) + artifact.get("rejected_proposals", []):
        proposal = item.get("proposal") or {}
        field = proposal.get("field")
        rows.append({
            "document_id": artifact.get("document_id"),
            "dataset": artifact.get("dataset"),
            "field": field,
            "deterministic_value": proposal.get("old_value") or (artifact.get("deterministic_result", {}).get("fields", {}) or {}).get(field or ""),
            "proposed_value": proposal.get("proposed_value"),
            "accepted_by_gate": item in artifact.get("accepted_proposals", []),
            "rejection_reason": "" if item in artifact.get("accepted_proposals", []) else item.get("reason"),
            "evidence_refs": ";".join(proposal.get("evidence_refs") or []),
            "model_confidence": proposal.get("confidence"),
            "ground_truth": truth.get(field or "") or truth.get({"supplier": "supplier_name", "customer": "customer_name", "total": "amount_ttc"}.get(field or "", "")),
            "manual_classification": "",
            "notes": "",
        })
    if not rows:
        rows.append({
            "document_id": artifact.get("document_id"),
            "dataset": artifact.get("dataset"),
            "field": "document",
            "deterministic_value": "",
            "proposed_value": "",
            "accepted_by_gate": False,
            "rejection_reason": artifact.get("hybrid_debug", {}).get("fallback_reason") or artifact.get("parser_status"),
            "evidence_refs": "",
            "model_confidence": (artifact.get("parsed_response") or {}).get("overall_confidence"),
            "ground_truth": "",
            "manual_classification": "insufficient_evidence" if (artifact.get("parsed_response") or {}).get("document_decision") == "insufficient_evidence" else "",
            "notes": "",
        })
    return rows


def invocation_row_from_artifact(run_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
    debug = artifact.get("hybrid_debug") or {}
    return {
        "run_id": run_id,
        "document_id": artifact.get("document_id"),
        "dataset": artifact.get("dataset"),
        "prompt_version": artifact.get("prompt_version"),
        "mode": artifact.get("mode"),
        "invoked": debug.get("invoked", False),
        "valid_json": artifact.get("parser_status") == "parsed",
        "document_decision": debug.get("document_decision"),
        "proposal_count": len(debug.get("proposals") or []),
        "accepted_count": len(debug.get("accepted_corrections") or []),
        "rejected_count": len(debug.get("rejected_corrections") or []),
        "latency_seconds": (debug.get("metrics") or {}).get("duration_seconds"),
        "cache_source": debug.get("cache_source"),
        "fallback_reason": debug.get("fallback_reason"),
    }


def generate_reports(run_root: Path, prompt_versions: tuple[str, ...]) -> None:
    artifacts = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_root / "artifacts").rglob("*.json"))] if (run_root / "artifacts").exists() else []
    metrics = calculate_metrics(artifacts)
    write_json(run_root / "hybrid_metrics.json", metrics)
    write_json(run_root / "prompt_version_metrics.json", calculate_prompt_version_metrics(artifacts))
    quality = calculate_ground_truth_quality(artifacts)
    write_json(run_root / "ground_truth_quality.json", quality)
    write_ground_truth_quality_report(run_root, quality)
    accuracy_rows = build_hybrid_field_accuracy_rows(artifacts)
    write_csv(run_root / "hybrid_field_accuracy.csv", accuracy_rows, FIELD_ACCURACY_COLUMNS)
    correction_rows = build_hybrid_correction_review_rows(artifacts)
    write_csv(run_root / "hybrid_correction_review.csv", correction_rows, CORRECTION_REVIEW_COLUMNS)
    accuracy_metrics = calculate_hybrid_accuracy_metrics(accuracy_rows, correction_rows, artifacts)
    write_json(run_root / "hybrid_accuracy_metrics.json", accuracy_metrics)
    write_hybrid_accuracy_report(run_root, accuracy_metrics, quality)
    taxonomy_rows = build_hybrid_error_taxonomy_rows(artifacts, correction_rows)
    write_csv(run_root / "hybrid_error_taxonomy.csv", taxonomy_rows, ERROR_TAXONOMY_COLUMNS)
    trigger_rows = build_hybrid_trigger_analysis_rows(artifacts, correction_rows)
    write_csv(run_root / "hybrid_trigger_analysis.csv", trigger_rows, TRIGGER_ANALYSIS_COLUMNS)
    latency_rows = build_hybrid_latency_breakdown_rows(artifacts)
    write_csv(run_root / "hybrid_latency_breakdown.csv", latency_rows, LATENCY_BREAKDOWN_COLUMNS)
    roi_metrics = calculate_hybrid_roi_metrics(artifacts, taxonomy_rows, trigger_rows, latency_rows, quality)
    write_json(run_root / "hybrid_roi_metrics.json", roi_metrics)
    write_hybrid_roi_report(run_root, roi_metrics, quality)
    write_hybrid_deployment_recommendation(run_root, roi_metrics, quality)
    write_hybrid_roi_charts(run_root, taxonomy_rows, trigger_rows, roi_metrics)
    write_comparison_csvs(run_root, artifacts)
    write_report(run_root, metrics)


def calculate_metrics(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [item for item in artifacts if item.get("status") == "success"]
    invoked = [item for item in successes if (item.get("hybrid_debug") or {}).get("invoked")]
    proposals = [proposal for item in successes for proposal in item.get("proposals", [])]
    accepted = [proposal for item in successes for proposal in item.get("accepted_proposals", [])]
    rejected = [proposal for item in successes for proposal in item.get("rejected_proposals", [])]
    latencies = [float((item.get("hybrid_debug") or {}).get("metrics", {}).get("duration_seconds") or 0) for item in invoked]
    manual = read_csv(Path(successes[0].get("_run_root", "")) / "hybrid_manual_review.csv") if successes and successes[0].get("_run_root") else []
    correct_accepted = [row for row in manual if row.get("accepted_by_gate") in {"True", "true", True} and row.get("manual_classification") == "correct improvement"]
    wrong_accepted = [row for row in manual if row.get("accepted_by_gate") in {"True", "true", True} and row.get("manual_classification") in {"wrong value", "unsupported hallucination"}]
    return {
        "documents_total": len(artifacts),
        "documents_success": len(successes),
        "documents_error": len(artifacts) - len(successes),
        "routed_documents": len(invoked),
        "invocation_rate": ratio(len(invoked), len(successes)),
        "bypass_rate": ratio(len(successes) - len(invoked), len(successes)),
        "valid_json_rate": ratio(sum(item.get("parser_status") == "parsed" for item in invoked), len(invoked)),
        "malformed_json_rate": ratio(sum(bool((item.get("hybrid_debug") or {}).get("error")) for item in invoked), len(invoked)),
        "proposal_count": len(proposals),
        "accepted_by_gate": len(accepted),
        "rejected_by_gate": len(rejected),
        "insufficient_evidence_decisions": sum((item.get("parsed_response") or {}).get("document_decision") == "insufficient_evidence" for item in invoked),
        "accepted_correction_precision": ratio(len(correct_accepted), len(correct_accepted) + len(wrong_accepted)),
        "false_acceptance_rate": ratio(len(wrong_accepted), len(correct_accepted) + len(wrong_accepted)),
        "ground_truth_accuracy_available": bool(correct_accepted or wrong_accepted),
        "latency": {
            "average": round(sum(latencies) / len(latencies), 4) if latencies else None,
            "median": round(median(latencies), 4) if latencies else None,
            "p90": percentile(latencies, 0.90),
            "max": max(latencies) if latencies else None,
        },
        "advisory_recommendation": "keep advisory mode until manual classifications prove accepted precision >= 90% and zero critical hallucinations",
    }


def calculate_ground_truth_quality(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    by_doc: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        if artifact.get("document_id") not in by_doc:
            by_doc[artifact.get("document_id", "")] = artifact
    documents = []
    total_required = 0
    present_required = 0
    for document_id, artifact in sorted(by_doc.items()):
        label = artifact.get("label") or {}
        missing = missing_ground_truth_fields(label)
        present = [field for field in REQUIRED_GT_FIELDS if field not in missing]
        total_required += len(REQUIRED_GT_FIELDS)
        present_required += len(present)
        documents.append({
            "document_id": document_id,
            "dataset": artifact.get("dataset"),
            "verified_by_human": label.get("verified_by_human") is True,
            "verification_status": label.get("verification_status") or ("verified" if label.get("verified_by_human") is True else "draft"),
            "present_fields": present,
            "missing_fields": missing,
            "missing_line_items": "line_items" in missing,
            "completeness": ratio(len(present), len(REQUIRED_GT_FIELDS)),
        })
    complete_docs = [doc for doc in documents if doc["verified_by_human"] and not doc["missing_fields"]]
    return {
        "documents": documents,
        "document_count": len(documents),
        "complete_verified_document_count": len(complete_docs),
        "all_documents_verified_complete": len(complete_docs) == len(documents) and bool(documents),
        "verification_completeness": ratio(present_required, total_required),
        "blocking_reason": None if len(complete_docs) == len(documents) and documents else "Verified complete labels are required before claiming hybrid accuracy.",
    }


def missing_ground_truth_fields(label: dict[str, Any]) -> list[str]:
    missing = []
    for field in REQUIRED_GT_FIELDS:
        value = label.get(field)
        if field == "line_items":
            if not meaningful_truth_line_items(label):
                missing.append(field)
        elif value in (None, "", []):
            missing.append(field)
    if label.get("verified_by_human") is not True:
        missing.append("verified_by_human")
    if label.get("verification_status") != "verified":
        missing.append("verification_status")
    return missing


def meaningful_truth_line_items(label: dict[str, Any]) -> list[dict[str, Any]]:
    rows = label.get("line_items") or []
    return [
        row for row in rows
        if isinstance(row, dict)
        and any(row.get(k) not in (None, "", []) for k in ("description", "quantity", "unit_price", "line_total_ht", "line_total_ttc", "total"))
    ]


def label_is_verified_complete(label: dict[str, Any]) -> bool:
    return validate_verified_label_quality(label)["eligible_for_accuracy"]


def write_ground_truth_quality_report(run_root: Path, quality: dict[str, Any]) -> None:
    lines = [
        "# Ground Truth Quality Report",
        "",
        f"- Documents: {quality['document_count']}",
        f"- Complete verified documents: {quality['complete_verified_document_count']}",
        f"- Verification completeness: {quality['verification_completeness']}",
        f"- Accuracy benchmark allowed: {quality['all_documents_verified_complete']}",
    ]
    if quality.get("blocking_reason"):
        lines.extend(["", f"**Blocked:** {quality['blocking_reason']}"])
    lines.extend(["", "| Document | Verified | Completeness | Missing Fields |", "|---|---:|---:|---|"])
    for doc in quality["documents"]:
        lines.append(f"| {doc['document_id']} | {doc['verified_by_human']} | {doc['completeness']} | {', '.join(doc['missing_fields'])} |")
    (run_root / "ground_truth_quality_report.md").write_text("\n".join(lines), encoding="utf-8")


def build_hybrid_field_accuracy_rows(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.get("status") != "success":
            continue
        label = artifact.get("label") or {}
        det_fields = (artifact.get("deterministic_result") or {}).get("fields") or {}
        hybrid_fields = (artifact.get("final_selected_result") or {}).get("fields") or {}
        for field in ACCURACY_FIELDS:
            truth = label.get(field)
            det_correct, _ = scalar_field_correct(field, det_fields.get(field), truth)
            hybrid_correct, _ = scalar_field_correct(field, hybrid_fields.get(field), truth)
            rows.append({
                "document_id": artifact.get("document_id"),
                "prompt_version": artifact.get("prompt_version"),
                "field": field,
                "deterministic_value": det_fields.get(field),
                "hybrid_value": hybrid_fields.get(field),
                "ground_truth": truth,
                "deterministic_correct": det_correct,
                "hybrid_correct": hybrid_correct,
                "improved": det_correct is False and hybrid_correct is True,
                "regressed": det_correct is True and hybrid_correct is False,
                "unchanged": det_correct == hybrid_correct,
            })
        det_count = ((artifact.get("deterministic_result") or {}).get("line_items_count"))
        hybrid_count = ((artifact.get("final_selected_result") or {}).get("line_items_count"))
        truth_count = len(meaningful_truth_line_items(label))
        applicable = truth_count > 0
        det_correct = (det_count == truth_count) if applicable else None
        hybrid_correct = (hybrid_count == truth_count) if applicable else None
        rows.append({
            "document_id": artifact.get("document_id"),
            "prompt_version": artifact.get("prompt_version"),
            "field": "line_item_count",
            "deterministic_value": det_count,
            "hybrid_value": hybrid_count,
            "ground_truth": truth_count if applicable else None,
            "deterministic_correct": det_correct,
            "hybrid_correct": hybrid_correct,
            "improved": det_correct is False and hybrid_correct is True,
            "regressed": det_correct is True and hybrid_correct is False,
            "unchanged": det_correct == hybrid_correct,
        })
    return rows


def build_hybrid_correction_review_rows(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        label = artifact.get("label") or {}
        deterministic_fields = (artifact.get("deterministic_result") or {}).get("fields") or {}
        for item in artifact.get("accepted_proposals", []) + artifact.get("rejected_proposals", []):
            proposal = item.get("proposal") or {}
            field = proposal.get("field")
            truth_field = {"supplier": "supplier_name", "customer": "customer_name", "total": "amount_ttc"}.get(field, field)
            truth = label.get(truth_field)
            accepted = item in artifact.get("accepted_proposals", [])
            classification = classify_correction(deterministic_fields.get(truth_field), proposal.get("proposed_value"), truth, accepted)
            rows.append({
                "document_id": artifact.get("document_id"),
                "prompt_version": artifact.get("prompt_version"),
                "field": field,
                "operation": proposal.get("operation"),
                "deterministic_value": deterministic_fields.get(truth_field),
                "proposed_value": proposal.get("proposed_value"),
                "ground_truth": truth,
                "accepted_by_gate": accepted,
                "classification": classification,
                "rejection_reason": "" if accepted else item.get("reason"),
                "evidence_refs": ";".join(proposal.get("evidence_refs") or []),
            })
    return rows


def classify_correction(deterministic_value: Any, proposed_value: Any, truth: Any, accepted: bool) -> str:
    if truth in (None, "", []):
        return "Unsupported correction" if accepted else "Unsupported correction"
    det_matches = values_match(deterministic_value, truth)
    prop_matches = values_match(proposed_value, truth)
    if prop_matches and not det_matches:
        return "Correct improvement"
    if prop_matches and det_matches:
        return "Correct but unnecessary"
    if not prop_matches and accepted:
        return "Wrong correction"
    if not accepted and proposed_value not in (None, "") and not prop_matches:
        return "Hallucination" if not evidence_like_value(proposed_value, truth) else "Unsupported correction"
    return "No change"


def values_match(value: Any, truth: Any) -> bool:
    if truth in (None, "", []):
        return False
    if isinstance(truth, (int, float)):
        try:
            return abs(float(value) - float(truth)) <= max(0.02, abs(float(truth)) * 0.005)
        except Exception:
            return False
    return normalize_text(value) == normalize_text(truth)


def evidence_like_value(value: Any, truth: Any) -> bool:
    return bool(normalize_text(value)) and normalize_text(value) in normalize_text(truth)


def calculate_hybrid_accuracy_metrics(rows: list[dict[str, Any]], correction_rows: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    field_metrics: dict[str, Any] = {}
    for field in sorted({row["field"] for row in rows}):
        applicable = [row for row in rows if row["field"] == field and row["deterministic_correct"] is not None and row["hybrid_correct"] is not None]
        field_metrics[field] = {
            "before_accuracy": ratio(sum(row["deterministic_correct"] is True for row in applicable), len(applicable)),
            "after_accuracy": ratio(sum(row["hybrid_correct"] is True for row in applicable), len(applicable)),
            "improved_count": sum(row["improved"] is True for row in applicable),
            "regressed_count": sum(row["regressed"] is True for row in applicable),
            "applicable_count": len(applicable),
        }
    accepted = [row for row in correction_rows if row.get("accepted_by_gate") in {True, "True", "true"}]
    correct_accepted = [row for row in accepted if row.get("classification") in {"Correct improvement", "Correct but unnecessary"}]
    wrong_accepted = [row for row in accepted if row.get("classification") == "Wrong correction"]
    hallucinations = [row for row in correction_rows if row.get("classification") == "Hallucination"]
    accuracy_claim_allowed = bool(artifacts) and all(label_is_verified_complete(item.get("label") or {}) for item in artifacts if item.get("status") == "success")
    return {
        "field_metrics": field_metrics,
        "overall_field_accuracy_before": ratio(sum(row["deterministic_correct"] is True for row in rows if row["deterministic_correct"] is not None), sum(row["deterministic_correct"] is not None for row in rows)),
        "overall_field_accuracy_after": ratio(sum(row["hybrid_correct"] is True for row in rows if row["hybrid_correct"] is not None), sum(row["hybrid_correct"] is not None for row in rows)),
        "erp_readiness_improvement_count": sum(erp_ready_improved(item) for item in artifacts),
        "accepted_correction_precision": ratio(len(correct_accepted), len(accepted)) if accuracy_claim_allowed else None,
        "accepted_correction_recall": ratio(sum(row.get("classification") == "Correct improvement" for row in accepted), sum(row.get("classification") == "Correct improvement" for row in correction_rows)) if accuracy_claim_allowed else None,
        "false_acceptance_rate": ratio(len(wrong_accepted), len(accepted)) if accuracy_claim_allowed else None,
        "hallucination_rate": ratio(len(hallucinations), len(correction_rows)) if accuracy_claim_allowed else None,
        "accepted_correction_count": len(accepted),
        "hallucination_count": len(hallucinations),
        "accuracy_claim_allowed": accuracy_claim_allowed,
    }


def erp_ready_improved(artifact: dict[str, Any]) -> bool:
    det_ready = ((artifact.get("deterministic_result") or {}).get("erp_readiness") or {}).get("ready")
    final_ready = ((artifact.get("final_selected_result") or {}).get("erp_readiness") or {}).get("ready")
    return det_ready is False and final_ready is True


def write_hybrid_accuracy_report(run_root: Path, metrics: dict[str, Any], quality: dict[str, Any]) -> None:
    allowed = quality.get("all_documents_verified_complete")
    recommendation = "A) Keep advisory mode"
    lines = [
        "# Hybrid Accuracy Report",
        "",
        f"- Verified complete labels: {allowed}",
        f"- Overall field accuracy before: {metrics.get('overall_field_accuracy_before')}",
        f"- Overall field accuracy after: {metrics.get('overall_field_accuracy_after')}",
        f"- Accepted correction precision: {metrics.get('accepted_correction_precision')}",
        f"- False acceptance rate: {metrics.get('false_acceptance_rate')}",
        f"- Hallucination rate: {metrics.get('hallucination_rate')}",
        "",
        "## Decision",
        "",
    ]
    if not allowed:
        lines.append("Did the hybrid layer improve extraction? **Not proven.** Verified complete ground truth is missing.")
    else:
        before = metrics.get("overall_field_accuracy_before") or 0
        after = metrics.get("overall_field_accuracy_after") or 0
        lines.append(f"Did the hybrid layer improve extraction? **{'Yes' if after > before else 'No'}**.")
    lines.extend([
        "",
        "## Field Metrics",
        "",
        "| Field | Before | After | Improved | Regressed | Applicable |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for field, row in metrics.get("field_metrics", {}).items():
        lines.append(f"| {field} | {row['before_accuracy']} | {row['after_accuracy']} | {row['improved_count']} | {row['regressed_count']} | {row['applicable_count']} |")
    lines.extend([
        "",
        "## Auto-Apply Recommendation",
        "",
        recommendation,
        "",
        "Reason: auto-apply requires verified precision and regression numbers. Current labels do not satisfy the verified completeness gate.",
        "",
        "Corrections that should never be auto-applied: unsupported corrections, hallucinations, protected high-confidence overwrites, and any financial/table correction without verified evidence.",
    ])
    (run_root / "hybrid_accuracy_report.md").write_text("\n".join(lines), encoding="utf-8")


def build_hybrid_error_taxonomy_rows(artifacts: list[dict[str, Any]], correction_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    correction_index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in correction_rows:
        key = (str(row.get("document_id") or ""), str(row.get("prompt_version") or ""), normalize_proposal_field(row.get("field")))
        correction_index.setdefault(key, []).append(row)

    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.get("status") != "success":
            rows.append(taxonomy_row(artifact, "OCR", "exception", "benchmark", "critical", False, False, False, False, True, "document could not be processed", artifact.get("error"), "unavailable"))
            continue
        det = artifact.get("deterministic_result") or {}
        fields = det.get("fields") or {}
        readiness = det.get("erp_readiness") or {}
        validation_status = det.get("validation_status")
        label = artifact.get("label") or {}
        ground_truth_status = "verified" if label_is_verified_complete(label) else "unverified"
        missing_fields = set(readiness.get("missing_fields") or [])
        triggered = bool((artifact.get("hybrid_debug") or {}).get("invoked"))
        trigger_reasons = set(artifact.get("trigger_reasons") or [])

        field_specs = [
            ("supplier_name", "PARTY", "supplier missing", "supplier incorrect", "supplier"),
            ("customer_name", "PARTY", "customer missing", "customer incorrect", "customer"),
            ("invoice_number", "METADATA", "invoice number missing", "invoice number incorrect", "invoice_number"),
            ("invoice_date", "METADATA", "invoice date missing", "invoice date incorrect", "invoice_date"),
            ("amount_ht", "TOTALS", "subtotal missing", "subtotal incorrect", "amount_ht"),
            ("tax_amount", "TOTALS", "VAT missing", "VAT incorrect", "tax_amount"),
            ("amount_ttc", "TOTALS", "TTC missing", "TTC incorrect", "total"),
        ]
        for field, category, missing_type, incorrect_type, proposal_field in field_specs:
            missing = field in missing_fields or fields.get(field) in (None, "", [])
            correctness = None
            if ground_truth_status == "verified":
                correctness, _ = scalar_field_correct(field, fields.get(field), label.get(field))
            if missing or correctness is False:
                related = correction_index.get((str(artifact.get("document_id") or ""), str(artifact.get("prompt_version") or ""), normalize_proposal_field(proposal_field)), [])
                rows.append(taxonomy_row(
                    artifact,
                    category,
                    missing_type if missing else incorrect_type,
                    "deterministic_result",
                    severity_for_category(category, missing_type if missing else incorrect_type),
                    triggered,
                    bool(related),
                    any(row.get("classification") == "Correct improvement" for row in related),
                    any(row.get("classification") in {"Wrong correction", "Hallucination"} for row in related),
                    not any(row.get("classification") == "Correct improvement" for row in related),
                    erp_impact_for_category(category, missing_type if missing else incorrect_type),
                    f"{field}={fields.get(field)!r}",
                    ground_truth_status,
                ))

        if validation_status == "invalid" or "financial_inconsistency" in trigger_reasons:
            rows.append(taxonomy_row(artifact, "TOTALS", "arithmetic inconsistency", "validation", "high", triggered, False, False, False, True, "can block ERP posting or payment approval", validation_status, ground_truth_status))

        line_count = det.get("line_items_count") or 0
        if line_count == 0:
            error_type = "missing rows"
        elif "line_items_need_review" in trigger_reasons:
            error_type = "row needs review"
        else:
            error_type = ""
        if error_type:
            related = [row for row in correction_rows if row.get("document_id") == artifact.get("document_id") and normalize_proposal_field(row.get("field")) in {"line_items", "table"}]
            rows.append(taxonomy_row(
                artifact,
                "TABLES",
                error_type,
                "deterministic_result",
                "critical",
                triggered,
                bool(related),
                any(row.get("classification") == "Correct improvement" for row in related),
                any(row.get("classification") in {"Wrong correction", "Hallucination"} for row in related),
                True,
                "line-item errors can create wrong ERP postings",
                f"line_items_count={line_count}",
                ground_truth_status,
            ))

    return rows


def taxonomy_row(
    artifact: dict[str, Any],
    category: str,
    error_type: str,
    source: str,
    severity: str,
    triggered_llm: bool,
    attempted: bool,
    succeeded: bool,
    failed: bool,
    needs_review: bool,
    impact: str,
    evidence: Any,
    ground_truth_status: str,
) -> dict[str, Any]:
    return {
        "document_id": artifact.get("document_id"),
        "dataset": artifact.get("dataset"),
        "prompt_version": artifact.get("prompt_version"),
        "category": category,
        "error_type": error_type,
        "source": source,
        "severity": severity,
        "triggered_llm": triggered_llm,
        "hybrid_attempted": attempted,
        "hybrid_succeeded": succeeded,
        "hybrid_failed": failed,
        "needs_human_review": needs_review,
        "potential_erp_impact": impact,
        "evidence": evidence,
        "ground_truth_status": ground_truth_status,
    }


def build_hybrid_trigger_analysis_rows(artifacts: list[dict[str, Any]], correction_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    triggers = sorted({trigger for artifact in artifacts for trigger in artifact.get("trigger_reasons", [])})
    rows: list[dict[str, Any]] = []
    for trigger in triggers:
        subset = [artifact for artifact in artifacts if trigger in (artifact.get("trigger_reasons") or [])]
        proposed = [row for row in correction_rows if any(item.get("document_id") == row.get("document_id") and trigger in (item.get("trigger_reasons") or []) for item in subset)]
        accepted = [row for row in proposed if row.get("accepted_by_gate") in {True, "True", "true"}]
        correct = [row for row in proposed if row.get("classification") == "Correct improvement"]
        wrong = [row for row in proposed if row.get("classification") in {"Wrong correction", "Hallucination"}]
        unsupported = [row for row in proposed if row.get("classification") == "Unsupported correction"]
        category = trigger_category(trigger)
        rows.append({
            "trigger": trigger,
            "category": category,
            "invoked_count": len(subset),
            "proposal_count": len(proposed),
            "accepted_count": len(accepted),
            "rejected_count": len(proposed) - len(accepted),
            "correct_count": len(correct),
            "wrong_count": len(wrong),
            "unsupported_count": len(unsupported),
            "provisional_success_rate": ratio(len(correct), len(proposed)),
            "recommendation": trigger_recommendation(trigger, len(proposed), len(correct), len(wrong), len(unsupported)),
        })
    return rows


def build_hybrid_latency_breakdown_rows(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.get("status") != "success":
            continue
        debug = artifact.get("hybrid_debug") or {}
        duration = (debug.get("metrics") or {}).get("duration_seconds")
        if duration is None:
            continue
        duration = float(duration)
        rows.append(latency_row(artifact, "llm_inference_end_to_end", duration, duration, True, "Only aggregate Ollama resolver duration is currently instrumented."))
        for stage in ("prompt_construction", "evidence_building", "parsing", "safety_gate", "final_validation"):
            rows.append(latency_row(artifact, stage, None, duration, False, "Stage-specific timing unavailable in existing artifacts; do not infer."))
    return rows


def calculate_hybrid_roi_metrics(
    artifacts: list[dict[str, Any]],
    taxonomy_rows: list[dict[str, Any]],
    trigger_rows: list[dict[str, Any]],
    latency_rows: list[dict[str, Any]],
    quality: dict[str, Any],
) -> dict[str, Any]:
    category_metrics = []
    for category in ROI_CATEGORY_ORDER:
        rows = [row for row in taxonomy_rows if row.get("category") == category]
        attempted = [row for row in rows if row.get("hybrid_attempted") in {True, "True", "true"}]
        succeeded = [row for row in rows if row.get("hybrid_succeeded") in {True, "True", "true"}]
        failed = [row for row in rows if row.get("hybrid_failed") in {True, "True", "true"}]
        review = [row for row in rows if row.get("needs_human_review") in {True, "True", "true"}]
        impact_weight = {"PARTY": 5, "TABLES": 5, "TOTALS": 5, "METADATA": 4, "OCR": 2}.get(category, 1)
        provisional_roi_score = len(rows) * impact_weight + len(succeeded) * 3 - len(failed) * 2
        category_metrics.append({
            "category": category,
            "frequency": len(rows),
            "hybrid_attempted": len(attempted),
            "hybrid_succeeded": len(succeeded),
            "hybrid_failed": len(failed),
            "needs_human_review": len(review),
            "potential_erp_impact": erp_impact_for_category(category, ""),
            "provisional_roi_score": provisional_roi_score,
        })
    llm_rows = [row for row in latency_rows if row.get("stage") == "llm_inference_end_to_end" and row.get("seconds") not in (None, "")]
    latencies = [float(row["seconds"]) for row in llm_rows]
    prompt_chars = [int((artifact.get("prompt_stats") or {}).get("prompt_characters") or 0) for artifact in artifacts if (artifact.get("prompt_stats") or {}).get("prompt_characters")]
    output_tokens = [int((artifact.get("prompt_stats") or {}).get("output_tokens_estimated") or 0) for artifact in artifacts if (artifact.get("prompt_stats") or {}).get("output_tokens_estimated")]
    average_latency = round(sum(latencies) / len(latencies), 4) if latencies else None
    average_prompt_chars = round(sum(prompt_chars) / len(prompt_chars), 1) if prompt_chars else None
    average_input_tokens = round(average_prompt_chars / 4) if average_prompt_chars else None
    return {
        "ground_truth_status": "verified_complete" if quality.get("all_documents_verified_complete") else "provisional_unverified",
        "documents": len({artifact.get("document_id") for artifact in artifacts}),
        "artifact_count": len(artifacts),
        "category_metrics": sorted(category_metrics, key=lambda row: row["provisional_roi_score"], reverse=True),
        "trigger_metrics": trigger_rows,
        "latency": {
            "average_llm_seconds": average_latency,
            "median_llm_seconds": round(median(latencies), 4) if latencies else None,
            "p90_llm_seconds": percentile(latencies, 0.90),
            "bottleneck": "LLM inference end-to-end" if latencies else "not measured",
            "stage_specific_timing_available": False,
        },
        "cost": {
            "average_prompt_characters": average_prompt_chars,
            "average_input_tokens": average_input_tokens,
            "average_output_tokens": round(sum(output_tokens) / len(output_tokens), 1) if output_tokens else None,
            "projected_seconds_100": round(average_latency * 100, 1) if average_latency else None,
            "projected_seconds_1000": round(average_latency * 1000, 1) if average_latency else None,
            "projected_seconds_10000": round(average_latency * 10000, 1) if average_latency else None,
        },
        "recommendation": deployment_recommendation(trigger_rows, quality),
    }


def write_hybrid_roi_report(run_root: Path, roi: dict[str, Any], quality: dict[str, Any]) -> None:
    lines = [
        "# Hybrid Error Taxonomy & ROI Report",
        "",
        "This report analyzes saved hybrid benchmark artifacts only. It does not rerun OCR, deterministic extraction, routing, parsing, or the safety gate.",
        "",
        f"- Ground-truth status: {roi['ground_truth_status']}",
        f"- Accuracy conclusions allowed: {quality.get('all_documents_verified_complete')}",
        f"- Documents represented: {roi['documents']}",
        f"- Artifact count: {roi['artifact_count']}",
        "",
        "Because verified labels are incomplete, success/failure conclusions are provisional unless explicitly marked as gate-level behavior.",
        "",
        "## Category ROI",
        "",
        "| Category | Frequency | Attempted | Succeeded | Failed | Needs Review | ERP Impact | Provisional ROI |",
        "|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in roi["category_metrics"]:
        lines.append(f"| {row['category']} | {row['frequency']} | {row['hybrid_attempted']} | {row['hybrid_succeeded']} | {row['hybrid_failed']} | {row['needs_human_review']} | {row['potential_erp_impact']} | {row['provisional_roi_score']} |")
    lines.extend([
        "",
        "## Trigger Usefulness",
        "",
        "| Trigger | Category | Invoked | Proposals | Accepted | Rejected | Correct | Wrong | Unsupported | Recommendation |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in roi["trigger_metrics"]:
        lines.append(f"| {row['trigger']} | {row['category']} | {row['invoked_count']} | {row['proposal_count']} | {row['accepted_count']} | {row['rejected_count']} | {row['correct_count']} | {row['wrong_count']} | {row['unsupported_count']} | {row['recommendation']} |")
    cost = roi["cost"]
    latency = roi["latency"]
    lines.extend([
        "",
        "## Latency And Cost",
        "",
        f"- Bottleneck: {latency['bottleneck']}",
        f"- Stage-specific timing available: {latency['stage_specific_timing_available']}",
        f"- Average LLM seconds: {latency['average_llm_seconds']}",
        f"- Median/P90 LLM seconds: {latency['median_llm_seconds']} / {latency['p90_llm_seconds']}",
        f"- Average prompt characters: {cost['average_prompt_characters']}",
        f"- Estimated input tokens: {cost['average_input_tokens']}",
        f"- Estimated output tokens: {cost['average_output_tokens']}",
        f"- Projected local time for 100 invoices: {format_seconds(cost['projected_seconds_100'])}",
        f"- Projected local time for 1,000 invoices: {format_seconds(cost['projected_seconds_1000'])}",
        f"- Projected local time for 10,000 invoices: {format_seconds(cost['projected_seconds_10000'])}",
        "",
        "## Final Conclusion",
        "",
        "Where does the hybrid layer help? Provisional evidence shows it is most useful when deterministic extraction is missing supplier/customer values and the safety gate can tie the proposal to evidence.",
        "",
        "Where does it not help? It does not help when prompts time out, when evidence is insufficient, or when labels are not verified enough to prove correctness.",
        "",
        "What should remain deterministic forever? OCR, layout normalization, invoice numbers, dates, arithmetic validation, ERP readiness gates, and any field already high-confidence and internally consistent.",
        "",
        "Which fields justify an LLM? Supplier/customer recovery may justify LLM routing when missing or low-confidence. Table recovery is not proven by this benchmark.",
        "",
        "Which fields never justify an LLM? High-confidence invoice numbers, dates, currency, validated totals, and protected ERP-ready outputs should remain deterministic.",
        "",
        f"Would you deploy this today? Yes, only under this policy: {roi['recommendation']['choice']} - {roi['recommendation']['reason']}",
    ])
    (run_root / "hybrid_roi_report.md").write_text("\n".join(lines), encoding="utf-8")


def write_hybrid_deployment_recommendation(run_root: Path, roi: dict[str, Any], quality: dict[str, Any]) -> None:
    recommendation = roi["recommendation"]
    lines = [
        "# Hybrid Deployment Recommendation",
        "",
        f"Recommendation: **{recommendation['choice']}**",
        "",
        recommendation["reason"],
        "",
        f"Ground truth complete: {quality.get('all_documents_verified_complete')}",
        "",
        "This recommendation is provisional because verified labels are incomplete. It is suitable for advisory deployment, not automatic ERP correction.",
        "",
        "## Keep Enabled",
        "",
        "- LLM routing for missing supplier/customer values.",
        "- Safety correction gate.",
        "- Human review assistant.",
        "",
        "## Keep Deterministic",
        "",
        "- OCR and layout.",
        "- Invoice number and dates when deterministic confidence is high.",
        "- Financial validation and ERP readiness.",
        "- Final export approval.",
        "",
        "## Do Not Enable Yet",
        "",
        "- Global auto-apply.",
        "- Table auto-correction.",
        "- Financial/totals auto-correction.",
    ]
    (run_root / "hybrid_deployment_recommendation.md").write_text("\n".join(lines), encoding="utf-8")


def write_hybrid_roi_charts(run_root: Path, taxonomy_rows: list[dict[str, Any]], trigger_rows: list[dict[str, Any]], roi: dict[str, Any]) -> None:
    charts_dir = run_root / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    write_chart_csv(charts_dir / "error_distribution.csv", count_by(taxonomy_rows, "category"), ["category", "count"])
    write_chart_csv(charts_dir / "trigger_effectiveness.csv", [{k: row[k] for k in ("trigger", "proposal_count", "accepted_count", "correct_count", "wrong_count", "unsupported_count")} for row in trigger_rows], ["trigger", "proposal_count", "accepted_count", "correct_count", "wrong_count", "unsupported_count"])
    write_chart_csv(charts_dir / "roi_ranking.csv", roi.get("category_metrics") or [], ["category", "frequency", "hybrid_attempted", "hybrid_succeeded", "hybrid_failed", "needs_human_review", "provisional_roi_score"])
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return
    render_bar_chart(charts_dir / "error_distribution.png", count_by(taxonomy_rows, "category"), "Error Distribution", "category", "count", plt)
    render_bar_chart(charts_dir / "roi_ranking.png", roi.get("category_metrics") or [], "ROI Ranking", "category", "provisional_roi_score", plt)


def write_chart_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    write_csv(path, rows, columns)


def render_bar_chart(path: Path, rows: list[dict[str, Any]], title: str, label_key: str, value_key: str, plt: Any) -> None:
    if not rows:
        return
    labels = [str(row.get(label_key)) for row in rows]
    values = [float(row.get(value_key) or 0) for row in rows]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(labels, values)
    ax.set_title(title)
    ax.set_ylabel(value_key.replace("_", " ").title())
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def normalize_proposal_field(field: Any) -> str:
    value = normalize_text(field).replace(" ", "_")
    aliases = {
        "supplier": "supplier",
        "supplier_name": "supplier",
        "vendor": "supplier",
        "customer": "customer",
        "customer_name": "customer",
        "client": "customer",
        "total": "total",
        "amount_ttc": "total",
        "ttc": "total",
        "line_item": "line_items",
        "line_items": "line_items",
        "table": "table",
    }
    return aliases.get(value, value)


def trigger_category(trigger: str) -> str:
    value = trigger.lower()
    if "confidence" in value:
        return "CONFIDENCE"
    if "supplier" in value or "customer" in value or "party" in value:
        return "PARTY"
    if "line" in value or "table" in value or "row" in value:
        return "TABLES"
    if "total" in value or "amount" in value or "financial" in value or "validation_invalid" in value:
        return "TOTALS"
    if "validation" in value or "review" in value:
        return "REVIEW"
    if "date" in value or "invoice" in value or "metadata" in value:
        return "METADATA"
    return "OCR"


def trigger_recommendation(trigger: str, proposed: int, correct: int, wrong: int, unsupported: int) -> str:
    category = trigger_category(trigger)
    if category == "PARTY" and wrong == 0:
        return "keep enabled as advisory"
    if category in {"TOTALS", "TABLES"} and correct == 0:
        return "keep deterministic; review manually"
    if proposed == 0:
        return "keep as routing signal only"
    if wrong:
        return "disable auto-apply"
    if unsupported:
        return "needs verified labels before decision"
    return "provisionally useful"


def deployment_recommendation(trigger_rows: list[dict[str, Any]], quality: dict[str, Any]) -> dict[str, Any]:
    party_activity = sum(int(row.get("proposal_count") or 0) for row in trigger_rows if row.get("category") == "PARTY")
    table_activity = sum(int(row.get("proposal_count") or 0) for row in trigger_rows if row.get("category") == "TABLES")
    wrong = sum(int(row.get("wrong_count") or 0) for row in trigger_rows)
    if not quality.get("all_documents_verified_complete"):
        return {
            "choice": "A) Never call LLM unless supplier/customer missing",
            "reason": "Verified labels are incomplete, and current accepted proposals are mainly party-field recovery. Keep the blast radius small.",
        }
    if table_activity > party_activity and wrong == 0:
        return {"choice": "B) Call only for tables", "reason": "Verified benchmark shows stronger table ROI than party ROI."}
    if wrong == 0:
        return {"choice": "C) Call only below confidence threshold", "reason": "Verified benchmark shows no wrong accepted proposals."}
    return {"choice": "A) Never call LLM unless supplier/customer missing", "reason": "Wrong or unsupported proposals make broad routing too risky."}


def severity_for_category(category: str, error_type: str) -> str:
    if category in {"TOTALS", "TABLES"}:
        return "critical"
    if category == "PARTY" and "missing" in error_type:
        return "high"
    if category == "METADATA":
        return "high"
    return "medium"


def erp_impact_for_category(category: str, error_type: str) -> str:
    if category == "PARTY":
        return "wrong supplier/customer can post to the wrong ERP account"
    if category == "METADATA":
        return "wrong document metadata can break matching and audit trail"
    if category == "TOTALS":
        return "wrong totals can create payment and tax posting errors"
    if category == "TABLES":
        return "wrong rows can create incorrect inventory/accounting entries"
    if category == "OCR":
        return "OCR failure forces manual review"
    return "requires review"


def latency_row(artifact: dict[str, Any], stage: str, seconds: float | None, total: float, instrumented: bool, note: str) -> dict[str, Any]:
    return {
        "prompt_version": artifact.get("prompt_version"),
        "document_id": artifact.get("document_id"),
        "stage": stage,
        "seconds": round(seconds, 4) if seconds is not None else None,
        "percent_of_llm_duration": round((seconds / total) * 100, 2) if seconds is not None and total else None,
        "instrumented": instrumented,
        "note": note,
    }


def count_by(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get(key) or "unknown")
        counts[label] = counts.get(label, 0) + 1
    return [{"category": label, "count": count} for label, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)]


def format_seconds(value: float | None) -> str:
    if value is None:
        return "not measured"
    if value < 120:
        return f"{value:.1f}s"
    minutes = value / 60
    if minutes < 120:
        return f"{minutes:.1f} min"
    return f"{minutes / 60:.1f} h"


def write_comparison_csvs(run_root: Path, artifacts: list[dict[str, Any]]) -> None:
    field_rows = []
    correction_rows = []
    failure_rows = []
    line_rows = []
    for item in artifacts:
        if item.get("status") != "success":
            failure_rows.append({"document_id": item.get("document_id"), "failure": item.get("error")})
            continue
        for field in ("supplier_name", "customer_name", "invoice_number", "invoice_date", "amount_ttc"):
            field_rows.append({
                "document_id": item["document_id"],
                "prompt_version": item["prompt_version"],
                "field": field,
                "deterministic_value": item["deterministic_result"]["fields"].get(field),
                "final_value": item["final_selected_result"]["fields"].get(field),
                "truth": (item.get("label") or {}).get(field),
            })
        for proposal in item.get("accepted_proposals", []) + item.get("rejected_proposals", []):
            row = proposal.get("proposal") or {}
            correction_rows.append({"document_id": item["document_id"], "prompt_version": item["prompt_version"], **row, "accepted": proposal in item.get("accepted_proposals", []), "gate_reason": proposal.get("reason")})
        line_rows.append({
            "document_id": item["document_id"],
            "prompt_version": item["prompt_version"],
            "truth_count": len((item.get("label") or {}).get("line_items") or []),
            "deterministic_count": item["deterministic_result"].get("line_items_count"),
            "final_count": item["final_selected_result"].get("line_items_count"),
        })
    write_csv(run_root / "hybrid_field_comparison.csv", field_rows, ["document_id", "prompt_version", "field", "deterministic_value", "final_value", "truth"])
    write_csv(run_root / "hybrid_correction_audit.csv", correction_rows, ["document_id", "prompt_version", "field", "operation", "old_value", "proposed_value", "confidence", "evidence_refs", "accepted", "gate_reason"])
    write_csv(run_root / "hybrid_failure_matrix.csv", failure_rows, ["document_id", "failure"])
    write_csv(run_root / "hybrid_line_item_comparison.csv", line_rows, ["document_id", "prompt_version", "truth_count", "deterministic_count", "final_count"])
    write_prompt_comparison(run_root, artifacts)


def write_prompt_comparison(run_root: Path, artifacts: list[dict[str, Any]]) -> None:
    by_doc: dict[str, dict[str, dict[str, Any]]] = {}
    for item in artifacts:
        by_doc.setdefault(item.get("document_id", ""), {})[item.get("prompt_version", "")] = item
    rows = []
    for document_id, versions in by_doc.items():
        row = {"document_id": document_id}
        for version in PROMPT_VERSIONS:
            item = versions.get(version) or {}
            short = version.replace("hybrid_prompt_", "")
            row[f"{short}_proposals"] = len(item.get("proposals") or [])
            row[f"{short}_accepted"] = len(item.get("accepted_proposals") or [])
            row[f"{short}_latency"] = ((item.get("hybrid_debug") or {}).get("metrics") or {}).get("duration_seconds")
            row[f"{short}_parser_status"] = item.get("parser_status")
        rows.append(row)
    columns = ["document_id"]
    for version in PROMPT_VERSIONS:
        short = version.replace("hybrid_prompt_", "")
        columns.extend([f"{short}_proposals", f"{short}_accepted", f"{short}_latency", f"{short}_parser_status"])
    write_csv(run_root / "prompt_version_comparison.csv", rows, columns)


def write_report(run_root: Path, metrics: dict[str, Any]) -> None:
    prompt_metrics = calculate_prompt_version_metrics([json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_root / "artifacts").rglob("*.json"))] if (run_root / "artifacts").exists() else [])
    lines = [
        "# Hybrid LLM Benchmark Report",
        "",
        "This report separates model proposals, gate decisions, ground-truth correctness, and final production result.",
        "Rejected proposals are not counted as applied improvements.",
        "If labels or manual classifications are blank, accuracy/precision fields are not yet proven and must not be presented as final accuracy.",
        "",
        f"- Documents total: {metrics['documents_total']}",
        f"- Routed documents: {metrics['routed_documents']}",
        f"- Invocation rate: {metrics['invocation_rate']}",
        f"- Valid JSON rate: {metrics['valid_json_rate']}",
        f"- Proposal count: {metrics['proposal_count']}",
        f"- Accepted by gate: {metrics['accepted_by_gate']}",
        f"- Rejected by gate: {metrics['rejected_by_gate']}",
        f"- Accepted-correction precision: {metrics['accepted_correction_precision']}",
        f"- False acceptance rate: {metrics['false_acceptance_rate']}",
        f"- Latency median/P90/max: {metrics['latency']['median']} / {metrics['latency']['p90']} / {metrics['latency']['max']}",
        "",
        "## Recommendation",
        metrics["advisory_recommendation"],
        "",
        "## Prompt Version Metrics",
        "",
        "| Prompt | Attempted | Completed | Valid JSON | Timeout Rate | Malformed Rate | Insufficient Evidence | Proposals | Accepted | Rejected | Median Latency | P90 Latency | Max Latency | Avg Prompt Chars | Est Tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in prompt_metrics:
        lines.append(
            f"| {row['prompt_version']} | {row['runs_attempted']} | {row['runs_completed']} | {row['valid_json_rate']} | {row['timeout_rate']} | {row['malformed_response_rate']} | {row['insufficient_evidence_count']} | {row['proposal_count']} | {row['accepted_by_gate_count']} | {row['rejected_by_gate_count']} | {row['median_latency']} | {row['p90_latency']} | {row['max_latency']} | {row['average_prompt_characters']} | {row['estimated_input_tokens']} |"
        )
    (run_root / "hybrid_benchmark_report.md").write_text("\n".join(lines), encoding="utf-8")


def artifact_path(run_root: Path, prompt_version: str, filename: str) -> Path:
    return run_root / "artifacts" / prompt_version / f"{Path(filename).stem}.json"


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed": []}
    return json.loads(path.read_text(encoding="utf-8"))


def was_failed(run_root: Path, prompt_version: str, filename: str) -> bool:
    path = artifact_path(run_root, prompt_version, filename)
    if not path.exists():
        return True
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if artifact.get("status") != "success":
        return True
    debug = artifact.get("hybrid_debug") or {}
    if debug.get("invoked") and artifact.get("parser_status") != "parsed":
        return True
    return False


def calculate_prompt_version_metrics(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for version in PROMPT_VERSIONS:
        subset = [item for item in artifacts if item.get("prompt_version") == version]
        if not subset:
            continue
        completed = [item for item in subset if item.get("status") == "success"]
        invoked = [item for item in completed if (item.get("hybrid_debug") or {}).get("invoked")]
        latencies = [float(((item.get("hybrid_debug") or {}).get("metrics") or {}).get("duration_seconds") or 0.0) for item in invoked]
        errors = [str((item.get("hybrid_debug") or {}).get("error") or item.get("error") or "") for item in subset]
        prompt_chars = [int((item.get("prompt_stats") or {}).get("prompt_characters") or 0) for item in subset if (item.get("prompt_stats") or {}).get("prompt_characters")]
        output_tokens = [int((item.get("prompt_stats") or {}).get("output_tokens_estimated") or 0) for item in subset if (item.get("prompt_stats") or {}).get("output_tokens_estimated")]
        valid_json_count = sum(item.get("parser_status") == "parsed" for item in invoked)
        timeout_count = sum("timed out" in error.lower() or "timeout" in error.lower() for error in errors)
        rows.append({
            "prompt_version": version,
            "runs_attempted": len(subset),
            "runs_completed": len(completed),
            "valid_json_rate": ratio(valid_json_count, len(invoked)),
            "timeout_rate": ratio(timeout_count, len(subset)),
            "malformed_response_rate": ratio(sum(bool((item.get("hybrid_debug") or {}).get("error")) for item in invoked) - timeout_count, len(invoked)),
            "insufficient_evidence_count": sum(((item.get("parsed_response") or {}).get("document_decision") == "insufficient_evidence") for item in invoked),
            "proposal_count": sum(len(item.get("proposals") or []) for item in subset),
            "accepted_by_gate_count": sum(len(item.get("accepted_proposals") or []) for item in subset),
            "rejected_by_gate_count": sum(len(item.get("rejected_proposals") or []) for item in subset),
            "hallucination_count": _manual_count(item for item in subset),
            "median_latency": round(median(latencies), 4) if latencies else None,
            "p90_latency": percentile(latencies, 0.90),
            "max_latency": max(latencies) if latencies else None,
            "average_prompt_characters": round(sum(prompt_chars) / len(prompt_chars), 1) if prompt_chars else None,
            "estimated_input_tokens": round((sum(prompt_chars) / len(prompt_chars)) / 4) if prompt_chars else None,
            "average_output_tokens": round(sum(output_tokens) / len(output_tokens), 1) if output_tokens else None,
        })
    return rows


def _manual_count(items) -> int:
    # Manual classifications are filled after review; default benchmark should not invent hallucination labels.
    return 0


def snapshot_settings() -> dict[str, Any]:
    return {
        "enable_llm_resolver": settings.enable_llm_resolver,
        "llm_resolver_mode": settings.llm_resolver_mode,
        "llm_resolver_auto_apply_safe_corrections": settings.llm_resolver_auto_apply_safe_corrections,
        "llm_resolver_prompt_version": settings.llm_resolver_prompt_version,
        "llm_resolver_model": settings.llm_resolver_model,
        "llm_resolver_url": settings.llm_resolver_url,
        "llm_resolver_timeout_seconds": settings.llm_resolver_timeout_seconds,
        "llm_resolver_cache_dir": settings.llm_resolver_cache_dir,
    }


def restore_settings(values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(settings, key, value)


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value).strip("_") or "run"


def processed_key(document: Any, prompt_version: str, model: str) -> str:
    return f"{prompt_version}:{model}:{document.filename}"


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=safe_json_default) + "\n")


def startup_log(run_root: Path | None, message: str, payload: dict[str, Any] | None = None) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    suffix = f" {json.dumps(payload, ensure_ascii=False, default=str)}" if payload else ""
    line = f"[{stamp}] {message}{suffix}"
    print(line, flush=True)
    if run_root is not None:
        path = run_root / "execution_diagnostics.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def prompt_stats_from_debug(hybrid_debug: dict[str, Any]) -> dict[str, Any]:
    payload = hybrid_debug.get("payload") or {}
    raw_response = ((hybrid_debug.get("resolution") or {}).get("raw_response")) or ""
    try:
        serialized = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
    except TypeError:
        serialized = str(payload)
    return {
        "prompt_characters": len(serialized),
        "estimated_input_tokens": round(len(serialized) / 4),
        "output_characters": len(raw_response),
        "output_tokens_estimated": round(len(raw_response) / 4) if raw_response else None,
    }


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return round(ordered[index], 4)


if __name__ == "__main__":
    main()
