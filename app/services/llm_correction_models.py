from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SUPPORTED_SCALAR_FIELDS = {
    "supplier",
    "supplier_name",
    "customer",
    "customer_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "subtotal",
    "amount_ht",
    "tax",
    "amount_tax",
    "tva_amount",
    "total",
    "amount_ttc",
}

SUPPORTED_LINE_ITEM_FIELDS = {
    "line_items",
    "line_item.description",
    "line_item.quantity",
    "line_item.unit_price",
    "line_item.total",
    "line_item.row_total",
}

SUPPORTED_FIELDS = SUPPORTED_SCALAR_FIELDS | SUPPORTED_LINE_ITEM_FIELDS

SUPPORTED_OPERATIONS = {
    "replace",
    "fill_missing",
    "remove",
    "merge_rows",
    "split_row",
    "restore_row",
}


@dataclass
class LLMEvidenceItem:
    ref: str
    kind: str
    text: str
    page: int | None = None
    bbox: Any = None
    confidence: float | None = None
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "text": self.text,
            "page": self.page,
            "bbox": self.bbox,
            "confidence": self.confidence,
            "source": self.source,
            "metadata": self.metadata,
        }


@dataclass
class LLMEvidencePackage:
    prompt_version: str
    evidence_items: list[LLMEvidenceItem]
    trigger_reasons: list[str]
    limits: dict[str, int]
    sections: dict[str, Any] = field(default_factory=dict)
    fingerprint: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "prompt_version": self.prompt_version,
            "evidence_items": [item.model_dump() for item in self.evidence_items],
            "trigger_reasons": self.trigger_reasons,
            "limits": self.limits,
            "sections": self.sections,
            "fingerprint": self.fingerprint,
        }

    @property
    def refs(self) -> set[str]:
        return {item.ref for item in self.evidence_items}


@dataclass
class LLMCorrectionProposal:
    field: str
    operation: str
    old_value: Any = None
    proposed_value: Any = None
    confidence: float = 0.0
    reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    row_index: int | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "operation": self.operation,
            "old_value": self.old_value,
            "proposed_value": self.proposed_value,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence_refs": self.evidence_refs,
            "row_index": self.row_index,
        }


@dataclass
class LLMCorrectionDecision:
    document_decision: Literal["no_change", "propose_corrections", "insufficient_evidence"] = "insufficient_evidence"
    proposals: list[LLMCorrectionProposal] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0
    raw_response: str = ""

    def model_dump(self) -> dict[str, Any]:
        return {
            "document_decision": self.document_decision,
            "proposals": [proposal.model_dump() for proposal in self.proposals],
            "unresolved_fields": self.unresolved_fields,
            "overall_confidence": self.overall_confidence,
            "raw_response": self.raw_response,
        }


@dataclass
class LLMCorrectionReview:
    proposal: LLMCorrectionProposal
    accepted: bool
    reason: str
    checks: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal.model_dump(),
            "accepted": self.accepted,
            "reason": self.reason,
            "checks": self.checks,
        }
