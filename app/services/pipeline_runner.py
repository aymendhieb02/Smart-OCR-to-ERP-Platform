from __future__ import annotations

from pathlib import Path
import time

from app.core.schemas import OCRLine, OCRResult, ProcessInvoiceResponse
from app.services.bbox_contract import apply_public_bbox_contract, bbox_loss_stage, count_public_ocr_boxes
from app.services.document_classifier import classify_document
from app.services.document_layout import analyze_document_layout
from app.services.dynamic_tables import build_dynamic_review_payload
from app.services.erp_mapper import build_erp_json, map_to_flat_erp
from app.services.extraction_quality import apply_extraction_quality_gate, build_validated_erp_json
from app.services.field_enricher import build_expanded_fields, build_field_boxes
from app.services.field_extractor import extract_with_candidates
from app.services.file_loader import load_document
from app.services.json_writer import write_erp_json, write_invoice_validation_report
from app.services.layout_analyzer import LayoutAnalyzer
from app.services.ocr_engine import OCREngine
from app.services.ocr_fallback_planner import determine_required_fallbacks
from app.services.preview_generator import generate_document_preview
from app.services.validation_explainer import build_validation_explanation
from app.services.validator import validate_invoice
from app.services.row_validation_engine import summarize_rows, validate_rows
from app.services.financial_reasoner import reason_financials
from app.services.confidence_engine import calculate_confidence
from app.services.erp_readiness import assess_erp_readiness
from app.services.correction_suggestions import suggest_corrections
from app.services.duplicate_detector import detect_duplicates
from app.services.fraud_indicators import detect_fraud_indicators
from app.services.invoice_validation_report import build_invoice_validation_report


def process_document_file(
    path: Path,
    *,
    original_filename: str | None = None,
    ocr_engine: OCREngine | None = None,
    include_preview: bool = True,
    persist_erp_json: bool = False,
    ocr_mode: str | None = None,
    use_ocr_cache: bool = True,
    refresh_ocr_cache: bool = False,
) -> ProcessInvoiceResponse:
    timings: dict[str, float] = {}
    stage_started = time.perf_counter()
    document = load_document(path, original_filename or path.name)
    timings["file_loading"] = round(time.perf_counter() - stage_started, 4)
    stage_started = time.perf_counter()
    engine = ocr_engine or OCREngine(mode=ocr_mode, use_disk_cache=use_ocr_cache, refresh_cache=refresh_ocr_cache)
    ocr_result = engine.run(document.images, document.embedded_text)
    timings.update(getattr(engine, "last_timings", {}))
    timings["ocr"] = round(time.perf_counter() - stage_started, 4)
    return _process_ocr_document(document, ocr_result, timings=timings, include_preview=include_preview, persist_erp_json=persist_erp_json, ocr_engine=engine)


def process_loaded_document(
    *,
    document,
    ocr_engine: OCREngine | None = None,
    include_preview: bool = True,
    persist_erp_json: bool = False,
) -> ProcessInvoiceResponse:
    engine = ocr_engine or OCREngine()
    timings: dict[str, float] = {}
    stage_started = time.perf_counter()
    document_preview = generate_document_preview(document) if include_preview else None
    timings["preview_generation"] = round(time.perf_counter() - stage_started, 4)
    stage_started = time.perf_counter()
    ocr_result = engine.run(document.images, document.embedded_text)
    timings.update(getattr(engine, "last_timings", {}))
    timings["ocr"] = round(time.perf_counter() - stage_started, 4)
    return _process_ocr_document(document, ocr_result, timings=timings, include_preview=False, persist_erp_json=persist_erp_json, ocr_engine=engine)


def _process_ocr_document(document, ocr_result, *, timings: dict[str, float], include_preview: bool, persist_erp_json: bool, ocr_engine: OCREngine | None = None) -> ProcessInvoiceResponse:
    document_preview = generate_document_preview(document) if include_preview else None
    if not ocr_result.raw_text:
        raise ValueError("No text could be extracted from the invoice")

    stage_started = time.perf_counter()
    layout_analyzer = LayoutAnalyzer(ocr_result.lines)
    layout_blocks = layout_analyzer.detect_layout_blocks()
    layout_debug = analyze_document_layout(ocr_result.lines)
    timings["layout_analysis"] = round(time.perf_counter() - stage_started, 4)
    classification = classify_document(ocr_result.raw_text, ocr_result.lines)
    stage_started = time.perf_counter()
    fields, candidates, field_confidences, extraction_debug = extract_with_candidates(
        ocr_result.raw_text,
        ocr_result.lines,
        classification,
    )
    if ocr_engine and ocr_engine.mode == "balanced" and not extraction_debug.get("fallback_recovery"):
        requested_fallbacks = determine_required_fallbacks(fields=fields, ocr_result=ocr_result, extraction_debug=extraction_debug)
        if requested_fallbacks:
            fallback_lines = ocr_engine.run_fallback_regions(document.images, requested_fallbacks)
            if fallback_lines:
                ocr_result = _merge_ocr_result(ocr_result, fallback_lines)
                layout_analyzer = LayoutAnalyzer(ocr_result.lines)
                layout_blocks = layout_analyzer.detect_layout_blocks()
                layout_debug = analyze_document_layout(ocr_result.lines)
                classification = classify_document(ocr_result.raw_text, ocr_result.lines)
                fields, candidates, field_confidences, extraction_debug = extract_with_candidates(
                    ocr_result.raw_text,
                    ocr_result.lines,
                    classification,
                )
                extraction_debug["fallback_recovery"] = {
                    "requested_regions": requested_fallbacks,
                    "added_lines": len(fallback_lines),
                }
                timings.update(getattr(ocr_engine, "last_timings", {}))
    timings["field_extraction"] = round(time.perf_counter() - stage_started, 4)
    stage_started = time.perf_counter()
    quality_gate = apply_extraction_quality_gate(fields, candidates, field_confidences)
    fields = quality_gate.sanitized_fields
    expanded_fields = build_expanded_fields(fields, candidates, field_confidences, ocr_result.raw_text)
    field_boxes = build_field_boxes(expanded_fields)
    extraction_debug["layout_analysis"] = layout_debug
    validation = validate_invoice(fields, ocr_result, classification)
    timings["table_extraction"] = round(time.perf_counter() - stage_started, 4)
    table_debug = extraction_debug.setdefault("table_extraction_debug", {})
    table_debug["validated_rows"] = [item.model_dump(mode="json") for item in quality_gate.line_items_validated]
    table_debug["review_rows"] = [item.model_dump(mode="json") for item in quality_gate.line_items_needs_review]
    table_debug["final_line_items"] = [item.model_dump(mode="json") for item in fields.line_items]
    table_debug["all_line_items"] = [item.model_dump(mode="json") for item in quality_gate.line_items_validated + quality_gate.line_items_needs_review]
    table_debug["counts"] = {
        "candidate_rows": len(table_debug.get("raw_candidate_rows", [])),
        "validated_rows": len(quality_gate.line_items_validated),
        "needs_review_rows": len(quality_gate.line_items_needs_review),
        "final_line_items": len(fields.line_items),
        "all_line_items": len(quality_gate.line_items_validated + quality_gate.line_items_needs_review),
    }
    validation.warnings.extend(quality_gate.validation_report.get("warnings", []))
    if quality_gate.validation_report.get("extraction_status") == "needs_review" and validation.status == "valid":
        validation.status = "needs_review"
        validation.is_valid = False
    all_items = quality_gate.line_items_validated + quality_gate.line_items_needs_review
    business_started = time.perf_counter()
    row_validation = validate_rows(all_items)
    row_summary = summarize_rows(row_validation)
    financial_reasoning = reason_financials(
        fields,
        all_items,
        document_type=classification.document_type,
        shipping=_expanded_number(expanded_fields, "shipping_amount"),
        discount=_expanded_number(expanded_fields, "discount_amount"),
        stamp_tax=_expanded_number(expanded_fields, "stamp_tax_amount"),
    )
    layout_confidence = _average([block.confidence for block in layout_blocks])
    table_confidence = _average([table.get("confidence") for table in layout_debug.get("tables", [])])
    field_confidence = _average(list(field_confidences.values()))
    base_confidence = calculate_confidence(
        ocr=ocr_result.confidence,
        layout=layout_confidence,
        table=table_confidence,
        fields=field_confidence,
        financial=financial_reasoning["financial_consistency_score"],
        validation=row_summary["validation_score"],
    )
    erp_readiness = assess_erp_readiness(fields, row_summary=row_summary, financial=financial_reasoning, confidence=base_confidence["overall_confidence"])
    confidence_breakdown = calculate_confidence(
        ocr=ocr_result.confidence,
        layout=layout_confidence,
        table=table_confidence,
        fields=field_confidence,
        financial=financial_reasoning["financial_consistency_score"],
        validation=row_summary["validation_score"],
        erp=erp_readiness["erp_ready_score"],
    )
    correction_suggestions = suggest_corrections(fields)
    duplicate_detection = detect_duplicates(fields)
    fraud = detect_fraud_indicators(fields, financial=financial_reasoning, duplicate=duplicate_detection, validation={"missing_fields": erp_readiness["missing_fields"]})
    if financial_reasoning["financial_errors"]:
        validation.errors.extend(financial_reasoning["financial_errors"])
    validation.warnings.extend(financial_reasoning["financial_warnings"])
    if erp_readiness["erp_ready_status"] == "Rejected":
        validation.status = "invalid"
        validation.is_valid = False
    elif erp_readiness["erp_ready_status"] == "Needs Review" and validation.status == "valid":
        validation.status = "needs_review"
        validation.is_valid = False
    invoice_report = build_invoice_validation_report(
        fields=fields,
        rows=row_validation,
        financial=financial_reasoning,
        confidence=confidence_breakdown,
        readiness=erp_readiness,
        warnings=validation.warnings,
        errors=validation.errors,
        corrections=correction_suggestions,
        duplicate=duplicate_detection,
        fraud=fraud,
    )
    timings["business_reasoning"] = round(time.perf_counter() - business_started, 4)
    validation_explanation = build_validation_explanation(validation)
    erp_json = build_erp_json(
        fields=fields,
        validation=validation,
        source_file=document.source_file,
        ocr_engine=ocr_result.engine,
        confidence=ocr_result.confidence,
        document_type=classification.document_type,
        field_confidences=field_confidences,
        languages=["fr", "en", "ar"],
        expanded_fields=expanded_fields,
    )
    erp_json.quality["validation_explanation"] = validation_explanation.model_dump(mode="json")
    erp_json.quality.update({
        "overall_confidence": confidence_breakdown["overall_confidence"],
        "confidence_breakdown": confidence_breakdown,
        "erp_readiness": erp_readiness,
        "financial_reasoning": financial_reasoning,
        "fraud_indicators": fraud,
    })
    review_display_fields = fields.model_copy(update={"line_items": all_items})
    dynamic_tables, extraction_layer, erp_layer = build_dynamic_review_payload(
        fields=review_display_fields,
        expanded_fields=expanded_fields,
        layout_blocks=layout_blocks,
        ocr_blocks=ocr_result.lines,
        validation=validation,
        erp_json=erp_json,
    )
    if persist_erp_json:
        write_erp_json(erp_json)
        write_invoice_validation_report(invoice_report, document.source_file, fields.invoice_number)
    validated_erp_json = build_validated_erp_json(erp_json, quality_gate.validation_report)
    validated_erp_json["erp_readiness"] = erp_readiness
    validated_erp_json["erp_export_allowed"] = erp_readiness["ready"]
    erp_export = map_to_flat_erp(erp_json)
    erp_export.source_payload = validated_erp_json
    extraction_debug["stage_timings"] = timings
    response = ProcessInvoiceResponse(
        extracted_text=ocr_result.raw_text,
        document_preview=document_preview,
        layout_blocks=layout_blocks,
        field_boxes=field_boxes,
        ocr_blocks=ocr_result.lines,
        document_classification=classification,
        detected_fields=fields,
        expanded_fields=expanded_fields,
        field_confidences=field_confidences,
        extraction_debug=extraction_debug,
        dynamic_tables=dynamic_tables,
        extraction_layer=extraction_layer,
        erp_layer=erp_layer,
        validation=validation,
        validation_explanation=validation_explanation,
        erp_json=erp_json,
        erp_export=erp_export,
        validated_erp_json=validated_erp_json,
        review_candidates=quality_gate.review_candidates,
        rejected_candidates=quality_gate.rejected_candidates,
        all_ocr_blocks=ocr_result.lines,
        table_candidates=layout_debug.get("tables", []),
        line_items_validated=quality_gate.line_items_validated,
        line_items_needs_review=quality_gate.line_items_needs_review,
        all_line_items=quality_gate.line_items_validated + quality_gate.line_items_needs_review,
        validation_report=quality_gate.validation_report,
        row_validation=row_validation,
        financial_reasoning=financial_reasoning,
        confidence_breakdown=confidence_breakdown,
        erp_readiness=erp_readiness,
        invoice_validation_report=invoice_report,
        correction_suggestions=correction_suggestions,
        duplicate_detection=duplicate_detection,
        fraud_indicators=fraud,
    )
    apply_public_bbox_contract(response)
    timings["public_boxes_count"] = count_public_ocr_boxes(response)
    timings["bbox_loss_stage"] = bbox_loss_stage(response)
    response.extraction_debug["stage_timings"] = timings
    serialization_started = time.perf_counter()
    response.model_dump(mode="json")
    timings["report_serialization"] = round(time.perf_counter() - serialization_started, 4)
    response.extraction_debug["stage_timings"] = timings
    return response


def _average(values: list[float | None], default: float = 0.0) -> float:
    numeric = [float(value) for value in values if value is not None]
    return round(sum(numeric) / len(numeric), 3) if numeric else default


def _merge_ocr_result(original: OCRResult, fallback_lines: list[OCRLine]) -> OCRResult:
    merged: list[OCRLine] = []
    seen: set[tuple[str, int, int, int]] = set()
    for line in [*original.lines, *fallback_lines]:
        text_key = (line.text or "").strip().lower()
        bbox = line.bbox
        key = (
            text_key,
            round(bbox.x1 / 8) if bbox else -1,
            round(bbox.y1 / 8) if bbox else -1,
            line.page_number,
        )
        if not text_key or key in seen:
            continue
        seen.add(key)
        line.line_index = len(merged)
        merged.append(line)
    confidence_values = [line.confidence for line in merged if line.confidence is not None]
    confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else original.confidence
    return OCRResult(
        raw_text="\n".join(line.text for line in merged),
        lines=merged,
        confidence=confidence,
        engine=original.engine,
        page_count=original.page_count,
    )


def _expanded_number(expanded_fields: dict, field_name: str) -> float | None:
    detail = expanded_fields.get(field_name)
    value = getattr(detail, "value", None) if detail else None
    return float(value) if isinstance(value, (int, float)) else None
