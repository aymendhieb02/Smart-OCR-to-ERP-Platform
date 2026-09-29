from datetime import date

from app.core.schemas import ExtractedInvoiceFields, LineItem, OCRResult
from app.services.validator import validate_invoice
from app.services.financial_reasoner import reason_financials


def test_valid_amounts_pass_validation():
    fields = ExtractedInvoiceFields(
        invoice_number="FAC-2026-0015",
        invoice_date=date(2026, 6, 12),
        amount_ht=1000.0,
        tva_amount=190.0,
        amount_ttc=1190.0,
        tax_rate=19.0,
        supplier_name="ABC Services",
        currency="TND",
    )
    result = validate_invoice(fields)
    assert result.is_valid is True
    assert result.status == "valid"
    assert result.errors == []


def test_amount_mismatch_fails_validation():
    fields = ExtractedInvoiceFields(
        invoice_number="FAC-2026-0015",
        invoice_date=date(2026, 6, 12),
        amount_ht=1000.0,
        tva_amount=190.0,
        amount_ttc=1180.0,
        tax_rate=19.0,
    )
    result = validate_invoice(fields)
    assert result.is_valid is False
    assert any("Amount mismatch" in error for error in result.errors)


def test_low_confidence_needs_review():
    fields = ExtractedInvoiceFields(
        invoice_number="FAC-2026-0015",
        invoice_date=date(2026, 6, 12),
        amount_ht=1000.0,
        tva_amount=190.0,
        amount_ttc=1190.0,
        tax_rate=19.0,
        supplier_name="ABC Services",
        currency="TND",
    )
    ocr = OCRResult(raw_text="test", engine="PaddleOCR", confidence=0.2)
    result = validate_invoice(fields, ocr)
    assert result.is_valid is False
    assert result.status == "needs_review"
    assert any("Low OCR confidence" in warning for warning in result.warnings)


def test_visible_table_without_parsed_rows_needs_review():
    fields = ExtractedInvoiceFields(
        supplier_name="ABC Services",
        invoice_number="FAC-1",
        invoice_date=date(2026, 6, 12),
        currency="TND",
        amount_ttc=10.0,
    )
    ocr = OCRResult(
        raw_text="Designation Code Produit Qte\n1 Product ABC-100 2 5.000 19 10.000",
        engine="Tesseract",
        confidence=0.9,
    )
    result = validate_invoice(fields, ocr)
    assert result.status == "needs_review"
    assert any("Product table text" in warning for warning in result.warnings)


def test_producer_total_and_line_sum_do_not_require_generic_ttc_or_tax_fields():
    fields = ExtractedInvoiceFields(
        invoice_number="INV-TEST-001",
        invoice_date=date(2026, 1, 4),
        currency="EUR",
        supplier_name="SUPPLIER_TEST",
        amount_ht=None,
        tva_amount=None,
        amount_ttc=None,
        tax_rate=None,
    )
    row = LineItem(
        description="CEMENT_TEST", quantity=20, unit="MT", unit_price=50,
        line_total_ht=1000,
    )

    validation = validate_invoice(
        fields, document_type="invoice", producer_invoice=True, producer_total=1000,
        producer_tax_applicable=False,
    )
    financials = reason_financials(
        fields, [row], document_type="invoice", producer_invoice=True, producer_total=1000,
    )

    assert not any("Total amount TTC is missing" in warning for warning in validation.warnings)
    assert not any("Tax rate is missing" in warning for warning in validation.warnings)
    assert not any("insufficient totals" in warning.lower() for warning in validation.warnings)
    assert "line_sum_to_producer_total" in financials["checks"]
    assert financials["checks"]["line_sum_to_producer_total"]["passed"] is True
    assert not any("insufficient totals" in warning for warning in financials["financial_warnings"])


def test_producer_missing_total_uses_producer_wording_not_ttc_wording():
    result = validate_invoice(
        ExtractedInvoiceFields(invoice_number="INV-TEST-001"),
        document_type="invoice", producer_invoice=True, producer_total=None,
    )

    assert "Producer invoice total is missing" in result.warnings
    assert "Total amount TTC is missing" not in result.warnings


def test_ruspina_invoice_validates_its_total_and_rows_without_generic_tax_requirements():
    fields = ExtractedInvoiceFields(
        invoice_number="INV-TEST-001", invoice_date=date(2026, 1, 4),
        supplier_name="SUPPLIER_TEST", currency="EUR",
        amount_ht=None, tva_amount=None, amount_ttc=None, tax_rate=None,
        line_items=[LineItem(description="CEMENT_TEST", quantity=10, unit="T", unit_price=100, total=1000)],
    )

    validation = validate_invoice(
        fields, document_type="invoice", ruspina_invoice=True, ruspina_total=1000,
    )
    financials = reason_financials(
        fields, fields.line_items, document_type="invoice",
        ruspina_invoice=True, ruspina_total=1000,
    )

    assert not any("Total amount TTC" in warning for warning in validation.warnings)
    assert not any("Tax rate" in warning for warning in validation.warnings)
    assert not any("insufficient totals" in warning.lower() for warning in validation.warnings)
    assert not any("insufficient totals" in warning.lower() for warning in financials["financial_warnings"])
    assert financials["checks"]["line_sum_to_ruspina_total"]["passed"] is True
