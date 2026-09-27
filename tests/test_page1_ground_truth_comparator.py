from copy import deepcopy

import pytest

from scripts.compare_page1_ground_truth import (
    UnverifiedGroundTruthError,
    _line_items,
    _result,
    compare_page1,
)


def _truth(fields):
    return {
        "label_status": "verified",
        "human_verified": True,
        "logical_documents": [{
            "document_role": "producer",
            "physical_page_numbers": [1],
            "fields": fields,
            "line_items": {"verified_rows": []},
        }, {
            # A synthetic non-Page-1 block proves the comparator ignores it.
            "document_role": "ruspina",
            "physical_page_numbers": [2],
            "fields": {"invoice_number": {"verified_value": "IGNORE"}},
        }],
    }


def _field(value, **extra):
    return {"verified_value": value, "presence_status": "present", **extra}


def _prediction(fields, rows=None):
    return {"physical_page": 1, "document_role": "producer", "fields": fields,
            "line_items": rows or [], "legacy_field_audit": {}}


def test_comparator_distinguishes_correct_missing_and_explicit_null_false_positive():
    truth = _truth({
        "invoice_number": _field("INV-TEST-001"),
        "truck_count": _field(None, presence_status="absent"),
        "bank": _field("BANK_TEST"),
    })
    prediction = _prediction({
        "invoice_number": {"raw_machine_value": "INV-TEST-001", "canonical_value": None},
        "truck_count": {"raw_machine_value": 3},
        "bank": {"raw_machine_value": None},
    })
    report = compare_page1(prediction, truth)
    items = {item["field"]: item for group in report["groups"].values() for item in group}
    assert items["invoice_number"]["status"] == "correct"
    assert items["truck_count"]["status"] == "false_positive"
    assert items["truck_count"]["root_cause"] == "FALSE_POSITIVE"
    assert items["bank"]["status"] == "missing"


def test_comparator_keeps_raw_canonical_and_human_correction_separate():
    truth = _truth({"total": _field(1000.0, normalized_value=1000.0)})
    prediction = _prediction({"total": {
        "raw_machine_value": 900.0, "canonical_value": 1000.0,
        "effective_machine_value": 900.0, "human_correction": 1100.0,
    }})
    item = compare_page1(prediction, truth)["groups"]["FINANCIAL"][0]
    assert item["status"] == "correct"
    assert item["raw_machine_value"] == 900.0
    assert item["canonical_machine_value"] == 1000.0
    assert item["human_correction"] == 1100.0
    assert item["corrected_effective_value"] == 1100.0
    assert item["predicted"] == 1000.0


def test_comparator_classifies_semantic_mismatch_ocr_and_formatting_only():
    truth = _truth({
        "total": _field(1000.0, normalized_value=1000.0),
        "seller": _field("SOCIÉTÉ TEST"),
        "client_address": _field("TRIPOLI - LIBYA"),
        "invoice_date": _field("2023-01-02", normalized_value="2023-01-02"),
    })
    prediction = _prediction({
        "total": {"raw_machine_value": 52.0},
        "seller": {"raw_machine_value": "SOCIETE TSET", "evidence_text": "SOCIETE TSET"},
        "client_address": {"raw_machine_value": "TRIPOLI-LIBYA"},
        "invoice_date": {"raw_machine_value": "02/01/2023"},
    })
    report = compare_page1(prediction, truth)
    by_field = {item["field"]: item for group in report["groups"].values() for item in group}
    assert by_field["total"]["status"] == "wrong_value"
    assert by_field["total"]["root_cause"] == "UNKNOWN"
    assert by_field["seller"]["root_cause"] == "OCR_TRANSCRIPTION"
    assert by_field["client_address"]["status"] == "formatting_only"
    assert by_field["invoice_date"]["status"] == "formatting_only"


def test_comparator_marks_a_value_reused_from_another_semantic_field():
    truth = _truth({
        "total": _field(52000.0),
        "unit_price": _field(52.0),
    })
    prediction = _prediction({
        "total": {"raw_machine_value": 52.0},
        "unit_price": {"raw_machine_value": 52.0},
    })
    total = compare_page1(prediction, truth)["groups"]["FINANCIAL"][0]
    assert total["status"] == "wrong_value"
    assert total["root_cause"] == "SEMANTIC_MAPPING"


def test_explicit_null_matches_explicit_absence_and_populated_is_false_positive():
    expected = _field(None, presence_status="absent")
    assert _result(None, None, "truck_count", presence="absent") == "correct"
    assert _result(None, 2, "truck_count", presence="absent") == "false_positive"
    assert _result("1000.00", 1000.0, "total") == "formatting_only"


def test_line_items_compare_cells_individually_and_use_canonical_line_total():
    producer = {"line_items": {"verified_rows": [{
        "row_id": "row-1",
            "verified_value": {"description": "CEMENT_TEST", "quantity": 2, "unit": "BAG", "unit_price": 50, "line_total": 100.0},
        "source_values": {"line_total": "100.00"},
        "normalized_values": {"line_total": 100.0},
        "likely_root_cause": {key: "TABLE_RECONSTRUCTION" for key in ("description", "quantity", "unit", "unit_price", "line_total")},
    }]}}
    result = _line_items({"line_items": [{"description": "CEMENT_TEST", "quantity": 2, "unit": "BAG", "unit_price": 50, "line_total": 100.0}]}, producer)
    assert len(result) == 5
    statuses = {item["field"]: item["status"] for item in result}
    assert all(status == "correct" for field, status in statuses.items() if field != "line_items[0].line_total")
    assert statuses["line_items[0].line_total"] == "formatting_only"
    missing = _line_items({"line_items": []}, producer)
    assert all(item["status"] == "missing" for item in missing)
    assert {item["root_cause"] for item in missing} == {"TABLE_RECONSTRUCTION"}


def test_comparator_rejects_non_page1_predictions_and_unverified_truth():
    prediction = _prediction({})
    truth = _truth({})
    unverified = deepcopy(truth)
    unverified["human_verified"] = False
    with pytest.raises(UnverifiedGroundTruthError):
        compare_page1(prediction, unverified)
    prediction["physical_page"] = 2
    with pytest.raises(ValueError, match="Page 1"):
        compare_page1(prediction, truth)
