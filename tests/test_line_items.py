from app.services.line_item_extractor import extract_line_items


def test_bank_details_are_rejected_as_line_items():
    text = """
    Product ABC-100 2 10.000 19 20.000
    RIB 05 012 3456789012345678 06
    IBAN TN59 0501 2345 6789 0123 4567 806
    """
    items = extract_line_items(text)
    assert len(items) == 1
    assert items[0].description == "Product"
