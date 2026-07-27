from app.core.schemas import Candidate
from app.services.field_extractor import collect_field_candidates, extract_with_candidates
from app.services.field_extractor import _repair_inconsistent_selected_totals


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



def test_summary_totals_ocr_text_selects_consistent_financial_fields():
    fields, _candidates, _confidences, _debug = extract_with_candidates("""
SUMMARY
VAT
Gross worth
74 237,40
7 423,74
81 661,14
Total
$ 74 237,40
$ 7 423,74
$ 81 661,14
""")

    assert fields.amount_ht == 74237.4
    assert fields.tva_amount == 7423.74
    assert fields.amount_ttc == 81661.14
    assert fields.tax_rate == 10.0


def test_inconsistent_selected_totals_are_repaired_from_consistent_summary_candidates():
    selected = {
        "amount_ht": Candidate(field="amount_ht", value=81661.14, score=0.91, source="bad spatial candidate"),
        "tva_amount": Candidate(field="tva_amount", value=26, score=0.91, source="bad spatial candidate"),
        "amount_ttc": Candidate(field="amount_ttc", value=81661.14, score=0.91, source="bad spatial candidate"),
        "tax_rate": Candidate(field="tax_rate", value=0.03, score=0.91, source="bad inferred candidate"),
    }
    candidates = {
        "amount_ht": [selected["amount_ht"], Candidate(field="amount_ht", value=74237.4, score=0.62, source="stacked totals first amount")],
        "tva_amount": [selected["tva_amount"], Candidate(field="tva_amount", value=7423.74, score=0.62, source="stacked totals middle amount")],
        "amount_ttc": [selected["amount_ttc"], Candidate(field="amount_ttc", value=81661.14, score=0.68, source="stacked totals rightmost/gross amount")],
        "tax_rate": [selected["tax_rate"]],
    }

    _repair_inconsistent_selected_totals(selected, candidates)

    assert selected["amount_ht"].value == 74237.4
    assert selected["tva_amount"].value == 7423.74
    assert selected["amount_ttc"].value == 81661.14
    assert selected["tax_rate"].value == 10.0
