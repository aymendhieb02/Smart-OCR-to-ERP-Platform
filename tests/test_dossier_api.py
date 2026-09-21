from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.schemas import DocumentPreview, ExtractedInvoiceFields, LineItem, PreviewPage, ProcessInvoiceResponse, ValidationResult
from app.main import app
from app.api import routes
from app.services.dossier_segmentation import LogicalDocumentGroup, PageClassification
from app.services.erp_mapper import build_erp_json, map_to_flat_erp
from app.services.pipeline_runner import DossierProcessResult, ProcessedLogicalDocument


def _response(number: str, page: int, *, status: str = "needs_review") -> ProcessInvoiceResponse:
    fields = ExtractedInvoiceFields(
        invoice_number=number,
        line_items=[LineItem(description=f"row-{number}", quantity=1, unit_price=10, total=10, page=page)],
    )
    validation = ValidationResult(status=status, is_valid=status == "valid")
    erp = build_erp_json(fields, validation, "INV 01.pdf", "test", 0.9)
    return ProcessInvoiceResponse(
        extracted_text=f"invoice {number}", detected_fields=fields, validation=validation,
        erp_json=erp, erp_export=map_to_flat_erp(erp), ocr_blocks=[], layout_blocks=[], field_boxes=[],
    )


def _group(index: int, page_numbers: tuple[int, ...], family: str | None, document_type: str = "commercial_invoice") -> LogicalDocumentGroup:
    classifications = tuple(PageClassification(page, document_type, family, 0.9, (), ("test",), page == page_numbers[0]) for page in page_numbers)
    return LogicalDocumentGroup(f"logical_document_{index}", page_numbers, document_type, family, classifications)


def test_process_dossier_returns_typed_isolated_documents_and_physical_preview(monkeypatch):
    documents = (
        ProcessedLogicalDocument(_group(1, (1,), "ciments_enfidha_invoice_v1"), _response("6608000533", 1, status="valid")),
        ProcessedLogicalDocument(_group(2, (2,), "ruspina_reinvoice_v1"), _response("202300001", 2)),
        ProcessedLogicalDocument(_group(3, (3,), "customs_tradenet_v1", "customs_declaration"), _response("CUSTOMS", 3)),
    )
    result = DossierProcessResult(
        source_file="INV 01.pdf", page_count=3,
        page_classifications=tuple(item.group.page_classifications[0] for item in documents),
        logical_documents=documents,
        document_preview=DocumentPreview(source_file="INV 01.pdf", pages=[PreviewPage(page=i, url=f"/p{i}.png", width=100, height=200) for i in (1, 2, 3)]),
        ocr_engine="test", timings={},
    )
    monkeypatch.setattr(routes, "process_dossier_file", lambda *args, **kwargs: result)

    response = TestClient(app).post("/process-dossier", files={"file": ("INV 01.pdf", b"pdf", "application/pdf")})

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_count"] == 3
    assert [page["page"] for page in payload["document_preview"]["pages"]] == [1, 2, 3]
    ids = [item["logical_document_id"] for item in payload["logical_documents"]]
    assert len(set(ids)) == 3
    assert all(item.startswith(payload["dossier_id"] + ":logical_document_") for item in ids)
    assert [item["response"]["detected_fields"]["invoice_number"] for item in payload["logical_documents"]] == ["6608000533", "202300001", "CUSTOMS"]
    assert [item["response"]["detected_fields"]["line_items"][0]["page"] for item in payload["logical_documents"]] == [1, 2, 3]
    assert payload["summary"] == {"status": "needs_review", "valid_count": 1, "needs_review_count": 2, "invalid_count": 0}


def test_process_dossier_supports_one_multi_page_logical_document(monkeypatch):
    group = _group(1, (1, 2), "ciments_enfidha_invoice_v1")
    result = DossierProcessResult(
        source_file="two-pages.pdf", page_count=2, page_classifications=group.page_classifications,
        logical_documents=(ProcessedLogicalDocument(group, _response("A-1", 2)),),
        document_preview=DocumentPreview(source_file="two-pages.pdf", pages=[PreviewPage(page=i, url=f"/p{i}.png", width=100, height=200) for i in (1, 2)]),
        ocr_engine="test", timings={},
    )
    monkeypatch.setattr(routes, "process_dossier_file", lambda *args, **kwargs: result)

    payload = TestClient(app).post("/process-dossier", files={"file": ("two-pages.pdf", b"pdf", "application/pdf")}).json()

    assert payload["document_count"] == 1
    assert payload["logical_documents"][0]["physical_page_numbers"] == [1, 2]
    assert [item["page_number"] for item in payload["logical_documents"][0]["page_classifications"]] == [1, 2]


def test_process_invoice_contract_remains_registered_as_process_invoice_response():
    route = next(route for route in routes.router.routes if getattr(route, "path", None) == "/process-invoice")
    assert route.response_model is ProcessInvoiceResponse
