from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.schemas import ExtractedInvoiceFields, FieldExtractionDetail, OCRLine, ValidationResult
from scripts.compare_dossier_ground_truth import UnverifiedGroundTruthError, compare_payloads
from scripts.dossier_ground_truth import build_review_template, serialize_machine_output


def _result() -> SimpleNamespace:
    documents = []
    definitions = [
        ("commercial_invoice", "producer_test", "INV-TEST-001", "SUPPLIER_TEST", "CUSTOMER_TEST", "Échantillon synthétique"),
        ("commercial_invoice", "reinvoice_test", None, "SUPPLIER_TEST", "CUSTOMER_TEST", "AS PER INVOICE: INV-TEST-001"),
        ("customs_declaration", None, None, None, None, "نص تجريبي"),
    ]
    for index, (doc_type, family, number, supplier, customer, evidence) in enumerate(definitions, start=1):
        fields = ExtractedInvoiceFields(
            supplier_name=supplier,
            customer_name=customer,
            invoice_number=number,
            currency="EUR" if index < 3 else None,
            line_items=[] if index != 1 else [{"description": "PRODUCT_TEST", "quantity": 2, "unit": "TEST_UNIT"}],
        )
        response = SimpleNamespace(
            detected_fields=fields,
            expanded_fields={"supplier_name": FieldExtractionDetail(value=supplier, page=index)},
            validation=ValidationResult(status="needs_review", is_valid=False, confidence=0.5),
            field_confidences={"invoice_number": 0.8},
            confidence_breakdown={},
            erp_readiness={"status": "needs_review"},
            all_ocr_blocks=[OCRLine(
                text=evidence,
                confidence=0.91,
                page_number=index,
                line_index=7,
                bbox={"x1": 1, "y1": 2, "x2": 3, "y2": 4},
                source="fixture",
            )],
        )
        group = SimpleNamespace(
            group_id=f"logical_document_{index}",
            pages=(index,),
            document_type=doc_type,
            document_family=family,
        )
        documents.append(SimpleNamespace(group=group, response=response))
    return SimpleNamespace(
        logical_documents=tuple(documents), page_count=3, ocr_engine="fixture"
    )


def _machine(tmp_path: Path) -> dict:
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"controlled fixture")
    return serialize_machine_output(
        _result(),
        source_path=source,
        pipeline_commit="7fe5339",
        generated_at="2026-09-22T00:00:00+00:00",
    )


def test_machine_output_serialization_preserves_pages_unicode_and_nulls(tmp_path):
    machine = _machine(tmp_path)

    assert machine["metadata"]["label_status"] == "machine_prediction"
    assert machine["dossier"]["page_count"] == 3
    documents = machine["dossier"]["logical_documents"]
    assert [item["physical_page_numbers"] for item in documents] == [[1], [2], [3]]
    assert documents[1]["identifiers"]["referenced_invoice"] is None
    assert documents[2]["financial"]["invoice_value"] is None
    assert documents[0]["evidence"][0]["text"] == "Échantillon synthétique"
    assert documents[2]["evidence"][0]["text"] == "نص تجريبي"
    assert documents[2]["evidence"][0]["bbox"] == {"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}


def test_review_template_has_missing_targets_and_never_prefills_verified_values(tmp_path):
    template = build_review_template(_machine(tmp_path))

    assert template["label_status"] == "draft"
    assert template["human_verified"] is False
    producer, ruspina, customs = template["logical_documents"]
    assert producer["fields"]["invoice_number"]["machine_value"] == "INV-TEST-001"
    assert ruspina["fields"]["invoice_number"]["machine_value"] is None
    assert ruspina["fields"]["referenced_invoice"]["machine_value"] is None
    assert customs["fields"]["referenced_invoice"]["machine_value"] is None
    assert customs["fields"]["invoice_value"]["machine_value"] is None
    assert customs["fields"]["exporter"]["machine_value"] is None
    assert all(
        field["verified_value"] is None and field["verification_status"] == "unverified"
        for document in template["logical_documents"]
        for field in document["fields"].values()
    )
    assert producer["line_items"]["machine_rows"]
    assert producer["line_items"]["verified_rows"] is None
    assert len(template["relations"]) == 3
    assert all(item["verified_status"] is None for item in template["relations"])


def test_comparator_refuses_draft_and_human_verified_false(tmp_path):
    machine = _machine(tmp_path)
    template = build_review_template(machine)

    with pytest.raises(UnverifiedGroundTruthError, match="label_status"):
        compare_payloads(machine, template)

    template["label_status"] = "verified"
    with pytest.raises(UnverifiedGroundTruthError, match="human_verified"):
        compare_payloads(machine, template)


def test_comparator_accepts_explicit_verification_and_classifies_mismatches(tmp_path):
    machine = _machine(tmp_path)
    truth = build_review_template(machine)
    truth["label_status"] = "verified"
    truth["human_verified"] = True
    truth["verified_by"] = "human-reviewer"
    truth["verified_at"] = "2026-09-22T01:00:00+00:00"
    for document in truth["logical_documents"]:
        for review in document["fields"].values():
            review["verified_value"] = copy.deepcopy(review["machine_value"])
            review["verification_status"] = "verified"
        document["line_items"]["verified_rows"] = copy.deepcopy(document["line_items"]["machine_rows"])
        document["line_items"]["verification_status"] = "verified"
    for relation in truth["relations"]:
        relation["verified_status"] = relation["machine_status"]
        relation["verification_status"] = "verified"

    truth["logical_documents"][0]["fields"]["document_type"]["verified_value"] = "customs_declaration"
    truth["logical_documents"][1]["fields"]["referenced_invoice"]["verified_value"] = "INV-TEST-001"
    truth["logical_documents"][2]["fields"]["exporter"]["verified_value"] = "شركة"
    truth["logical_documents"][0]["line_items"]["verified_rows"].append({"description": "Missed row"})

    result = compare_payloads(machine, truth)
    errors = {
        item["result"]
        for section in result["comparisons"].values()
        for item in section
    }
    assert "WRONG_DOCUMENT_TYPE" in errors
    assert "MISSING_FIELD" in errors
    assert "TABLE_ROW_MISSING" in errors
    assert result["summary_by_section"]["document"]["errors"] == 1


def test_comparator_uses_structured_fields_and_excludes_ocr_row_metadata(tmp_path):
    machine = _machine(tmp_path)
    machine["dossier"]["logical_documents"][0]["line_items"] = [{
        "description": "Échantillon synthétique", "quantity": 2, "unit": "TEST_UNIT",
        "confidence": 0.9, "bbox": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
    }]
    truth = build_review_template(machine)
    truth["label_status"] = "verified"
    truth["human_verified"] = True
    for doc in truth["logical_documents"]:
        for review in doc["fields"].values():
            review["verified_value"] = copy.deepcopy(review["machine_value"])
            review["verification_status"] = "verified"
        doc["line_items"]["verified_rows"] = [
            {"description": "Échantillon synthétique", "quantity": 2, "unit": "TEST_UNIT"}
        ] if doc["document_role"] == "producer" else []
        doc["line_items"]["verification_status"] = "verified"
    for relation in truth["relations"]:
        relation["verified_status"] = relation["machine_status"]
        relation["verification_status"] = "verified"

    result = compare_payloads(machine, truth)
    row_result = next(item for item in result["comparisons"]["table"] if item["field"] == "line_items[0]")
    assert row_result["result"] == "CORRECT"
    assert "bbox" not in row_result["predicted"]
    assert result["comparisons"]["document"][0]["page"] == 1


def test_comparator_marks_unclear_null_as_unknown_and_maps_custom_fields():
    from scripts.compare_dossier_ground_truth import _prediction_values, _comparison

    prediction = {
        "document_type": "customs_declaration",
        "document_family": "customs_test",
        "detected_fields": {"hs_code": "HS_TEST_001", "gross_weight": 9999},
        "identifiers": {"declaration_number": "DECL-TEST-01", "declaration_date": "2026-01-02"},
        "financial": {"invoice_value": 52000.0, "currency": "EUR"},
        "parties": {"exporter": "SUPPLIER_TEST"},
    }
    values = _prediction_values(prediction)
    assert values["declaration_number"] == "DECL-TEST-01"
    assert values["declaration_date"] == "2026-01-02"
    assert values["invoice_value"] == 52000.0
    assert values["hs_code"] == "HS_TEST_001"
    assert values["exporter"] == "SUPPLIER_TEST"
    unclear = _comparison("doc_test", "optional_test", None, None, "identifiers", presence_status="unclear")
    assert unclear["result"] == "UNKNOWN"
