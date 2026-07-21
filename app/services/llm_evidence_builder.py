from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.core.config import settings
from app.services.llm_correction_models import LLMEvidenceItem, LLMEvidencePackage


PROMPT_VERSION = "hybrid_prompt_v1"

_METADATA_RE = re.compile(r"(invoice|facture|date|due|echeance|échéance|issued|number|n°|no\b|ref)", re.I)
_TOTALS_RE = re.compile(r"(total|ttc|ht|tva|vat|tax|subtotal|sous.?total|remise|discount|shipping|stamp|timbre)", re.I)
_PARTY_RE = re.compile(r"(supplier|vendor|client|customer|bill.?to|livr[ée]|mf|ice|vat|tax|email|tel|phone|address|adresse)", re.I)
_TABLE_RE = re.compile(r"(description|designation|désignation|qty|qte|quantity|prix|price|unit|total|amount|tva|vat)", re.I)


def build_evidence_package(response: Any, trigger_reasons: list[str] | None = None) -> LLMEvidencePackage:
    triggers = trigger_reasons or infer_trigger_reasons(response)
    version = _prompt_version()
    limits = _version_limits(version)
    max_blocks = min(int(settings.llm_resolver_max_evidence_blocks or limits["blocks"]), limits["blocks"])
    max_lines = min(int(settings.llm_resolver_max_evidence_lines or limits["lines"]), limits["lines"])
    max_chars = min(int(settings.llm_resolver_max_evidence_characters or limits["characters"]), limits["characters"])
    max_rows = min(int(settings.llm_resolver_max_table_rows or limits["rows"]), limits["rows"])

    if version in {"hybrid_prompt_v3", "hybrid_prompt_v4"}:
        items = _targeted_evidence(response, triggers, max_blocks=max_blocks, max_lines=max_lines, max_rows=max_rows)
    else:
        items: list[LLMEvidenceItem] = []
        items.extend(_layout_evidence(response, max_blocks))
        items.extend(_ocr_line_evidence(response, max_lines))
        items.extend(_table_evidence(response, max_rows))
    items = _bounded_unique(items, max_lines + max_blocks + max_rows, max_chars)

    package = LLMEvidencePackage(
        prompt_version=version,
        evidence_items=items,
        trigger_reasons=triggers,
        limits={
            "maximum_blocks": max_blocks,
            "maximum_lines": max_lines,
            "maximum_characters": max_chars,
            "maximum_table_rows": max_rows,
        },
        sections={
            "source_policy": "bounded_relevant_ocr_evidence_only",
            "excluded": ["full_document_ocr_dump", "pdf_file", "invoice_image"],
        },
    )
    package.fingerprint = fingerprint_evidence(package.model_dump())
    return package


def infer_trigger_reasons(response: Any) -> list[str]:
    reasons: list[str] = []
    fields = response.detected_fields
    readiness = response.erp_readiness or {}
    confidence = response.confidence_breakdown or {}
    missing = set(readiness.get("missing_fields") or [])
    for field in ("supplier_name", "customer_name", "invoice_number", "invoice_date", "amount_ttc"):
        if field in missing or getattr(fields, field, None) in (None, ""):
            reasons.append(f"missing_{field}")
    if getattr(response.validation, "status", None) in {"needs_review", "invalid"}:
        reasons.append(f"validation_{response.validation.status}")
    if float(confidence.get("overall_confidence") or 0.0) < float(settings.llm_resolver_confidence_threshold or 0.78):
        reasons.append("low_overall_confidence")
    if response.line_items_needs_review:
        reasons.append("line_items_need_review")
    financial = response.financial_reasoning or {}
    if financial.get("financial_errors") or not financial.get("financially_consistent", True):
        reasons.append("financial_inconsistency")
    return sorted(set(reasons))


def fingerprint_evidence(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _prompt_version() -> str:
    version = str(getattr(settings, "llm_resolver_prompt_version", PROMPT_VERSION) or PROMPT_VERSION)
    if version not in {"hybrid_prompt_v1", "hybrid_prompt_v2", "hybrid_prompt_v3", "hybrid_prompt_v4"}:
        return PROMPT_VERSION
    return version


def _version_limits(version: str) -> dict[str, int]:
    if version == "hybrid_prompt_v4":
        return {"blocks": 3, "lines": 8, "characters": 1600, "rows": 4}
    if version == "hybrid_prompt_v3":
        return {"blocks": 5, "lines": 12, "characters": 2500, "rows": 5}
    return {"blocks": 8, "lines": 40, "characters": 6000, "rows": 12}


def _targeted_evidence(response: Any, triggers: list[str], *, max_blocks: int, max_lines: int, max_rows: int) -> list[LLMEvidenceItem]:
    items: list[LLMEvidenceItem] = []
    wants_party = any("supplier" in reason or "customer" in reason for reason in triggers)
    wants_metadata = any("invoice_number" in reason or "invoice_date" in reason or "date" in reason for reason in triggers)
    wants_totals = any("amount_ttc" in reason or "financial" in reason for reason in triggers)
    wants_table = any("line_items" in reason or "table" in reason for reason in triggers)
    if not any((wants_party, wants_metadata, wants_totals, wants_table)):
        wants_party = wants_metadata = wants_totals = True

    if wants_party:
        items.extend(_layout_evidence_by_type(response, {"supplier", "customer", "invoice metadata", "metadata", "unknown"}, max(1, max_blocks // 2)))
        items.extend(_ocr_line_evidence_by_regex(response, _PARTY_RE, max(2, max_lines // 3), source="party_evidence"))
        items.extend(_party_candidate_evidence(response, limit=5))
    if wants_metadata:
        items.extend(_layout_evidence_by_type(response, {"invoice metadata", "metadata", "unknown"}, max(1, max_blocks // 3)))
        items.extend(_ocr_line_evidence_by_regex(response, _METADATA_RE, max(2, max_lines // 4), source="metadata_evidence"))
    if wants_totals:
        items.extend(_layout_evidence_by_type(response, {"totals", "taxes", "unknown"}, max(1, max_blocks // 3)))
        items.extend(_ocr_line_evidence_by_regex(response, _TOTALS_RE, max(2, max_lines // 4), source="totals_evidence"))
    if wants_table:
        items.extend(_layout_evidence_by_type(response, {"products", "unknown"}, max(1, max_blocks // 3)))
        items.extend(_ocr_line_evidence_by_regex(response, _TABLE_RE, max(2, max_lines // 4), source="table_evidence"))
        items.extend(_table_evidence(response, max_rows))
    return items


def _layout_evidence_by_type(response: Any, block_types: set[str], limit: int) -> list[LLMEvidenceItem]:
    output: list[LLMEvidenceItem] = []
    for index, block in enumerate(getattr(response, "layout_blocks", None) or []):
        block_type = str(getattr(block, "block_type", "") or "")
        text = (getattr(block, "text", "") or "").strip()
        if not text or (block_type not in block_types and not _is_relevant_text(text)):
            continue
        output.append(LLMEvidenceItem(
            ref=f"block:{block_type.replace(' ', '_') or 'unknown'}:{index}",
            kind="layout_block",
            text=_compact_text(text, 260),
            page=getattr(block, "page", None),
            bbox=_dump_bbox(getattr(block, "bbox", None)),
            confidence=getattr(block, "confidence", None),
            source=f"layout:{block_type}",
            metadata={"block_type": block_type},
        ))
        if len(output) >= limit:
            break
    return output


def _ocr_line_evidence_by_regex(response: Any, pattern: re.Pattern[str], limit: int, *, source: str) -> list[LLMEvidenceItem]:
    output: list[LLMEvidenceItem] = []
    lines = getattr(response, "ocr_blocks", None) or getattr(response, "all_ocr_blocks", None) or []
    for index, line in enumerate(lines):
        text = (getattr(line, "text", "") or "").strip()
        if not text or not pattern.search(text):
            continue
        output.append(LLMEvidenceItem(
            ref=f"line:{getattr(line, 'line_index', index)}",
            kind="ocr_line",
            text=_compact_text(text, 180),
            page=getattr(line, "page_number", None),
            bbox=_dump_bbox(getattr(line, "bbox", None)),
            confidence=getattr(line, "confidence", None),
            source=source,
            metadata={"line_index": getattr(line, "line_index", index)},
        ))
        if len(output) >= limit:
            break
    return output


def _party_candidate_evidence(response: Any, limit: int) -> list[LLMEvidenceItem]:
    output: list[LLMEvidenceItem] = []
    party_debug = ((getattr(response, "extraction_debug", None) or {}).get("party_resolver") or {})
    pairs = [("supplier", party_debug.get("supplier_candidates") or []), ("customer", party_debug.get("customer_candidates") or [])]
    for role, candidates in pairs:
        for index, candidate in enumerate(candidates[: max(1, limit // 2)]):
            if not isinstance(candidate, dict) or not candidate.get("value"):
                continue
            output.append(LLMEvidenceItem(
                ref=f"candidate:{role}:{index}",
                kind="candidate",
                text=_compact_text(str(candidate.get("value")), 120),
                page=candidate.get("page"),
                bbox=candidate.get("bbox"),
                confidence=candidate.get("score") or candidate.get("confidence"),
                source=f"{role}_candidate",
                metadata={"role": role, "reason": candidate.get("selected_reason") or candidate.get("reason")},
            ))
    return output


def _layout_evidence(response: Any, limit: int) -> list[LLMEvidenceItem]:
    relevant_types = {"supplier", "customer", "invoice metadata", "metadata", "products", "totals", "taxes", "unknown"}
    output: list[LLMEvidenceItem] = []
    for index, block in enumerate(getattr(response, "layout_blocks", None) or []):
        block_type = getattr(block, "block_type", "")
        text = getattr(block, "text", "") or ""
        if block_type not in relevant_types and not _is_relevant_text(text):
            continue
        output.append(LLMEvidenceItem(
            ref=f"block:page{getattr(block, 'page', 1)}_{block_type.replace(' ', '_')}_{index}",
            kind="layout_block",
            text=text[:800],
            page=getattr(block, "page", None),
            bbox=_dump_bbox(getattr(block, "bbox", None)),
            confidence=getattr(block, "confidence", None),
            source=f"layout:{block_type}",
            metadata={"block_type": block_type, "fields": getattr(block, "fields", [])},
        ))
        if len(output) >= limit:
            break
    return output


def _ocr_line_evidence(response: Any, limit: int) -> list[LLMEvidenceItem]:
    output: list[LLMEvidenceItem] = []
    lines = getattr(response, "ocr_blocks", None) or getattr(response, "all_ocr_blocks", None) or []
    selected_indexes: set[int] = set()
    for index, line in enumerate(lines):
        text = getattr(line, "text", "") or ""
        if _is_relevant_text(text):
            for neighbor in (index - 1, index, index + 1):
                if 0 <= neighbor < len(lines):
                    selected_indexes.add(neighbor)
    for order, index in enumerate(sorted(selected_indexes)):
        line = lines[index]
        text = getattr(line, "text", "") or ""
        if not text.strip():
            continue
        output.append(LLMEvidenceItem(
            ref=f"line:page{getattr(line, 'page_number', 1)}_line_{getattr(line, 'line_index', index)}",
            kind="ocr_line",
            text=text[:500],
            page=getattr(line, "page_number", None),
            bbox=_dump_bbox(getattr(line, "bbox", None)),
            confidence=getattr(line, "confidence", None),
            source="ocr_relevant_neighbor",
            metadata={"reading_order": order, "line_index": getattr(line, "line_index", index)},
        ))
        if len(output) >= limit:
            break
    return output


def _table_evidence(response: Any, limit: int) -> list[LLMEvidenceItem]:
    output: list[LLMEvidenceItem] = []
    rows = list(getattr(response, "line_items_validated", None) or []) + list(getattr(response, "line_items_needs_review", None) or [])
    for index, row in enumerate(rows[:limit]):
        data = row.model_dump(mode="json") if hasattr(row, "model_dump") else dict(row)
        text = " | ".join(str(data.get(key) or "") for key in ("description", "quantity", "unit_price", "tax_rate", "total", "line_total_ttc"))
        output.append(LLMEvidenceItem(
            ref=f"row:line_item_{index}",
            kind="table_row",
            text=text,
            page=data.get("page"),
            bbox=data.get("bbox"),
            confidence=data.get("confidence"),
            source=data.get("source") or "table_reconstruction",
            metadata={"row_index": index, "row": data},
        ))
    debug_rows = ((response.extraction_debug or {}).get("table_extraction_debug") or {}).get("review_rows") or []
    for index, row in enumerate(debug_rows[: max(0, limit - len(output))]):
        if not isinstance(row, dict):
            continue
        output.append(LLMEvidenceItem(
            ref=f"rejected_row:table_like_{index}",
            kind="rejected_table_row",
            text=str(row.get("description") or row)[:800],
            page=row.get("page"),
            bbox=row.get("bbox"),
            confidence=row.get("confidence"),
            source="rejected_table_like_row",
            metadata={"row": row},
        ))
    return output


def _is_relevant_text(text: str) -> bool:
    return bool(_METADATA_RE.search(text) or _TOTALS_RE.search(text) or _PARTY_RE.search(text) or _TABLE_RE.search(text))


def _bounded_unique(items: list[LLMEvidenceItem], max_items: int, max_chars: int) -> list[LLMEvidenceItem]:
    output: list[LLMEvidenceItem] = []
    seen: set[str] = set()
    chars = 0
    for item in items:
        key = f"{item.kind}:{item.text.strip().lower()}:{item.page}"
        if key in seen:
            continue
        seen.add(key)
        length = len(item.text or "")
        if output and chars + length > max_chars:
            break
        output.append(item)
        chars += length
        if len(output) >= max_items:
            break
    return output


def _dump_bbox(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _compact_text(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean[:limit]
