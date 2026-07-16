from datetime import date

from app.core.schemas import ExtractedInvoiceFields, LineItem
from app.services.confidence_engine import calculate_confidence
from app.services.correction_suggestions import suggest_corrections
from app.services.duplicate_detector import detect_duplicates
from app.services.erp_readiness import assess_erp_readiness
from app.services.financial_reasoner import reason_financials
from app.services.fraud_indicators import detect_fraud_indicators
from app.services.row_validation_engine import validate_row


def base_fields(**updates):
    values = {
        "supplier_name": "ACME Medical",
        "invoice_number": "INV-1001",
        "invoice_date": date(2026, 5, 6),
        "currency": "USD",
        "amount_ht": 100.0,
        "tva_amount": 19.0,
        "amount_ttc": 119.0,
        "tax_rate": 19.0,
    }
    values.update(updates)
    return ExtractedInvoiceFields(**values)


def test_row_arithmetic_and_missing_field_are_distinguished():
    valid = validate_row(LineItem(description="Service", quantity=2, unit_price=10, total=20))
    review = validate_row(LineItem(description="Service", quantity=2, unit_price=10, total=None))
    invalid = validate_row(LineItem(description="Service", quantity=2, unit_price=10, total=99))
    assert valid["status"] == "validated"
    assert review["status"] == "needs_review"
    assert invalid["status"] == "invalid"


def test_financial_reasoner_supports_tolerance_and_vat():
    fields = base_fields(amount_ttc=119.02)
    result = reason_financials(fields, [], tolerance=0.05)
    assert result["financially_consistent"] is True
    assert "ht_vat_shipping_discount_to_ttc" in result["checks"]


def test_confidence_is_weighted_and_componentized():
    result = calculate_confidence(ocr=0.9, layout=0.8, table=0.7, fields=0.6, financial=0.5, validation=0.4)
    assert result["overall_confidence"] != round((0.9 + 0.8 + 0.7 + 0.6 + 0.5 + 0.4) / 6, 3)
    assert "financial_confidence" in result


def test_erp_readiness_has_three_business_states():
    fields = base_fields(customer_name="North Clinic")
    consistent = reason_financials(fields, [LineItem(description="A", quantity=10, unit_price=10, total=100)])
    ready = assess_erp_readiness(fields, row_summary={"invalid": 0, "needs_review": 0, "validation_score": 0.95}, financial=consistent, confidence=0.95)
    review = assess_erp_readiness(fields.model_copy(update={"customer_name": None}), row_summary={"invalid": 0, "needs_review": 0, "validation_score": 0.95}, financial=consistent, confidence=0.95)
    rejected = assess_erp_readiness(fields, row_summary={"invalid": 1, "needs_review": 0, "validation_score": 0.2}, financial={"financial_errors": ["bad total"], "financial_consistency_score": 0.2, "financially_consistent": False}, confidence=0.2)
    assert ready["erp_ready_status"] == "ERP Ready"
    assert review["erp_ready_status"] == "Needs Review"
    assert rejected["erp_ready_status"] == "Rejected"


def test_duplicate_detection_and_fraud_indicators_are_non_claims():
    fields = base_fields()
    duplicate = detect_duplicates(fields, [{"document_id": "old-1", "invoice_number": "INV-1001", "supplier_name": "ACME Medical", "invoice_date": date(2026, 5, 6), "amount_ttc": 119.0}])
    fraud = detect_fraud_indicators(fields, financial={"financial_errors": ["VAT mismatch"]}, duplicate=duplicate, validation={"missing_fields": []})
    assert duplicate["possible_duplicate"] is True
    assert fraud["fraud_score"] > 0
    assert "does not claim fraud" in fraud["disclaimer"]


def test_corrections_are_suggestions_only():
    suggestions = suggest_corrections(base_fields(invoice_number="INV-O01"))
    assert suggestions[0]["original"] == "INV-O01"
    assert suggestions[0]["corrected"] == "INV-001"
