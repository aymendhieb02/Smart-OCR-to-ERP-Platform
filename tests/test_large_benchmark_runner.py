from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import large_benchmark_runner as runner


@dataclass
class RawDoc:
    dataset_name: str
    split: str
    file_path: Path
    label_path: Path | None = None


def test_stable_document_id_changes_with_hash_or_path() -> None:
    first = runner._stable_document_id("dataset", "a/invoice.png", 10, "abc")
    same = runner._stable_document_id("dataset", "a/invoice.png", 10, "abc")
    changed_hash = runner._stable_document_id("dataset", "a/invoice.png", 10, "def")
    changed_path = runner._stable_document_id("dataset", "b/invoice.png", 10, "abc")

    assert first == same
    assert first != changed_hash
    assert first != changed_path


def test_atomic_checkpoint_roundtrip(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    payload = {"schema_version": 1, "completed_document_ids": ["doc1"]}

    runner._atomic_json(checkpoint, payload)

    assert json.loads(checkpoint.read_text(encoding="utf-8")) == payload
    assert not checkpoint.with_suffix(".json.tmp").exists()


def test_jsonl_reader_ignores_truncated_line(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text('{"document_id": "ok"}\n{"document_id": ', encoding="utf-8")

    rows = runner._read_jsonl(path)

    assert rows == [{"document_id": "ok"}]


def test_resume_refuses_incompatible_configuration(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps({"benchmark_configuration": {"configuration_hash": "old"}}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="Checkpoint configuration does not match"):
        runner._load_or_create_checkpoint(
            checkpoint,
            "run",
            {"configuration_hash": "new"},
            {},
            [],
            resume=True,
        )


def test_size_mapping_defaults_to_smoke() -> None:
    assert runner._size_to_total_limit(None) == 12
    assert runner._size_to_total_limit("medium") == 300
    assert runner._size_to_total_limit("full") is None


def test_small_selection_fills_remainder(tmp_path: Path) -> None:
    grouped = {}
    for dataset_index in range(6):
        dataset = f"dataset_{dataset_index}"
        docs = []
        for doc_index in range(10):
            path = tmp_path / dataset / f"doc_{doc_index}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{dataset}-{doc_index}".encode("utf-8"))
            docs.append(RawDoc(dataset, "unknown", path))
        grouped[dataset] = docs
    args = argparse.Namespace(seed=42, size="small", limit_per_dataset=50, offset=0, limit=None)

    selected = runner._select_documents(grouped, tmp_path, args, __import__("scripts.benchmark_multi_datasets", fromlist=["sample_documents"]))

    assert len(selected) == 50


def test_cache_flag_compatibility() -> None:
    args = argparse.Namespace(disable_cache=False, no_ocr_cache=True, refresh_cache=False, refresh_ocr_cache=True)

    assert runner._disable_cache(args) is True
    assert runner._refresh_cache(args) is True


def test_latest_result_wins_for_retried_documents() -> None:
    rows = [
        {"document_id": "doc1", "status": "error"},
        {"document_id": "doc2", "status": "success"},
        {"document_id": "doc1", "status": "success"},
    ]

    deduped = {row["document_id"]: row for row in runner._dedupe_latest_results(rows)}

    assert deduped["doc1"]["status"] == "success"
    assert deduped["doc2"]["status"] == "success"


def test_normalized_status_separates_execution_from_extraction() -> None:
    rows = [{"document_id": "doc1", "status": "success", "validation_status": "invalid", "erp_export_allowed": False}]

    normalized = runner._normalize_attempts(rows, run_id="run")

    assert normalized[0]["execution_status"] == "completed"
    assert normalized[0]["extraction_status"] == "invalid"
    assert normalized[0]["erp_status"] == "blocked"
    assert normalized[0]["execution_error_type"] == ""


def test_failed_execution_has_unavailable_extraction() -> None:
    rows = [{"document_id": "doc1", "status": "error", "error_type": "exception", "error_message": "boom"}]

    normalized = runner._normalize_attempts(rows, run_id="run")

    assert normalized[0]["execution_status"] == "failed"
    assert normalized[0]["extraction_status"] == "unavailable"
    assert normalized[0]["erp_status"] == "unavailable"
    assert normalized[0]["execution_error_type"] == "exception"


def test_attempt_numbers_and_latest_selection_are_inferred() -> None:
    rows = [
        {"document_id": "doc1", "status": "error"},
        {"document_id": "doc1", "status": "success", "validation_status": "needs_review"},
    ]

    attempts = runner._normalize_attempts(rows, run_id="run")

    assert attempts[0]["attempt_number"] == 1
    assert attempts[1]["attempt_number"] == 2
    assert attempts[1]["is_retry"] is True
    assert attempts[1]["previous_execution_status"] == "failed"
    assert attempts[1]["selected_as_latest_result"] is True


def test_fresh_and_cached_performance_are_separated() -> None:
    attempts = runner._normalize_attempts(
        [
            {"document_id": "fresh", "status": "success", "validation_status": "invalid", "duration_seconds": 20, "total_paddle_calls": 1, "disk_cache_hit": False},
            {"document_id": "cached", "status": "success", "validation_status": "invalid", "duration_seconds": 1, "total_paddle_calls": 0, "disk_cache_hit": True},
        ],
        run_id="run",
    )
    latest = runner._dedupe_latest_results(attempts)

    perf = runner._performance_payloads(attempts, latest)

    assert perf["fresh_ocr"]["median"] == 20
    assert perf["cached"]["median"] == 1


def test_summary_does_not_confuse_execution_with_valid_extraction() -> None:
    attempts = runner._normalize_attempts(
        [
            {"document_id": "doc1", "status": "success", "validation_status": "invalid", "erp_export_allowed": False, "duration_seconds": 10, "total_paddle_calls": 1},
            {"document_id": "doc2", "status": "success", "validation_status": "needs_review", "erp_export_allowed": False, "duration_seconds": 12, "total_paddle_calls": 1},
        ],
        run_id="run",
    )
    latest = runner._dedupe_latest_results(attempts)
    perf = runner._performance_payloads(attempts, latest)
    quality = runner._quality_payloads(latest)
    failures = runner.failure_summary(latest)

    summary = runner._summary(attempts, latest, {"run_id": "run", "ocr_profile": "optimized_mobile_v4"}, perf, quality, failures)

    assert summary["execution_completed_count"] == 2
    assert summary["validation_valid_count"] == 0
    assert summary["ERP_blocked_count"] == 2
    assert summary["success_count_definition"] == "execution completed without uncaught exception"


def test_resume_configuration_rejects_explicit_timeout_mismatch(monkeypatch) -> None:
    args = argparse.Namespace(document_timeout=60, ocr_profile=None, ocr_mode="balanced", size=None, seed=42, limit=None, offset=0, workers=1, datasets=None, dataset=None)
    monkeypatch.setattr(runner.sys, "argv", ["benchmark", "--resume", "--document-timeout", "60"])

    with pytest.raises(SystemExit, match="document_timeout was 120"):
        runner._validate_resume_configuration(args, {"document_timeout": 120})


def test_resume_configuration_allows_retry_flag_without_redeclaring_timeout(monkeypatch) -> None:
    args = argparse.Namespace(document_timeout=None, ocr_profile=None, ocr_mode="balanced", size=None, seed=42, limit=None, offset=0, workers=1, datasets=None, dataset=None)
    monkeypatch.setattr(runner.sys, "argv", ["benchmark", "--resume", "--retry-failed"])

    runner._validate_resume_configuration(args, {"document_timeout": 120})


def test_suspicious_party_and_amount_helpers() -> None:
    assert "PARTY_IS_LABEL_ONLY" in runner._party_suspicious_codes("SHIP_TO:")
    assert "PARTY_IS_TABLE_HEADER" in runner._party_suspicious_codes("Unit price")

    fields = argparse.Namespace(amount_ttc=1011600038390.0, tva_amount=None, amount_ht=None, tax_rate=None)

    assert "TTC_IMPLAUSIBLY_LARGE" in runner._amount_suspicious_codes(fields)
