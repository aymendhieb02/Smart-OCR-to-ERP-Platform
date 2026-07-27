from app.core.schemas import BoundingBox, OCRLine
from app.services.document_layout import OCRVisualLine, _reconstruct_row_from_text


def _block(text: str, x1: float, y1: float, x2: float, y2: float, index: int) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.95,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def _visual_line(text: str) -> OCRVisualLine:
    block = _block(text, 10, 100, 700, 120, 1)
    return OCRVisualLine(page=1, text=text, bbox=block.bbox, confidence=0.95, blocks=[block])


def test_text_fallback_maps_five_numeric_columns_in_header_order():
    columns = {
        "description": {"center": 100, "label": "Designation"},
        "quantity": {"center": 260, "label": "Quantite"},
        "tax_rate": {"center": 340, "label": "TVA"},
        "discount": {"center": 420, "label": "Remise"},
        "unit_price": {"center": 520, "label": "Prix U HT"},
        "total": {"center": 650, "label": "Total TTC"},
    }

    row = _reconstruct_row_from_text(
        [_visual_line("Formation Premium 1.00 20 0.00 2900 2900")],
        columns,
    )

    assert row is not None
    values = row["values"]
    assert values["quantity"] == 1.0
    assert values["tax_rate"] == 20
    assert values["discount"] == 0.0
    assert values["unit_price"] == 2900
    assert values["total"] == 2900
    assert row["needs_review"] is False


def test_text_fallback_preserves_simple_three_numeric_column_table():
    columns = {
        "description": {"center": 100, "label": "Description"},
        "quantity": {"center": 360, "label": "Quantity"},
        "unit_price": {"center": 500, "label": "Price"},
        "total": {"center": 650, "label": "Total"},
    }

    row = _reconstruct_row_from_text([_visual_line("Service A 3 12.50 37.50")], columns)

    assert row is not None
    values = row["values"]
    assert values["description"] == "Service A"
    assert values["quantity"] == 3
    assert values["unit_price"] == 12.5
    assert values["total"] == 37.5
    assert row["needs_review"] is False


def test_text_fallback_marks_mismatched_numeric_column_count_for_review():
    columns = {
        "description": {"center": 100, "label": "Designation"},
        "quantity": {"center": 260, "label": "Quantite"},
        "tax_rate": {"center": 340, "label": "TVA"},
        "discount": {"center": 420, "label": "Remise"},
        "unit_price": {"center": 520, "label": "Prix U HT"},
        "total": {"center": 650, "label": "Total TTC"},
    }

    row = _reconstruct_row_from_text([_visual_line("Formation Premium 1.00 20 2900 2900")], columns)

    assert row is not None
    assert row["needs_review"] is True
    assert row["values"]["quantity"] == 1.0
    assert row["values"]["tax_rate"] == 20

def test_text_fallback_ignores_leading_row_number_before_five_numeric_columns():
    columns = {
        "description": {"center": 100, "label": "Designation"},
        "quantity": {"center": 260, "label": "Quantite"},
        "tax_rate": {"center": 340, "label": "TVA"},
        "discount": {"center": 420, "label": "Remise"},
        "unit_price": {"center": 520, "label": "Prix U HT"},
        "total": {"center": 650, "label": "Total TTC"},
    }

    row = _reconstruct_row_from_text([_visual_line("1 Formation Premium 1.00 20 0.00 2900 2900")], columns)

    assert row is not None
    values = row["values"]
    assert values["description"] == "Formation Premium"
    assert values["quantity"] == 1.0
    assert values["tax_rate"] == 20
    assert values["discount"] == 0.0
    assert values["unit_price"] == 2900
    assert values["total"] == 2900
    assert row["needs_review"] is False
