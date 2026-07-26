from app.services.field_extractor import collect_field_candidates


def tax_candidates_from(source_text: str):
    return collect_field_candidates(source_text).get("tax_rate", [])


def test_stacked_totals_keeps_plausible_inferred_tax_rate():
    candidates = tax_candidates_from("""
Total
$100.00
$20.00
$120.00
""")

    stacked = [candidate for candidate in candidates if candidate.source == "stacked totals inferred tax rate"]
    assert stacked
    assert stacked[0].value == 20.0
    assert stacked[0].score < 0.80


def test_stacked_totals_drops_implausible_inferred_tax_rate():
    candidates = tax_candidates_from("""
Total
$1.00
$49.00
$50.00
""")

    assert not [candidate for candidate in candidates if candidate.source == "stacked totals inferred tax rate"]
    assert all(candidate.value != 4900 for candidate in candidates)
