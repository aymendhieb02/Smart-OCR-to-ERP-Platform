from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.services.llm_correction_models import LLMEvidencePackage


PROMPT_INSTRUCTIONS = {
    "hybrid_prompt_v1": (
        "You are reviewing an existing invoice extraction. You are not performing OCR. "
        "Use only supplied structured candidates and bounded OCR evidence. "
        "Every proposal must include evidence_refs from the evidence list. "
        "If evidence is insufficient, return insufficient_evidence. "
        "Return JSON only. No Markdown. Do not invent values."
    ),
    "hybrid_prompt_v2": (
        "You are a conservative invoice correction reviewer. You are not performing OCR. "
        "Use only supplied evidence_refs; never use outside knowledge or infer hidden text. "
        "Prefer insufficient_evidence over guessing. Do not replace high-confidence values without strong cited evidence. "
        "Never invent product rows, and never treat totals/footer/payment rows as line items. "
        "Return strict JSON only, with no Markdown or prose outside JSON."
    ),
    "hybrid_prompt_v3": (
        "You are reviewing an invoice extraction. OCR is finished. Do not perform OCR. "
        "Use only supplied evidence. Never invent information. "
        "If evidence is insufficient, return JSON with document_decision insufficient_evidence. "
        "Return valid JSON only. No Markdown."
    ),
    "hybrid_prompt_v4": (
        "Review the extracted invoice fields using only the evidence below. "
        "Do not guess. Cite evidence_refs for every proposal. "
        "If unsure, return insufficient_evidence. JSON only."
    ),
}


def build_llm_payload(response: Any, evidence_package: LLMEvidencePackage | None = None) -> dict[str, Any]:
    """Build the structured LLM input without raw OCR text."""
    version = str(getattr(evidence_package, "prompt_version", None) or settings.llm_resolver_prompt_version)
    if version in {"hybrid_prompt_v3", "hybrid_prompt_v4"}:
        return _build_compact_payload(response, evidence_package, version)
    debug = response.extraction_debug or {}
    party_debug = debug.get("party_resolver") or {}
    table_debug = debug.get("table_extraction_debug") or {}
    fields = response.detected_fields
    max_candidates = max(1, int(settings.llm_resolver_max_candidates or 8))
    payload = {
        "document_type": response.document_classification.document_type if response.document_classification else "unknown",
        "supplier_candidates": _candidate_slice(party_debug.get("supplier_candidates"), max_candidates),
        "customer_candidates": _candidate_slice(party_debug.get("customer_candidates"), max_candidates),
        "table_candidates": {
            "validated_rows": _safe_items(response.line_items_validated),
            "review_rows": _safe_items(response.line_items_needs_review),
            "debug_counts": table_debug.get("counts") or {},
            "selected_strategy": (table_debug.get("p3_table_reconstruction") or {}).get("selected_strategy")
            or (table_debug.get("p3_table_reconstruction") or {}).get("diagnostics", {}).get("selected_strategy"),
        },
        "totals": {
            "amount_ht": fields.amount_ht,
            "tva_amount": fields.tva_amount,
            "amount_ttc": fields.amount_ttc,
            "tax_rate": fields.tax_rate,
            "financial_reasoning": response.financial_reasoning,
        },
        "dates": {
            "invoice_number": fields.invoice_number,
            "invoice_date": fields.invoice_date.isoformat() if fields.invoice_date else None,
            "due_date": fields.due_date.isoformat() if fields.due_date else None,
            "purchase_order_number": fields.purchase_order_number,
            "currency": fields.currency,
        },
        "warnings": list(response.validation.warnings or []) + list(response.validation.errors or []),
        "confidence": response.confidence_breakdown,
        "erp_readiness": response.erp_readiness,
        "schema": {
            "document_decision": "no_change | propose_corrections | insufficient_evidence",
            "proposals": [{
                "field": "supplier | customer | invoice_number | invoice_date | due_date | amount_ht | tva_amount | amount_ttc | line_items",
                "operation": "replace | fill_missing | remove | merge_rows | split_row | restore_row",
                "old_value": "current deterministic value or null",
                "proposed_value": "value supported by evidence",
                "confidence": "number from 0 to 1",
                "reason": "short evidence-grounded reason",
                "evidence_refs": ["line:page1_line_7"],
            }],
            "unresolved_fields": "array of field names",
            "overall_confidence": "number from 0 to 1",
        },
    }
    if evidence_package is not None:
        payload["bounded_ocr_evidence"] = evidence_package.model_dump()
    return payload


def build_llm_prompt(payload: dict[str, Any]) -> str:
    prompt_version = str((payload.get("bounded_ocr_evidence") or {}).get("prompt_version") or settings.llm_resolver_prompt_version)
    instructions = PROMPT_INSTRUCTIONS.get(prompt_version, PROMPT_INSTRUCTIONS["hybrid_prompt_v1"])
    if prompt_version in {"hybrid_prompt_v3", "hybrid_prompt_v4"}:
        return _build_compact_prompt(payload, instructions, prompt_version)
    return (
        f"{instructions}\n\n"
        f"Prompt version: {prompt_version}\n"
        "Every correction proposal must cite evidence_refs from bounded_ocr_evidence.evidence_items. "
        "If evidence is insufficient, return insufficient_evidence. "
        "Do not request image/PDF access. "
        "Return exactly this JSON shape: "
        '{"document_decision":"insufficient_evidence","proposals":[],"unresolved_fields":[],"overall_confidence":0.0}'
        "\n\nStructured input:\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


def _build_compact_payload(response: Any, evidence_package: LLMEvidencePackage | None, version: str) -> dict[str, Any]:
    debug = response.extraction_debug or {}
    party_debug = debug.get("party_resolver") or {}
    fields = response.detected_fields
    evidence_items = evidence_package.evidence_items if evidence_package else []
    evidence_limit = 14 if version == "hybrid_prompt_v3" else 9
    candidate_limit = 3 if version == "hybrid_prompt_v3" else 2
    payload = {
        "summary": {
            "document_type": response.document_classification.document_type if response.document_classification else "unknown",
            "supplier": fields.supplier_name,
            "customer": fields.customer_name,
            "invoice_number": fields.invoice_number,
            "invoice_date": fields.invoice_date.isoformat() if fields.invoice_date else None,
            "due_date": fields.due_date.isoformat() if fields.due_date else None,
            "amount_ht": fields.amount_ht,
            "tax": fields.tva_amount,
            "total": fields.amount_ttc,
            "currency": fields.currency,
            "line_items": len(fields.line_items or []),
            "validation": response.validation.status,
            "missing": (response.erp_readiness or {}).get("missing_fields") or [],
            "warnings": _short_list(list(response.validation.warnings or []) + list(response.validation.errors or []), 5),
        },
        "candidates": {
            "supplier": _compact_candidates(party_debug.get("supplier_candidates"), candidate_limit),
            "customer": _compact_candidates(party_debug.get("customer_candidates"), candidate_limit),
        },
        "evidence": [_compact_evidence_item(item) for item in evidence_items[:evidence_limit]],
        "prompt_version": version,
    }
    return payload


def _build_compact_prompt(payload: dict[str, Any], instructions: str, prompt_version: str) -> str:
    return (
        f"{instructions}\n\n"
        "Return one of these JSON shapes:\n"
        '{"document_decision":"insufficient_evidence","proposals":[],"unresolved_fields":[],"overall_confidence":0.0}\n'
        '{"document_decision":"propose_corrections","proposals":[{"field":"supplier","operation":"fill_missing","old_value":null,"proposed_value":"ABC SARL","confidence":0.91,"reason":"cited evidence","evidence_refs":["line:12"]}],"unresolved_fields":[],"overall_confidence":0.91}\n\n'
        "Allowed fields: supplier, customer, invoice_number, invoice_date, due_date, amount_ht, tva_amount, amount_ttc, line_items.\n"
        "Allowed operations: fill_missing, replace, restore_row, merge_rows, split_row.\n"
        "Never use totals/footer/payment rows as products.\n\n"
        "Extraction summary, candidates, and evidence:\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str, separators=(',', ':'))}"
    )


def _candidate_slice(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output = []
    for item in value[:limit]:
        if isinstance(item, dict):
            output.append({
                "value": item.get("value"),
                "score": item.get("score"),
                "score_breakdown": item.get("score_breakdown") or {},
                "selected_reason": item.get("selected_reason") or item.get("reason"),
                "source": item.get("source"),
                "page": item.get("page"),
                "bbox": item.get("bbox"),
            })
    return output


def _safe_items(items: Any) -> list[dict[str, Any]]:
    if not items:
        return []
    output = []
    for item in items:
        if hasattr(item, "model_dump"):
            output.append(item.model_dump(mode="json"))
        elif isinstance(item, dict):
            output.append(item)
    return output


def _compact_candidates(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        output.append({
            "value": item.get("value"),
            "score": item.get("score") or item.get("confidence"),
            "reason": item.get("selected_reason") or item.get("reason"),
        })
    return output


def _compact_evidence_item(item: Any) -> dict[str, Any]:
    return {
        "ref": item.ref,
        "kind": item.kind,
        "text": item.text,
        "conf": item.confidence,
        "role": (item.metadata or {}).get("role") or (item.metadata or {}).get("block_type") or item.source,
        "page": item.page,
    }


def _short_list(values: list[Any], limit: int) -> list[str]:
    return [str(value)[:140] for value in values[:limit] if value not in (None, "")]
