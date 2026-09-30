import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.schemas import (
    CorrectionResponse,
    CorrectionSubmission,
    ERPFlatExport,
    ERPInvoiceJSON,
    DossierLogicalDocument,
    DossierRoutingResolutionSubmission,
    DossierRoutingReviewItem,
    DossierPageClassification,
    DossierReviewSummary,
    DossierReconciliationSubmission,
    SimpleDossierInput,
    ProcessDossierResponse,
    ProcessInvoiceResponse,
    ReviewCorrectionResponse,
    ReviewCorrectionSubmission,
    RuspinaAddressRereadContext,
    RuspinaAddressRereadResponse,
    SimpleDossierOutput,
)
from app.services.correction_store import submit_corrections, validate_review_corrections
from app.services.correction_identity import correction_document_id
from app.services.dossier_reconciler import reconcile_dossier
from app.services.erp_mapper import map_to_flat_erp
from app.services.file_loader import load_document_page, save_upload_to_temp
from app.services.manual_field_reread import reread_ruspina_address as run_ruspina_address_reread
from app.services.ocr_engine import OCREngine
from app.services.json_writer import write_erp_json, write_invoice_validation_report
from app.services.pipeline_runner import process_dossier_file
from app.services.dossier_segmentation import semantic_group_for_document
from app.services.dossier_routing_review import register_routing_session, resolve_routing_page
from app.services.simple_dossier_json import build_simple_dossier_output

router = APIRouter()
ocr_engine = OCREngine()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = PROJECT_ROOT / "dataset" / "demo"
DEMO_DOCUMENTS = {
    "good": {
        "filename": "demo_good_invoice.png",
        "title": "Good invoice",
        "description": "Clean invoice with supplier, customer, product rows, totals, and ERP readiness.",
    },
    "review": {
        "filename": "demo_review_invoice.png",
        "title": "Needs-review invoice",
        "description": "Table-heavy invoice used to demonstrate field review and correction.",
    },
    "noisy": {
        "filename": "demo_noisy_document.png",
        "title": "Noisy document",
        "description": "Lower-confidence document used to show safe fallback and blocked export.",
    },
}


@router.post("/process-invoice", response_model=ProcessInvoiceResponse)
async def process_invoice(file: UploadFile = File(...)) -> ProcessInvoiceResponse:
    temp_path: Path | None = None
    try:
        temp_path = await save_upload_to_temp(file)
        result = process_dossier_file(
            temp_path,
            original_filename=file.filename,
            ocr_engine=ocr_engine,
            persist_erp_json=False,
        )
        if len(result.logical_documents) != 1:
            raise HTTPException(
                status_code=422,
                detail="This file contains multiple logical documents; use /process-dossier.",
            )

        response = result.logical_documents[0].response
        response.document_preview = result.document_preview
        write_erp_json(response.erp_json)
        write_invoice_validation_report(
            response.invoice_validation_report,
            result.source_file,
            response.detected_fields.invoice_number,
        )
        return response
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Invoice processing failed: {exc}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.post("/process-dossier", response_model=ProcessDossierResponse)
async def process_dossier(file: UploadFile = File(...)) -> ProcessDossierResponse:
    temp_path: Path | None = None
    try:
        temp_path = await save_upload_to_temp(file)
        result = process_dossier_file(
            temp_path,
            original_filename=file.filename,
            ocr_engine=ocr_engine,
            persist_erp_json=False,
        )
        dossier_id = str(uuid.uuid4())
        if result.routing_context is not None:
            register_routing_session(dossier_id, result.routing_context)
        documents = [
            DossierLogicalDocument(
                logical_document_id=f"{dossier_id}:{item.group.group_id}",
                correction_document_id=correction_document_id(temp_path, item.group.group_id),
                document_index=index,
                document_type=item.group.document_type,
                document_family=item.group.document_family,
                semantic_group=semantic_group_for_document(
                    item.group.document_type,
                    item.group.document_family,
                    tuple(anchor for classification in item.group.page_classifications for anchor in classification.matched_anchors),
                    item.group.page_classifications[0].routing_status if item.group.page_classifications else "auto",
                ),
                physical_page_numbers=list(item.group.pages),
                page_classifications=[
                    DossierPageClassification(
                        **classification.__dict__,
                        semantic_group=semantic_group_for_document(
                            classification.document_type,
                            classification.document_family,
                            classification.matched_anchors,
                            classification.routing_status,
                        ),
                    )
                    for classification in item.group.page_classifications
                ],
                response=item.response,
            )
            for index, item in enumerate(result.logical_documents, start=1)
        ]
        statuses = [item.response.validation.status for item in result.logical_documents]
        valid_count = sum(status == "valid" for status in statuses)
        invalid_count = sum(status in {"invalid", "rejected"} for status in statuses)
        routing_review_items = [
            DossierRoutingReviewItem(
                physical_page=item.page_number,
                current_generic_classification=item.document_type,
                detected_family=item.document_family,
                routing_status=item.routing_status,
                candidate_semantic_groups=list(item.candidate_semantic_groups),
            )
            for item in result.page_classifications
            if item.routing_status == "review_required"
        ]
        needs_review_count = len(statuses) - valid_count - invalid_count + len(routing_review_items)
        summary_status = "invalid" if invalid_count else "needs_review" if needs_review_count else "valid"
        return ProcessDossierResponse(
            dossier_id=dossier_id,
            source_file=result.source_file,
            page_count=result.page_count,
            document_count=len(documents) + len(routing_review_items),
            summary=DossierReviewSummary(
                status=summary_status,
                valid_count=valid_count,
                needs_review_count=needs_review_count,
                invalid_count=invalid_count,
            ),
            document_preview=result.document_preview,
            page_classifications=[
                DossierPageClassification(
                    **classification.__dict__,
                    semantic_group=semantic_group_for_document(
                        classification.document_type,
                        classification.document_family,
                        classification.matched_anchors,
                        classification.routing_status,
                    ),
                )
                for classification in result.page_classifications
            ],
            logical_documents=documents,
            routing_review_items=routing_review_items,
            relationships=list(result.relationships),
            ocr_engine=result.ocr_engine,
            timings=result.timings,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Dossier processing failed: {exc}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.post("/resolve-dossier-routing")
async def resolve_dossier_routing(payload: DossierRoutingResolutionSubmission) -> dict:
    """Resolve an ambiguous page using the OCR evidence retained from upload."""
    if payload.semantic_group not in {"page1", "page2", "page3"}:
        raise HTTPException(status_code=422, detail="Choose a supported document type")
    try:
        processed = resolve_routing_page(
            payload.dossier_id,
            payload.physical_page,
            payload.semantic_group,
        )
    except LookupError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    group = processed.group
    document = DossierLogicalDocument(
        logical_document_id=f"{payload.dossier_id}:{group.group_id}",
        correction_document_id=processed.correction_document_id or f"{payload.dossier_id}:{group.group_id}",
        document_index=group.pages[0],
        document_type=group.document_type,
        document_family=group.document_family,
        semantic_group=payload.semantic_group,
        routing_status="manually_resolved",
        physical_page_numbers=list(group.pages),
        page_classifications=[
            DossierPageClassification(
                **classification.__dict__,
                semantic_group=payload.semantic_group,
            )
            for classification in group.page_classifications
        ],
        response=processed.response,
    )
    return {"logical_document": document.model_dump(mode="json"), "resolved_physical_page": payload.physical_page}


@router.post("/export-simple-dossier-json", response_model=SimpleDossierOutput)
async def export_simple_dossier_json(payload: SimpleDossierInput) -> SimpleDossierOutput:
    """Serialize the current dossier review payload without changing ERP export."""
    if payload.routing_review_items:
        raise HTTPException(status_code=409, detail="Resolve ambiguous document routing before exporting dossier JSON")
    try:
        return build_simple_dossier_output(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/demo-documents")
async def list_demo_documents() -> dict:
    return {
        "demo_mode": True,
        "documents": [
            {
                "id": demo_id,
                **metadata,
                "exists": (DEMO_ROOT / metadata["filename"]).exists(),
            }
            for demo_id, metadata in DEMO_DOCUMENTS.items()
        ],
        "note": "Demo documents exercise the normal processing pipeline; they do not bypass extraction or validation.",
    }


@router.post("/demo-documents/{demo_id}/process", response_model=ProcessInvoiceResponse)
async def process_demo_document(demo_id: str) -> ProcessInvoiceResponse:
    metadata = DEMO_DOCUMENTS.get(demo_id)
    if not metadata:
        raise HTTPException(status_code=404, detail=f"Unknown demo document: {demo_id}")

    demo_path = DEMO_ROOT / metadata["filename"]
    if not demo_path.exists():
        raise HTTPException(status_code=404, detail=f"Demo document not found: {metadata['filename']}")

    try:
        return process_document_file(
            demo_path,
            original_filename=demo_path.name,
            ocr_engine=ocr_engine,
            include_preview=True,
            persist_erp_json=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Demo processing failed: {exc}") from exc



@router.post("/corrections", response_model=CorrectionResponse)
async def submit_invoice_corrections(payload: CorrectionSubmission) -> CorrectionResponse:
    try:
        return submit_corrections(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Correction submission failed: {exc}") from exc


@router.post("/review/validate-corrections", response_model=ReviewCorrectionResponse)
async def validate_invoice_review_corrections(payload: ReviewCorrectionSubmission) -> ReviewCorrectionResponse:
    try:
        return validate_review_corrections(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Review correction validation failed: {exc}") from exc


@router.post("/review/ruspina-address/reread", response_model=RuspinaAddressRereadResponse)
async def reread_ruspina_address(
    file: UploadFile = File(...),
    context_json: str = Form(...),
) -> RuspinaAddressRereadResponse:
    """Reread only the reviewed RUSPINA address row; never updates corrections."""
    started = time.perf_counter()
    temp_path: Path | None = None
    try:
        try:
            context = RuspinaAddressRereadContext.model_validate(json.loads(context_json))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid address reread context") from exc
        if (context.semantic_group, context.document_family, context.field) != (
            "page2", "ruspina_reinvoice_v1", "address",
        ):
            raise HTTPException(status_code=422, detail="Only the RUSPINA Page-2 address can be reread")
        temp_path = await save_upload_to_temp(file)
        image = load_document_page(temp_path, context.page)
        result = run_ruspina_address_reread(image, context, ocr_engine)
        result.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Targeted address OCR failed; the current value was not changed") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.post("/review/reconcile-dossier")
async def reconcile_reviewed_dossier(payload: DossierReconciliationSubmission) -> dict:
    documents = [
        SimpleNamespace(
            group=SimpleNamespace(
                group_id=item.logical_document_id,
                document_type=item.document_type,
                document_family=item.document_family,
                pages=tuple(item.physical_page_numbers),
            ),
            response=item.response,
        )
        for item in payload.logical_documents
    ]
    return {"relationships": [item.model_dump(mode="json") for item in reconcile_dossier(documents)]}

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
