import json

import pytest

from scripts.manual_benchmark_utils import (
    amount_correct,
    compare_line_items,
    compare_prediction_to_label,
    name_correct,
    normalize_date,
    summarize_results,
    validate_verified_label,
)


def test_unverified_label_is_rejected(tmp_path):
    label = tmp_path / "label.json"
    label.write_text(json.dumps({"verified_by_human": False}), encoding="utf-8")

    with pytest.raises(ValueError):
        validate_verified_label(label)


def test_amount_tolerance_allows_small_absolute_and_relative_differences():
    assert amount_correct(100.01, 100.0) is True
    assert amount_correct(1005.0, 1000.0) is True
    assert amount_correct(101.0, 100.0) is False


def test_date_normalization_flags_ambiguous_formats():
    normalized, ambiguous = normalize_date("05/06/2026")

    assert normalized in {"2026-05-06", "2026-06-05"}
    assert ambiguous is True


def test_name_similarity_accepts_punctuation_and_case_changes():
    assert name_correct("Vital Distribution SARL", "VITAL DISTRIBUTION, S.A.R.L.") is True


def test_line_item_row_matching_ignores_row_order():
    truth = [
        {"description": "Paracetamol 500mg", "quantity": 50, "unit_price": 0.45, "line_total_ttc": 22.5},
        {"description": "Amoxicillin 1g", "quantity": 30, "unit_price": 1.25, "line_total_ttc": 37.5},
    ]
    predicted = [
        {"description": "Amoxicillin 1 g", "quantity": 30, "unit_price": 1.25, "line_total_ttc": 37.5},
        {"description": "Paracetamol 500 mg", "quantity": 50, "unit_price": 0.45, "line_total_ttc": 22.5},
    ]

    result = compare_line_items(predicted, truth)

    assert result["matched_count"] == 2
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["f1"] == 1.0


def test_missing_ground_truth_is_excluded_from_denominator():
    comparison = compare_prediction_to_label(
        {"supplier_name": "Wrong", "invoice_number": "INV-1"},
        [],
        {"supplier_name": None, "invoice_number": "INV-1", "line_items": [], "verified_by_human": True},
    )

    assert comparison["fields"]["supplier_name"]["applicable"] is False
    assert comparison["fields"]["invoice_number"]["applicable"] is True
    assert comparison["fields"]["invoice_number"]["correct"] is True


def test_false_erp_ready_is_counted():
    rows = [
        {
            "status": "success",
            "dataset": "sample",
            "document_type_hint": "invoice",
            "false_erp_ready": True,
            "fully_correct_document": False,
            "incorrect_prediction_count": 1,
            "missing_prediction_count": 0,
        }
    ]

    summary = summarize_results(rows)

    assert summary["document_metrics"]["false_erp_ready_count"] == 1
    assert summary["document_metrics"]["incorrect_prediction_count"] == 1


def test_summary_metrics_are_deterministic_except_timestamp():
    rows = [
        {
            "status": "success",
            "dataset": "sample",
            "document_type_hint": "invoice",
            "invoice_number_applicable": True,
            "invoice_number_correct": True,
            "amount_ttc_applicable": True,
            "amount_ttc_correct": False,
            "amount_ttc_prediction_missing": True,
            "line_items_applicable": True,
            "line_item_f1": 0.5,
            "line_item_precision": 0.5,
            "line_item_recall": 0.5,
            "false_erp_ready": False,
            "fully_correct_document": False,
            "incorrect_prediction_count": 1,
            "missing_prediction_count": 1,
        }
    ]

    first = summarize_results(rows)
    second = summarize_results(rows)
    first.pop("generated_at")
    second.pop("generated_at")

    assert first == second
