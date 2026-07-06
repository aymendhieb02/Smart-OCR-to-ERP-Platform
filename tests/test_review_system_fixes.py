from datetime import date

from app.core.schemas import BoundingBox, ExtractedInvoiceFields, OCRLine, ValidationResult
from app.services.field_extractor import extract_with_candidates
from app.services.line_item_extractor import extract_line_items_from_blocks
from app.services.validation_explainer import build_validation_explanation


def block(text: str, x1: float, y1: float, x2: float, y2: float, index: int) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.9,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def test_seller_and_client_blocks_extract_names_addresses_and_tax_ids():
    text = """
Seller:
ACME Medical Supplies LLC
123 Prairie Street, Dallas TX 75201
Tax ID: US123456789
Client:
North Clinic Inc
44 Summit Road, Austin TX 73301
Tax ID: US987654321
Invoice Number: INV-2013-0001
Date of issue:
04/13/2013
Total
$5 640,17
$ 564,02
$ 6 204,19
"""

    fields, _candidates, _confidences, _debug = extract_with_candidates(text)

    assert fields.supplier_name == "ACME Medical Supplies LLC"
    assert fields.supplier_address == "123 Prairie Street, Dallas TX 75201"
    assert fields.supplier_tax_id == "US123456789"
    assert fields.customer_name == "North Clinic Inc"
    assert fields.customer_address == "44 Summit Road, Austin TX 73301"
    assert fields.customer_tax_id == "US987654321"
    assert fields.invoice_date == date(2013, 4, 13)


def test_stacked_usd_totals_extract_amounts_currency_and_tax_rate():
    text = """
Total
$5 640,17
$ 564,02
$ 6 204,19
"""

    fields, _candidates, _confidences, _debug = extract_with_candidates(text)

    assert fields.amount_ht == 5640.17
    assert fields.tva_amount == 564.02
    assert fields.amount_ttc == 6204.19
    assert fields.currency == "USD"
    assert fields.tax_rate == 10.0


def test_coordinate_line_items_ignore_row_number_and_map_columns():
    blocks = [
        block("No Description Qty Unit Net Price Net Worth VAT Gross Worth", 10, 100, 820, 120, 1),
        block("1", 12, 140, 24, 158, 2),
        block("Grey paper", 80, 140, 210, 158, 2),
        block("6", 300, 140, 322, 158, 2),
        block("Each", 360, 140, 395, 158, 2),
        block("$940,03", 430, 140, 490, 158, 2),
        block("$5 640,17", 525, 140, 610, 158, 2),
        block("10%", 640, 140, 675, 158, 2),
        block("$6 204,19", 710, 140, 805, 158, 2),
    ]

    items = extract_line_items_from_blocks(blocks)

    assert len(items) == 1
    assert items[0].description == "Grey paper"
    assert items[0].quantity == 6
    assert items[0].unit == "Each"
    assert items[0].unit_price == 940.03
    assert items[0].line_total_ht == 5640.17
    assert items[0].tax_rate == 10
    assert items[0].line_total_ttc == 6204.19


def test_validation_explanation_is_user_friendly():
    validation = ValidationResult(
        is_valid=False,
        status="invalid",
        errors=["Total amount TTC is missing"],
        warnings=["Tax rate is missing"],
    )

    explanation = build_validation_explanation(validation)

    assert explanation.status == "invalid"
    assert explanation.blocking_errors[0].field == "amount_ttc"
    assert "Fix the blocking errors" in explanation.suggested_action
