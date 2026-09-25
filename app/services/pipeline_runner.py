from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from app.core.config import settings
from app.core.schemas import DocumentPreview, OCRLine, OCRResult, ProcessInvoiceResponse
from app.services.bbox_contract import apply_public_bbox_contract, bbox_loss_stage, count_public_ocr_boxes
from app.services.document_classifier import classify_document
from app.services.document_layout import analyze_document_layout
from app.services.dynamic_tables import build_dynamic_review_payload, compute_unmapped_ocr_ratio
from app.services.erp_mapper import build_erp_json, map_to_flat_erp
from app.services.extraction_quality import apply_extraction_quality_gate, build_validated_erp_json
from app.services.field_enricher import build_expanded_fields, build_field_boxes
from app.services.field_extractor import extract_with_candidates
from app.services.tradenet_field_extractor import extract_tradenet_fields
from app.services.file_loader import LoadedDocument, load_document
from app.services.json_writer import write_erp_json, write_invoice_validation_report
from app.services.layout_analyzer import LayoutAnalyzer
from app.services.layout_model.layout_model_router import detect_layout_blocks_with_model
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
from app.services.performance_timer import PipelineTimer
from app.services.review_assistant import build_review_assistant
from app.services.dossier_segmentation import (
    LogicalDocumentGroup,
    PageClassification,
    classify_pages,
    group_logical_documents,
)


@dataclass(frozen=True)
class ProcessedLogicalDocument:
    group: LogicalDocumentGroup
    response: ProcessInvoiceResponse


@dataclass(frozen=True)
class DossierProcessResult:
    source_file: str
    page_count: int
    page_classifications: tuple[PageClassification, ...]
    logical_documents: tuple[ProcessedLogicalDocument, ...]
    document_preview: DocumentPreview
    ocr_engine: str
    timings: dict


def process_dossier_file(
    path: Path,
    *,
    original_filename: str | None = None,
    ocr_engine: OCREngine | None = None,
    persist_erp_json: bool = False,
    ocr_mode: str | None = None,
    use_ocr_cache: bool = True,
    refresh_ocr_cache: bool = False,
    timing_recorder: PipelineTimer | None = None,
) -> DossierProcessResult:
    """OCR a dossier once, then reuse the existing pipeline per logical document."""
    timer = timing_recorder
    timings: dict = {}
    source_file = original_filename or path.name
    with _timer_stage(timer, "total_pipeline", document=source_file, dossier=True):
        stage_started = time.perf_counter()
        with _timer_stage(timer, "file_loading", input_type=path.suffix.lower(), dossier=True):
            document = load_document(path, source_file, timing_recorder=timer)
        timings["file_loading"] = round(time.perf_counter() - stage_started, 4)
        _set_document_timer_metadata(timer, document)
        document_preview = generate_document_preview(document)

        stage_started = time.perf_counter()
        with _timer_stage(timer, "ocr_engine_initialization", ocr_mode=ocr_mode, dossier=True):
            engine = ocr_engine or OCREngine(
                mode=ocr_mode,
                use_disk_cache=use_ocr_cache,
                refresh_cache=refresh_ocr_cache,
                timing_recorder=timer,
            )
            if ocr_engine is not None:
                setattr(engine, "timing_recorder", timer)
        ocr_result = engine.run(document.images, document.embedded_text)
        timings.update(getattr(engine, "last_timings", {}))
        timings["ocr"] = round(time.perf_counter() - stage_started, 4)

        page_classifications = classify_pages(ocr_result)
        suspected_tradenet_pages = [
            item.page_number for item in page_classifications
            if item.document_type == "customs_declaration"
            and item.document_family in {None, "customs_tradenet_v1", "customs_douanes_tunisiennes_v1"}
        ]
        fallback_runner = getattr(engine, "run_fallback_regions", None)
        if suspected_tradenet_pages and callable(fallback_runner):
            page_images = [document.images[page_number - 1] for page_number in suspected_tradenet_pages if 0 < page_number <= len(document.images)]
            page_numbers = [page_number for page_number in suspected_tradenet_pages if 0 < page_number <= len(document.images)]
            if page_images:
                with _timer_stage(timer, "tradenet_header_ocr", pages=page_numbers):
                    header_lines = fallback_runner(page_images, ["header_parties"], page_numbers=page_numbers)
                if header_lines:
                    ocr_result = _merge_ocr_result(ocr_result, header_lines)
                    timings.update(getattr(engine, "last_timings", {}))
                    page_classifications = classify_pages(ocr_result)
        tradenet_pages = [
            item.page_number for item in page_classifications
            if item.document_type == "customs_declaration"
            and item.document_family in {None, "customs_tradenet_v1", "customs_douanes_tunisiennes_v1"}
            and 0 < item.page_number <= len(document.images)
        ]
        if tradenet_pages and callable(fallback_runner):
            targeted_started = time.perf_counter()
            with _timer_stage(timer, "tradenet_targeted_ocr", pages=tradenet_pages):
                targeted_lines = fallback_runner(
                    [document.images[page_number - 1] for page_number in tradenet_pages],
                    ["tradenet_declaration_header", "tradenet_parties", "tradenet_financial"],
                    page_numbers=tradenet_pages,
                )
            timings["tradenet_targeted_ocr"] = round(time.perf_counter() - targeted_started, 4)
            timings["tradenet_targeted_lines"] = len(targeted_lines)
            if targeted_lines:
                ocr_result = _merge_ocr_result(ocr_result, targeted_lines)
                timings.update(getattr(engine, "last_timings", {}))
                page_classifications = classify_pages(ocr_result)
            total_retry_pages = []
            for page_number in tradenet_pages:
                page_lines = [line for line in ocr_result.lines if line.page_number == page_number]
                image = document.images[page_number - 1]
                preview_fields = extract_tradenet_fields(
                    page_lines,
                    page_dimensions={page_number: (int(image.shape[1]), int(image.shape[0]))},
                )
                total = preview_fields["customs_total_value_tnd"]
                if total.value is None or (total.confidence or 0.0) < 0.82:
                    total_retry_pages.append(page_number)
            if total_retry_pages:
                retry_started = time.perf_counter()
                with _timer_stage(timer, "tradenet_customs_total_retry", pages=total_retry_pages):
                    total_lines = fallback_runner(
                        [document.images[page_number - 1] for page_number in total_retry_pages],
                        ["tradenet_customs_total"],
                        page_numbers=total_retry_pages,
                    )
                retry_elapsed = time.perf_counter() - retry_started
                timings["tradenet_customs_total_retry"] = round(retry_elapsed, 4)
                timings["tradenet_targeted_ocr"] = round(timings["tradenet_targeted_ocr"] + retry_elapsed, 4)
                timings["tradenet_targeted_lines"] += len(total_lines)
                if total_lines:
                    ocr_result = _merge_ocr_result(ocr_result, total_lines)
                    timings.update(getattr(engine, "last_timings", {}))
                    page_classifications = classify_pages(ocr_result)
        groups = group_logical_documents(page_classifications)
        processed: list[ProcessedLogicalDocument] = []
        for group in groups:
            logical_document = _logical_loaded_document(document, group.pages)
            logical_ocr = _logical_ocr_result(ocr_result, group.pages)
            response = _process_ocr_document(
                logical_document,
                logical_ocr,
                timings=dict(timings),
                include_preview=False,
                persist_erp_json=persist_erp_json,
                ocr_engine=engine,
                timing_recorder=timer,
                physical_page_numbers=group.pages,
                document_family=group.document_family,
                fixed_customs_form=(
                    group.document_type == "customs_declaration"
                    and any("customs_structure" in item.matched_anchors for item in group.page_classifications)
                ),
            )
            processed.append(ProcessedLogicalDocument(group=group, response=response))

    return DossierProcessResult(
        source_file=source_file,
        page_count=ocr_result.page_count,
        page_classifications=tuple(page_classifications),
        logical_documents=tuple(processed),
        document_preview=document_preview,
        ocr_engine=ocr_result.engine,
        timings=timings,
    )


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
    timing_recorder: PipelineTimer | None = None,
) -> ProcessInvoiceResponse:
    timer = timing_recorder
    timings: dict[str, float] = {}
    with _timer_stage(timer, "total_pipeline", document=original_filename or path.name):
        stage_started = time.perf_counter()
        with _timer_stage(timer, "file_loading", input_type=path.suffix.lower()):
            document = load_document(path, original_filename or path.name, timing_recorder=timer)
        timings["file_loading"] = round(time.perf_counter() - stage_started, 4)
        _set_document_timer_metadata(timer, document)
        stage_started = time.perf_counter()
        with _timer_stage(timer, "ocr_engine_initialization", ocr_mode=ocr_mode):
            engine = ocr_engine or OCREngine(mode=ocr_mode, use_disk_cache=use_ocr_cache, refresh_cache=refresh_ocr_cache, timing_recorder=timer)
            if ocr_engine is not None:
                setattr(engine, "timing_recorder", timer)
        ocr_result = engine.run(document.images, document.embedded_text)
        timings.update(getattr(engine, "last_timings", {}))
        timings["ocr"] = round(time.perf_counter() - stage_started, 4)
        return _process_ocr_document(document, ocr_result, timings=timings, include_preview=include_preview, persist_erp_json=persist_erp_json, ocr_engine=engine, timing_recorder=timer)


def process_loaded_document(
    *,
    document,
    ocr_engine: OCREngine | None = None,
    include_preview: bool = True,
    persist_erp_json: bool = False,
    timing_recorder: PipelineTimer | None = None,
) -> ProcessInvoiceResponse:
    timer = timing_recorder
    engine = ocr_engine or OCREngine()
    setattr(engine, "timing_recorder", timer)
    timings: dict[str, float] = {}
    stage_started = time.perf_counter()
    with _timer_stage(timer, "response_preparation", part="preview_generation"):
        document_preview = generate_document_preview(document) if include_preview else None
    timings["preview_generation"] = round(time.perf_counter() - stage_started, 4)
    stage_started = time.perf_counter()
    ocr_result = engine.run(document.images, document.embedded_text)
    timings.update(getattr(engine, "last_timings", {}))
    timings["ocr"] = round(time.perf_counter() - stage_started, 4)
    return _process_ocr_document(document, ocr_result, timings=timings, include_preview=False, persist_erp_json=persist_erp_json, ocr_engine=engine, timing_recorder=timer)


def _process_ocr_document(document, ocr_result, *, timings: dict[str, float], include_preview: bool, persist_erp_json: bool, ocr_engine: OCREngine | None = None, timing_recorder: PipelineTimer | None = None, physical_page_numbers: tuple[int, ...] | list[int] | None = None, document_family: str | None = None, fixed_customs_form: bool = False) -> ProcessInvoiceResponse:
    timer = timing_recorder
    with _timer_stage(timer, "response_preparation", part="preview_generation"):
        document_preview = generate_document_preview(document) if include_preview else None
    if not ocr_result.raw_text:
        raise ValueError("No text could be extracted from the invoice")

    stage_started = time.perf_counter()
    layout_analyzer = LayoutAnalyzer(ocr_result.lines)
    layout_model_debug: dict[str, Any] = {"enabled": False}
    layout_retry_debug: dict[str, Any] = {"attempted": False}
    with _timer_stage(timer, "semantic_block_detection"):
        model_layout_blocks, layout_model_debug = detect_layout_blocks_with_model(document.images, ocr_result.lines)
        layout_blocks = model_layout_blocks or layout_analyzer.detect_layout_blocks()
        if not model_layout_blocks:
            layout_blocks, layout_retry_debug = _retry_layout_if_unmapped(layout_analyzer, layout_blocks, ocr_result.lines)
    with _timer_stage(timer, "layout_analysis"):
        layout_debug = analyze_document_layout(ocr_result.lines)
    timings["layout_analysis"] = round(time.perf_counter() - stage_started, 4)
    classification = classify_document(ocr_result.raw_text, ocr_result.lines)
    stage_started = time.perf_counter()
    fields, candidates, field_confidences, extraction_debug = extract_with_candidates(
        ocr_result.raw_text,
        ocr_result.lines,
        classification,
        timing_recorder=timer,
    )
    if ocr_engine and ocr_engine.mode == "balanced" and not extraction_debug.get("fallback_recovery"):
        requested_fallbacks = determine_required_fallbacks(fields=fields, ocr_result=ocr_result, extraction_debug=extraction_debug)
        if requested_fallbacks:
            if physical_page_numbers is None:
                fallback_lines = ocr_engine.run_fallback_regions(document.images, requested_fallbacks)
            else:
                fallback_lines = ocr_engine.run_fallback_regions(
                    document.images,
                    requested_fallbacks,
                    page_numbers=physical_page_numbers,
                )
            if fallback_lines:
                ocr_result = _merge_ocr_result(ocr_result, fallback_lines)
                layout_analyzer = LayoutAnalyzer(ocr_result.lines)
                with _timer_stage(timer, "semantic_block_detection", fallback=True):
                    model_layout_blocks, layout_model_debug = detect_layout_blocks_with_model(document.images, ocr_result.lines)
                    layout_blocks = model_layout_blocks or layout_analyzer.detect_layout_blocks()
                    if not model_layout_blocks:
                        layout_blocks, layout_retry_debug = _retry_layout_if_unmapped(layout_analyzer, layout_blocks, ocr_result.lines)
                with _timer_stage(timer, "layout_analysis", fallback=True):
                    layout_debug = analyze_document_layout(ocr_result.lines)
                classification = classify_document(ocr_result.raw_text, ocr_result.lines)
                fields, candidates, field_confidences, extraction_debug = extract_with_candidates(
                    ocr_result.raw_text,
                    ocr_result.lines,
                    classification,
                    timing_recorder=timer,
                )
                extraction_debug["fallback_recovery"] = {
                    "requested_regions": requested_fallbacks,
                    "added_lines": len(fallback_lines),
                }
                timings.update(getattr(ocr_engine, "last_timings", {}))
    timings["field_extraction"] = round(time.perf_counter() - stage_started, 4)
    stage_started = time.perf_counter()
    with _timer_stage(timer, "financial_validation", part="quality_gate"):
        quality_gate = apply_extraction_quality_gate(fields, candidates, field_confidences)
        fields = quality_gate.sanitized_fields
    expanded_fields = build_expanded_fields(fields, candidates, field_confidences, ocr_result.raw_text)
    if fixed_customs_form or document_family in {"customs_tradenet_v1", "customs_douanes_tunisiennes_v1"}:
        page_dimensions = _physical_page_dimensions(document, physical_page_numbers)
        tradenet_fields = extract_tradenet_fields(ocr_result.lines, page_dimensions=page_dimensions)
        expanded_fields.update(tradenet_fields)
        extraction_debug["tradenet_field_extraction"] = {
            "source_fields": [
                "declaration_number", "declaration_date", "declaration_type",
                "exporter", "importer", "ptfn_amount", "currency_conversion_rate",
                "customs_total_value_tnd", "declaration_article_count",
            ],
            "declaration_code": {
                "source": "derived",
                "derived_from": ["declaration_type", "declaration_article_count"],
            },
        }
    field_boxes = build_field_boxes(expanded_fields)
    extraction_debug["layout_analysis"] = layout_debug
    extraction_debug["layout_model"] = layout_model_debug
    extraction_debug["layout_retry"] = layout_retry_debug
    with _timer_stage(timer, "financial_validation", part="validate_invoice"):
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
    with _timer_stage(timer, "financial_validation", part="rows_and_financials"):
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
    with _timer_stage(timer, "confidence_computation", pass_name="base"):
        base_confidence = calculate_confidence(
            ocr=ocr_result.confidence,
            layout=layout_confidence,
            table=table_confidence,
            fields=field_confidence,
            financial=financial_reasoning["financial_consistency_score"],
            validation=row_summary["validation_score"],
            validation_status=validation.status,
        )
    with _timer_stage(timer, "erp_readiness"):
        erp_readiness = assess_erp_readiness(fields, row_summary=row_summary, financial=financial_reasoning, confidence=base_confidence["overall_confidence"])
    with _timer_stage(timer, "confidence_computation", pass_name="final"):
        confidence_breakdown = calculate_confidence(
            ocr=ocr_result.confidence,
            layout=layout_confidence,
            table=table_confidence,
            fields=field_confidence,
            financial=financial_reasoning["financial_consistency_score"],
            validation=row_summary["validation_score"],
            erp=erp_readiness["erp_ready_score"],
            validation_status=validation.status,
            missing_required_fields=erp_readiness["missing_fields"],
            erp_ready=erp_readiness["ready"],
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
    with _timer_stage(timer, "response_preparation", part="erp_payload"):
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
    with _timer_stage(timer, "response_preparation", part="dynamic_review_payload"):
        dynamic_tables, extraction_layer, erp_layer = build_dynamic_review_payload(
            fields=review_display_fields,
            expanded_fields=expanded_fields,
            layout_blocks=layout_blocks,
            ocr_blocks=ocr_result.lines,
            validation=validation,
            erp_json=erp_json,
        )
    if persist_erp_json:
        with _timer_stage(timer, "result_serialization", part="erp_disk_write"):
            write_erp_json(erp_json)
            write_invoice_validation_report(invoice_report, document.source_file, fields.invoice_number)
    validated_erp_json = build_validated_erp_json(erp_json, quality_gate.validation_report)
    validated_erp_json["erp_readiness"] = erp_readiness
    validated_erp_json["erp_export_allowed"] = erp_readiness["ready"]
    erp_export = map_to_flat_erp(erp_json)
    erp_export.source_payload = validated_erp_json
    extraction_debug["stage_timings"] = timings
    with _timer_stage(timer, "response_preparation", part="pydantic_response"):
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
            field_consistency=financial_reasoning.get("field_consistency", {}),
            confidence_breakdown=confidence_breakdown,
            erp_readiness=erp_readiness,
            invoice_validation_report=invoice_report,
            correction_suggestions=correction_suggestions,
            review_assistant={},
            duplicate_detection=duplicate_detection,
            fraud_indicators=fraud,
        )
        apply_public_bbox_contract(response)
        response.review_assistant = build_review_assistant(response)
    timings["public_boxes_count"] = count_public_ocr_boxes(response)
    timings["bbox_loss_stage"] = bbox_loss_stage(response)
    response.extraction_debug["stage_timings"] = timings
    serialization_started = time.perf_counter()
    with _timer_stage(timer, "result_serialization", part="model_dump"):
        response.model_dump(mode="json")
    timings["report_serialization"] = round(time.perf_counter() - serialization_started, 4)
    response.extraction_debug["stage_timings"] = timings
    _set_result_timer_metadata(timer, response, candidates)
    return response


def _logical_loaded_document(document: LoadedDocument, pages: tuple[int, ...]) -> LoadedDocument:
    images = []
    for page_number in pages:
        index = page_number - 1
        if index < 0 or index >= len(document.images):
            raise ValueError(f"Logical document references unavailable physical page {page_number}")
        images.append(document.images[index])
    return LoadedDocument(
        source_file=document.source_file,
        extension=document.extension,
        embedded_text="",
        images=images,
    )


def _logical_ocr_result(ocr_result: OCRResult, pages: tuple[int, ...]) -> OCRResult:
    selected = set(pages)
    lines = [line.model_copy(deep=True) for line in ocr_result.lines if line.page_number in selected]
    confidence_values = [line.confidence for line in lines if line.confidence is not None]
    confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else None
    return OCRResult(
        raw_text="\n".join(line.text for line in lines),
        lines=lines,
        confidence=confidence,
        engine=ocr_result.engine,
        page_count=len(pages),
    )


def _physical_page_dimensions(document: LoadedDocument, pages: tuple[int, ...] | list[int] | None) -> dict[int, tuple[int, int]]:
    page_numbers = list(pages or range(1, len(document.images) + 1))
    return {
        page_number: (int(image.shape[1]), int(image.shape[0]))
        for page_number, image in zip(page_numbers, document.images)
    }


def _retry_layout_if_unmapped(layout_analyzer: LayoutAnalyzer, layout_blocks: list, ocr_lines: list[OCRLine]) -> tuple[list, dict[str, Any]]:
    initial_ratio = compute_unmapped_ocr_ratio(layout_blocks, ocr_lines)
    threshold = float(settings.unmapped_ratio_retry_threshold or 0.60)
    debug: dict[str, Any] = {
        "attempted": False,
        "initial_unmapped_ratio": initial_ratio,
        "threshold": threshold,
    }
    if initial_ratio <= threshold:
        return layout_blocks, debug
    original_threshold = settings.layout_fuzzy_threshold
    try:
        settings.layout_fuzzy_threshold = int(settings.layout_retry_fuzzy_threshold or original_threshold)
        retry_blocks = layout_analyzer.detect_layout_blocks()
    finally:
        settings.layout_fuzzy_threshold = original_threshold
    retry_ratio = compute_unmapped_ocr_ratio(retry_blocks, ocr_lines)
    debug.update({
        "attempted": True,
        "retry_threshold": settings.layout_retry_fuzzy_threshold,
        "retry_unmapped_ratio": retry_ratio,
        "accepted": retry_ratio < initial_ratio,
    })
    return (retry_blocks if retry_ratio < initial_ratio else layout_blocks), debug


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


def _timer_stage(timing_recorder: PipelineTimer | None, name: str, **metadata):
    if timing_recorder is None:
        return _noop_stage()
    return timing_recorder.stage(name, **metadata)


def _set_document_timer_metadata(timer: PipelineTimer | None, document) -> None:
    if timer is None:
        return
    dimensions = [
        {"width": int(image.shape[1]), "height": int(image.shape[0])}
        for image in getattr(document, "images", []) or []
        if getattr(image, "shape", None) is not None and len(image.shape) >= 2
    ]
    timer.set_metadata(
        document=getattr(document, "source_file", None),
        filename=getattr(document, "source_file", None),
        input_type=getattr(document, "extension", None),
        page_count=len(getattr(document, "images", []) or []),
        image_dimensions=dimensions,
    )


def _set_result_timer_metadata(timer: PipelineTimer | None, response: ProcessInvoiceResponse, candidates: dict[str, list]) -> None:
    if timer is None:
        return
    timings = response.extraction_debug.get("stage_timings", {}) if response.extraction_debug else {}
    timer.set_metadata(
        ocr_engine=response.erp_json.metadata.ocr_engine if response.erp_json else None,
        ocr_mode=timings.get("ocr_mode"),
        cache_hit=bool(timings.get("disk_cache_hit") or timings.get("memory_cache_hits")),
        ocr_blocks=len(response.ocr_blocks),
        layout_blocks=len(response.layout_blocks),
        candidate_count=sum(len(values) for values in candidates.values()),
        extracted_lines=len(response.all_line_items or response.detected_fields.line_items),
        validation_status=response.validation.status if response.validation else None,
    )


class _noop_stage:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False
