from __future__ import annotations

from app.core.schemas import BoundingBox, DocumentClassification, ExtractedInvoiceFields, OCRLine
from app.services.field_extractor import extract_with_candidates
from app.services.financial_reasoner import reason_financials


def block(text: str, x1: float, y1: float, x2: float, y2: float, index: int, confidence: float = 0.94) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=confidence,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        source="synthetic fixture",
    )


def test_total_including_taxes_anchors_separate_numeric_block_and_preserves_evidence():
    blocks = [
        block("Total Including All taxes", 180, 100, 490, 122, 1),
        block("54 321,09", 890, 101, 995, 123, 2),
        block("EUR", 1002, 101, 1034, 123, 3),
    ]
    text = "\n".join(item.text for item in blocks)

    fields, candidates, _confidences, _debug = extract_with_candidates(
        text, blocks, DocumentClassification(document_type="invoice", confidence=0.9)
    )

    assert fields.amount_ttc == 54321.09
    chosen = next(item for item in candidates["amount_ttc"] if "label-anchored spatial amount" in item.source)
    assert chosen.evidence_text == "54 321,09"
    assert chosen.page == 1
    assert chosen.bbox.x1 == 890
    assert chosen.confidence is not None and chosen.confidence >= 0.9


def test_total_including_taxes_parses_a_different_generic_value():
    fields, _candidates, _confidences, _debug = extract_with_candidates(
        "Total Including All taxes 12 345,67 EUR"
    )
    assert fields.amount_ttc == 12345.67


def test_nearby_unaligned_amount_cannot_override_anchored_total():
    blocks = [
        block("Total Including All taxes", 180, 100, 490, 122, 1),
        block("12 345,67 EUR", 890, 101, 1035, 123, 2),
        block("999,00", 890, 150, 955, 172, 3),
    ]
    fields, _candidates, _confidences, _debug = extract_with_candidates(
        "\n".join(item.text for item in blocks), blocks
    )
    assert fields.amount_ttc == 12345.67


def test_total_in_words_is_used_only_when_spatially_below_total_label_with_currency():
    blocks = [
        block("Total Including All taxes", 320, 300, 610, 322, 1),
        block("Twelve thousand three hundred forty-five euros", 270, 355, 595, 377, 2),
    ]
    fields, candidates, _confidences, _debug = extract_with_candidates(
        "\n".join(item.text for item in blocks), blocks
    )
    assert fields.amount_ttc == 12345.0
    assert any("amount-in-words" in item.source for item in candidates["amount_ttc"])


def test_absent_ht_and_tax_remain_null_despite_line_item_totals_and_tax_rate():
    text = """
    Total Including All taxes 12 345.67 EUR
    Description Qty Unit Price Total
    Generic product 2 6 172.835 12 345.67
    VAT rate 19%
    """
    fields, _candidates, _confidences, _debug = extract_with_candidates(text)
    assert fields.amount_ttc == 12345.67
    assert fields.amount_ht is None
    assert fields.tva_amount is None


def test_explicit_ht_and_explicit_tax_amount_are_still_extracted():
    fields, _candidates, _confidences, _debug = extract_with_candidates(
        "Subtotal HT 100.00 EUR\nTax Amount 20.00 EUR\nTotal TTC 120.00 EUR"
    )
    assert fields.amount_ht == 100.0
    assert fields.tva_amount == 20.0
    assert fields.amount_ttc == 120.0


def test_existing_financial_reasoning_still_validates_explicit_values():
    fields = ExtractedInvoiceFields(amount_ht=100.0, tva_amount=20.0, amount_ttc=120.0, tax_rate=20.0)
    result = reason_financials(fields, [])
    assert result["financially_consistent"] is True
    assert result["financial_errors"] == []
