from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.core.schemas import ExtractedInvoiceFields, LineItem, ProcessInvoiceResponse, ValidationResult
from app.services.review_assistant import build_review_assistant


def test_review_assistant_explains_party_candidates_without_modifying_fields() -> None:
    response = sample_response()
    original_supplier = response.detected_fields.supplier_name

    assistant = build_review_assistant(response)

    assert response.detected_fields.supplier_name == original_supplier
    assert assistant["status"] == "needs_review"
    assert any(issue["type"] == "supplier_name" for issue in assistant["issues"])
    supplier_issue = next(issue for issue in assistant["issues"] if issue["type"] == "supplier_name")
    assert supplier_issue["suggested_correction"]["supplier_name"] == "ABC SARL"
    assert supplier_issue["evidence"][0]["value"] == "ABC SARL"
    assert "No extraction value is changed automatically" in assistant["reviewer_control"]


def test_review_assistant_reports_financial_and_erp_impact() -> None:
    response = sample_response()

    assistant = build_review_assistant(response)

    assert any(issue["type"] == "financial_reasoning" for issue in assistant["issues"])
    assert any(issue["type"] == "missing_required_fields" for issue in assistant["issues"])
    assert assistant["erp_impact"] == "Needs Review"


def test_process_response_accepts_review_assistant_payload() -> None:
    payload = {
        "status": "needs_review",
        "confidence": 0.7,
        "issues": [{"type": "supplier_name", "title": "Supplier needs review"}],
    }

    response = ProcessInvoiceResponse(
        extracted_text="",
        detected_fields=ExtractedInvoiceFields(),
        validation=ValidationResult(status="needs_review", is_valid=False),
        erp_json=_erp_json(),
        erp_export=_erp_export(),
        review_assistant=payload,
    )

    assert response.review_assistant["issues"][0]["type"] == "supplier_name"


def sample_response():
    fields = ExtractedInvoiceFields(
        supplier_name=None,
        customer_name="Client SARL",
        invoice_number="INV-1",
        invoice_date=date(2026, 7, 21),
        amount_ht=100,
        tva_amount=19,
        amount_ttc=130,
        currency="TND",
        line_items=[LineItem(description="Item A", quantity=1, unit_price=100, total=100, confidence=0.5, source="review row")],
    )
    return SimpleNamespace(
        detected_fields=fields,
        validation=ValidationResult(status="needs_review", is_valid=False, warnings=["Amount mismatch"]),
        confidence_breakdown={"overall_confidence": 0.55},
        erp_readiness={"ready": False, "erp_ready_status": "Needs Review", "missing_fields": ["supplier_name"]},
        financial_reasoning={
            "financial_consistency_score": 0.25,
            "financial_errors": ["Amount mismatch"],
            "financial_warnings": [],
            "checks": {"ht_plus_tax_equals_ttc": {"passed": False, "expected": 119, "actual": 130}},
        },
        line_items_needs_review=fields.line_items,
        extraction_debug={
            "party_resolver": {
                "supplier_candidates": [
                    {"value": "ABC SARL", "score": 0.92, "selected_reason": "VAT nearby"},
                    {"value": "ABC Distribution", "score": 0.84, "selected_reason": "header candidate"},
                ],
                "customer_candidates": [],
            }
        },
    )


def _erp_json():
    from app.core.schemas import AmountsERP, ERPInvoiceJSON, InvoiceERP, Metadata, SupplierERP

    return ERPInvoiceJSON(
        supplier=SupplierERP(),
        invoice=InvoiceERP(),
        amounts=AmountsERP(),
        validation=ValidationResult(status="needs_review", is_valid=False),
        metadata=Metadata(ocr_engine="test", source_file="test.png"),
    )


def _erp_export():
    from app.core.schemas import ERPFlatExport

    return ERPFlatExport(validation_status="needs_review")
