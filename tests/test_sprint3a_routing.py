from app.core.schemas import BoundingBox, ExtractedInvoiceFields, OCRLine
from app.services.extraction_quality import apply_extraction_quality_gate
from app.services.field_extractor import extract_with_candidates
from app.services.document_layout import reconstruct_tables


def ocr(text, x1, y1, x2, y2, index):
    return OCRLine(
        text=text,
        confidence=0.94,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def test_review_line_items_are_preserved_by_quality_gate():
    fields = ExtractedInvoiceFields(
        line_items=[{
            "description": "Incomplete service",
            "quantity": None,
            "unit_price": 10,
            "total": 10,
            "confidence": 0.6,
            "source": "reconstructed table review",
        }]
    )
    gate = apply_extraction_quality_gate(fields, {}, {})
    assert gate.line_items_validated == []
    assert len(gate.line_items_needs_review) == 1


def test_graph_company_candidate_reaches_party_selector():
    fields, candidates, _confidences, _debug = extract_with_candidates(
        "ACME MEDICAL\nBill To\nNorth Clinic",
        [
            ocr("ACME MEDICAL", 30, 40, 220, 62, 1),
            ocr("Bill To", 420, 40, 490, 62, 2),
            ocr("North Clinic", 420, 70, 600, 92, 3),
        ],
    )
    assert fields.supplier_name == "ACME MEDICAL"
    assert fields.customer_name == "North Clinic"
    assert candidates.get("supplier_name")


def test_address_only_values_remain_rejected():
    fields, _candidates, _confidences, _debug = extract_with_candidates(
        "123 Main Street\n10001",
        [ocr("123 Main Street", 30, 40, 190, 62, 1), ocr("10001", 30, 70, 90, 92, 2)],
    )
    assert fields.supplier_name is None
    assert fields.customer_name is None


def test_repeated_numeric_alignment_creates_low_confidence_table_without_header():
    blocks = [
        ocr("Surgical Mask Pack", 40, 220, 250, 240, 1),
        ocr("10", 430, 220, 450, 240, 2),
        ocr("12.50", 520, 220, 575, 240, 3),
        ocr("125.00", 680, 220, 745, 240, 4),
        ocr("Thermal Scanner", 40, 270, 250, 290, 5),
        ocr("2", 430, 270, 445, 290, 6),
        ocr("85.00", 520, 270, 575, 290, 7),
        ocr("170.00", 680, 270, 745, 290, 8),
    ]
    tables = reconstruct_tables(blocks)
    assert len(tables) == 1
    assert tables[0].confidence < 0.6
    assert len(tables[0].rows) == 2


def test_company_without_legal_suffix_is_safe_party_candidate():
    fields, candidates, _confidences, _debug = extract_with_candidates(
        "Vital Distribution\n15 Rue des Entrepreneurs\nClient\nPharma Plus",
        [
            ocr("Vital Distribution", 30, 40, 220, 62, 1),
            ocr("15 Rue des Entrepreneurs", 30, 70, 240, 92, 2),
            ocr("Client", 420, 40, 480, 62, 3),
            ocr("Pharma Plus", 420, 70, 560, 92, 4),
        ],
    )
    assert candidates.get("supplier_name")
    assert fields.supplier_name == "Vital Distribution"
    assert fields.customer_name == "Pharma Plus"


def test_table_debug_has_single_structured_source():
    blocks = [
        ocr("Description", 40, 100, 160, 120, 1),
        ocr("Qty", 430, 100, 460, 120, 2),
        ocr("Price", 520, 100, 580, 120, 3),
        ocr("Total", 680, 100, 740, 120, 4),
        ocr("Mask Pack", 40, 145, 220, 165, 5),
        ocr("2", 430, 145, 445, 165, 6),
        ocr("5.00", 520, 145, 570, 165, 7),
        ocr("10.00", 680, 145, 740, 165, 8),
    ]
    _fields, _candidates, _confidences, debug = extract_with_candidates("", blocks)
    table_debug = debug["table_extraction_debug"]
    assert set(("table_anchor_candidates", "selected_table_region", "inferred_columns", "raw_candidate_rows", "validated_rows", "review_rows")).issubset(table_debug)
    assert table_debug["raw_candidate_rows"]


def test_text_sequence_table_recovers_rows_without_ocr_boxes():
    blocks = [
        OCRLine(text="Qty Description Unit Price Amount", confidence=0.9, page_number=1, line_index=1),
        OCRLine(text="5.00", confidence=0.9, page_number=1, line_index=2),
        OCRLine(text="Half", confidence=0.9, page_number=1, line_index=3),
        OCRLine(text="35.66", confidence=0.9, page_number=1, line_index=4),
        OCRLine(text="178.30", confidence=0.9, page_number=1, line_index=5),
        OCRLine(text="2.00", confidence=0.9, page_number=1, line_index=6),
        OCRLine(text="Rise since", confidence=0.9, page_number=1, line_index=7),
        OCRLine(text="93.61", confidence=0.9, page_number=1, line_index=8),
        OCRLine(text="187.22", confidence=0.9, page_number=1, line_index=9),
    ]
    from app.services.line_item_extractor import extract_line_items

    items = extract_line_items("\n".join(block.text for block in blocks), blocks)
    assert len(items) == 2
    assert items[0].description == "Half"
    assert items[0].quantity == 5
    assert "review" in (items[0].source or "")
