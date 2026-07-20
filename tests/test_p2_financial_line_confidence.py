from datetime import date

from app.core.schemas import BoundingBox, ExtractedInvoiceFields, LineItem, OCRLine
from app.services.confidence_engine import calculate_confidence
from app.services.extraction_quality import apply_extraction_quality_gate
from app.services.financial_reasoner import reason_financials
from app.services.line_item_extractor import extract_line_items


def block(text: str, x1: float, y1: float, x2: float, y2: float, index: int) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.94,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def test_financial_reasoning_accepts_stamp_discount_and_shipping() -> None:
    fields = ExtractedInvoiceFields(amount_ht=100.0, tva_amount=19.0, amount_ttc=123.6, tax_rate=19.0)

    result = reason_financials(fields, [], shipping=5.0, discount=1.0, stamp_tax=0.6)

    assert result["financially_consistent"] is True
    assert result["checks"]["ht_vat_adjustments_to_ttc"]["expected"] == 123.6
    assert result["financial_errors"] == []


def test_quality_gate_recovers_totals_from_line_sum_and_tax_rate() -> None:
    fields = ExtractedInvoiceFields(amount_ht=None, tva_amount=None, amount_ttc=119.0, tax_rate=19.0)
    fields.line_items = [
        LineItem(description="A", quantity=2, unit_price=20, line_total_ht=40, total=40),
        LineItem(description="B", quantity=3, unit_price=20, line_total_ht=60, total=60),
    ]

    result = apply_extraction_quality_gate(fields, {}, {})

    assert result.sanitized_fields.amount_ht == 100.0
    assert result.sanitized_fields.tva_amount == 19.0
    assert result.sanitized_fields.amount_ttc == 119.0


def test_discount_and_vat_table_columns_are_extracted() -> None:
    blocks = [
        block("#", 20, 100, 35, 118, 1),
        block("Description", 60, 100, 160, 118, 2),
        block("Qty", 360, 100, 395, 118, 3),
        block("Unit Price", 440, 100, 520, 118, 4),
        block("Discount", 560, 100, 635, 118, 5),
        block("VAT", 665, 100, 700, 118, 6),
        block("Total", 740, 100, 790, 118, 7),
        block("1", 22, 135, 35, 153, 8),
        block("Consulting service", 60, 135, 240, 153, 9),
        block("2", 370, 135, 385, 153, 10),
        block("50.000", 455, 135, 510, 153, 11),
        block("5", 585, 135, 600, 153, 12),
        block("19", 675, 135, 692, 153, 13),
        block("95.000", 745, 135, 800, 153, 14),
    ]

    items = extract_line_items("", blocks)

    assert len(items) == 1
    assert items[0].description == "Consulting service"
    assert items[0].quantity == 2
    assert items[0].unit_price == 50
    assert items[0].discount == 5
    assert items[0].tax_rate == 19
    assert items[0].line_total_ttc == 95


def test_invalid_extraction_confidence_is_capped_below_90() -> None:
    result = calculate_confidence(
        ocr=0.99,
        layout=0.98,
        table=0.96,
        fields=0.97,
        financial=0.2,
        validation=0.2,
        erp=0.1,
        validation_status="invalid",
        missing_required_fields=["invoice_date"],
        erp_ready=False,
    )

    assert result["overall_confidence"] <= 0.59
    assert result["confidence_type"] == "calibrated_business_confidence"


def test_valid_confidence_keeps_component_breakdown() -> None:
    result = calculate_confidence(
        ocr=0.91,
        layout=0.88,
        table=0.84,
        fields=0.86,
        financial=0.95,
        validation=0.94,
        erp=0.92,
        validation_status="valid",
        missing_required_fields=[],
        erp_ready=True,
    )

    assert result["overall_confidence"] > 0.85
    assert result["ocr_confidence"] == 0.91
