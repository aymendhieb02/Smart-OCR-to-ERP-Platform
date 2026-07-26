from app.services.line_item_extractor import parse_line_item


def test_line_item_parser_uses_precise_unit_price_before_final_total_rounding():
    item = parse_line_item("Produit X PRD-1 15 58,9617 0 884,4255")

    assert item is not None
    assert item.quantity == 15
    assert item.unit_price == 58.9617
    assert item.quantity * item.unit_price == 884.4255
    assert item.line_total_ht == 884.426
    assert item.line_total_ttc == 884.4255
