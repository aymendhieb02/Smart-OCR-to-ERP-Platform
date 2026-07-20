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


@dataclass
class RankedPartyCandidate:
    candidate: Candidate
    role: str
    score: float
    score_breakdown: dict[str, float]
    selected_reason: str
    reasons: list[str]
    penalties: list[str]
    original_field: str


def resolve_parties(candidates: dict[str, list[Candidate]]) -> PartyDecision:
    pool = _party_candidate_pool(candidates)
    supplier_ranked = _rank_party_candidates(pool, "supplier")
    customer_ranked = _rank_party_candidates(pool, "customer")
    supplier_scores = [(item.candidate, item.score, item.reasons + item.penalties) for item in supplier_ranked]
    customer_scores = [(item.candidate, item.score, item.reasons + item.penalties) for item in customer_ranked]

    supplier = _winner(supplier_ranked)
    customer = _winner(customer_ranked)
    decision_reasons = []
    if supplier and customer and _same_party(supplier, customer):
        supplier_score, supplier_match = _find_candidate_score(supplier_scores, supplier)
        customer_score, customer_match = _find_candidate_score(customer_scores, customer)
        decision_reasons.append({
            "type": "same_party_resolution",
            "supplier_score": supplier_score,
            "supplier_match": supplier_match,
            "customer_score": customer_score,
            "customer_match": customer_match,
        })
        if supplier_score >= customer_score:
            customer = None
            decision_reasons.append({"type": "same_party_winner", "role": "supplier"})
        else:
            supplier = None
            decision_reasons.append({"type": "same_party_winner", "role": "customer"})

    conflicts = []
    if supplier and customer and _same_party(supplier, customer):
        conflicts.append({"type": "same_party_candidate", "value": supplier.value})
    return PartyDecision(
        supplier=supplier,
        customer=customer,
        debug={
            "supplier_candidates": [_payload_ranked(item) for item in supplier_ranked],
            "customer_candidates": [_payload_ranked(item) for item in customer_ranked],
            "supplier_name": [_payload_ranked(item) for item in supplier_ranked],
            "customer_name": [_payload_ranked(item) for item in customer_ranked],
            "all_ranked_candidates": [_payload_ranked(item) for item in supplier_ranked + customer_ranked],
            "selected_supplier": _payload(supplier, supplier.score, ["selected by deterministic party ranking"]) if supplier else None,
            "selected_customer": _payload(customer, customer.score, ["selected by deterministic party ranking"]) if customer else None,
            "conflicts": conflicts,
            "decision_reasons": decision_reasons,
            "rejection_reasons": _rejections(supplier_scores, supplier) + _rejections(customer_scores, customer),
        },
    )


def party_adjusted_score(candidate: Candidate, role: str) -> float:
    ranked = _score_party_candidate(candidate, role, candidate.field)
    return ranked.score


def _party_candidate_pool(candidates: dict[str, list[Candidate]]) -> list[tuple[Candidate, str]]:
    pool: list[tuple[Candidate, str]] = []
    for field in ("supplier_name", "customer_name"):
        for candidate in candidates.get(field, []):
            pool.append((candidate, field))
    return pool


def _rank_party_candidates(pool: list[tuple[Candidate, str]], role: str) -> list[RankedPartyCandidate]:
    ranked = [_score_party_candidate(candidate, role, original_field) for candidate, original_field in pool]
    ranked = _dedupe_ranked(ranked)
    return sorted(
        ranked,
        key=lambda item: (
            item.score,
            1 if item.candidate.bbox else 0,
            _role_position_sort(item.candidate, role),
            -(item.candidate.line_index or 9999),
        ),
        reverse=True,
    )[:10]


def _score_party_candidate(candidate: Candidate, role: str, original_field: str | None = None) -> RankedPartyCandidate:
    value = str(candidate.value or "").strip()
    plain = strip_accents(value).lower()
    source = (candidate.source or "").lower()
    evidence = strip_accents(candidate.evidence_text or "").lower()
    context = " ".join((source, evidence, plain))
    reasons: list[str] = []
    penalties: list[str] = []
    breakdown = {
        "base_candidate_score": min(0.22, max(0.0, float(candidate.score or 0.0)) * 0.22),
        "company_plausibility": 0.0,
        "legal_suffix": 0.0,
        "tax_nearby": 0.0,
        "contact_nearby": 0.0,
        "address_nearby": 0.0,
        "logo_or_header_proximity": 0.0,
        "semantic_block": 0.0,
        "ocr_confidence": 0.0,
        "font_emphasis": 0.0,
        "invoice_title_proximity": 0.0,
        "layout_position": 0.0,
        "role_label": 0.0,
        "multilingual_label": 0.0,
        "repetition": 0.0,
        "metadata_consistency": 0.0,
        "negative_context": 0.0,
        "duplicate_penalty": 0.0,
        "generic_text_penalty": 0.0,
    }

    if (
        not value
        or is_forbidden_party_name(value)
        or _is_label_only_party_text(plain)
        or _looks_like_address_or_contact(plain)
        or _looks_like_footer_or_payment(plain, source, evidence)
        or not is_company_candidate_text(value)
    ):
        return RankedPartyCandidate(candidate, role, 0.0, breakdown, "rejected: not a safe company candidate", ["rejected: not a safe company candidate"], [], original_field or candidate.field)

    breakdown["company_plausibility"] = _company_plausibility_score(value)
    if breakdown["company_plausibility"]:
        reasons.append("company name plausibility")
    if _has_legal_suffix(plain):
        breakdown["legal_suffix"] = 0.11
        reasons.append("legal/company suffix")
    if any(token in context for token in ("tax", "tax id", "mf", "ice", "vat", "matricule", "identifiant fiscal")):
        breakdown["tax_nearby"] = 0.10
        reasons.append("tax identifier nearby")
    if any(token in context for token in ("email", "e-mail", "@", "phone", "tel", "tél", "mobile", "fax")):
        breakdown["contact_nearby"] = 0.06
        reasons.append("contact evidence nearby")
    if any(token in context for token in ("address", "adresse", "street", "road", "rue", "avenue", "route", "city", "state", "tunisie", "canada")):
        breakdown["address_nearby"] = 0.07
        reasons.append("address evidence nearby")
    if "document graph" in source:
        breakdown["semantic_block"] += 0.08
        reasons.append("document graph evidence")
    if "layout" in source or "block" in source:
        breakdown["semantic_block"] += 0.06
        reasons.append("layout/semantic block evidence")
    if candidate.confidence is not None:
        breakdown["ocr_confidence"] = min(0.05, max(0.0, float(candidate.confidence)) * 0.05)
        reasons.append("OCR/candidate confidence")
    elif candidate.score:
        breakdown["ocr_confidence"] = min(0.04, float(candidate.score) * 0.04)
    if _looks_emphasized(value):
        breakdown["font_emphasis"] = 0.05
        reasons.append("font emphasis")
    if "invoice" in context or "facture" in context:
        breakdown["invoice_title_proximity"] = 0.03
        reasons.append("invoice title nearby")
    position_score = _layout_position_score(candidate, role)
    if position_score:
        breakdown["layout_position"] = position_score
        reasons.append(f"{role} layout position")
    if original_field == f"{role}_name":
        breakdown["role_label"] += 0.06
    if _role_label_present(context, role):
        breakdown["role_label"] += 0.13
        reasons.append(f"{role} label context")
    if _multilingual_role_label_present(context, role):
        breakdown["multilingual_label"] = 0.05
        reasons.append("multilingual party label")
    if _repetition_signal(context, plain):
        breakdown["repetition"] = 0.04
        reasons.append("candidate repeated in nearby context")
    if _metadata_consistency_signal(context):
        breakdown["metadata_consistency"] = 0.04
        reasons.append("consistent with business metadata")

    if role == "supplier":
        if _role_label_present(context, "customer") or any(token in context for token in ("ship to", "livre a", "livré a", "delivered to")):
            breakdown["negative_context"] -= 0.38
            penalties.append("penalty: customer/shipping context")
        if "header" in source or "supplier" in source:
            breakdown["logo_or_header_proximity"] += 0.10
            reasons.append("supplier/header context")
    else:
        if _role_label_present(context, "supplier") or (any(token in source for token in ("supplier", "seller", "vendor", "header")) and "customer" not in source):
            breakdown["negative_context"] -= 0.30
            penalties.append("penalty: supplier/header context")
        if any(token in context for token in ("customer", "client", "bill to", "billed to", "ship to", "acheteur", "destinataire", "livre a", "livré a")):
            breakdown["role_label"] += 0.12
            reasons.append("customer/billing label context")

    if _looks_like_product_or_header(plain):
        breakdown["negative_context"] -= 0.65
        penalties.append("penalty: product table/header text")
    if _looks_like_address_or_contact(evidence):
        breakdown["negative_context"] -= 0.04
        penalties.append("penalty: contact/address-heavy evidence")
    if _looks_like_footer_or_payment(plain, source, evidence):
        breakdown["negative_context"] -= 0.48
        penalties.append("penalty: footer/payment context")
    if _negative_section_context(context):
        breakdown["negative_context"] -= 0.24
        penalties.append("penalty: totals/payment/table section")
    if _generic_company_text(plain):
        breakdown["generic_text_penalty"] -= 0.18
        penalties.append("penalty: generic text")
    if not reasons:
        reasons.append("base candidate score")
    score = round(max(0.0, min(0.99, sum(breakdown.values()))), 3)
    selected_reason = "; ".join((reasons + penalties)[:5])
    return RankedPartyCandidate(candidate, role, score, {key: round(value, 3) for key, value in breakdown.items()}, selected_reason, reasons, penalties, original_field or candidate.field)


def _winner(ranked: list[RankedPartyCandidate]) -> Candidate | None:
    ranked = [item for item in ranked if item.score >= 0.45]
    if not ranked:
        return None
    item = ranked[0]
    candidate = item.candidate
    score = item.score
    updated = candidate.model_copy(deep=True)
    updated.score = score
    updated.confidence = score
    updated.source = f"{candidate.source}; deterministic party ranking"
    updated.score_breakdown = dict(candidate.score_breakdown or {})
    updated.score_breakdown.update(item.score_breakdown)
    updated.score_breakdown["party_resolver_score"] = score
    updated.evidence_text = candidate.evidence_text or item.selected_reason
    return updated


def _same_party(first: Candidate, second: Candidate) -> bool:
    return strip_accents(str(first.value or "")).lower() == strip_accents(str(second.value or "")).lower()


def _find_candidate_score(scores: list[tuple[Candidate, float, list[str]]], selected: Candidate) -> tuple[float, dict[str, object]]:
    for candidate, score, _reason in scores:
        if candidate is selected:
            return score, {"strategy": "identity", "matched": True}
    selected_value = strip_accents(str(selected.value or "")).lower()
    for candidate, score, _reason in scores:
        if strip_accents(str(candidate.value or "")).lower() == selected_value:
            return score, {"strategy": "normalized_value", "matched": True}
    fallback = float(selected.score or selected.confidence or 0.0)
    return fallback, {"strategy": "selected_candidate_score_fallback", "matched": False}


def _looks_like_product_or_header(plain: str) -> bool:
    if any(word in plain for word in ("description", "designation", "quantity", "qty", "qte", "unit price", "unit", "price", "prix", "total", "amount", "vat", "tva", "code produit", "po number", "purchase order", "order number")):
        return True
    if re.search(r"\b[A-Z]{2,}[A-Z0-9]*-[A-Z0-9]+\b", plain, flags=re.IGNORECASE) and re.search(r"\b\d+(?:mg|ml|gr|kg|g|pcs?|piece|pi[eè]ce)\b", plain, flags=re.IGNORECASE):
        return True
    if len(re.findall(r"\d+(?:[,.]\d+)?", plain)) >= 2 and re.search(r"\b(?:mg|ml|roll|case|box|boite|piece|pi[eè]ce)\b", plain, flags=re.IGNORECASE):
        return True
    return False


def _is_label_only_party_text(plain: str) -> bool:
    compact = " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
    return compact in {
        "ship to",
        "shipto",
        "bill to",
        "billto",
        "customer",
        "client",
        "supplier",
        "vendor",
        "seller",
        "address",
        "adresse",
        "bank",
        "phone",
        "email",
        "unit price",
        "quantity",
        "description",
        "ship_to",
        "bill_to",
        "total",
        "amount",
        "po number",
        "purchase order",
        "order number",
    }


def _looks_like_address_or_contact(plain: str) -> bool:
    if re.search(r"@|\b(?:email|e-mail|tel|phone|fax|mobile|gsm)\b", plain):
        return True
    if re.match(r"^\s*(?:address|adresse)\s*[:#-]?\s*\d", plain):
        return True
    if re.search(r"\b(?:iban|rib|swift|bic|bank|banque|account|compte)\b", plain):
        return True
    if re.search(r"\d", plain) and re.search(r"\b(?:street|st|road|rd|avenue|ave|rue|route|suite|unit|apt|zip|postal|city|state|tunisie|tunis|sfax|ariana|canada)\b", plain):
        return True
    if re.search(r"\b(?:fpo|apo|uss)\b", plain) and re.search(r"\d", plain):
        return True
    if re.search(r"\b[A-Z]{2}\s*\d{4,6}\b", plain, flags=re.IGNORECASE) or re.search(r"\b\d{4,6}\s*(?:us|usa|tunisie|canada|france)\b", plain, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[a-z]\d[a-z]\s*\d[a-z]\d|\d{4,6}", plain, flags=re.IGNORECASE):
        return True
    return False


def _looks_like_footer_or_payment(plain: str, source: str, evidence: str) -> bool:
    joined = " ".join((plain, source, evidence))
    return bool(re.search(r"\b(?:thank you|merci|conditions|payment terms|signature|received by|prepared by|recu par|préparé par|reglement|r[eé]glement|penalit|banque|bank details|coordonnees bancaires)\b", joined))


def _looks_like_legal_company(plain: str) -> bool:
    if _has_legal_suffix(plain) or re.search(r"\b(?:group|distribution|pharma|pharmacy|medical|clinic|trading|industries|technologies|solutions|laboratory|logistics)\b", plain):
        return True
    words = [word for word in re.split(r"\s+", plain) if len(word) >= 3]
    return 2 <= len(words) <= 5 and not _looks_like_product_or_header(plain)


def _has_legal_suffix(plain: str) -> bool:
    return bool(re.search(r"\b(?:inc|inc\.|ltd|ltd\.|llc|limited|sarl|s\.a\.r\.l|suarl|sa|s\.a|sas|corp|corporation|company|co\.?|gmbh|bv|nv|plc|spa)\b", plain))


def _company_plausibility_score(value: str) -> float:
    plain = strip_accents(value).lower()
    words = [word for word in re.split(r"\s+", plain) if word]
    if not words:
        return 0.0
    alpha_chars = sum(1 for char in value if char.isalpha())
    total_chars = sum(1 for char in value if not char.isspace())
    alpha_ratio = alpha_chars / max(1, total_chars)
    score = 0.08 if alpha_ratio >= 0.55 else 0.02
    if 1 <= len(words) <= 6:
        score += 0.05
    if _looks_like_legal_company(plain):
        score += 0.07
    if any(word in plain for word in ("invoice", "facture", "date", "page", "total")):
        score -= 0.08
    return max(0.0, min(0.18, score))


def _looks_emphasized(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and sum(1 for char in letters if char.isupper()) / len(letters) >= 0.75


def _layout_position_score(candidate: Candidate, role: str) -> float:
    bbox = candidate.bbox
    if not bbox:
        return 0.0
    page_width = float(candidate.page_width or 0)
    page_height = float(candidate.page_height or 0)
    y_ratio = bbox.y1 / page_height if page_height else 0.0
    x_center = ((bbox.x1 + bbox.x2) / 2) / page_width if page_width else 0.0
    score = 0.0
    if y_ratio and y_ratio <= 0.34:
        score += 0.07
    elif bbox.y1 <= 260:
        score += 0.05
    if role == "supplier":
        if x_center and x_center <= 0.55:
            score += 0.05
    else:
        if x_center and x_center >= 0.38:
            score += 0.06
    return min(0.12, score)


def _role_position_sort(candidate: Candidate, role: str) -> float:
    if not candidate.bbox:
        return 0.0
    if role == "supplier":
        return max(0.0, 10000 - candidate.bbox.y1 - candidate.bbox.x1 * 0.15)
    page_width = float(candidate.page_width or 0)
    right_bonus = candidate.bbox.x1 if page_width else 0
    return max(0.0, 10000 - candidate.bbox.y1 + right_bonus * 0.15)


def _role_label_present(context: str, role: str) -> bool:
    labels = {
        "supplier": ("supplier", "seller", "vendor", "from", "bill from", "fournisseur", "vendeur", "issuer"),
        "customer": ("customer", "client", "bill to", "billed to", "ship to", "sold to", "acheteur", "destinataire", "livre a", "livré a", "facture a", "facturé a"),
    }
    return any(label in context for label in labels[role])


def _multilingual_role_label_present(context: str, role: str) -> bool:
    if role == "supplier":
        return any(label in context for label in ("fournisseur", "vendeur", "المورد", "البائع"))
    return any(label in context for label in ("client", "acheteur", "destinataire", "livre", "العميل", "المشتري"))


def _repetition_signal(context: str, plain: str) -> bool:
    compact = " ".join(plain.split())
    return bool(compact and len(compact) >= 5 and context.count(compact) >= 2)


def _metadata_consistency_signal(context: str) -> bool:
    return any(token in context for token in ("invoice", "facture", "tax", "mf", "ice", "vat", "email", "tel", "phone", "address", "adresse"))


def _negative_section_context(context: str) -> bool:
    return bool(re.search(r"\b(?:subtotal|sous-total|total due|total ttc|amount due|payment|bank|iban|rib|swift|description|quantity|unit price|prix|tva|vat|footer|signature|shipping and handling)\b", context))


def _generic_company_text(plain: str) -> bool:
    compact = " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
    return compact in {"company", "customer", "supplier", "vendor", "seller", "client", "invoice", "facture", "address", "adresse", "po number", "purchase order", "order number"} or len(compact) <= 2


def _dedupe_ranked(ranked: list[RankedPartyCandidate]) -> list[RankedPartyCandidate]:
    best: dict[str, RankedPartyCandidate] = {}
    for item in ranked:
        key = _party_key(item.candidate)
        previous = best.get(key)
        if previous is None or item.score > previous.score:
            best[key] = item
        elif previous:
            previous.score_breakdown["duplicate_penalty"] = min(0.0, previous.score_breakdown.get("duplicate_penalty", 0.0))
    return list(best.values())


def _party_key(candidate: Candidate) -> str:
    return re.sub(r"[^a-z0-9]+", "", strip_accents(str(candidate.value or "")).lower())


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


def _payload_ranked(item: RankedPartyCandidate) -> dict[str, object]:
    payload = _payload(item.candidate, item.score, item.reasons + item.penalties)
    payload["role"] = item.role
    payload["original_field"] = item.original_field
    payload["score_breakdown"] = item.score_breakdown
    payload["selected_reason"] = item.selected_reason
    payload["penalties"] = item.penalties
    return payload


def _rejections(scores: list[tuple[Candidate, float, list[str]]], selected: Candidate | None) -> list[dict[str, object]]:
    rejected = []
    selected_value = str(selected.value) if selected else None
    for candidate, score, reasons in scores:
        if selected_value is not None and str(candidate.value) == selected_value:
            continue
        rejected.append({"value": candidate.value, "score": score, "reason": reasons})
    return rejected
