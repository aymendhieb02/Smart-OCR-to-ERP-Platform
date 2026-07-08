from __future__ import annotations

import json
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.schemas import (
    CorrectionItem,
    CorrectionResponse,
    CorrectionSubmission,
    ExtractedInvoiceFields,
    LineItem,
    ValidationResult,
)
from app.services.erp_mapper import build_erp_json
from app.services.extraction_quality import apply_extraction_quality_gate, build_validated_erp_json
from app.services.validator import validate_invoice

CORRECTION_DIR = settings.output_dir / "corrections"
CORRECTION_FILE = CORRECTION_DIR / "corrections.jsonl"

FIELD_TYPES = {
    "supplier_name": "supplier",
    "supplier_tax_id": "supplier",
    "supplier_address": "supplier",
    "customer_name": "customer",
    "customer_tax_id": "customer",
    "customer_address": "customer",
    "amount_ht": "total",
    "tva_amount": "total",
    "amount_ttc": "total",
    "tax_rate": "total",
}


def submit_corrections(payload: CorrectionSubmission) -> CorrectionResponse:
    document_id = payload.document_id or str(uuid.uuid4())
    corrected_fields = _apply_payload_corrections(payload)
    validation = _validate_corrected_fields(corrected_fields)
    quality_gate = apply_extraction_quality_gate(corrected_fields, {}, {})
    if quality_gate.validation_report.get("extraction_status") == "needs_review" and validation.status == "valid":
        validation.status = "needs_review"
        validation.is_valid = False
        validation.warnings.extend(quality_gate.validation_report.get("warnings", []))

    erp_json = build_erp_json(
        fields=quality_gate.sanitized_fields,
        validation=validation,
        source_file=payload.source_file or "manual-correction",
        ocr_engine="human-correction",
        confidence=1.0,
        document_type="invoice",
        field_confidences={field: 1.0 for field in payload.corrected_fields},
        expanded_fields={},
    )
    validated_erp_json = build_validated_erp_json(erp_json, quality_gate.validation_report)
    records = build_correction_records(payload, document_id)
    append_correction_records(records)
    return CorrectionResponse(
        document_id=document_id,
        stored_count=len(records),
        corrections=records,
        corrected_fields=quality_gate.sanitized_fields,
        validation=validation,
        validated_erp_json=validated_erp_json,
        memory_summary=get_correction_memory(),
    )


def build_correction_records(payload: CorrectionSubmission, document_id: str) -> list[CorrectionItem]:
    records: list[CorrectionItem] = []
    original_fields = payload.detected_fields or ExtractedInvoiceFields()
    for item in payload.corrections:
        item.document_id = item.document_id or document_id
        item.source_file = item.source_file or payload.source_file
        records.append(item)
    for field_name, corrected_value in payload.corrected_fields.items():
        if any(record.field_name == field_name and record.corrected_value == corrected_value for record in records):
            continue
        records.append(CorrectionItem(
            document_id=document_id,
            field_name=field_name,
            original_value=getattr(original_fields, field_name, None),
            corrected_value=corrected_value,
            source_file=payload.source_file,
            correction_type=FIELD_TYPES.get(field_name, "field"),
            user_action="edited",
        ))
    for index, item in enumerate(payload.corrected_line_items):
        records.append(CorrectionItem(
            document_id=document_id,
            field_name=f"line_items[{index}]",
            original_value=None,
            corrected_value=item.model_dump(mode="json"),
            original_bbox=item.bbox,
            page=item.page,
            confidence=item.confidence,
            source_file=payload.source_file,
            source=item.source,
            correction_type="line_item",
            user_action="edited",
            line_item_index=index,
        ))
    return records


def append_correction_records(records: list[CorrectionItem]) -> None:
    if not records:
        return
    CORRECTION_DIR.mkdir(parents=True, exist_ok=True)
    with CORRECTION_FILE.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")


def load_correction_records() -> list[dict[str, Any]]:
    if not CORRECTION_FILE.exists():
        return []
    records: list[dict[str, Any]] = []
    with CORRECTION_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def get_correction_memory() -> dict[str, Any]:
    records = load_correction_records()
    memory: dict[str, Any] = {
        "known_supplier_names": [],
        "known_customer_names": [],
        "common_item_descriptions": [],
        "common_field_labels": [],
        "record_count": len(records),
    }
    suppliers: Counter[str] = Counter()
    customers: Counter[str] = Counter()
    items: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    for record in records:
        action = record.get("user_action", "edited")
        if action not in {"accepted", "edited"}:
            continue
        value = record.get("corrected_value")
        field_name = record.get("field_name") or ""
        if not value:
            continue
        if field_name == "supplier_name" and isinstance(value, str):
            suppliers[value] += 1
        elif field_name == "customer_name" and isinstance(value, str):
            customers[value] += 1
        elif record.get("correction_type") == "line_item" and isinstance(value, dict):
            description = value.get("description")
            if description:
                items[str(description)] += 1
        labels[field_name] += 1
    memory["known_supplier_names"] = [name for name, _ in suppliers.most_common(50)]
    memory["known_customer_names"] = [name for name, _ in customers.most_common(50)]
    memory["common_item_descriptions"] = [name for name, _ in items.most_common(100)]
    memory["common_field_labels"] = [name for name, _ in labels.most_common(100)]
    return memory


def boost_candidates_from_memory(candidates: dict[str, list[Any]], text: str | None = None) -> None:
    memory = get_correction_memory()
    known_by_field = {
        "supplier_name": set(memory.get("known_supplier_names", [])),
        "customer_name": set(memory.get("known_customer_names", [])),
    }
    for field_name, known_values in known_by_field.items():
        for candidate in candidates.get(field_name, []):
            if str(candidate.value) in known_values:
                candidate.score = min(1.0, candidate.score + 0.16)
                candidate.source = f"{candidate.source} + correction memory"
                candidate.score_breakdown["memory_score"] = 0.16
    if text:
        lowered = text.lower()
        for field_name, known_values in known_by_field.items():
            for value in known_values:
                if value and value.lower() in lowered and not any(str(candidate.value) == value for candidate in candidates.get(field_name, [])):
                    from app.core.schemas import Candidate
                    candidates.setdefault(field_name, []).append(Candidate(
                        field=field_name,
                        value=value,
                        score=0.78,
                        source="correction memory exact text match",
                        normalized_value=value,
                        confidence=0.78,
                        evidence_text=value,
                        score_breakdown={"memory_score": 0.35, "business_score": 0.25, "layout_score": 0.10, "label_score": 0.05, "regex_score": 0.03},
                    ))


def _apply_payload_corrections(payload: CorrectionSubmission) -> ExtractedInvoiceFields:
    fields = payload.detected_fields.model_copy(deep=True) if payload.detected_fields else ExtractedInvoiceFields()
    for correction in payload.corrections:
        if correction.user_action in {"accepted", "edited"} and correction.corrected_value is not None and hasattr(fields, correction.field_name):
            setattr(fields, correction.field_name, correction.corrected_value)
    for field_name, corrected_value in payload.corrected_fields.items():
        if hasattr(fields, field_name):
            setattr(fields, field_name, corrected_value)
    if payload.corrected_line_items:
        fields.line_items = [item.model_copy(update={"source": item.source or "manual correction", "confidence": item.confidence or 1.0}) for item in payload.corrected_line_items]
        _recompute_amounts_from_line_items(fields)
    return fields


def _recompute_amounts_from_line_items(fields: ExtractedInvoiceFields) -> None:
    if not fields.line_items:
        return
    ht_values = [item.line_total_ht for item in fields.line_items if item.line_total_ht is not None]
    ttc_values = [(item.line_total_ttc if item.line_total_ttc is not None else item.total) for item in fields.line_items]
    ttc_values = [value for value in ttc_values if value is not None]
    tax_values = [item.tax_amount for item in fields.line_items if item.tax_amount is not None]
    if ht_values:
        fields.amount_ht = round(sum(ht_values), 3)
    if tax_values:
        fields.tva_amount = round(sum(tax_values), 3)
    if ttc_values:
        fields.amount_ttc = round(sum(ttc_values), 3)
    elif fields.amount_ht is not None and fields.tva_amount is not None:
        fields.amount_ttc = round(fields.amount_ht + fields.tva_amount, 3)
    if fields.amount_ht and fields.tva_amount is not None:
        fields.tax_rate = round((fields.tva_amount / fields.amount_ht) * 100, 2)


def _validate_corrected_fields(fields: ExtractedInvoiceFields) -> ValidationResult:
    validation = validate_invoice(fields, None, "invoice")
    if fields.amount_ht is not None and fields.tva_amount is not None:
        expected_ttc = round(fields.amount_ht + fields.tva_amount, 3)
        if fields.amount_ttc is None:
            fields.amount_ttc = expected_ttc
        elif abs(expected_ttc - fields.amount_ttc) <= max(0.05, abs(fields.amount_ttc) * 0.002):
            validation.errors = [error for error in validation.errors if "Amount mismatch" not in error]
    if not validation.errors and validation.warnings:
        validation.status = "needs_review"
        validation.is_valid = False
    elif not validation.errors:
        validation.status = "valid"
        validation.is_valid = True
    return validation
