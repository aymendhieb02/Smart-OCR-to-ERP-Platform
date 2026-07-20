from __future__ import annotations

from pathlib import Path

from app.services.extraction_failure_taxonomy import analyze_failure
from scripts import large_benchmark_runner as runner


def test_invalid_extraction_gets_failure_code() -> None:
    analysis = analyze_failure({"execution_status": "completed", "extraction_status": "invalid", "overall_confidence": 0.4})

    assert analysis.failure_codes
    assert analysis.primary_failure_code is not None


def test_missing_currency_maps_to_code() -> None:
    analysis = analyze_failure({"execution_status": "completed", "extraction_status": "invalid", "currency_pred": ""})

    assert "MISSING_CURRENCY" in analysis.failure_codes


def test_ttc_year_and_implausibly_large_codes() -> None:
    year = analyze_failure({"execution_status": "completed", "extraction_status": "invalid", "amount_ttc_pred": 2026})
    huge = analyze_failure({"execution_status": "completed", "extraction_status": "invalid", "amount_ttc_pred": 1011600038390.0})

    assert "TTC_LOOKS_LIKE_YEAR" in year.failure_codes
    assert "TTC_IMPLAUSIBLY_LARGE" in huge.failure_codes


def test_label_and_table_header_party_codes() -> None:
    label = analyze_failure({"execution_status": "completed", "extraction_status": "invalid", "supplier_name_pred": "SHIP_TO:"})
    header = analyze_failure({"execution_status": "completed", "extraction_status": "invalid", "customer_name_pred": "Unit price"})

    assert "PARTY_LABEL_ONLY" in label.failure_codes
    assert "PARTY_TABLE_HEADER" in header.failure_codes


def test_legacy_suspicious_codes_map_to_normalized_taxonomy() -> None:
    analysis = analyze_failure({"execution_status": "completed", "extraction_status": "invalid", "suspicious_field_codes": ["TVA_GREATER_THAN_TTC"]})

    assert "TAX_INCONSISTENT" in analysis.failure_codes
    assert "TVA_GREATER_THAN_TTC" not in analysis.failure_codes


def test_execution_failure_is_not_extraction_invalid() -> None:
    analysis = analyze_failure({"execution_status": "failed", "extraction_status": "unavailable", "execution_error_message": "boom"})

    assert "EXECUTION_EXCEPTION" in analysis.failure_codes
    assert "TOTALS_INCONSISTENT" not in analysis.failure_codes


def test_cached_attempt_is_excluded_from_fresh_metrics() -> None:
    attempts = runner._normalize_attempts(
        [
            {"document_id": "fresh", "status": "success", "validation_status": "invalid", "duration_seconds": 30, "total_paddle_calls": 1, "disk_cache_hit": False},
            {"document_id": "cached", "status": "success", "validation_status": "invalid", "duration_seconds": 1, "total_paddle_calls": 0, "disk_cache_hit": True},
        ],
        run_id="run",
    )
    latest = runner._dedupe_latest_results(attempts)

    performance = runner._performance_payloads(attempts, latest)

    assert performance["fresh_ocr"]["count"] == 1
    assert performance["fresh_ocr"]["median"] == 30
    assert performance["cached"]["count"] == 1
    assert "cached attempts" in performance["cached"]["percentile_note"]


def test_ground_truth_metrics_ignore_docs_without_truth() -> None:
    latest = [
        {"document_id": "no_gt", "has_ground_truth": False, "ground_truth_supported": False},
        {"document_id": "gt", "has_ground_truth": True, "ground_truth_supported": True, "invoice_number_pred": "INV-1", "invoice_number_true": "INV1"},
    ]

    quality = runner._quality_payloads(latest)

    assert quality["ground_truth"]["ground_truth_evaluated_count"] == 1
    assert quality["ground_truth"]["no_ground_truth_count"] == 1
    assert quality["field_accuracy"]["invoice_number"]["evaluated_document_count"] == 1


def test_failure_outputs_are_deterministic(tmp_path: Path) -> None:
    rows = [
        runner._attach_failure_analysis({
            "dataset_name": "ds",
            "document_id": "doc",
            "filename": "a.png",
            "execution_status": "completed",
            "extraction_status": "invalid",
            "erp_status": "blocked",
            "currency_pred": "",
            "duration_seconds": 1,
        })
    ]

    path = tmp_path / "failure_matrix.csv"
    runner._write_failure_matrix(path, rows)

    first = path.read_text(encoding="utf-8")
    runner._write_failure_matrix(path, rows)
    assert path.read_text(encoding="utf-8") == first
