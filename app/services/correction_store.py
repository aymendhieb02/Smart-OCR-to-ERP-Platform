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
    ReviewCorrectionResponse,
    ReviewCorrectionSubmission,
    ValidationResult,
)
from app.services.confidence_engine import calculate_confidence
from app.services.correction_suggestions import suggest_corrections
from app.services.duplicate_detector import detect_duplicates
from app.services.erp_mapper import build_erp_json
from app.services.erp_readiness import assess_erp_readiness
from app.services.extraction_quality import apply_extraction_quality_gate, build_validated_erp_json
from app.services.financial_reasoner import reason_financials
from app.services.fraud_indicators import detect_fraud_indicators
from app.services.invoice_validation_report import build_invoice_validation_report
from app.services.row_validation_engine import summarize_rows, validate_rows
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


def validate_review_corrections(payload: ReviewCorrectionSubmission) -> ReviewCorrectionResponse:
    """Apply human review edits, preserve evidence, and rerun business validation."""
    document_id = payload.document_id or str(uuid.uuid4())
    fields = _review_base_fields(payload)
    source_file = payload.source_file or _payload_source_file(payload) or "manual-review"
    original_evidence = _collect_original_evidence(payload)
    records: list[CorrectionItem] = []

    for field_name, correction in payload.field_corrections.items():
        value, original_value, metadata = _normalize_field_correction(correction, fields, field_name, original_evidence)
        if hasattr(fields, field_name):
            setattr(fields, field_name, value)
        records.append(CorrectionItem(
            document_id=document_id,
            field_name=field_name,
            original_value=original_value,
            corrected_value=value,
            original_bbox=metadata.get("bbox"),
            page=metadata.get("page"),
            confidence=metadata.get("confidence"),
            source_file=source_file,
            source=metadata.get("source", "human"),
            correction_type=FIELD_TYPES.get(field_name, "field"),
            user_action=metadata.get("user_action", "edited"),
        ))

    if payload.line_item_corrections:
        fields.line_items = _review_line_items(payload.line_item_corrections, payload.ignored_rows)
        _recompute_amounts_from_line_items(fields)
        for index, item in enumerate(fields.line_items):
            records.append(CorrectionItem(
                document_id=document_id,
                field_name=f"line_items[{index}]",
                original_value=None,
                corrected_value=item.model_dump(mode="json"),
                original_bbox=item.bbox,
                page=item.page,
                confidence=item.confidence,
                source_file=source_file,
                source=item.source or "human",
                correction_type="line_item",
                user_action="edited",
                line_item_index=index,
            ))
    for ignored in payload.ignored_rows:
        records.append(CorrectionItem(
            document_id=document_id,
            field_name=f"line_items[{ignored}]",
            original_value=None,
            corrected_value=None,
            source_file=source_file,
            correction_type="line_item",
            user_action="rejected",
        ))

    validation = validate_invoice(fields, None, "invoice")
    row_validation = validate_rows(fields.line_items)
    row_summary = summarize_rows(row_validation)
    financial = reason_financials(fields, fields.line_items, document_type="invoice")
    if financial["financial_errors"]:
        validation.errors.extend(financial["financial_errors"])
    validation.warnings.extend(financial["financial_warnings"])

    base_confidence = _review_source_confidence(payload)
    confidence = calculate_confidence(
        ocr=base_confidence.get("ocr"),
        layout=base_confidence.get("layout"),
        table=base_confidence.get("table"),
        fields=1.0 if records else base_confidence.get("fields"),
        financial=financial["financial_consistency_score"],
        validation=row_summary["validation_score"],
    )
    readiness = assess_erp_readiness(fields, row_summary=row_summary, financial=financial, confidence=confidence["overall_confidence"])
    confidence = calculate_confidence(
        ocr=base_confidence.get("ocr"),
        layout=base_confidence.get("layout"),
        table=base_confidence.get("table"),
        fields=1.0 if records else base_confidence.get("fields"),
        financial=financial["financial_consistency_score"],
        validation=row_summary["validation_score"],
        erp=readiness["erp_ready_score"],
    )
    if readiness["erp_ready_status"] == "Rejected":
        validation.status = "invalid"
        validation.is_valid = False
    elif readiness["erp_ready_status"] == "Needs Review":
        validation.status = "needs_review"
        validation.is_valid = False
    elif not validation.errors:
        validation.status = "valid"
        validation.is_valid = True

    duplicate = detect_duplicates(fields)
    fraud = detect_fraud_indicators(fields, financial=financial, duplicate=duplicate, validation={"missing_fields": readiness["missing_fields"]})
    suggestions = suggest_corrections(fields)
    report = build_invoice_validation_report(
        fields=fields,
        rows=row_validation,
        financial=financial,
        confidence=confidence,
        readiness=readiness,
        warnings=validation.warnings,
        errors=validation.errors,
        corrections=suggestions,
        duplicate=duplicate,
        fraud=fraud,
    )
    erp_json = build_erp_json(
        fields=fields,
        validation=validation,
        source_file=source_file,
        ocr_engine="human-review",
        confidence=confidence["overall_confidence"],
        document_type="invoice",
        field_confidences={field: 1.0 for field in payload.field_corrections},
        expanded_fields={},
    )
    erp_json.quality.update({
        "overall_confidence": confidence["overall_confidence"],
        "confidence_breakdown": confidence,
        "erp_readiness": readiness,
        "financial_reasoning": financial,
        "fraud_indicators": fraud,
        "correction_metadata": {
            "corrected_by": "human",
            "correction_count": len(records),
            "original_evidence_preserved": True,
        },
    })
    validated_erp_json = build_validated_erp_json(erp_json, {
        "extraction_status": validation.status,
        "blocking_errors": readiness["blocking_errors"],
        "warnings": validation.warnings,
    })
    validated_erp_json["erp_readiness"] = readiness
    validated_erp_json["erp_export_allowed"] = readiness["ready"]
    append_correction_records(records)
    return ReviewCorrectionResponse(
        document_id=document_id,
        corrected_fields=fields,
        corrected_line_items=fields.line_items,
        corrections=records,
        validation=validation,
        erp_json=erp_json.model_dump(mode="json"),
        validated_erp_json=validated_erp_json,
        invoice_validation_report=report,
        row_validation=row_validation,
        financial_reasoning=financial,
        confidence_breakdown=confidence,
        erp_readiness=readiness,
        correction_metadata={
            "corrected_by": "human",
            "correction_count": len(records),
            "ignored_rows": payload.ignored_rows,
            "original_evidence_preserved": True,
        },
        original_evidence=original_evidence,
        erp_export_allowed=readiness["ready"],
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


def _review_base_fields(payload: ReviewCorrectionSubmission) -> ExtractedInvoiceFields:
    if payload.detected_fields is not None:
        return payload.detected_fields.model_copy(deep=True)
    detected = (payload.original_payload or {}).get("detected_fields")
    if isinstance(detected, dict):
        return ExtractedInvoiceFields.model_validate(detected)
    return ExtractedInvoiceFields()


def _payload_source_file(payload: ReviewCorrectionSubmission) -> str | None:
    original = payload.original_payload or {}
    return (
        original.get("erp_json", {}).get("metadata", {}).get("source_file")
        or original.get("document_preview", {}).get("source_file")
    )


def _collect_original_evidence(payload: ReviewCorrectionSubmission) -> dict[str, Any]:
    original = payload.original_payload or {}
    evidence: dict[str, Any] = {}
    for field_name, detail in (original.get("expanded_fields") or {}).items():
        if isinstance(detail, dict):
            evidence[field_name] = {
                "value": detail.get("value"),
                "bbox": detail.get("bbox"),
                "page": detail.get("page"),
                "confidence": detail.get("confidence"),
                "source": detail.get("source"),
            }
    return evidence


def _normalize_field_correction(
    correction: Any,
    fields: ExtractedInvoiceFields,
    field_name: str,
    original_evidence: dict[str, Any],
) -> tuple[Any, Any, dict[str, Any]]:
    if isinstance(correction, dict):
        value = correction.get("value", correction.get("corrected_value"))
        original_value = correction.get("original_value", getattr(fields, field_name, None))
        metadata = dict(correction)
    else:
        value = correction
        original_value = getattr(fields, field_name, None)
        metadata = {"source": "human"}
    evidence = original_evidence.get(field_name, {})
    metadata.setdefault("bbox", evidence.get("bbox"))
    metadata.setdefault("page", evidence.get("page"))
    metadata.setdefault("confidence", evidence.get("confidence"))
    metadata.setdefault("source", "human")
    return value, original_value, metadata


def _review_line_items(raw_items: list[dict[str, Any]], ignored_rows: list[Any]) -> list[LineItem]:
    ignored = {str(item) for item in ignored_rows}
    line_items: list[LineItem] = []
    for index, raw in enumerate(raw_items):
        row_key = str(raw.get("row_key") or raw.get("key") or index)
        if row_key in ignored or str(index) in ignored or str(index + 1) in ignored:
            continue
        values = raw.get("values") if isinstance(raw.get("values"), dict) else raw
        item_payload = {
            "reference": values.get("reference"),
            "description": values.get("description"),
            "quantity": _float_or_none(values.get("quantity")),
            "unit": values.get("unit"),
            "unit_price": _float_or_none(values.get("unit_price")),
            "discount": _float_or_none(values.get("discount")),
            "line_total_ht": _float_or_none(values.get("line_total_ht", values.get("amount_ht"))),
            "tax_amount": _float_or_none(values.get("tax_amount")),
            "tax_rate": _float_or_none(values.get("tax_rate")),
            "line_total_ttc": _float_or_none(values.get("line_total_ttc", values.get("amount_ttc"))),
            "total": _float_or_none(values.get("total", values.get("amount_ttc"))),
            "confidence": _float_or_none(values.get("confidence")) or _float_or_none(raw.get("confidence")) or 1.0,
            "bbox": raw.get("bbox"),
            "page": _int_or_none(raw.get("page") or values.get("page")),
            "source": raw.get("source") or values.get("source") or "human correction",
        }
        line_items.append(LineItem(**item_payload))
    return line_items


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    normalized = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _review_source_confidence(payload: ReviewCorrectionSubmission) -> dict[str, float | None]:
    original = payload.original_payload or {}
    confidence = original.get("confidence_breakdown") or original.get("erp_json", {}).get("quality", {}).get("confidence_breakdown", {})
    return {
        "ocr": confidence.get("ocr_confidence", original.get("erp_json", {}).get("metadata", {}).get("confidence")),
        "layout": confidence.get("layout_confidence"),
        "table": confidence.get("table_confidence"),
        "fields": confidence.get("field_confidence"),
    }


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


def get_correction_memory(*, tenant_id: str = "default", document_family: str | None = None, supplier_identity: str | None = None) -> dict[str, Any]:
    records = load_correction_records()
    memory: dict[str, Any] = {
        "known_supplier_names": [],
        "known_customer_names": [],
        "common_item_descriptions": [],
        "common_field_labels": [],
        "record_count": 0,
        "tenant_id": tenant_id,
        "document_family": document_family,
        "supplier_identity": supplier_identity,
    }
    suppliers: Counter[str] = Counter()
    customers: Counter[str] = Counter()
    items: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    for record in records:
        if (record.get("tenant_id") or "default") != tenant_id:
            continue
        if document_family and record.get("document_family") not in {None, document_family}:
            continue
        if supplier_identity and record.get("supplier_identity") not in {None, supplier_identity}:
            continue
        memory["record_count"] += 1
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


def boost_candidates_from_memory(
    candidates: dict[str, list[Any]],
    text: str | None = None,
    *,
    tenant_id: str = "default",
    document_family: str | None = None,
    supplier_identity: str | None = None,
) -> None:
    memory = get_correction_memory(tenant_id=tenant_id, document_family=document_family, supplier_identity=supplier_identity)
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
                if (
                    value
                    and value.lower() in lowered
                    and _memory_text_context_allows(field_name, value, text)
                    and not any(str(candidate.value) == value for candidate in candidates.get(field_name, []))
                ):
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


def _memory_text_context_allows(field_name: str, value: str, text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    value_index = lowered.find(value.lower())
    if value_index < 0:
        return False
    window = lowered[max(0, value_index - 160): value_index + len(value) + 160]
    if any(marker in window for marker in ("description", "qty", "quantity", "price", "total", "iban", "swift", "footer")):
        return False
    if field_name == "supplier_name":
        return any(marker in window for marker in ("seller", "supplier", "vendor", "from", "fournisseur"))
    if field_name == "customer_name":
        return any(marker in window for marker in ("client", "customer", "bill to", "acheteur"))
    return False


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
