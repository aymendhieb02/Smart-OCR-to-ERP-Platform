from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.core.schemas import Candidate, ERPInvoiceJSON, ExtractedInvoiceFields, LineItem
from app.utils.helpers import parse_amount, strip_accents

REQUIRED_CONFIDENCE = {
    "supplier_name": 0.45,
    "customer_name": 0.55,
    "invoice_number": 0.62,
    "purchase_order_number": 0.58,
    "amount_ht": 0.58,
    "tva_amount": 0.58,
    "amount_ttc": 0.62,
    "tax_rate": 0.55,
}

TABLE_HEADER_VALUES = {
    "quantity", "qty", "qte", "unit", "price", "prix", "total", "description",
    "designation", "montant", "tva", "vat", "ht", "ttc", "tax", "subtotal", "sous-total",
}

TECHNICAL_TOKEN = re.compile(r"^(?:acct_|cus_|tok_|pi_|cs_)[A-Za-z0-9_\-]{8,}$", re.IGNORECASE)
BASE64_LIKE = re.compile(r"^[A-Za-z0-9+/=_-]{18,}$")
SHORT_FRAGMENT = re.compile(r"^[A-Za-z]{1,3}$")
INVOICE_REF_PATTERN = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9_./\-]{3,}$", re.IGNORECASE)
TAX_ID_PATTERN = re.compile(r"(?=.*\d)[A-Z0-9/.-]{5,}$", re.IGNORECASE)
REASONABLE_TAX_RATES = {0, 7, 10, 13, 19, 20}


@dataclass
class QualityGateResult:
    sanitized_fields: ExtractedInvoiceFields
    review_candidates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rejected_candidates: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    validation_report: dict[str, Any] = field(default_factory=dict)
    line_items_validated: list[LineItem] = field(default_factory=list)
    line_items_needs_review: list[LineItem] = field(default_factory=list)


def apply_extraction_quality_gate(
    fields: ExtractedInvoiceFields,
    candidates: dict[str, list[Candidate]],
    field_confidences: dict[str, float],
) -> QualityGateResult:
    sanitized = fields.model_copy(deep=True)
    totals_recovery = _recover_consistent_totals(sanitized, candidates, fields.line_items)
    review_candidates: dict[str, list[dict[str, Any]]] = {}
    rejected_candidates: dict[str, list[dict[str, Any]]] = {}
    field_report: dict[str, Any] = {}
    if totals_recovery:
        field_report["totals_recovery"] = totals_recovery

    for field_name in _guarded_field_names():
        value = getattr(sanitized, field_name, None)
        selected_candidate = _selected_candidate(candidates.get(field_name, []), value)
        confidence = field_confidences.get(field_name, selected_candidate.score if selected_candidate else None)
        accepted, reason, breakdown = validate_field_value(field_name, value, selected_candidate, confidence)
        field_report[field_name] = {
            "value": value,
            "accepted": accepted,
            "reason": reason,
            "confidence": confidence,
            "score_breakdown": breakdown,
        }
        review_candidates[field_name] = [_candidate_payload(candidate, field_name) for candidate in candidates.get(field_name, [])]
        rejected_candidates[field_name] = [payload for payload in review_candidates[field_name] if payload.get("rejected")]
        if not accepted:
            setattr(sanitized, field_name, None)
            if selected_candidate:
                rejected_candidates[field_name].append(_candidate_payload(selected_candidate, field_name, reason))

    totals_report = validate_totals(sanitized, sanitized)
    if not totals_report["accepted"]:
        field_report["financial_gate_results"] = [
            _financial_gate_result(field_name, getattr(sanitized, field_name, None), totals_report)
            for field_name in ("amount_ht", "tva_amount", "amount_ttc", "tax_rate")
        ]
        field_report["totals_consistency"] = totals_report

    valid_lines, review_lines, line_report = validate_line_items(fields.line_items)
    sanitized.line_items = valid_lines

    status = "valid"
    warnings = []
    blocking_errors = []
    if any(not item.get("accepted", True) for item in field_report.values() if isinstance(item, dict)):
        status = "needs_review"
        warnings.append("Some extracted fields were withheld from ERP export and require review")
    if not totals_report["accepted"]:
        status = "needs_review"
        warnings.append(totals_report["reason"])
    if review_lines:
        status = "needs_review"
        warnings.append("One or more line items need human review")

    validation_report = {
        "extraction_status": status,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        "fields": field_report,
        "totals": totals_report,
        "line_items": line_report,
    }
    return QualityGateResult(
        sanitized_fields=sanitized,
        review_candidates=review_candidates,
        rejected_candidates=rejected_candidates,
        validation_report=validation_report,
        line_items_validated=valid_lines,
        line_items_needs_review=review_lines,
    )


def validate_field_value(
    field_name: str,
    value: Any,
    candidate: Candidate | None,
    confidence: float | None,
) -> tuple[bool, str | None, dict[str, float]]:
    if value is None or value == "":
        return True, None, {"business_validation": 1.0}
    text = str(value).strip()
    plain = strip_accents(text).lower()
    breakdown = {
        "label_proximity": min(1.0, max(0.0, candidate.score if candidate else 0.0)),
        "layout": _layout_score(candidate),
        "regex": _regex_score(field_name, text),
        "business_validation": 1.0,
        "consistency": 1.0,
    }
    min_confidence = REQUIRED_CONFIDENCE.get(field_name, 0.35)
    if confidence is not None and confidence < min_confidence:
        return False, f"low extraction confidence for {field_name}", breakdown
    if _is_invalid_text_value(field_name, text):
        breakdown["business_validation"] = 0.0
        return False, f"invalid or unsafe value for {field_name}", breakdown
    if field_name in {"invoice_number", "purchase_order_number"} and not INVOICE_REF_PATTERN.search(text):
        breakdown["regex"] = 0.0
        return False, f"weak document reference for {field_name}", breakdown
    if field_name.endswith("tax_id") and not TAX_ID_PATTERN.search(text):
        breakdown["regex"] = 0.0
        return False, f"weak tax id format for {field_name}", breakdown
    if field_name in {"supplier_name", "customer_name"}:
        if len(text) < 4 or sum(char.isalpha() for char in text) < 3:
            return False, f"weak party name for {field_name}", breakdown
        if plain in TABLE_HEADER_VALUES:
            return False, f"table header cannot be {field_name}", breakdown
    return True, None, breakdown


def validate_totals(sanitized: ExtractedInvoiceFields, original: ExtractedInvoiceFields) -> dict[str, Any]:
    ht = original.amount_ht
    tva = original.tva_amount
    ttc = original.amount_ttc
    tax_rate = original.tax_rate
    report = {"accepted": True, "reason": None, "expected_ttc": None, "mismatch": None}
    if ht is not None and tva is not None and ttc is not None:
        expected = round(ht + tva, 3)
        mismatch = round(abs(expected - ttc), 3)
        report.update({"expected_ttc": expected, "mismatch": mismatch})
        if mismatch > max(0.05, abs(ttc) * 0.002):
            report["accepted"] = False
            report["reason"] = f"Totals inconsistent: HT + TVA = {expected}, TTC = {ttc}"
            return report
    if ht and tva is not None:
        computed_rate = round((tva / ht) * 100, 2)
        report["computed_tax_rate"] = computed_rate
        if tax_rate is not None and abs(computed_rate - tax_rate) > 0.75:
            report["accepted"] = False
            report["reason"] = f"Tax rate inconsistent: computed {computed_rate}%, extracted {tax_rate}%"
            return report
    if tax_rate is not None and min(abs(tax_rate - rate) for rate in REASONABLE_TAX_RATES) > 0.5:
        report["accepted"] = False
        report["reason"] = f"Suspicious tax rate: {tax_rate}%"
    return report


def validate_line_items(items: list[LineItem]) -> tuple[list[LineItem], list[LineItem], list[dict[str, Any]]]:
    valid: list[LineItem] = []
    review: list[LineItem] = []
    report: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        accepted, reasons = _validate_line_item(item)
        item_payload = item.model_dump(mode="json")
        item_payload["row_index"] = index
        item_payload["accepted"] = accepted
        item_payload["reasons"] = reasons
        report.append(item_payload)
        if accepted:
            valid.append(item)
        else:
            review.append(item)
    return valid, review, report


def _financial_gate_result(field_name: str, value: Any, totals_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "field": field_name,
        "before": value,
        "after": value,
        "status": "needs_review" if value is not None else "missing",
        "reason": totals_report.get("reason"),
        "preserved_as_review_candidate": value is not None,
    }


def build_validated_erp_json(erp_json: ERPInvoiceJSON, validation_report: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(erp_json.model_dump(mode="json"))
    payload["extraction_status"] = validation_report.get("extraction_status", erp_json.validation_status)
    payload["blocking_errors"] = validation_report.get("blocking_errors", [])
    payload["warnings"] = validation_report.get("warnings", [])
    payload["review_candidates_required"] = payload["extraction_status"] != "valid"
    return payload



def _recover_consistent_totals(
    fields: ExtractedInvoiceFields,
    candidates: dict[str, list[Candidate]],
    line_items: list[LineItem],
) -> dict[str, Any] | None:
    ht_candidates = _numeric_candidate_values(candidates.get("amount_ht", []), fields.amount_ht)
    tva_candidates = _numeric_candidate_values(candidates.get("tva_amount", []), fields.tva_amount)
    ttc_candidates = _numeric_candidate_values(candidates.get("amount_ttc", []), fields.amount_ttc)
    if not ht_candidates or not ttc_candidates:
        return None
    if not tva_candidates:
        tva_candidates = [(None, 0.0, "missing")]
    line_sum = _line_total_sum(line_items)
    best: tuple[float, float | None, float | None, float | None, str] | None = None
    for ht, ht_score, ht_source in ht_candidates:
        for tva, tva_score, tva_source in tva_candidates:
            for ttc, ttc_score, ttc_source in ttc_candidates:
                if ht is None or ttc is None:
                    continue
                if tva is None:
                    expected_tva = round(ttc - ht, 3)
                    if expected_tva < 0:
                        continue
                    tva_value = expected_tva
                    tax_score = 0.25
                else:
                    tva_value = tva
                    tax_score = tva_score
                expected = round(ht + tva_value, 3)
                mismatch = abs(expected - ttc)
                if mismatch > max(0.05, abs(ttc) * 0.003):
                    continue
                line_bonus = 0.0
                if line_sum is not None:
                    if abs(line_sum - ht) <= max(0.05, abs(ht) * 0.02) or abs(line_sum - ttc) <= max(0.05, abs(ttc) * 0.02):
                        line_bonus = 0.4
                    else:
                        line_bonus = -0.2
                score = ht_score + tax_score + ttc_score + line_bonus - mismatch
                source = f"{ht_source} | {tva_source} | {ttc_source}"
                if best is None or score > best[0]:
                    best = (score, ht, tva_value, ttc, source)
    if not best:
        return None
    _score, ht, tva, ttc, source = best
    original = {"amount_ht": fields.amount_ht, "tva_amount": fields.tva_amount, "amount_ttc": fields.amount_ttc}
    if fields.amount_ht == ht and fields.tva_amount == tva and fields.amount_ttc == ttc:
        return None
    fields.amount_ht = ht
    fields.tva_amount = tva
    fields.amount_ttc = ttc
    fields.tax_rate = round((tva / ht) * 100, 2) if ht else fields.tax_rate
    return {
        "accepted": True,
        "reason": "Recovered consistent totals from candidate combinations",
        "source": source,
        "original": original,
        "recovered": {"amount_ht": ht, "tva_amount": tva, "amount_ttc": ttc, "tax_rate": fields.tax_rate},
    }


def _numeric_candidate_values(candidates: list[Candidate], selected_value: float | None) -> list[tuple[float | None, float, str]]:
    values: list[tuple[float | None, float, str]] = []
    if selected_value is not None:
        values.append((selected_value, 0.6, "selected"))
    for candidate in candidates:
        amount = candidate.value if isinstance(candidate.value, (int, float)) else parse_amount(str(candidate.value))
        if amount is None:
            continue
        values.append((float(amount), candidate.score, candidate.source))
    deduped: dict[float | None, tuple[float | None, float, str]] = {}
    for amount, score, source in values:
        key = round(amount, 3) if amount is not None else None
        current = deduped.get(key)
        if current is None or score > current[1]:
            deduped[key] = (amount, score, source)
    return list(deduped.values())


def _line_total_sum(line_items: list[LineItem]) -> float | None:
    totals = [item.line_total_ttc if item.line_total_ttc is not None else item.total for item in line_items]
    totals = [value for value in totals if value is not None]
    if not totals:
        return None
    return round(sum(totals), 3)
def _validate_line_item(item: LineItem) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if "review" in (item.source or "").lower():
        reasons.append("table reconstruction is low-confidence and requires review")
    description = (item.description or "").strip()
    if len(description) < 3 or sum(char.isalpha() for char in description) < 3:
        reasons.append("missing or weak description")
    if _is_invalid_text_value("line_item_description", description):
        reasons.append("description looks like header, footer, or technical token")
    if item.quantity is None or item.quantity <= 0:
        reasons.append("quantity must be greater than zero")
    if item.unit_price is None or item.unit_price < 0:
        reasons.append("unit price must be non-negative")
    total_ht = item.line_total_ht
    total_ttc = item.line_total_ttc if item.line_total_ttc is not None else item.total
    if total_ttc is None or total_ttc < 0:
        reasons.append("line total is missing or negative")
    if item.quantity is not None and item.unit_price is not None:
        expected_ht = round(item.quantity * item.unit_price, 3)
        compare_total = total_ht if total_ht is not None else total_ttc
        if compare_total is not None and abs(expected_ht - compare_total) > max(0.05, abs(compare_total) * 0.05):
            reasons.append(f"line total mismatch: quantity * unit price = {expected_ht}, total = {compare_total}")
    if total_ht is not None and item.tax_amount is not None and total_ttc is not None:
        expected_ttc = round(total_ht + item.tax_amount, 3)
        if abs(expected_ttc - total_ttc) > max(0.05, abs(total_ttc) * 0.01):
            reasons.append(f"line TTC mismatch: HT + tax = {expected_ttc}, TTC = {total_ttc}")
    if item.tax_rate is not None and min(abs(item.tax_rate - rate) for rate in REASONABLE_TAX_RATES) > 0.5:
        reasons.append(f"suspicious line tax rate: {item.tax_rate}%")
    if (item.source or "").lower() == "flexible numeric row" and (item.confidence or 0) < 0.75:
        reasons.append("fallback regex row requires human review")
    return not reasons, reasons


def _candidate_payload(candidate: Candidate, field_name: str, forced_reason: str | None = None) -> dict[str, Any]:
    accepted, reason, breakdown = validate_field_value(field_name, candidate.value, candidate, candidate.score)
    return {
        "field": field_name,
        "value": candidate.value,
        "normalized_value": candidate.normalized_value if candidate.normalized_value is not None else candidate.value,
        "confidence": candidate.confidence if candidate.confidence is not None else candidate.score,
        "bbox": candidate.bbox.model_dump(mode="json") if candidate.bbox else None,
        "page": candidate.page,
        "line_index": candidate.line_index,
        "source": candidate.source,
        "rejected": bool(forced_reason or not accepted),
        "rejection_reason": forced_reason or reason,
        "evidence_text": candidate.evidence_text,
        "score_breakdown": candidate.score_breakdown or breakdown,
    }


def _selected_candidate(candidates: list[Candidate], value: Any) -> Candidate | None:
    if not candidates:
        return None
    if value is not None:
        for candidate in candidates:
            if str(candidate.value) == str(value):
                return candidate
    return sorted(candidates, key=lambda item: item.score, reverse=True)[0]


def _guarded_field_names() -> tuple[str, ...]:
    return (
        "supplier_name", "supplier_tax_id", "supplier_address", "customer_name", "customer_tax_id",
        "invoice_number", "invoice_date", "due_date", "currency", "amount_ht", "tva_amount",
        "amount_ttc", "tax_rate", "purchase_order_number",
    )


def _is_invalid_text_value(field_name: str, value: str) -> bool:
    if not value:
        return False
    plain = strip_accents(value).lower().strip(" :#-_")
    if plain in TABLE_HEADER_VALUES:
        return True
    if TECHNICAL_TOKEN.match(value) or BASE64_LIKE.match(value):
        return True
    if SHORT_FRAGMENT.match(value) and field_name in {"invoice_number", "purchase_order_number", "supplier_name", "customer_name"}:
        return True
    if any(word in plain for word in ("subtotal", "sales tax", "shipping", "amount due", "total due")) and field_name.endswith("name"):
        return True
    if field_name.endswith("name") and any(word in plain for word in ("quantity", "description", "price", "total", "montant")):
        return True
    return False


def _layout_score(candidate: Candidate | None) -> float:
    if not candidate:
        return 0.0
    if candidate.bbox is not None:
        return 0.8
    return 0.4


def _regex_score(field_name: str, value: str) -> float:
    if field_name in {"invoice_number", "purchase_order_number"}:
        return 1.0 if INVOICE_REF_PATTERN.search(value) else 0.2
    if field_name.endswith("tax_id"):
        return 1.0 if TAX_ID_PATTERN.search(value) else 0.2
    if field_name in {"amount_ht", "tva_amount", "amount_ttc", "tax_rate"}:
        return 1.0 if parse_amount(value) is not None else 0.0
    return 0.8
