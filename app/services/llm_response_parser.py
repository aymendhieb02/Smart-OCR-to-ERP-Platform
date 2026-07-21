from __future__ import annotations

import json
import re
from typing import Any

from app.services.llm_correction_models import LLMCorrectionDecision, LLMCorrectionProposal


class LLMResolution(LLMCorrectionDecision):
    @property
    def supplier(self) -> str | None:
        return _proposal_value(self.proposals, "supplier")

    @property
    def customer(self) -> str | None:
        return _proposal_value(self.proposals, "customer")

    @property
    def corrected_rows(self) -> list[dict[str, Any]]:
        return [
            proposal.proposed_value
            for proposal in self.proposals
            if proposal.field.startswith("line_item") or proposal.field == "line_items"
            if isinstance(proposal.proposed_value, dict)
        ]

    @property
    def confidence(self) -> float:
        return self.overall_confidence

    @property
    def reasoning(self) -> str:
        reasons = [proposal.reason for proposal in self.proposals if proposal.reason]
        return "; ".join(reasons)


class LLMResponseParseError(ValueError):
    pass


def parse_llm_response(raw: str) -> LLMResolution:
    data = _load_json(raw)
    if not isinstance(data, dict):
        raise LLMResponseParseError("LLM response must be a JSON object")
    if "document_decision" in data or "proposals" in data:
        return _parse_correction_schema(data, raw)
    return _parse_legacy_schema(data, raw)


def _parse_correction_schema(data: dict[str, Any], raw: str) -> LLMResolution:
    decision = str(data.get("document_decision") or "insufficient_evidence").strip()
    if decision not in {"no_change", "propose_corrections", "insufficient_evidence"}:
        raise LLMResponseParseError("unsupported document_decision")
    raw_proposals = data.get("proposals") or []
    if not isinstance(raw_proposals, list):
        raise LLMResponseParseError("proposals must be a list")
    proposals: list[LLMCorrectionProposal] = []
    for item in raw_proposals:
        if not isinstance(item, dict):
            raise LLMResponseParseError("proposal must be an object")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list):
            raise LLMResponseParseError("proposal evidence_refs must be a list")
        confidence = _coerce_confidence(item.get("confidence"))
        proposals.append(LLMCorrectionProposal(
            field=str(item.get("field") or "").strip(),
            operation=str(item.get("operation") or "").strip(),
            old_value=item.get("old_value"),
            proposed_value=item.get("proposed_value"),
            confidence=confidence,
            reason=_clean_string(item.get("reason")) or "",
            evidence_refs=[str(ref).strip() for ref in refs if str(ref).strip()],
            row_index=_coerce_int(item.get("row_index")),
        ))
    unresolved = data.get("unresolved_fields") or []
    if not isinstance(unresolved, list):
        raise LLMResponseParseError("unresolved_fields must be a list")
    return LLMResolution(
        document_decision=decision,  # type: ignore[arg-type]
        proposals=proposals,
        unresolved_fields=[str(item) for item in unresolved],
        overall_confidence=_coerce_confidence(data.get("overall_confidence")),
        raw_response=raw,
    )


def _parse_legacy_schema(data: dict[str, Any], raw: str) -> LLMResolution:
    confidence = _coerce_confidence(data.get("confidence"))
    rows = data.get("corrected_rows") or []
    if not isinstance(rows, list):
        raise LLMResponseParseError("corrected_rows must be a list")
    proposals: list[LLMCorrectionProposal] = []
    if _clean_string(data.get("supplier")):
        proposals.append(LLMCorrectionProposal(
            field="supplier",
            operation="replace",
            proposed_value=_clean_string(data.get("supplier")),
            confidence=confidence,
            reason=_clean_string(data.get("reasoning")) or "legacy supplier proposal",
            evidence_refs=[],
        ))
    if _clean_string(data.get("customer")):
        proposals.append(LLMCorrectionProposal(
            field="customer",
            operation="replace",
            proposed_value=_clean_string(data.get("customer")),
            confidence=confidence,
            reason=_clean_string(data.get("reasoning")) or "legacy customer proposal",
            evidence_refs=[],
        ))
    for index, row in enumerate(row for row in rows if isinstance(row, dict)):
        proposals.append(LLMCorrectionProposal(
            field="line_items",
            operation="replace",
            proposed_value=row,
            confidence=confidence,
            reason=_clean_string(data.get("reasoning")) or "legacy row proposal",
            evidence_refs=[],
            row_index=index,
        ))
    decision = "propose_corrections" if proposals else "insufficient_evidence"
    return LLMResolution(
        document_decision=decision,
        proposals=proposals,
        unresolved_fields=[],
        overall_confidence=confidence,
        raw_response=raw,
    )


def _load_json(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        raise LLMResponseParseError("empty LLM response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise LLMResponseParseError("malformed JSON response") from None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMResponseParseError(f"malformed JSON response: {exc}") from exc


def _coerce_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, number)), 3)


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _proposal_value(proposals: list[LLMCorrectionProposal], role: str) -> str | None:
    aliases = {role, f"{role}_name"}
    for proposal in proposals:
        if proposal.field in aliases and proposal.proposed_value is not None:
            return str(proposal.proposed_value)
    return None
