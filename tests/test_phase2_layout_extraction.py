from app.core.schemas import BoundingBox, ExtractedInvoiceFields, LineItem, OCRLine
from app.services.document_layout import group_ocr_lines, reconstruct_tables
from app.services.extraction_quality import apply_extraction_quality_gate, validate_line_items
from app.services.field_extractor import extract_with_candidates
from app.services.line_item_extractor import extract_line_items


def ocr(text, x1, y1, x2, y2, index):
    return OCRLine(
        text=text,
        confidence=0.94,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def test_table_header_does_not_become_customer_name_with_layout_candidates():
    blocks = [
        ocr("INVOICE", 600, 20, 720, 45, 1),
        ocr("Bill To", 430, 90, 500, 110, 2),
        ocr("ACME Clinic LLC", 430, 120, 610, 140, 3),
        ocr("Description", 60, 240, 160, 260, 4),
        ocr("Quantity", 450, 240, 530, 260, 5),
        ocr("Price", 560, 240, 620, 260, 6),
        ocr("Total", 680, 240, 735, 260, 7),
    ]
    fields, candidates, _conf, _debug = extract_with_candidates("\n".join(block.text for block in blocks), blocks)

    assert fields.customer_name == "ACME Clinic LLC"
    assert fields.customer_name != "Quantity"
    assert all(candidate.value != "Quantity" for candidate in candidates.get("customer_name", []))


def test_totals_are_preferred_from_totals_block_not_product_line_amounts():
    blocks = [
        ocr("Description", 60, 220, 160, 240, 1),
        ocr("Quantity", 450, 220, 530, 240, 2),
        ocr("Price", 560, 220, 620, 240, 3),
        ocr("Total", 680, 220, 735, 240, 4),
        ocr("01", 20, 260, 40, 280, 5),
        ocr("Medical Supplies", 60, 260, 210, 280, 6),
        ocr("5", 480, 260, 500, 280, 7),
        ocr("10.00", 560, 260, 620, 280, 8),
        ocr("50.00", 680, 260, 740, 280, 9),
        ocr("Subtotal", 560, 520, 630, 540, 10),
        ocr("100.00", 680, 520, 750, 540, 11),
        ocr("VAT 20%", 560, 550, 635, 570, 12),
        ocr("20.00", 680, 550, 750, 570, 13),
        ocr("Amount Due", 560, 580, 660, 600, 14),
        ocr("120.00", 680, 580, 750, 600, 15),
    ]
    fields, _candidates, _conf, _debug = extract_with_candidates("\n".join(block.text for block in blocks), blocks)

    assert fields.amount_ht == 100
    assert fields.tva_amount == 20
    assert fields.amount_ttc == 120


def test_reconstructed_table_keeps_values_in_correct_columns():
    blocks = [
        ocr("Description", 60, 100, 160, 120, 1),
        ocr("Quantity", 440, 100, 520, 120, 2),
        ocr("Price", 560, 100, 620, 120, 3),
        ocr("Total", 680, 100, 740, 120, 4),
        ocr("01", 20, 145, 40, 165, 5),
        ocr("Service A", 60, 145, 160, 165, 6),
        ocr("3", 475, 145, 490, 165, 7),
        ocr("12.50", 560, 145, 620, 165, 8),
        ocr("37.50", 680, 145, 740, 165, 9),
    ]
    tables = reconstruct_tables(blocks, group_ocr_lines(blocks))

    assert len(tables) == 1
    row = tables[0].rows[0]["values"]
    assert row["description"] == "Service A"
    assert row["quantity"] == 3
    assert row["unit_price"] == 12.5
    assert row["total"] == 37.5


def test_invalid_fallback_line_items_cannot_be_validated():
    items = [LineItem(description="cru ing ponge", quantity=2, unit_price=10, line_total_ttc=99, total=99, source="flexible numeric row")]
    valid, review, report = validate_line_items(items)

    assert valid == []
    assert review == items
    assert report[0]["accepted"] is False


def test_consistent_table_line_items_can_be_validated():
    blocks = [
        ocr("Description", 60, 100, 160, 120, 1),
        ocr("Quantity", 440, 100, 520, 120, 2),
        ocr("Price", 560, 100, 620, 120, 3),
        ocr("Total", 680, 100, 740, 120, 4),
        ocr("01", 20, 145, 40, 165, 5),
        ocr("Service A", 60, 145, 160, 165, 6),
        ocr("3", 475, 145, 490, 165, 7),
        ocr("12.50", 560, 145, 620, 165, 8),
        ocr("37.50", 680, 145, 740, 165, 9),
    ]
    items = extract_line_items("", blocks)
    result = apply_extraction_quality_gate(ExtractedInvoiceFields(line_items=items), {}, {})

    assert len(result.line_items_validated) == 1
    assert result.line_items_needs_review == []
    assert result.sanitized_fields.line_items[0].description == "Service A"

def test_consistent_total_candidate_combination_can_recover_from_wrong_selected_total():
    from app.core.schemas import Candidate

    fields = ExtractedInvoiceFields(amount_ht=100, tva_amount=20, amount_ttc=999)
    candidates = {
        "amount_ht": [Candidate(field="amount_ht", value=100, score=0.9, source="totals block HT")],
        "tva_amount": [Candidate(field="tva_amount", value=20, score=0.9, source="totals block VAT")],
        "amount_ttc": [
            Candidate(field="amount_ttc", value=999, score=0.6, source="weak amount"),
            Candidate(field="amount_ttc", value=120, score=0.95, source="totals block amount due"),
        ],
    }

    result = apply_extraction_quality_gate(fields, candidates, {})

    assert result.sanitized_fields.amount_ht == 100
    assert result.sanitized_fields.tva_amount == 20
    assert result.sanitized_fields.amount_ttc == 120
    assert result.validation_report["fields"]["totals_recovery"]["accepted"] is True


def test_consistent_but_flexible_regex_row_still_needs_review():
    items = [LineItem(description="Service A", quantity=2, unit_price=10, line_total_ttc=20, total=20, confidence=0.62, source="flexible numeric row")]
    valid, review, report = validate_line_items(items)

    assert valid == []
    assert review == items
    assert "fallback regex row requires human review" in report[0]["reasons"]
