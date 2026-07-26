from app.core.schemas import BoundingBox, OCRLine
from app.services.document_layout import group_ocr_lines, reconstruct_tables
from app.services.semantic_classifier import classify_node
from app.services.document_graph import DocumentNode


def line(text: str, x1: float, y1: float, x2: float, y2: float, index: int) -> OCRLine:
    return OCRLine(text=text, confidence=0.9, bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2), page_number=1, line_index=index)


def test_noisy_table_header_is_detected_with_fuzzy_keywords():
    blocks = [
        line("Docrlption", 40, 100, 150, 120, 1),
        line("Quantlty", 230, 100, 300, 120, 2),
        line("Unlt Price", 340, 100, 430, 120, 3),
        line("TotaI", 500, 100, 560, 120, 4),
        line("Produit X", 40, 140, 150, 160, 5),
        line("2", 250, 140, 270, 160, 6),
        line("10.00", 360, 140, 420, 160, 7),
        line("20.00", 500, 140, 560, 160, 8),
    ]

    tables = reconstruct_tables(blocks, group_ocr_lines(blocks))

    assert tables
    assert tables[0].rows


def test_semantic_classifier_recognizes_noisy_table_header():
    node = DocumentNode(id="n1", text="Docrlption Quantlty Unlt Price TotaI", normalized_text="docrlption quantlty unlt price totai", bbox=None, confidence=0.9, page=1)

    assert classify_node(node) == "table_header"
