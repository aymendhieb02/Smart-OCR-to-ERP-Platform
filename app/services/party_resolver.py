from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.schemas import Candidate
from app.services.semantic_classifier import is_company_candidate_text, is_forbidden_party_name
from app.utils.helpers import strip_accents


@dataclass
class PartyDecision:
    supplier: Candidate | None
    customer: Candidate | None
    debug: dict[str, list[dict[str, object]]]


def resolve_parties(candidates: dict[str, list[Candidate]]) -> PartyDecision:
    supplier_scores = [_score_party_candidate(candidate, "supplier") for candidate in candidates.get("supplier_name", [])]
    customer_scores = [_score_party_candidate(candidate, "customer") for candidate in candidates.get("customer_name", [])]
    supplier_scores = [item for item in supplier_scores if item[1] >= 0.45]
    customer_scores = [item for item in customer_scores if item[1] >= 0.45]

    supplier = _winner(supplier_scores)
    customer = _winner(customer_scores)
    if supplier and customer and _same_party(supplier, customer):
        supplier_score = next(score for cand, score, _reason in supplier_scores if cand is supplier)
        customer_score = next(score for cand, score, _reason in customer_scores if cand is customer)
        if supplier_score >= customer_score:
            customer = None
        else:
            supplier = None

    conflicts = []
    if supplier and customer and _same_party(supplier, customer):
        conflicts.append({"type": "same_party_candidate", "value": supplier.value})
    return PartyDecision(
        supplier=supplier,
        customer=customer,
        debug={
            "supplier_candidates": [_payload(candidate, score, reason) for candidate, score, reason in supplier_scores],
            "customer_candidates": [_payload(candidate, score, reason) for candidate, score, reason in customer_scores],
            "supplier_name": [_payload(candidate, score, reason) for candidate, score, reason in supplier_scores],
            "customer_name": [_payload(candidate, score, reason) for candidate, score, reason in customer_scores],
            "selected_supplier": _payload(supplier, supplier.score, ["selected by party resolver"]) if supplier else None,
            "selected_customer": _payload(customer, customer.score, ["selected by party resolver"]) if customer else None,
            "conflicts": conflicts,
            "rejection_reasons": _rejections(supplier_scores, supplier) + _rejections(customer_scores, customer),
        },
    )


def party_adjusted_score(candidate: Candidate, role: str) -> float:
    _candidate, score, _reason = _score_party_candidate(candidate, role)
    return score


def _score_party_candidate(candidate: Candidate, role: str) -> tuple[Candidate, float, list[str]]:
    value = str(candidate.value or "").strip()
    plain = strip_accents(value).lower()
    source = (candidate.source or "").lower()
    evidence = strip_accents(candidate.evidence_text or "").lower()
    reasons: list[str] = []
    score = float(candidate.score or 0.0)

    if not value or is_forbidden_party_name(value) or not is_company_candidate_text(value):
        return candidate, 0.0, ["rejected: not a safe company candidate"]
    if re.search(r"\b(?:inc|ltd|llc|sarl|sa|sas|corp|company|group|distribution|pharma|medical)\b", plain):
        score += 0.10
        reasons.append("company indicator")
    if "document graph" in source:
        score += 0.08
        reasons.append("graph evidence")
    if "layout" in source or "block" in source:
        score += 0.06
        reasons.append("layout block evidence")
    if "label" in source:
        score += 0.08
        reasons.append("near role label")
    if any(token in evidence for token in ("tax", "mf", "ice", "vat", "email", "phone", "tel", "address", "rue", "street")):
        score += 0.05
        reasons.append("near party business evidence")

    if role == "supplier":
        if any(token in source for token in ("customer", "client", "bill to", "ship to")):
            score -= 0.28
            reasons.append("penalty: customer context")
        if "header" in source or "supplier" in source:
            score += 0.08
            reasons.append("supplier/header context")
    else:
        if any(token in source for token in ("supplier", "seller", "vendor", "header")) and "customer" not in source:
            score -= 0.18
            reasons.append("penalty: supplier/header context")
        if any(token in source for token in ("customer", "client", "bill to", "ship to")):
            score += 0.12
            reasons.append("customer label context")

    if _looks_like_product_or_header(plain):
        score -= 0.45
        reasons.append("penalty: product/table-like text")
    if not reasons:
        reasons.append("base candidate score")
    return candidate, round(max(0.0, min(0.99, score)), 3), reasons


def _winner(scores: list[tuple[Candidate, float, list[str]]]) -> Candidate | None:
    if not scores:
        return None
    candidate, score, reasons = sorted(
        scores,
        key=lambda item: (item[1], 1 if item[0].bbox else 0, 1 if item[0].page is not None else 0),
        reverse=True,
    )[0]
    updated = candidate.model_copy(deep=True)
    updated.score = score
    updated.confidence = score
    updated.source = f"{candidate.source}; party resolver"
    updated.score_breakdown = dict(candidate.score_breakdown or {})
    updated.score_breakdown["party_resolver_score"] = score
    updated.evidence_text = candidate.evidence_text or "; ".join(reasons)
    return updated


def _same_party(first: Candidate, second: Candidate) -> bool:
    return strip_accents(str(first.value or "")).lower() == strip_accents(str(second.value or "")).lower()


def _looks_like_product_or_header(plain: str) -> bool:
    return any(word in plain for word in ("description", "quantity", "qty", "price", "prix", "total", "vat", "tva"))


def _payload(candidate: Candidate, score: float, reasons: list[str]) -> dict[str, object]:
    return {
        "value": candidate.value,
        "score": score,
        "source": candidate.source,
        "evidence_text": candidate.evidence_text,
        "reason": reasons,
        "bbox": candidate.bbox.model_dump(mode="json") if candidate.bbox else None,
        "page": candidate.page,
        "line_index": candidate.line_index,
    }


def _rejections(scores: list[tuple[Candidate, float, list[str]]], selected: Candidate | None) -> list[dict[str, object]]:
    rejected = []
    selected_value = str(selected.value) if selected else None
    for candidate, score, reasons in scores:
        if selected_value is not None and str(candidate.value) == selected_value:
            continue
        rejected.append({"value": candidate.value, "score": score, "reason": reasons})
    return rejected
