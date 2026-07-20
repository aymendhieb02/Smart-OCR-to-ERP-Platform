from app.core.schemas import BoundingBox, OCRLine
from app.services.field_extractor import _add_safe_party_region_candidates
from app.services.line_item_extractor import _detect_table_stop_y


def test_safe_party_region_candidate_does_not_crash_when_labels_are_below_candidate() -> None:
    blocks = [
        OCRLine(
            text="ACME SARL",
            confidence=0.91,
            page_number=1,
            bbox=BoundingBox(x1=380, y1=20, x2=520, y2=44),
        ),
        OCRLine(
            text="Client",
            confidence=0.94,
            page_number=1,
            bbox=BoundingBox(x1=370, y1=80, x2=430, y2=102),
        ),
    ]
    collected = []

    def add(field, value, score, source, block=None):
        collected.append((field, value, score, source, block))

    _add_safe_party_region_candidates(add, blocks)

    assert isinstance(collected, list)


def test_table_stop_y_handles_blocks_without_bboxes() -> None:
    blocks = [
        OCRLine(text="Description Quantity Price Total", confidence=0.9, page_number=1),
        OCRLine(text="Subtotal", confidence=0.9, page_number=1),
    ]

    assert _detect_table_stop_y(blocks, header_y=120) == 140

