from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.core.config import settings
from app.services.llm_correction_models import (
    LLMCorrectionDecision,
    LLMCorrectionProposal,
    LLMCorrectionReview,
    LLMEvidencePackage,
    SUPPORTED_FIELDS,
    SUPPORTED_OPERATIONS,
)


_COMPANY_RE = re.compile(r"[A-Za-zÀ-ÿ\u0600-\u06FF0-9][A-Za-zÀ-ÿ\u0600-\u06FF0-9 .&'/-]{2,}")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
_NUMERIC_FIELDS = {"subtotal", "amount_ht", "tax", "amount_tax", "tva_amount", "total", "amount_ttc"}
_FIELD_ALIASES = {
    "supplier": "supplier_name",
    "customer": "customer_name",
    "subtotal": "amount_ht",
    "tax": "tva_amount",
    "amount_tax": "tva_amount",
    "total": "amount_ttc",
}


def review_llm_corrections(response: Any, decision: LLMCorrectionDecision, evidence: LLMEvidencePackage) -> tuple[list[LLMCorrectionReview], list[LLMCorrectionReview]]:
    accepted: list[LLMCorrectionReview] = []
    rejected: list[LLMCorrectionReview] = []
    for proposal in decision.proposals:
        review = _review_one(response, proposal, evidence)
        if review.accepted:
            accepted.append(review)
        else:
            rejected.append(review)
    return accepted, rejected


def normalize_field_name(field: str) -> str:
    clean = (field or "").strip()
    return _FIELD_ALIASES.get(clean, clean)


def current_value(response: Any, field: str) -> Any:
    field = normalize_field_name(field)
    if field.startswith("line_item"):
        return None
    return getattr(response.detected_fields, field, None)


def _review_one(response: Any, proposal: LLMCorrectionProposal, evidence: LLMEvidencePackage) -> LLMCorrectionReview:
    checks: dict[str, Any] = {}
    field = normalize_field_name(proposal.field)
    checks["supported_field"] = proposal.field in SUPPORTED_FIELDS or field in SUPPORTED_FIELDS
    checks["supported_operation"] = proposal.operation in SUPPORTED_OPERATIONS
    checks["confidence_threshold"] = proposal.confidence >= float(settings.llm_resolver_acceptance_threshold or 0.85)
    checks["has_evidence_refs"] = bool(proposal.evidence_refs)
    checks["evidence_refs_exist"] = all(ref in evidence.refs for ref in proposal.evidence_refs)
    checks["proposed_value_present"] = _proposal_supported_by_evidence(proposal, evidence)
    checks["old_value_matches"] = _old_value_matches(response, proposal)
    checks["high_confidence_protected"] = _high_confidence_allowed(response, field, proposal)
    checks["type_specific"] = _type_specific_check(response, field, proposal, evidence)

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return LLMCorrectionReview(proposal=proposal, accepted=False, reason="failed checks: " + ", ".join(failed), checks=checks)
    return LLMCorrectionReview(proposal=proposal, accepted=True, reason="proposal passed evidence and safety checks", checks=checks)


def _proposal_supported_by_evidence(proposal: LLMCorrectionProposal, evidence: LLMEvidencePackage) -> bool:
    value = proposal.proposed_value
    if proposal.operation in {"merge_rows", "split_row"} and isinstance(value, (dict, list)):
        return True
    text = _normalize(str(value or ""))
    if not text:
        return False
    evidence_text = " ".join(item.text for item in evidence.evidence_items if item.ref in set(proposal.evidence_refs))
    normalized_evidence = _normalize(evidence_text)
    if text in normalized_evidence:
        return True
    # Conservative OCR normalization: allow punctuation and whitespace differences.
    compact_value = re.sub(r"[^a-z0-9\u0600-\u06ff]", "", text)
    compact_evidence = re.sub(r"[^a-z0-9\u0600-\u06ff]", "", normalized_evidence)
    return bool(compact_value and compact_value in compact_evidence)


def _old_value_matches(response: Any, proposal: LLMCorrectionProposal) -> bool:
    if proposal.operation == "fill_missing":
        return current_value(response, proposal.field) in (None, "")
    if proposal.old_value in (None, ""):
        return True
    old = current_value(response, proposal.field)
    return _normalize(str(old or "")) == _normalize(str(proposal.old_value or ""))


def _high_confidence_allowed(response: Any, field: str, proposal: LLMCorrectionProposal) -> bool:
    field_confidences = getattr(response, "field_confidences", None) or {}
    confidence = field_confidences.get(field) or field_confidences.get(proposal.field)
    if confidence is None:
        return True
    try:
        deterministic_confidence = float(confidence)
    except (TypeError, ValueError):
        return True
    if deterministic_confidence < 0.90:
        return True
    existing = current_value(response, field)
    if existing in (None, ""):
        return True
    return proposal.operation == "fill_missing" and proposal.confidence >= 0.97


def _type_specific_check(response: Any, field: str, proposal: LLMCorrectionProposal, evidence: LLMEvidencePackage) -> bool:
    value = proposal.proposed_value
    if field in {"supplier_name", "customer_name"}:
        return isinstance(value, str) and bool(_COMPANY_RE.search(value)) and not _inside_bad_region(proposal, evidence)
    if field in {"invoice_date", "due_date"}:
        if not isinstance(value, str) or not _DATE_RE.search(value.strip()):
            return False
        if field == "due_date" and response.detected_fields.invoice_date:
            parsed = _parse_iso_or_numeric(value)
            return parsed is None or parsed >= response.detected_fields.invoice_date
        return True
    if field in _NUMERIC_FIELDS:
        return _coerce_float(value) is not None
    if field == "invoice_number":
        return bool(str(value or "").strip()) and not _inside_bad_region(proposal, evidence)
    if field.startswith("line_item") or field == "line_items":
        return _line_item_check(response, proposal, evidence)
    return True


def _line_item_check(response: Any, proposal: LLMCorrectionProposal, evidence: LLMEvidencePackage) -> bool:
    refs = [item for item in evidence.evidence_items if item.ref in set(proposal.evidence_refs)]
    if not any(item.kind in {"table_row", "rejected_table_row", "ocr_line", "layout_block"} for item in refs):
        return False
    joined = " ".join(item.text.lower() for item in refs)
    if any(label in joined for label in ("subtotal", "sous-total", "total ttc", "tax", "tva", "vat")) and "description" not in joined:
        return False
    if isinstance(proposal.proposed_value, dict):
        qty = _coerce_float(proposal.proposed_value.get("quantity"))
        unit = _coerce_float(proposal.proposed_value.get("unit_price"))
        total = _coerce_float(proposal.proposed_value.get("total") or proposal.proposed_value.get("line_total_ht"))
        if qty is not None and unit is not None and total is not None:
            return abs(qty * unit - total) <= max(0.05, abs(total) * 0.02)
    return True


def _inside_bad_region(proposal: LLMCorrectionProposal, evidence: LLMEvidencePackage) -> bool:
    for item in evidence.evidence_items:
        if item.ref not in set(proposal.evidence_refs):
            continue
        block_type = str((item.metadata or {}).get("block_type") or item.source or "").lower()
        if any(bad in block_type for bad in ("products", "totals", "payment", "footer", "bank")):
            return True
    return False


def _parse_iso_or_numeric(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return date.fromisoformat(text) if fmt == "%Y-%m-%d" else __import__("datetime").datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()
