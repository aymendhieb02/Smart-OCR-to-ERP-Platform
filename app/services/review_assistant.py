from __future__ import annotations

from typing import Any

from app.services.review_explanation_builder import build_issue, candidate_evidence


def build_review_assistant(response: Any) -> dict[str, Any]:
    """Generate advisory review guidance without modifying extraction."""
    issues: list[dict[str, Any]] = []
    debug = response.extraction_debug or {}
    party = debug.get("party_resolver") or {}

    issues.extend(_party_issues(response, party, "supplier"))
    issues.extend(_party_issues(response, party, "customer"))
    issues.extend(_line_item_issues(response))
    issues.extend(_financial_issues(response))
    issues.extend(_validation_issues(response))

    confidence = _assistant_confidence(issues, response)
    return {
        "status": "needs_review" if issues else "no_action_needed",
        "confidence": confidence,
        "summary": _summary(issues, response),
        "issues": issues,
        "suggestions": [_suggestion_from_issue(issue) for issue in issues if issue.get("suggested_correction") not in (None, "", [])],
        "financial_reasoning": response.financial_reasoning or {},
        "erp_impact": _erp_impact(response),
        "reviewer_control": "Assistant suggestions are advisory only. No extraction value is changed automatically.",
    }


def _party_issues(response: Any, party: dict[str, Any], role: str) -> list[dict[str, Any]]:
    field_name = f"{role}_name"
    value = getattr(response.detected_fields, field_name, None)
    candidates = party.get(f"{role}_candidates") or party.get(field_name) or []
    top = candidates[0] if candidates else {}
    top_score = _number(top.get("score") or top.get("confidence"))
    missing = not value
    low = top_score is not None and top_score < 0.78
    if not (missing or low):
        return []
    evidence = [candidate_evidence(candidate, index + 1) for index, candidate in enumerate(candidates[:5]) if isinstance(candidate, dict)]
    suggested = evidence[0]["value"] if evidence else None
    title = f"{role.title()} needs review"
    explanation = (
        f"{role.title()} was not detected confidently."
        if missing
        else f"{role.title()} confidence is below the review threshold."
    )
    suspected = "missing_party" if missing else "low_party_confidence"
    return [build_issue(
        issue_type=field_name,
        title=title,
        explanation=explanation,
        confidence=top_score or 0.0,
        suspected_problem=suspected,
        suggested_correction={field_name: suggested} if suggested else None,
        evidence=evidence,
        erp_impact=f"{field_name} is required for reliable vendor/customer mapping.",
    )]


def _line_item_issues(response: Any) -> list[dict[str, Any]]:
    review_rows = response.line_items_needs_review or []
    if not review_rows:
        return []
    evidence = []
    for index, row in enumerate(review_rows[:5]):
        payload = row.model_dump(mode="json") if hasattr(row, "model_dump") else dict(row)
        evidence.append({
            "rank": index + 1,
            "value": payload.get("description"),
            "confidence": payload.get("confidence"),
            "reason": payload.get("source"),
            "row": payload,
        })
    return [build_issue(
        issue_type="line_items",
        title="Product lines need review",
        explanation=f"{len(review_rows)} line item row(s) need reviewer confirmation.",
        confidence=_average(item.get("confidence") for item in evidence),
        suspected_problem="line_item_rows_need_review",
        suggested_correction={"review_rows": [item.get("row") for item in evidence]},
        evidence=evidence,
        financial_reasoning=response.financial_reasoning or {},
        erp_impact="ERP export can be blocked or financially wrong if product rows are wrong.",
    )]


def _financial_issues(response: Any) -> list[dict[str, Any]]:
    reasoning = response.financial_reasoning or {}
    errors = reasoning.get("financial_errors") or []
    warnings = reasoning.get("financial_warnings") or []
    if not errors and not warnings:
        return []
    checks = reasoning.get("checks") or {}
    evidence = [
        {"rank": index + 1, "value": name, "confidence": 1.0 if check.get("passed") else 0.4, "reason": check}
        for index, (name, check) in enumerate(checks.items())
        if isinstance(check, dict) and not check.get("passed")
    ]
    return [build_issue(
        issue_type="financial_reasoning",
        title="Financial totals need review",
        explanation="Financial checks produced warnings or errors.",
        confidence=float(reasoning.get("financial_consistency_score") or 0.0),
        suspected_problem="financial_inconsistency",
        suggested_correction=None,
        evidence=evidence,
        financial_reasoning=reasoning,
        erp_impact="ERP posting should wait until totals, taxes, and line sums are reconciled.",
    )]


def _validation_issues(response: Any) -> list[dict[str, Any]]:
    issues = []
    missing = (response.erp_readiness or {}).get("missing_fields") or []
    if missing:
        issues.append(build_issue(
            issue_type="missing_required_fields",
            title="Required ERP fields are missing",
            explanation="One or more ERP-required fields were not extracted confidently.",
            confidence=0.9,
            suspected_problem="required_fields_missing",
            suggested_correction={"fields_to_review": missing},
            evidence=[{"rank": index + 1, "value": field, "confidence": 0.0, "reason": "missing required field"} for index, field in enumerate(missing)],
            erp_impact="ERP export remains blocked until required fields are confirmed.",
        ))
    return issues


def _suggestion_from_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": issue.get("type"),
        "suggested_correction": issue.get("suggested_correction"),
        "confidence": issue.get("confidence"),
        "reason": issue.get("explanation"),
        "evidence": issue.get("evidence"),
        "erp_impact": issue.get("erp_impact"),
    }


def _assistant_confidence(issues: list[dict[str, Any]], response: Any) -> float:
    if not issues:
        return float((response.confidence_breakdown or {}).get("overall_confidence") or 1.0)
    return round(sum(float(issue.get("confidence") or 0.0) for issue in issues) / len(issues), 3)


def _summary(issues: list[dict[str, Any]], response: Any) -> str:
    if not issues:
        return "No major review issues detected by the assistant."
    return f"{len(issues)} review issue(s) found. Reviewer should inspect evidence before ERP export."


def _erp_impact(response: Any) -> str:
    readiness = response.erp_readiness or {}
    if readiness.get("ready"):
        return "ERP export appears ready, but reviewer can still inspect assistant evidence."
    return readiness.get("erp_ready_status") or "ERP export is not ready."


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values) -> float:
    numeric = [_number(value) for value in values]
    numeric = [value for value in numeric if value is not None]
    return round(sum(numeric) / len(numeric), 3) if numeric else 0.0
