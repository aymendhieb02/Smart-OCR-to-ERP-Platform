from app.core.schemas import (
    CorrectionMetadata,
    DynamicTableRow,
    ERPInvoiceJSON,
    ExtractedInvoiceFields,
    FieldExtractionDetail,
    Metadata,
    ValidationResult,
    SupplierERP,
    InvoiceERP,
    AmountsERP,
)
from app.services.dynamic_tables import build_dynamic_review_payload


def sample_erp_json(fields: ExtractedInvoiceFields, validation: ValidationResult) -> ERPInvoiceJSON:
    return ERPInvoiceJSON(
        supplier=SupplierERP(name=fields.supplier_name, tax_id=fields.supplier_tax_id),
        invoice=InvoiceERP(number=fields.invoice_number, date=fields.invoice_date, currency=fields.currency),
        amounts=AmountsERP(ht=fields.amount_ht, tva=fields.tva_amount, ttc=fields.amount_ttc),
        validation=validation,
        metadata=Metadata(ocr_engine="test", source_file="sample.png"),
    )


def test_dynamic_tables_include_required_review_tables():
    fields = ExtractedInvoiceFields(
        supplier_name="ABC SARL",
        supplier_tax_id="1234567A/M/000",
        invoice_number="FAC-1",
        currency="TND",
        amount_ttc=120.0,
    )
    expanded = {
        "supplier_name": FieldExtractionDetail(value="ABC SARL", confidence=0.91, source="supplier_block"),
        "supplier_email": FieldExtractionDetail(value="contact@example.com", confidence=0.62, source="expanded regex"),
        "amount_ttc": FieldExtractionDetail(value=120.0, confidence=0.9, source="totals"),
    }
    validation = ValidationResult()

    tables, extraction_layer, erp_layer = build_dynamic_review_payload(
        fields=fields,
        expanded_fields=expanded,
        layout_blocks=[],
        ocr_blocks=[],
        validation=validation,
        erp_json=sample_erp_json(fields, validation),
    )

    table_ids = {table.id for table in tables}
    assert {
        "erp_fields",
        "all_extracted_fields",
        "line_items",
        "ocr_blocks",
        "unmapped_text",
    }.issubset(table_ids)
    assert "all_extracted_fields" in extraction_layer.tables
    assert erp_layer.export_allowed is True


def test_dynamic_rows_mark_erp_required_and_non_erp_fields():
    fields = ExtractedInvoiceFields(supplier_name="ABC SARL", amount_ttc=120.0)
    expanded = {
        "supplier_name": FieldExtractionDetail(value="ABC SARL", confidence=0.91, source="supplier_block"),
        "supplier_email": FieldExtractionDetail(value="contact@example.com", confidence=0.62, source="expanded regex"),
    }
    validation = ValidationResult()

    tables, _extraction_layer, _erp_layer = build_dynamic_review_payload(
        fields=fields,
        expanded_fields=expanded,
        layout_blocks=[],
        ocr_blocks=[],
        validation=validation,
        erp_json=sample_erp_json(fields, validation),
    )

    all_fields = next(table for table in tables if table.id == "all_extracted_fields")
    supplier_name = next(row for row in all_fields.rows if row.key == "supplier_name")
    supplier_email = next(row for row in all_fields.rows if row.key == "supplier_email")
    assert supplier_name.required_for_erp is True
    assert supplier_name.included_in_erp is True
    assert supplier_email.required_for_erp is False
    assert supplier_email.included_in_erp is False


def test_manual_correction_metadata_format():
    row = DynamicTableRow(
        key="amount_ttc",
        label="Total TTC",
        value=120.0,
        correction=CorrectionMetadata(
            original_value=120.0,
            corrected_value=121.0,
            corrected_by="human",
        ),
        status="manually_corrected",
    )

    payload = row.model_dump(mode="json")
    assert payload["status"] == "manually_corrected"
    assert payload["correction"]["original_value"] == 120.0
    assert payload["correction"]["corrected_value"] == 121.0
    assert payload["correction"]["corrected_by"] == "human"
