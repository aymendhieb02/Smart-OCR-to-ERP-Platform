from __future__ import annotations

from app.services.party_name_normalizer import compare_party_names, normalize_party_text


def test_inline_address_tail_is_removed_from_party_truth() -> None:
    normalized = normalize_party_text("Perez, Pace and Bailey 9406 Ashley Fords Suite 962 Jacksonfurt, AR 85423")

    assert normalized.canonical_name == "perez pace and bailey"
    assert normalized.address_removed is True
    assert "inline_address_tail_removed" in normalized.warnings


def test_company_name_prediction_matches_truth_with_inline_address() -> None:
    comparison = compare_party_names(
        "Smith, Anderson and Johnson.",
        "Smith, Anderson and Johnson 24142 Lambert Shore Suite 134 Mayoport, IL 75224",
    )

    assert comparison.final_match is True
    assert comparison.match_classification in {"canonical_exact", "partial"}


def test_address_fragment_does_not_match_company_with_military_address() -> None:
    comparison = compare_party_names("FPO AP 94327", "Sims-Olson USS Bell FPO AP 94327")

    assert comparison.final_match is False
