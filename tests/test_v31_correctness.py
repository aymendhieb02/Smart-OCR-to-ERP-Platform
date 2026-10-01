from app.core.schemas import BoundingBox, OCRLine
from app.services.field_extractor import extract_with_candidates
from app.services.line_item_extractor import extract_line_items
from app.services.producer_invoice_review import _extract_labeled_details


def _line(text, x1, y1, x2, y2, index):
    return OCRLine(
        text=text,
        confidence=0.96,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        source="synthetic OCR",
    )


def test_producer_total_uses_amount_column_instead_of_unit_price_column():
    lines = [
        _line("Unit Price", 920, 100, 1010, 120, 1),
        _line("Amount", 760, 100, 830, 120, 2),
        _line("Total", 120, 300, 180, 320, 3),
        _line("45,000.00", 760, 300, 830, 320, 4),
        _line("90.00", 920, 300, 980, 320, 5),
    ]

    result = _extract_labeled_details(lines, {"total": (r"total",)})

    assert result["total"].value == 45000.0
    assert result["total"].bbox.x1 == 760


def test_inline_unit_price_after_total_label_yields_to_right_aligned_amount_cell():
    lines = [
        _line("Total", 399, 548, 450, 568, 1),
        _line("90.00", 823, 510, 873, 530, 2),
        _line("45,000.00", 1000, 507, 1084, 526, 3),
    ]

    result = _extract_labeled_details(lines, {"total": (r"^total$",)})

    assert result["total"].value == 45000.0
    assert result["total"].bbox.x1 == 1000


def test_graph_total_label_uses_right_aligned_amount_not_nearby_unit_price():
    blocks = [
        _line("Total", 399, 548, 450, 568, 1),
        _line("90.00", 823, 510, 873, 530, 2),
        _line("45,000.00", 1000, 507, 1084, 526, 3),
        _line("CEMENT TEST 50 KG", 169, 520, 405, 544, 4),
    ]

    fields, _candidates, _confidences, _debug = extract_with_candidates(
        "Total\n90.00\n45,000.00\nCEMENT TEST 50 KG", blocks
    )

    assert fields.amount_ttc == 45000.0


def test_currency_in_commercial_price_context_beats_bank_account_currency():
    text = """IBAN TN590501234567890123456789 TND
Unit Price EUR Amount EUR
Description Quantity Unit Price Amount
SYNTHETIC GOODS 500 90.00 45000.00 EUR
Invoice Total 45000.00 EUR
IBAN TN120001234567890 TND
Company capital and bank account reported in TND"""
    fields, _candidates, _confidences, _debug = extract_with_candidates(text)

    assert fields.currency == "EUR"


def test_real_tnd_commercial_invoice_currency_remains_supported():
    text = """Description Quantity Unit Price Amount
SYNTHETIC GOODS 2 45.00 90.00 TND
Invoice Total 90.00 TND
IBAN TN590501234567890123456789"""
    fields, _candidates, _confidences, _debug = extract_with_candidates(text)

    assert fields.currency == "TND"


def test_packaging_numeric_note_is_not_a_commercial_line_item():
    assert extract_line_items("Packaging: pack of 50 kg, 200 bags, 90.00, 18000.00") == []


def test_company_registration_and_capital_are_not_commercial_line_items():
    assert extract_line_items("Company registration RC 123456, capital 900000, 40.00, 480000.00") == []
    assert extract_line_items("SYNTHETIC MANUFACTURER au capital de 900000 RC 123456 45.00 13500.00") == []


def test_false_rows_are_rejected_while_genuine_commercial_row_remains():
    items = extract_line_items(
        "Packaging: pack of 50 kg, 200 bags, 90.00, 18000.00\n"
        "Company registration RC 123456, capital 900000, 40.00, 480000.00\n"
        "SYNTHETIC GOODS GRADE A 500 T 90.00 45000.00"
    )

    assert len(items) == 1
    assert items[0].description == "SYNTHETIC GOODS GRADE A"
    assert items[0].total == 45000.0


def test_aligned_positioned_row_survives_while_packaging_and_footer_are_rejected():
    blocks = [
        _line("Description", 100, 100, 250, 120, 1),
        _line("Quantity", 600, 100, 680, 120, 2),
        _line("Unit Price", 800, 100, 900, 120, 3),
        _line("Amount", 1000, 100, 1080, 120, 4),
        _line("SYNTHETIC CEMENT PRODUCT", 100, 200, 400, 220, 5),
        _line("500", 600, 200, 650, 220, 6),
        _line("90.00", 800, 200, 850, 220, 7),
        _line("45,000.00", 1000, 200, 1080, 220, 8),
        _line("Colisage 10,000 bags of 50 kg", 100, 300, 470, 320, 9),
        _line("SYNTHETIC COMPANY au capital de 900,000 RC 12345", 100, 400, 600, 420, 10),
    ]

    items = extract_line_items("", blocks)

    assert len(items) == 1
    assert items[0].description == "SYNTHETIC CEMENT PRODUCT"
    assert items[0].quantity == 500.0
    assert items[0].unit_price == 90.0
    assert items[0].total == 45000.0
    assert items[0].source in {"p3 reconstructed table", "aligned OCR commercial row"}


def test_duplicate_native_and_ocr_text_row_is_returned_once_but_distinct_row_remains():
    items = extract_line_items(
        "SYNTHETIC GOODS A 2 90.00 180.00\n"
        "SYNTHETIC GOODS A 2 90.00 180.00\n"
        "SYNTHETIC GOODS A 3 90.00 270.00"
    )

    assert len(items) == 2
    assert [item.quantity for item in items] == [2.0, 3.0]


def test_positioned_image_only_party_and_bank_labels_recover_nearby_values():
    lines = [
        _line("Address", 100, 100, 170, 120, 1),
        _line("SYNTHETIC CLIENT STREET", 190, 100, 380, 120, 2),
        _line("Consignee", 100, 160, 190, 180, 3),
        _line("SYNTHETIC RECEIVER", 210, 160, 380, 180, 4),
        _line("Bank Account", 100, 220, 200, 240, 5),
        _line("TN590501234567890123456789", 220, 220, 470, 240, 6),
    ]
    labels = {
        "client_address": (r"^address$",),
        "consignee": (r"^consignee$",),
        "bank_account": (r"^bank\s*account$",),
    }

    result = _extract_labeled_details(lines, labels)

    assert result["client_address"].value == "SYNTHETIC CLIENT STREET"
    assert result["consignee"].value == "SYNTHETIC RECEIVER"
    assert result["bank_account"].value == "TN590501234567890123456789"


def test_image_only_labeled_recovery_does_not_use_unpositioned_values():
    lines = [
        OCRLine(text="Address", confidence=0.96, page_number=1, line_index=1, source="synthetic OCR"),
        OCRLine(text="DISTANT SYNTHETIC ADDRESS", confidence=0.96, page_number=1, line_index=2, source="synthetic OCR"),
    ]

    assert _extract_labeled_details(lines, {"client_address": (r"^address$",)}) == {}
