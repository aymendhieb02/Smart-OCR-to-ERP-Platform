from app.utils.helpers import parse_amount, parse_date


def test_tunisian_millimes_and_thousands_are_preserved():
    assert parse_amount("104.751 TND") == 104.751
    assert parse_amount("1.810.576") == 1810.576
    assert parse_amount("1 234,56") == 1234.56
    assert parse_amount("1,234.56") == 1234.56


def test_amount_parser_preserves_source_precision_until_final_rounding():
    unit_price = parse_amount("58,9617")
    quantity = parse_amount("15")

    assert unit_price == 58.9617
    assert quantity == 15
    assert quantity * unit_price == 884.4255
    assert round(quantity * unit_price, 3) == 884.426


def test_date_parsing_formats():
    assert parse_date("06/05/2026").isoformat() == "2026-05-06"
    assert parse_date("2026-05-06").isoformat() == "2026-05-06"
    assert parse_date("6 mai 2026").isoformat() == "2026-05-06"
