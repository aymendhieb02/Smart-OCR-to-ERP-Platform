from app.core.schemas import BoundingBox, ExtractedInvoiceFields, OCRLine
from app.services.field_extractor import extract_with_candidates
from app.services.line_item_extractor import extract_line_items
from app.services.validator import validate_invoice


def ocr(text, x1, y1, x2, y2, index):
    return OCRLine(
        text=text,
        confidence=0.9,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def test_missing_invoice_date_is_needs_review_not_invalid():
    fields = ExtractedInvoiceFields(invoice_number="INV-1", amount_ttc=100.0, supplier_name="ABC", currency="USD")
    validation = validate_invoice(fields)
    assert validation.status == "needs_review"
    assert validation.errors == []
    assert any("date" in warning.lower() for warning in validation.warnings)


def test_flexible_line_items_without_product_code_are_extracted():
    text = """
    Description Qty Unit Price VAT Total
    Adult Kids Pokemon Pikachu 5 9.95 10 54.73
    Kigurumi Pajama Cosplay Christmas Costume Sleepwear 3 49.96 10 164.87
    """
    items = extract_line_items(text)
    assert len(items) == 2
    assert items[0].description == "Adult Kids Pokemon Pikachu"
    assert items[0].quantity == 5
    assert items[0].unit_price == 9.95
    assert items[0].tax_rate == 10


def test_labeled_supplier_customer_blocks_use_company_not_address():
    blocks = [
        ocr("Supplier", 40, 40, 120, 60, 1),
        ocr("ACME Medical LLC", 40, 75, 230, 95, 2),
        ocr("123 Prairie Street", 40, 105, 230, 125, 3),
        ocr("Tax ID: US123456789", 40, 135, 250, 155, 4),
        ocr("Email: sales@acme.example", 40, 165, 280, 185, 5),
        ocr("Bill To", 500, 40, 590, 60, 6),
        ocr("North Clinic Inc", 500, 75, 690, 95, 7),
        ocr("44 Summit Road", 500, 105, 690, 125, 8),
        ocr("Tax ID: US987654321", 500, 135, 710, 155, 9),
        ocr("Email: ap@north.example", 500, 165, 730, 185, 10),
        ocr("Invoice Date", 500, 220, 650, 240, 11),
        ocr("04/13/2013", 500, 250, 620, 270, 12),
        ocr("Total TTC 120.00 USD", 500, 700, 760, 730, 13),
    ]
    text = "\n".join(block.text for block in blocks)
    fields, _candidates, _confidences, _debug = extract_with_candidates(text, blocks)
    assert fields.supplier_name == "ACME Medical LLC"
    assert fields.supplier_address == "123 Prairie Street"
    assert fields.supplier_tax_id == "US123456789"
    assert fields.supplier_email == "sales@acme.example"
    assert fields.customer_name == "North Clinic Inc"
    assert fields.customer_address == "44 Summit Road"
    assert fields.customer_tax_id == "US987654321"
    assert fields.customer_email == "ap@north.example"
    assert fields.invoice_date.isoformat() == "2013-04-13"
