from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any

from app.core.schemas import ExtractedInvoiceFields, LineItem, ValidationResult
from app.services.erp_readiness import assess_erp_readiness
from app.services.financial_reasoner import reason_financials
from app.services.llm_correction_gate import normalize_field_name
from app.services.llm_correction_models import LLMCorrectionReview
from app.services.row_validation_engine import summarize_rows, validate_rows
from app.services.validator import validate_invoice


def build_hybrid_candidate(response: Any, accepted_reviews: list[LLMCorrectionReview]) -> dict[str, Any]:
    fields: ExtractedInvoiceFields = response.detected_fields.model_copy(deep=True)
    line_items = list(fields.line_items)
    applied: list[dict[str, Any]] = []
    for review in accepted_reviews:
        proposal = review.proposal
        field = normalize_field_name(proposal.field)
        if field.startswith("line_item") or field == "line_items":
            changed = _apply_line_item(line_items, proposal)
        else:
            changed = _apply_scalar(fields, field, proposal.proposed_value)
        if changed:
            applied.append(review.model_dump())
    fields.line_items = line_items
    row_validation = validate_rows(line_items)
    row_summary = summarize_rows(row_validation)
    financial = reason_financials(fields, line_items, document_type=getattr(response.document_classification, "document_type", "invoice"))
    validation = validate_invoice(fields, None, getattr(response.document_classification, "document_type", "invoice"))
    validation.errors.extend(financial.get("financial_errors") or [])
    validation.warnings.extend(financial.get("financial_warnings") or [])
    confidence = _hybrid_confidence(response, accepted_reviews, financial, row_summary)
    readiness = assess_erp_readiness(fields, row_summary=row_summary, financial=financial, confidence=confidence)
    if readiness["erp_ready_status"] == "Rejected":
        validation.status = "invalid"
        validation.is_valid = False
    elif readiness["erp_ready_status"] == "Needs Review" and validation.status == "valid":
        validation.status = "needs_review"
        validation.is_valid = False
    return {
        "fields": fields,
        "line_items": line_items,
        "validation": validation.model_dump(mode="json") if isinstance(validation, ValidationResult) else validation,
        "row_validation": row_validation,
        "financial_reasoning": financial,
        "erp_readiness": readiness,
        "applied_reviews": applied,
        "improves_safely": _improves_safely(response, validation, readiness, financial),
    }


def clone_response_with_hybrid_candidate(response: Any, candidate: dict[str, Any]):
    cloned = response.model_copy(deep=True)
    fields = candidate["fields"]
    cloned.detected_fields = fields
    cloned.validation = ValidationResult(**candidate["validation"])
    cloned.financial_reasoning = candidate["financial_reasoning"]
    cloned.erp_readiness = candidate["erp_readiness"]
    cloned.line_items_validated = candidate["line_items"]
    cloned.line_items_needs_review = []
    cloned.all_line_items = candidate["line_items"]
    cloned.validated_erp_json = deepcopy(response.validated_erp_json) if response.validated_erp_json else {}
    cloned.validated_erp_json["hybrid_corrected_fields"] = fields.model_dump(mode="json")
    cloned.validated_erp_json["erp_readiness"] = cloned.erp_readiness
    cloned.validated_erp_json["erp_export_allowed"] = cloned.erp_readiness.get("ready", False)
    return cloned


def _apply_scalar(fields: ExtractedInvoiceFields, field: str, value: Any) -> bool:
    if not hasattr(fields, field):
        return False
    if field in {"invoice_date", "due_date"}:
        value = _parse_date(value)
        if value is None:
            return False
    if field in {"amount_ht", "tva_amount", "amount_ttc", "tax_rate"}:
        value = _parse_float(value)
        if value is None:
            return False
    setattr(fields, field, value)
    return True


def _apply_line_item(line_items: list[LineItem], proposal: Any) -> bool:
    value = proposal.proposed_value
    if proposal.operation == "restore_row" and isinstance(value, dict):
        candidate = LineItem(**value)
        if _row_duplicate(line_items, candidate):
            return False
        line_items.append(candidate)
        return True
    if proposal.row_index is None or proposal.row_index < 0 or proposal.row_index >= len(line_items):
        return False
    if not isinstance(value, dict):
        return False
    current = line_items[proposal.row_index].model_copy(deep=True)
    for key in ("description", "quantity", "unit_price", "total", "line_total_ht", "line_total_ttc", "tax_rate"):
        if key in value:
            setattr(current, key, value[key])
    line_items[proposal.row_index] = current
    return True


def _row_duplicate(rows: list[LineItem], candidate: LineItem) -> bool:
    key = ((candidate.description or "").casefold().strip(), candidate.quantity, candidate.unit_price, candidate.total)
    for row in rows:
        if ((row.description or "").casefold().strip(), row.quantity, row.unit_price, row.total) == key:
            return True
    return False


def _hybrid_confidence(response: Any, reviews: list[LLMCorrectionReview], financial: dict[str, Any], row_summary: dict[str, Any]) -> float:
    base = float((response.confidence_breakdown or {}).get("overall_confidence") or 0.0)
    correction = sum(review.proposal.confidence for review in reviews) / len(reviews) if reviews else 0.0
    return round(max(base, correction * 0.45 + financial.get("financial_consistency_score", 0) * 0.35 + row_summary.get("validation_score", 0) * 0.20), 3)


def _improves_safely(response: Any, validation: ValidationResult, readiness: dict[str, Any], financial: dict[str, Any]) -> bool:
    before_readiness = response.erp_readiness or {}
    before_missing = len(before_readiness.get("missing_fields") or [])
    after_missing = len(readiness.get("missing_fields") or [])
    before_errors = len(getattr(response.validation, "errors", []) or []) + len((response.financial_reasoning or {}).get("financial_errors") or [])
    after_errors = len(validation.errors or []) + len(financial.get("financial_errors") or [])
    if after_errors > before_errors:
        return False
    if after_missing < before_missing:
        return True
    if readiness.get("ready") and not before_readiness.get("ready"):
        return True
    return after_errors < before_errors


def _parse_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            if fmt == "%Y-%m-%d":
                return date.fromisoformat(text)
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
