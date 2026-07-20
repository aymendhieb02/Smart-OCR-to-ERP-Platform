from __future__ import annotations

from app.core.schemas import BoundingBox, OCRLine
from app.services.line_item_extractor import extract_line_items
from app.services.table_reconstruction_engine import reconstruct_line_items


def block(text: str, x1: float, y1: float, x2: float, y2: float, index: int, page: int = 1) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.95,
        page_number=page,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def simple_header(y: int = 100) -> list[OCRLine]:
    return [
        block("#", 20, y, 35, y + 18, 1),
        block("Description", 60, y, 170, y + 18, 2),
        block("Qty", 360, y, 400, y + 18, 3),
        block("Unit Price", 445, y, 530, y + 18, 4),
        block("VAT", 610, y, 650, y + 18, 5),
        block("Total", 720, y, 780, y + 18, 6),
    ]


def test_simple_bordered_table_rows_validate() -> None:
    blocks = simple_header() + [
        block("1", 22, 135, 35, 153, 7),
        block("Paracetamol 500mg", 60, 135, 220, 153, 8),
        block("2", 370, 135, 390, 153, 9),
        block("10.000", 455, 135, 510, 153, 10),
        block("19", 620, 135, 640, 153, 11),
        block("20.000", 730, 135, 790, 153, 12),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 1
    assert result.rows[0].validation_status == "validated"
    assert result.line_items[0].quantity == 2


def test_borderless_table_without_header_uses_numeric_alignment_for_review() -> None:
    blocks = [
        block("Service A", 60, 135, 180, 153, 1),
        block("2", 370, 135, 390, 153, 2),
        block("10.000", 455, 135, 510, 153, 3),
        block("20.000", 730, 135, 790, 153, 4),
        block("Service B", 60, 170, 180, 188, 5),
        block("3", 370, 170, 390, 188, 6),
        block("5.000", 455, 170, 510, 188, 7),
        block("15.000", 730, 170, 790, 188, 8),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 2
    assert result.regions[0].detection_method == "numeric_alignment"
    assert all("review" in (item.source or "") for item in result.line_items)


def test_wrapped_three_line_description_is_merged() -> None:
    blocks = simple_header() + [
        block("1", 22, 135, 35, 153, 7),
        block("Steel beam", 60, 130, 170, 148, 8),
        block("200 x 100", 60, 150, 150, 168, 9),
        block("Grade A", 60, 170, 130, 188, 10),
        block("4", 370, 138, 390, 156, 11),
        block("25.000", 455, 138, 510, 156, 12),
        block("100.000", 730, 138, 800, 156, 13),
    ]

    items = extract_line_items("", blocks)

    assert len(items) == 1
    assert items[0].description == "Steel beam 200 x 100 Grade A"


def test_reference_on_separate_line_is_preserved() -> None:
    blocks = simple_header() + [
        block("1", 22, 135, 35, 153, 7),
        block("Widget pack", 60, 130, 170, 148, 8),
        block("WID-100", 60, 150, 130, 168, 9),
        block("5", 370, 138, 390, 156, 10),
        block("3.000", 455, 138, 510, 156, 11),
        block("15.000", 730, 138, 790, 156, 12),
    ]

    items = extract_line_items("", blocks)

    assert items[0].reference == "WID-100"
    assert "Widget pack" in (items[0].description or "")


def test_unit_price_header_split_into_two_boxes() -> None:
    blocks = [
        block("Description", 60, 100, 170, 118, 1),
        block("Qty", 360, 100, 400, 118, 2),
        block("Unit", 445, 100, 485, 118, 3),
        block("Price", 490, 100, 535, 118, 4),
        block("Total", 720, 100, 780, 118, 5),
        block("Service", 60, 135, 150, 153, 6),
        block("2", 370, 135, 390, 153, 7),
        block("10.000", 455, 135, 510, 153, 8),
        block("20.000", 730, 135, 790, 153, 9),
    ]

    items = extract_line_items("", blocks)

    assert len(items) == 1
    assert items[0].unit_price == 10


def test_french_headers_are_supported() -> None:
    blocks = [
        block("Désignation", 60, 100, 170, 118, 1),
        block("Qté", 360, 100, 400, 118, 2),
        block("Prix Unitaire", 445, 100, 550, 118, 3),
        block("TVA", 610, 100, 650, 118, 4),
        block("Total TTC", 720, 100, 790, 118, 5),
        block("Produit test", 60, 135, 170, 153, 6),
        block("2", 370, 135, 390, 153, 7),
        block("10,000", 455, 135, 510, 153, 8),
        block("19", 620, 135, 640, 153, 9),
        block("20,000", 730, 135, 790, 153, 10),
    ]

    items = extract_line_items("", blocks)

    assert len(items) == 1
    assert items[0].description == "Produit test"
    assert items[0].tax_rate == 19


def test_missing_quantity_goes_to_review_not_invalid_drop() -> None:
    blocks = simple_header() + [
        block("Consulting", 60, 135, 180, 153, 7),
        block("50.000", 455, 135, 510, 153, 8),
        block("50.000", 730, 135, 790, 153, 9),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 1
    assert result.rows[0].validation_status == "needs_review"
    assert "QUANTITY_MISSING" in result.rows[0].warning_codes


def test_discount_and_vat_columns_are_parsed() -> None:
    blocks = [
        block("Description", 60, 100, 170, 118, 1),
        block("Qty", 350, 100, 390, 118, 2),
        block("Unit Price", 430, 100, 520, 118, 3),
        block("Remise", 555, 100, 625, 118, 4),
        block("TVA", 655, 100, 695, 118, 5),
        block("Total", 735, 100, 790, 118, 6),
        block("Service", 60, 135, 150, 153, 7),
        block("2", 360, 135, 380, 153, 8),
        block("50.000", 445, 135, 510, 153, 9),
        block("5", 580, 135, 600, 153, 10),
        block("19", 665, 135, 690, 153, 11),
        block("95.000", 745, 135, 800, 153, 12),
    ]

    items = extract_line_items("", blocks)

    assert items[0].discount == 5
    assert items[0].tax_rate == 19


def test_subtotal_tax_and_shipping_rows_are_excluded() -> None:
    blocks = simple_header() + [
        block("1", 22, 135, 35, 153, 7),
        block("Product A", 60, 135, 160, 153, 8),
        block("2", 370, 135, 390, 153, 9),
        block("10.000", 455, 135, 510, 153, 10),
        block("20.000", 730, 135, 790, 153, 11),
        block("Subtotal", 600, 170, 680, 188, 12),
        block("20.000", 730, 170, 790, 188, 13),
        block("Shipping", 600, 195, 680, 213, 14),
        block("5.000", 730, 195, 790, 213, 15),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 1
    assert result.regions[0].footer_bbox is not None


def test_legitimate_delivery_service_product_is_not_excluded() -> None:
    blocks = simple_header() + [
        block("1", 22, 135, 35, 153, 7),
        block("Delivery Service", 60, 135, 200, 153, 8),
        block("1", 370, 135, 390, 153, 9),
        block("25.000", 455, 135, 510, 153, 10),
        block("25.000", 730, 135, 790, 153, 11),
    ]

    items = extract_line_items("", blocks)

    assert len(items) == 1
    assert items[0].description == "Delivery Service"


def test_ambiguous_fragment_is_preserved_for_review() -> None:
    blocks = simple_header() + [
        block("Unpriced note", 60, 135, 180, 153, 7),
    ]

    result = reconstruct_line_items(blocks)

    assert not result.line_items
    assert result.unresolved_fragments or result.diagnostics["candidate_row_count"] == 0


def test_table_reconciliation_payload_is_emitted() -> None:
    result = reconstruct_line_items(simple_header() + [
        block("1", 22, 135, 35, 153, 7),
        block("Service", 60, 135, 150, 153, 8),
        block("2", 370, 135, 390, 153, 9),
        block("10.000", 455, 135, 510, 153, 10),
        block("20.000", 730, 135, 790, 153, 11),
    ])

    assert result.reconciliation["line_sum"] == 20
    assert result.reconciliation["reconciliation_status"] == "not_compared"
