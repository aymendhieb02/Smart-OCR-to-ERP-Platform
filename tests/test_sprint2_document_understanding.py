from app.core.schemas import BoundingBox, ExtractedInvoiceFields, OCRLine
from app.services.document_graph import build_document_graph
from app.services.document_layout import group_ocr_lines, reconstruct_tables
from app.services.line_item_extractor import extract_line_items
from app.services.validator import validate_invoice


def ocr(text: str, x1: float, y1: float, x2: float, y2: float, index: int, confidence: float = 0.94) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=confidence,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def test_document_graph_exposes_semantic_blocks_and_neighbors():
    blocks = [
        ocr("Invoice", 520, 20, 610, 40, 1),
        ocr("Supplier", 30, 70, 100, 88, 2),
        ocr("ACME Medical LLC", 30, 96, 220, 116, 3),
        ocr("Bill To", 420, 70, 490, 88, 4),
        ocr("North Clinic Inc", 420, 96, 610, 116, 5),
        ocr("Description", 40, 220, 150, 240, 6),
        ocr("Qty", 390, 220, 430, 240, 7),
        ocr("Price", 500, 220, 560, 240, 8),
        ocr("Total", 650, 220, 710, 240, 9),
    ]

    graph = build_document_graph(blocks)

    assert graph.blocks
    assert any(block["block_type"] == "supplier_block" for block in graph.blocks)
    assert any(block["block_type"] == "customer_block" for block in graph.blocks)
    assert any(node.neighbors for node in graph.nodes)


def test_borderless_table_with_wrapped_description_is_reconstructed():
    blocks = [
        ocr("Reference", 40, 160, 120, 180, 1),
        ocr("Description", 130, 160, 260, 180, 2),
        ocr("Qty", 420, 160, 450, 180, 3),
        ocr("Unit Price", 510, 160, 610, 180, 4),
        ocr("Total", 680, 160, 740, 180, 5),
        ocr("MED-100", 40, 205, 110, 223, 6),
        ocr("Premium Surgical Mask Pack", 130, 202, 340, 220, 7),
        ocr("50 pieces", 130, 224, 220, 242, 8),
        ocr("10", 425, 205, 440, 223, 9),
        ocr("12.50", 525, 205, 580, 223, 10),
        ocr("125.00", 685, 205, 748, 223, 11),
        ocr("MED-200", 40, 255, 110, 273, 12),
        ocr("Thermal Scanner", 130, 255, 270, 273, 13),
        ocr("2", 425, 255, 435, 273, 14),
        ocr("85.00", 525, 255, 580, 273, 15),
        ocr("170.00", 685, 255, 748, 273, 16),
        ocr("Grand Total", 560, 330, 660, 350, 17),
        ocr("295.00", 685, 330, 748, 350, 18),
    ]

    tables = reconstruct_tables(blocks, group_ocr_lines(blocks))
    items = extract_line_items("", blocks)

    assert len(tables) == 1
    assert len(items) == 2
    assert items[0].reference == "MED-100"
    assert items[0].description == "Premium Surgical Mask Pack 50 pieces"
    assert items[0].quantity == 10
    assert items[0].line_total_ttc == 125


def test_missing_quantity_row_is_preserved_for_review():
    blocks = [
        ocr("Item", 60, 120, 110, 140, 1),
        ocr("Description", 140, 120, 250, 140, 2),
        ocr("Price", 520, 120, 575, 140, 3),
        ocr("Total", 680, 120, 740, 140, 4),
        ocr("REF-01", 60, 165, 120, 183, 5),
        ocr("Consulting Service", 140, 165, 300, 183, 6),
        ocr("150.00", 525, 165, 585, 183, 7),
        ocr("150.00", 685, 165, 748, 183, 8),
    ]

    items = extract_line_items("", blocks)

    assert len(items) == 1
    assert items[0].description == "Consulting Service"
    assert items[0].quantity is None
    assert "review" in (items[0].source or "")


def test_missing_total_is_needs_review_not_invalid():
    fields = ExtractedInvoiceFields(
        supplier_name="ACME Medical LLC",
        invoice_number="INV-100",
        currency="USD",
    )

    validation = validate_invoice(fields)

    assert validation.status == "needs_review"
    assert validation.errors == []
