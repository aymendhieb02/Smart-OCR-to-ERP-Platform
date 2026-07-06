from datetime import date

from app.core.schemas import ExtractedInvoiceFields, OCRResult
from app.services.validator import validate_invoice


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
