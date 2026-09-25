from app.core.schemas import Candidate
from app.services.field_extractor import collect_field_candidates, extract_with_candidates
from app.services.field_extractor import _repair_inconsistent_selected_totals


def tax_candidates_from(source_text: str):
    return collect_field_candidates(source_text).get("tax_rate", [])


def test_unlabeled_numeric_cluster_does_not_infer_financial_fields():
    candidates = tax_candidates_from("""
Total
$100.00
$20.00
$120.00
""")

    assert candidates == []


def test_stacked_totals_drops_implausible_inferred_tax_rate():
    candidates = tax_candidates_from("""
Total
$1.00
$49.00
$50.00
""")

    assert not candidates
    assert all(candidate.value != 4900 for candidate in candidates)



def test_explicit_summary_labels_select_consistent_financial_fields():
    fields, _candidates, _confidences, _debug = extract_with_candidates("""
Subtotal HT 1 000,00
Tax Amount 200,00
Total Including All taxes 1 200,00 EUR
""")

    assert fields.amount_ht == 1000.0
    assert fields.tva_amount == 200.0
    assert fields.amount_ttc == 1200.0
    assert fields.tax_rate == 20.0


def test_inconsistent_selected_totals_are_repaired_from_consistent_summary_candidates():
    selected = {
        "amount_ht": Candidate(field="amount_ht", value=950.0, score=0.30, source="bad spatial candidate"),
        "tva_amount": Candidate(field="tva_amount", value=50.0, score=0.30, source="bad spatial candidate"),
        "amount_ttc": Candidate(field="amount_ttc", value=1150.0, score=0.30, source="bad spatial candidate"),
        "tax_rate": Candidate(field="tax_rate", value=0.03, score=0.30, source="bad inferred candidate"),
    }
    candidates = {
        "amount_ht": [selected["amount_ht"], Candidate(field="amount_ht", value=1000.0, score=0.92, source="subtotal HT explicit label")],
        "tva_amount": [selected["tva_amount"], Candidate(field="tva_amount", value=200.0, score=0.91, source="Tax Amount explicit label")],
        "amount_ttc": [selected["amount_ttc"], Candidate(field="amount_ttc", value=1200.0, score=0.95, source="Total TTC explicit label")],
        "tax_rate": [selected["tax_rate"]],
    }

    _repair_inconsistent_selected_totals(selected, candidates)

    assert selected["amount_ht"].value == 1000.0
    assert selected["tva_amount"].value == 200.0
    assert selected["amount_ttc"].value == 1200.0
    assert selected["tax_rate"].value == 20.0
