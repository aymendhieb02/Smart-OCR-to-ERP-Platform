from app.core.schemas import ExtractedInvoiceFields, FieldExtractionDetail, ProcessInvoiceResponse
from app.services.producer_invoice_review import apply_producer_review_fields


SOTACIB_FAMILY = "sotacib_kasserine_white_invoice_v1"


def _response(expanded_fields):
    return ProcessInvoiceResponse.model_construct(
        detected_fields=ExtractedInvoiceFields(),
        expanded_fields=expanded_fields,
        field_boxes=[],
    )


def test_explicit_bank_rib_evidence_populates_producer_bank_account_alias():
    source = FieldExtractionDetail(
        value="TND BANKTEST 12345678901234",
        confidence=0.62,
        source="expanded regex",
    )
    response = _response({"bank_rib": source})

    apply_producer_review_fields(response, SOTACIB_FAMILY)

    detail = response.expanded_fields["bank_account"]
    assert detail.value == source.value
    assert detail.source == "expanded regex"
    assert detail.confidence == 0.62


def test_explicit_human_bank_account_correction_keeps_precedence_over_bank_rib():
    response = _response({
        "bank_account": FieldExtractionDetail(value="HUMAN_OVERRIDE", source="human correction"),
        "bank_rib": FieldExtractionDetail(value="TND BANKTEST 12345678901234", source="expanded regex"),
    })

    apply_producer_review_fields(response, SOTACIB_FAMILY)

    assert response.expanded_fields["bank_account"].value == "HUMAN_OVERRIDE"
