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
    issues.extend(_hybrid_llm_issues(response))
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


def _hybrid_llm_issues(response: Any) -> list[dict[str, Any]]:
    hybrid = ((response.extraction_debug or {}).get("hybrid_llm") or {})
    accepted = hybrid.get("accepted_corrections") or []
    rejected = hybrid.get("rejected_corrections") or []
    proposals = hybrid.get("proposals") or []
    if not hybrid.get("invoked") and not proposals:
        return []
    issues = []
    if accepted:
        evidence = [_correction_evidence(item, index + 1) for index, item in enumerate(accepted[:8])]
        issues.append(build_issue(
            issue_type="hybrid_llm_accepted_corrections",
            title="Safe LLM corrections available",
            explanation="The LLM proposed corrections that passed the evidence gate. They are shown for review and are not applied unless validated-apply mode is explicitly enabled.",
            confidence=_average(_proposal_confidence(item) for item in accepted),
            suspected_problem="deterministic_extraction_uncertain",
            suggested_correction={"accepted_corrections": accepted},
            evidence=evidence,
            financial_reasoning=(hybrid.get("hybrid_candidate_result") or {}).get("financial_reasoning"),
            erp_impact=_hybrid_erp_impact(hybrid),
        ))
    if rejected:
        evidence = [_correction_evidence(item, index + 1) for index, item in enumerate(rejected[:8])]
        issues.append(build_issue(
            issue_type="hybrid_llm_rejected_corrections",
            title="Unsafe LLM proposals rejected",
            explanation="One or more LLM proposals failed evidence, safety, or validation checks.",
            confidence=_average(_proposal_confidence(item) for item in rejected),
            suspected_problem="unsafe_or_unsupported_llm_proposal",
            suggested_correction=None,
            evidence=evidence,
            financial_reasoning=None,
            erp_impact="Rejected proposals are not used for ERP export, but reviewers can inspect why they failed.",
        ))
    if hybrid.get("fallback_reason"):
        issues.append(build_issue(
            issue_type="hybrid_llm_fallback",
            title="Hybrid result kept deterministic output",
            explanation=str(hybrid.get("fallback_reason")),
            confidence=0.8,
            suspected_problem="hybrid_not_applied",
            suggested_correction=None,
            evidence=[{"rank": 1, "value": hybrid.get("final_source"), "confidence": 1.0, "reason": hybrid.get("fallback_reason")}],
            erp_impact="The final response remains deterministic unless safe validated apply is enabled.",
        ))
    return issues


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


def _correction_evidence(item: dict[str, Any], rank: int) -> dict[str, Any]:
    proposal = item.get("proposal") or {}
    return {
        "rank": rank,
        "value": proposal.get("proposed_value"),
        "confidence": proposal.get("confidence"),
        "reason": item.get("reason") or proposal.get("reason"),
        "field": proposal.get("field"),
        "operation": proposal.get("operation"),
        "old_value": proposal.get("old_value"),
        "evidence_refs": proposal.get("evidence_refs") or [],
        "checks": item.get("checks") or {},
    }


def _proposal_confidence(item: dict[str, Any]) -> float:
    return _number((item.get("proposal") or {}).get("confidence")) or 0.0


def _hybrid_erp_impact(hybrid: dict[str, Any]) -> str:
    candidate = hybrid.get("hybrid_candidate_result") or {}
    readiness = candidate.get("erp_readiness") or {}
    if readiness.get("ready"):
        return "Hybrid candidate would make the document ERP ready after validation."
    if readiness.get("erp_ready_status"):
        return f"Hybrid candidate ERP status: {readiness['erp_ready_status']}."
    return "Hybrid correction is advisory unless validated and explicitly applied."
