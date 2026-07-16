from app.core.schemas import BoundingBox, OCRLine
from app.services.document_classifier import classify_document
from app.services.field_extractor import _extract_invoice_number, extract_with_candidates


def line(text: str, x1: float, y1: float, x2: float, y2: float, idx: int) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.95,
        page_number=1,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        line_index=idx,
    )


def test_month_name_metadata_label_value_blocks_are_extracted() -> None:
    blocks = [
        line("Invoice number", 2500, 1500, 2850, 1550, 1),
        line("PO-621303", 3000, 1500, 3300, 1550, 2),
        line("Date", 2500, 1620, 2650, 1670, 3),
        line("Oct. 24, 2023", 3000, 1620, 3400, 1670, 4),
        line("due_date", 2500, 1860, 2750, 1910, 5),
        line("Nov. 16, 2023", 3000, 1860, 3400, 1910, 6),
    ]
    text = "\n".join(block.text for block in blocks)

    fields, candidates, _confidences, _debug = extract_with_candidates(text, blocks, classify_document(text, blocks))

    assert fields.invoice_number == "PO-621303"
    assert fields.invoice_date and fields.invoice_date.isoformat() == "2023-10-24"
    assert fields.due_date and fields.due_date.isoformat() == "2023-11-16"
    assert candidates["invoice_date"]
    assert candidates["due_date"]


def test_invoice_number_label_only_does_not_create_umber_candidate() -> None:
    assert _extract_invoice_number("Invoice number") is None

    blocks = [line("Invoice number", 20, 20, 200, 40, 1)]
    fields, candidates, _confidences, _debug = extract_with_candidates("Invoice number", blocks)

    assert fields.invoice_number is None
    assert all(candidate.value != "umber" for candidate in candidates.get("invoice_number", []))
    assert all(candidate.value != "number" for candidate in candidates.get("invoice_number", []))
