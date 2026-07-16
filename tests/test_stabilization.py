import math

from app.core.schemas import Candidate, ExtractedInvoiceFields, LineItem
from app.services.confidence_engine import calculate_confidence
from app.services.extraction_quality import apply_extraction_quality_gate
from app.services.field_extractor import extract_with_candidates
from app.services.financial_reasoner import reason_financials
from app.services.correction_store import boost_candidates_from_memory, get_correction_memory


def test_french_invoice_number_labels_are_supported():
    samples = [
        "Facture N° FAC-2026-001",
        "Facture Nº FAC-2026-002",
        "Facture No FAC-2026-003",
        "Facture N. FAC-2026-004",
        "Numéro facture FAC-2026-005",
    ]

    for sample in samples:
        fields, _candidates, _confidences, _debug = extract_with_candidates(sample)
        assert fields.invoice_number and fields.invoice_number.startswith("FAC-2026-")


def test_confidence_index_is_bounded_and_labeled_uncalibrated():
    confidence = calculate_confidence(
        ocr=1.4,
        layout=-0.3,
        table=math.nan,
        fields=0.8,
        financial=1.2,
        validation=0.7,
    )

    assert confidence["ocr_confidence"] == 1.0
    assert confidence["layout_confidence"] == 0.0
    assert confidence["confidence_type"] == "uncalibrated_composite_index"
    assert confidence["display_name"] == "Composite Confidence Index"


def test_financial_reasoner_supports_shipping_discount_stamp_tax():
    fields = ExtractedInvoiceFields(amount_ht=100.0, tva_amount=20.0, amount_ttc=127.0)

    result = reason_financials(fields, [], shipping=10.0, discount=5.0, stamp_tax=2.0)

    assert result["checks"]["ht_vat_adjustments_to_ttc"]["expected"] == 127.0
    assert result["financial_errors"] == []


def test_mixed_vat_rates_route_to_review_safely():
    fields = ExtractedInvoiceFields(amount_ht=100.0, tva_amount=15.0, amount_ttc=115.0)
    items = [
        LineItem(description="A", quantity=1, unit_price=50, line_total_ht=50, line_total_ttc=55, tax_rate=10),
        LineItem(description="B", quantity=1, unit_price=50, line_total_ht=50, line_total_ttc=60, tax_rate=20),
    ]

    result = reason_financials(fields, items)

    assert result["checks"]["mixed_vat_rates"]["rates"] == [10.0, 20.0]
    assert any("multiple VAT rates" in warning for warning in result["financial_warnings"])


def test_quality_gate_preserves_review_candidate_for_inconsistent_totals():
    fields = ExtractedInvoiceFields(amount_ht=100.0, tva_amount=20.0, amount_ttc=140.0, tax_rate=20.0)
    candidates = {
        "amount_ht": [Candidate(field="amount_ht", value=100.0, score=0.9, source="totals")],
        "tva_amount": [Candidate(field="tva_amount", value=20.0, score=0.9, source="totals")],
        "amount_ttc": [Candidate(field="amount_ttc", value=140.0, score=0.9, source="totals")],
    }

    result = apply_extraction_quality_gate(fields, candidates, {key: 0.9 for key in candidates})

    assert result.sanitized_fields.amount_ht == 100.0
    assert result.sanitized_fields.tva_amount == 20.0
    assert result.sanitized_fields.amount_ttc == 140.0
    assert result.validation_report["fields"]["financial_gate_results"][2]["preserved_as_review_candidate"] is True


def test_correction_memory_is_tenant_scoped(monkeypatch):
    records = [
        {"tenant_id": "a", "field_name": "supplier_name", "corrected_value": "Tenant A Ltd", "user_action": "edited"},
        {"tenant_id": "b", "field_name": "supplier_name", "corrected_value": "Tenant B Ltd", "user_action": "edited"},
    ]
    monkeypatch.setattr("app.services.correction_store.load_correction_records", lambda: records)

    assert get_correction_memory(tenant_id="a")["known_supplier_names"] == ["Tenant A Ltd"]
    assert get_correction_memory(tenant_id="b")["known_supplier_names"] == ["Tenant B Ltd"]


def test_correction_memory_does_not_inject_party_from_product_text(monkeypatch):
    records = [
        {"tenant_id": "default", "field_name": "supplier_name", "corrected_value": "ACME Ltd", "user_action": "edited"},
    ]
    monkeypatch.setattr("app.services.correction_store.load_correction_records", lambda: records)
    candidates = {}

    boost_candidates_from_memory(candidates, "Description ACME Ltd premium product Total 50")

    assert candidates == {}
