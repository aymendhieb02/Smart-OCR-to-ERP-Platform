from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.core.schemas import (
    DocumentPreview, DossierLogicalDocument, ExtractedInvoiceFields, FieldExtractionDetail,
    LineItem, PreviewPage, ProcessInvoiceResponse,
    ValidationResult,
)
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
    assert [item["semantic_group"] for item in payload["logical_documents"]] == ["page1", "page2", "page3"]
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


def test_process_invoice_uses_dossier_orchestration_for_single_logical_document(monkeypatch):
    group = _group(1, (1,), "sotacib_kasserine_white_invoice_v1")
    response = _response("INV-TEST-001", 1)
    response.expanded_fields["seller"] = FieldExtractionDetail(value="SUPPLIER_TEST", source="known-template")
    preview = DocumentPreview(
        source_file="one-page.pdf",
        pages=[PreviewPage(page=1, url="/p1.png", width=100, height=200)],
    )
    result = DossierProcessResult(
        source_file="one-page.pdf", page_count=1,
        page_classifications=group.page_classifications,
        logical_documents=(ProcessedLogicalDocument(group, response),),
        document_preview=preview, ocr_engine="synthetic", timings={},
    )
    calls = []

    def process_with_dossier(path, **kwargs):
        calls.append((path, kwargs))
        return result

    monkeypatch.setattr(routes, "process_dossier_file", process_with_dossier)
    writes = []
    monkeypatch.setattr(routes, "write_erp_json", lambda payload: writes.append("erp"))
    monkeypatch.setattr(routes, "write_invoice_validation_report", lambda *args: writes.append("validation"))

    http_response = TestClient(app).post(
        "/process-invoice", files={"file": ("one-page.pdf", b"pdf", "application/pdf")}
    )

    assert http_response.status_code == 200
    payload = http_response.json()
    assert payload["document_preview"]["pages"][0]["page"] == 1
    assert payload["expanded_fields"]["seller"]["value"] == "SUPPLIER_TEST"
    assert len(calls) == 1
    assert calls[0][1]["persist_erp_json"] is False
    assert writes == ["erp", "validation"]


def test_process_invoice_rejects_multi_logical_document_dossier(monkeypatch):
    groups = (
        ProcessedLogicalDocument(_group(1, (1,), "sotacib_kairouan_grey_invoice_v1"), _response("INV-TEST-001", 1)),
        ProcessedLogicalDocument(_group(2, (2,), "ruspina_reinvoice_v1"), _response("INV-TEST-002", 2)),
    )
    result = DossierProcessResult(
        source_file="two-documents.pdf", page_count=2,
        page_classifications=tuple(item for group in groups for item in group.group.page_classifications),
        logical_documents=groups,
        document_preview=DocumentPreview(
            source_file="two-documents.pdf",
            pages=[PreviewPage(page=i, url=f"/p{i}.png", width=100, height=200) for i in (1, 2)],
        ),
        ocr_engine="synthetic", timings={},
    )
    monkeypatch.setattr(routes, "process_dossier_file", lambda *args, **kwargs: result)
    writes = []
    monkeypatch.setattr(routes, "write_erp_json", lambda payload: writes.append("erp"))
    monkeypatch.setattr(routes, "write_invoice_validation_report", lambda *args: writes.append("validation"))

    response = TestClient(app).post(
        "/process-invoice", files={"file": ("two-documents.pdf", b"pdf", "application/pdf")}
    )

    assert response.status_code == 422
    assert "use /process-dossier" in response.json()["detail"]
    assert writes == []


def test_simple_dossier_json_endpoint_returns_only_three_semantic_groups():
    docs = [
        DossierLogicalDocument(
            logical_document_id=f"synthetic:{index}", document_index=index,
            document_type=doc_type, document_family=family, semantic_group=semantic,
            physical_page_numbers=[index], response=_response(f"INV-TEST-{index:03d}", index),
        )
        for index, (doc_type, family, semantic) in enumerate((
            ("commercial_invoice", "sotacib_kairouan_grey_invoice_v1", "page1"),
            ("commercial_invoice", "ruspina_reinvoice_v1", "page2"),
            ("customs_declaration", "customs_tradenet_v1", "page3"),
        ), start=1)
    ]
    response = TestClient(app).post(
        "/export-simple-dossier-json",
        json={"logical_documents": [document.model_dump(mode="json") for document in docs]},
    )

    assert response.status_code == 200
    output = response.json()
    assert list(output) == ["page1", "page2", "page3"]
    assert len(output["page1"]["fields"]) > 0
    assert len(output["page2"]["fields"]) > 0
    assert len(output["page3"]["fields"]) == 8


@pytest.mark.parametrize(
    ("document_type", "family", "semantic_group", "expected_nonempty"),
    [
        ("commercial_invoice", "ruspina_reinvoice_v1", "page2", "page2"),
        ("customs_declaration", "customs_tradenet_v1", "page3", "page3"),
    ],
)
def test_simple_dossier_json_single_semantic_document_needs_no_fake_physical_pages(
    document_type, family, semantic_group, expected_nonempty,
):
    document = DossierLogicalDocument(
        logical_document_id="synthetic:one-page", document_index=1,
        document_type=document_type, document_family=family, semantic_group=semantic_group,
        physical_page_numbers=[1], response=_response("INV-TEST-001", 1),
    )

    response = TestClient(app).post(
        "/export-simple-dossier-json",
        json={"logical_documents": [document.model_dump(mode="json")]},
    )

    assert response.status_code == 200
    output = response.json()
    assert output[expected_nonempty]["fields"]
    assert all(output[key]["fields"] == [] for key in {"page1", "page2", "page3"} - {expected_nonempty})
