from pathlib import Path
import subprocess
import sys

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.schemas import ERPFlatExport, ERPInvoiceJSON, ProcessInvoiceResponse
from app.services.document_classifier import classify_document
from app.services.dynamic_tables import build_dynamic_review_payload
from app.services.erp_mapper import build_erp_json, map_to_flat_erp
from app.services.field_enricher import build_expanded_fields, build_field_boxes
from app.services.field_extractor import extract_with_candidates
from app.services.file_loader import load_document, save_upload_to_temp
from app.services.json_writer import write_erp_json
from app.services.layout_analyzer import LayoutAnalyzer
from app.services.ocr_engine import OCREngine
from app.services.preview_generator import generate_document_preview
from app.services.validator import validate_invoice
from app.services.validation_explainer import build_validation_explanation

router = APIRouter()
ocr_engine = OCREngine()


@router.post("/process-invoice", response_model=ProcessInvoiceResponse)
async def process_invoice(file: UploadFile = File(...)) -> ProcessInvoiceResponse:
    temp_path: Path | None = None
    try:
        temp_path = await save_upload_to_temp(file)
        document = load_document(temp_path, file.filename)
        document_preview = generate_document_preview(document)
        ocr_result = ocr_engine.run(document.images, document.embedded_text)
        if not ocr_result.raw_text:
            raise HTTPException(status_code=422, detail="No text could be extracted from the invoice")

        layout_analyzer = LayoutAnalyzer(ocr_result.lines)
        layout_blocks = layout_analyzer.detect_layout_blocks()
        classification = classify_document(ocr_result.raw_text, ocr_result.lines)
        fields, candidates, field_confidences, extraction_debug = extract_with_candidates(
            ocr_result.raw_text,
            ocr_result.lines,
            classification,
        )
        expanded_fields = build_expanded_fields(fields, candidates, field_confidences, ocr_result.raw_text)
        field_boxes = build_field_boxes(expanded_fields)
        validation = validate_invoice(fields, ocr_result, classification)
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
        dynamic_tables, extraction_layer, erp_layer = build_dynamic_review_payload(
            fields=fields,
            expanded_fields=expanded_fields,
            layout_blocks=layout_blocks,
            ocr_blocks=ocr_result.lines,
            validation=validation,
            erp_json=erp_json,
        )
        write_erp_json(erp_json)
        erp_export = map_to_flat_erp(erp_json)
        return ProcessInvoiceResponse(
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
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Invoice processing failed: {exc}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.post("/export-erp-json", response_model=ERPFlatExport)
async def export_erp_json(payload: ERPInvoiceJSON) -> ERPFlatExport:
    return map_to_flat_erp(payload)


@router.post("/evaluate-dataset")
async def evaluate_dataset() -> dict:
    script = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_dataset.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=script.parents[1],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr or result.stdout)
    return {"report": result.stdout}
