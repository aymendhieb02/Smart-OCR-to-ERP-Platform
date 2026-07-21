from __future__ import annotations

from typing import Any


def build_issue(
    *,
    issue_type: str,
    title: str,
    explanation: str,
    confidence: float,
    suspected_problem: str,
    suggested_correction: Any = None,
    evidence: list[dict[str, Any]] | None = None,
    financial_reasoning: dict[str, Any] | None = None,
    erp_impact: str = "Needs reviewer confirmation before ERP export.",
) -> dict[str, Any]:
    return {
        "type": issue_type,
        "title": title,
        "explanation": explanation,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
        "suspected_problem": suspected_problem,
        "suggested_correction": suggested_correction,
        "financial_reasoning": financial_reasoning or {},
        "erp_impact": erp_impact,
        "evidence": evidence or [],
    }


def candidate_evidence(candidate: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "value": candidate.get("value"),
        "confidence": candidate.get("confidence") or candidate.get("score"),
        "reason": candidate.get("selected_reason") or candidate.get("reason"),
        "source": candidate.get("source"),
        "bbox": candidate.get("bbox"),
        "page": candidate.get("page"),
    }
