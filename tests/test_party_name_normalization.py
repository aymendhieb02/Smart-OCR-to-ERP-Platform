from __future__ import annotations

from app.services.party_name_normalizer import (
    adapt_party_ground_truth,
    compare_party_names,
    normalize_party_text,
)
from scripts import large_benchmark_runner as runner


def test_truth_company_plus_address_matches_company_only_prediction() -> None:
    truth = "Perez, Pace and Bailey\n9406 Ashley Fords Suite 123\nNew York, NY 10001"
    comparison = compare_party_names("Perez, Pace and Bailey", truth)

    assert comparison.strict_exact_match is False
    assert comparison.final_match is True
    assert comparison.match_classification in {"canonical_exact", "partial", "strong_fuzzy"}
    assert comparison.truth is not None
    assert comparison.truth.address_removed is True


def test_legal_suffix_difference_is_tracked_separately() -> None:
    comparison = compare_party_names("Acme Medical", "ACME Medical LLC")

    assert comparison.strict_exact_match is False
    assert comparison.canonical_without_suffix_exact_match is True
    assert comparison.final_match is True


def test_accented_and_unaccented_text_match() -> None:
    comparison = compare_party_names("Societe Generale SARL", "Société Générale SARL")

    assert comparison.final_match is True
    assert comparison.canonical_exact_match is True


def test_multiline_phone_email_and_vat_are_removed() -> None:
    normalized = normalize_party_text("Vital Distribution\nTel: +216 71 123 456\nEmail: x@y.tn\nVAT: 123456")

    assert normalized.canonical_name == "vital distribution"
    assert normalized.contact_removed is True
    assert normalized.tax_id_removed is True


def test_french_address_line_is_removed() -> None:
    normalized = normalize_party_text("PHARMA PLUS\n45 Rue Habib Bourguiba\n3000 Sfax Tunisie")

    assert normalized.canonical_name == "pharma plus"
    assert normalized.address_removed is True


def test_arabic_address_and_contact_markers_are_removed() -> None:
    normalized = normalize_party_text("شركة النور\nشارع الحرية 12\nهاتف 123456")

    assert normalized.canonical_name == "شركة النور"
    assert normalized.address_removed is True
    assert normalized.contact_removed is True


def test_generic_single_token_overlap_does_not_match() -> None:
    comparison = compare_party_names("Global Services", "International Services Group")

    assert comparison.final_match is not True
    assert comparison.match_classification in {"ambiguous", "mismatch"}


def test_different_companies_with_similar_suffixes_do_not_match() -> None:
    comparison = compare_party_names("Blue Lake LLC", "Red River LLC")

    assert comparison.final_match is False
    assert comparison.match_classification == "mismatch"


def test_short_company_names_remain_conservative() -> None:
    comparison = compare_party_names("ABC", "ABC Trading")

    assert comparison.final_match is not True


def test_nested_dictionary_ground_truth_adapter() -> None:
    adapted = adapt_party_ground_truth({"seller": {"name": "North Clinic Inc", "address": "1 Main Street"}})

    assert adapted.canonical_name == "north clinic inc"
    assert adapted.source_schema.startswith("nested_")


def test_array_ground_truth_adapter() -> None:
    adapted = adapt_party_ground_truth(["North Clinic Inc", "1 Main Street"])

    assert adapted.canonical_name == "north clinic inc"
    assert adapted.source_schema == "array"


def test_unsupported_schema_is_reported() -> None:
    adapted = adapt_party_ground_truth(12345)

    assert adapted.canonical_name is None
    assert adapted.source_schema.startswith("unsupported")
    assert adapted.normalization_warnings


def test_strict_metric_false_while_canonical_metric_true_in_benchmark_row() -> None:
    comparison = runner._compare_party_field(
        "Perez, Pace and Bailey",
        "Perez, Pace and Bailey\n9406 Ashley Fords Suite 123",
    )

    assert comparison["strict_match"] is False
    assert comparison["canonical_match"] is True
    assert comparison["match_classification"] in {"canonical_exact", "partial", "strong_fuzzy"}


def test_existing_non_party_field_metrics_remain_unchanged() -> None:
    comparison = runner._compare_field("INV-1", "INV-1", "id")

    assert comparison["normalized_match"] is True
    assert "canonical_match" not in comparison
