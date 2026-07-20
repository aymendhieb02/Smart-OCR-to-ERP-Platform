from __future__ import annotations

from app.core.schemas import BoundingBox, Candidate
from app.services.party_resolver import resolve_parties, _find_candidate_score


def test_party_resolver_does_not_use_identity_for_copied_winners() -> None:
    same = "ACME Company"
    candidates = {
        "supplier_name": [Candidate(field="supplier_name", value=same, score=0.8, source="header supplier")],
        "customer_name": [Candidate(field="customer_name", value=same, score=0.7, source="customer label")],
    }

    decision = resolve_parties(candidates)

    assert (decision.supplier is None) != (decision.customer is None)


def test_party_resolver_handles_empty_candidates() -> None:
    decision = resolve_parties({})

    assert decision.supplier is None
    assert decision.customer is None
    assert decision.debug["decision_reasons"] == []


def test_party_resolver_handles_all_candidates_filtered_out() -> None:
    decision = resolve_parties({
        "supplier_name": [Candidate(field="supplier_name", value="SHIP_TO:", score=0.9, source="test")],
        "customer_name": [Candidate(field="customer_name", value="Unit price", score=0.9, source="test")],
    })

    assert decision.supplier is None
    assert decision.customer is None


def test_party_score_lookup_handles_copied_candidate() -> None:
    original = Candidate(field="supplier_name", value="ACME Company", score=0.7, source="test")
    copied = original.model_copy(deep=True)

    score, match = _find_candidate_score([(original, 0.81, ["reason"])], copied)

    assert score == 0.81
    assert match["strategy"] == "normalized_value"


def test_party_score_lookup_falls_back_without_inventing_high_score() -> None:
    selected = Candidate(field="supplier_name", value="Other Company", score=0.42, confidence=0.43, source="test")

    score, match = _find_candidate_score([], selected)

    assert score == 0.42
    assert match["matched"] is False


def test_party_resolver_returns_top_n_rankings_with_breakdown() -> None:
    decision = resolve_parties({
        "supplier_name": [
            Candidate(
                field="supplier_name",
                value="ACME Medical LLC",
                score=0.72,
                confidence=0.92,
                source="layout supplier/header block",
                evidence_text="Invoice ACME Medical LLC Tax ID 123 Email billing@acme.test",
                bbox=BoundingBox(x1=40, y1=50, x2=260, y2=80),
                page_width=1000,
                page_height=1400,
            ),
            Candidate(field="supplier_name", value="Description Quantity Total", score=0.98, source="products table header"),
        ],
        "customer_name": [
            Candidate(
                field="customer_name",
                value="North Clinic Inc",
                score=0.70,
                confidence=0.90,
                source="layout customer block",
                evidence_text="Bill To North Clinic Inc 55 King Street Phone +1 555 0101",
                bbox=BoundingBox(x1=570, y1=250, x2=810, y2=280),
                page_width=1000,
                page_height=1400,
            ),
        ],
    })

    assert decision.supplier and decision.supplier.value == "ACME Medical LLC"
    assert decision.customer and decision.customer.value == "North Clinic Inc"
    supplier_top = decision.debug["supplier_candidates"][0]
    customer_top = decision.debug["customer_candidates"][0]
    assert supplier_top["score_breakdown"]["tax_nearby"] > 0
    assert supplier_top["selected_reason"]
    assert customer_top["score_breakdown"]["role_label"] > 0
    assert "all_ranked_candidates" in decision.debug


def test_party_resolver_scores_one_pool_for_both_roles() -> None:
    decision = resolve_parties({
        "supplier_name": [
            Candidate(
                field="supplier_name",
                value="Left Header Trading SA",
                score=0.60,
                source="header supplier block",
                evidence_text="Left Header Trading SA MF 1234567A/M/000",
                bbox=BoundingBox(x1=40, y1=80, x2=300, y2=110),
                page_width=1000,
                page_height=1400,
            ),
        ],
        "customer_name": [
            Candidate(
                field="customer_name",
                value="Right Buyer SARL",
                score=0.60,
                source="customer label block",
                evidence_text="Client Right Buyer SARL Rue de Tunis",
                bbox=BoundingBox(x1=610, y1=260, x2=860, y2=290),
                page_width=1000,
                page_height=1400,
            ),
        ],
    })

    supplier_ranking = decision.debug["supplier_candidates"]
    customer_ranking = decision.debug["customer_candidates"]

    assert supplier_ranking[0]["value"] == "Left Header Trading SA"
    assert customer_ranking[0]["value"] == "Right Buyer SARL"
    assert any(item["value"] == "Right Buyer SARL" for item in supplier_ranking)
    assert any(item["value"] == "Left Header Trading SA" for item in customer_ranking)
