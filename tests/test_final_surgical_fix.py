import ast
import json
from pathlib import Path

import pytest

from app.core.schemas import BoundingBox, OCRLine
from app.services.bbox_contract import normalize_public_bbox
from app.services.field_extractor import extract_with_candidates
from app.services.line_item_extractor import extract_line_items_from_blocks
from app.services.semantic_classifier import is_company_candidate_text


ROOT = Path(__file__).resolve().parents[1]
SURGICAL_LABEL = Path(
    r"D:\Stage_udgroup\sources\datasets\invoices-and-receipts_ocr_v1"
    r"\invoices-and-receipts_ocr_v1\data\exported\labels"
    r"\test-00000-of-00001-af2d92d1cee28514_000016.json"
)


def _surgical_ocr_lines() -> list[OCRLine]:
    if not SURGICAL_LABEL.exists():
        pytest.skip(f"Local surgical fixture not found: {SURGICAL_LABEL}")
    payload = json.loads(SURGICAL_LABEL.read_text(encoding="utf-8"))
    raw = json.loads(payload["raw_data"])
    boxes = ast.literal_eval(raw["ocr_boxes"])
    lines: list[OCRLine] = []
    for index, (polygon, text_info) in enumerate(boxes):
        text, confidence = text_info
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        lines.append(OCRLine(
            text=text,
            confidence=float(confidence),
            page_number=1,
            line_index=index,
            bbox=BoundingBox(x1=min(xs), y1=min(ys), x2=max(xs), y2=max(ys)),
            source="stored_paddle_fixture",
        ))
    return lines


def test_public_bbox_contract_accepts_zero_flat_dict_and_polygon():
    assert normalize_public_bbox([0, 0, 10, 20]) == BoundingBox(x1=0, y1=0, x2=10, y2=20)
    assert normalize_public_bbox({"x1": 0, "y1": 4, "x2": 12, "y2": 18}) == BoundingBox(x1=0, y1=4, x2=12, y2=18)
    assert normalize_public_bbox([[0, 1], [5, 1], [5, 9], [0, 9]]) == BoundingBox(x1=0, y1=1, x2=5, y2=9)


def test_hyphenated_company_without_legal_suffix_is_candidate():
    assert is_company_candidate_text("Stout-Miller")
    assert not is_company_candidate_text("80207 Samantha Streets Suite 926")
    assert not is_company_candidate_text("Net price")


def test_surgical_invoice_extracts_parties_without_swap():
    lines = _surgical_ocr_lines()
    fields, candidates, _confidences, debug = extract_with_candidates("\n".join(line.text for line in lines), lines)

    assert fields.supplier_name == "Whitaker Ltd"
    assert fields.customer_name == "Stout-Miller"
    assert fields.supplier_tax_id == "931-73-0965"
    assert fields.customer_tax_id == "938-72-5881"
    assert fields.supplier_name != fields.customer_name
    assert any(candidate.value == "Stout-Miller" and candidate.bbox for candidate in candidates["customer_name"])
    assert debug["party_resolver"]["selected_customer"]["value"] == "Stout-Miller"


def test_surgical_invoice_reconstructs_seven_product_rows():
    lines = _surgical_ocr_lines()
    items = extract_line_items_from_blocks(lines)

    assert len(items) == 7
    assert items[0].description == "personal computer"
    assert items[0].quantity == 4
    assert items[0].unit_price == 59
    assert items[0].line_total_ht == 236
    assert items[0].tax_rate == 10
    assert items[0].line_total_ttc == 259.60
    assert "Wireless Mouse" in items[5].description
    assert items[-1].line_total_ttc == 329.97


def test_frontend_bbox_normalizer_supports_contract_shapes():
    script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "function normalizeBbox" in script
    assert "box.bbox || box.page_bbox || box" in script
    assert "Number.isFinite" in script
    assert "bbox.slice(0, 4)" in script
    assert "rejectedBoxes" in script
    assert "nonEmptyArray(response.all_line_items)" in script
