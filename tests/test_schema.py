from datetime import date

from app.core.schemas import ExtractedInvoiceFields, ValidationResult
from app.services.erp_mapper import build_erp_json, map_to_flat_erp


def test_erp_json_schema_and_flat_mapping():
    fields = ExtractedInvoiceFields(
        supplier_name="ABC Services",
        supplier_tax_id="1234567A",
        invoice_number="FAC-2026-0015",
        invoice_date=date(2026, 6, 12),
        due_date=date(2026, 7, 12),
        currency="TND",
        amount_ht=1000.0,
        tva_amount=190.0,
        amount_ttc=1190.0,
        tax_rate=19.0,
    )
    erp_json = build_erp_json(
        fields=fields,
        validation=ValidationResult(),
        source_file="invoice.pdf",
        ocr_engine="PaddleOCR",
        confidence=0.91,
    )
    payload = erp_json.model_dump(mode="json")
    assert payload["document_type"] == "invoice"
    assert payload["supplier"]["name"] == "ABC Services"
    assert payload["amounts"]["ttc"] == 1190.0

    flat = map_to_flat_erp(erp_json)
    assert flat.vendor_name == "ABC Services"
    assert flat.invoice_ref == "FAC-2026-0015"
    assert flat.validation_status == "valid"
