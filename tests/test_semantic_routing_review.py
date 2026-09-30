from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.core.schemas import OCRLine, OCRResult
from app.services.file_loader import LoadedDocument
from app.services.dossier_segmentation import classify_page, semantic_group_for_document
from app.services.pipeline_runner import process_dossier_file, process_routing_review_selection
from app.services.dossier_routing_review import clear_routing_sessions_for_tests, register_routing_session, resolve_routing_page
from app.services.simple_dossier_json import build_simple_dossier_output


def _lines(texts: tuple[str, ...], page: int = 1) -> list[OCRLine]:
    return [OCRLine(text=text, confidence=0.95, page_number=page) for text in texts]


PRODUCER = (
    "CIMENTS D'ENFIDHA", "Invoice No INV-TEST-001", "Date 14/02/2026",
    "Supplier SUPPLIER_TEST", "Customer CUSTOMER_TEST", "Total 1000.00",
)
RUSPINA = (
    "RUSPINA INVOICE", "Invoice No INV-TEST-002", "Date 15/02/2026",
    "As Per Invoice INV-TEST-001", "Client CUSTOMER_TEST", "Gross Weight 1000T",
    "Net Weight 900T", "Number of Bags 200", "Delivery EX-WORKS", "Payment BANK TRANSFER",
)
CUSTOMS = (
    "TRADENET Declaration en detail", "Exporter SUPPLIER_TEST", "Importer CUSTOMER_TEST",
    "Declaration 123456 14-02-2026",
)
AMBIGUOUS_COMMERCIAL = (
    "Commercial Invoice No INV-TEST-003", "Date 16/02/2026", "Total 1000.00 EUR",
    "Gross Weight 1000 KG", "Net Weight 900 KG", "Number of Packages 20",
    "Delivery EX-WORKS", "Country of Origin TUNISIA", "Payment BANK TRANSFER", "Bank BANK_TEST",
)


@pytest.mark.parametrize(
    ("texts", "expected_group", "expected_family"),
    [
        (PRODUCER, "page1", "ciments_enfidha_invoice_v1"),
        (RUSPINA, "page2", "ruspina_reinvoice_v1"),
        (CUSTOMS, "page3", "customs_tradenet_v1"),
    ],
    ids=("strong-producer-auto", "strong-ruspina-auto", "strong-customs-auto"),
)
def test_confident_known_families_remain_automatically_routed(texts, expected_group, expected_family):
    classification = classify_page(_lines(texts), 1)

    assert classification.routing_status == "auto"
    assert classification.document_family == expected_family
    assert semantic_group_for_document(
        classification.document_type, classification.document_family,
        classification.matched_anchors, classification.routing_status,
    ) == expected_group


def test_unresolved_export_style_invoice_requires_review_and_is_not_page1():
    classification = classify_page(_lines(AMBIGUOUS_COMMERCIAL), 1)

    assert classification.document_type == "commercial_invoice"
    assert classification.document_family is None
    assert classification.routing_status == "review_required"
    assert classification.candidate_semantic_groups == ("page1", "page2")
    assert semantic_group_for_document(
        classification.document_type, classification.document_family,
        classification.matched_anchors, classification.routing_status,
    ) is None


def test_arabic_cargo_and_payment_labels_are_preserved_as_ambiguity_evidence():
    classification = classify_page(_lines((
        "فاتورة تجارية رقم INV-TEST-AR-001", "Date 16/02/2026", "Total 1000.00",
        "الوزن الصافي 900 KG", "عدد الأكياس 20", "شروط التسليم EX-WORKS",
        "بلد المنشأ TEST", "طريقة الدفع BANK TRANSFER", "البنك BANK_TEST",
    )), 1)

    assert classification.routing_status == "review_required"
    assert semantic_group_for_document(
        classification.document_type, classification.document_family,
        classification.matched_anchors, classification.routing_status,
    ) is None


def test_unresolved_invoice_without_combined_logistics_and_financial_signals_keeps_existing_auto_path():
    classification = classify_page(_lines((
        "Invoice No INV-TEST-004", "Date 17/02/2026", "Supplier SUPPLIER_TEST",
        "Customer CUSTOMER_TEST", "Total 1000.00 EUR",
    )), 1)

    assert classification.routing_status == "auto"
    assert semantic_group_for_document(
        classification.document_type, classification.document_family, classification.matched_anchors,
    ) == "page1"


def test_ambiguity_is_independent_of_physical_page_position():
    pages = [PRODUCER, AMBIGUOUS_COMMERCIAL, CUSTOMS]
    reversed_pages = [CUSTOMS, AMBIGUOUS_COMMERCIAL, PRODUCER]

    for physical_page, texts in enumerate((*pages, *reversed_pages), start=1):
        classification = classify_page(_lines(texts, physical_page), physical_page)
        if texts == AMBIGUOUS_COMMERCIAL:
            assert classification.routing_status == "review_required"
            assert semantic_group_for_document(
                classification.document_type, classification.document_family,
                classification.matched_anchors, classification.routing_status,
            ) is None


def _ambiguous_ocr_result() -> OCRResult:
    lines = _lines(AMBIGUOUS_COMMERCIAL)
    return OCRResult(raw_text="\n".join(line.text for line in lines), lines=lines, confidence=0.95, engine="synthetic", page_count=1)


def test_initial_pipeline_defers_ambiguous_page_and_resolution_reuses_ocr(monkeypatch, tmp_path):
    document = LoadedDocument(
        source_file="synthetic.pdf", extension=".pdf", images=[np.zeros((16, 16, 3), dtype=np.uint8)],
    )
    ocr_result = _ambiguous_ocr_result()

    class FakeEngine:
        mode = "fast"
        last_timings = {}

        def __init__(self):
            self.full_page_calls = 0

        def run(self, images, embedded_text):
            self.full_page_calls += 1
            return ocr_result

    engine = FakeEngine()
    monkeypatch.setattr("app.services.pipeline_runner.load_document", lambda *args, **kwargs: document)
    monkeypatch.setattr("app.services.pipeline_runner.generate_document_preview", lambda doc: SimpleNamespace())
    monkeypatch.setattr("app.services.pipeline_runner._process_ocr_document", lambda *args, **kwargs: pytest.fail("ambiguous page must not run an extractor before resolution"))
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(b"synthetic document")

    result = process_dossier_file(path, ocr_engine=engine)

    assert result.logical_documents == ()
    assert result.routing_context is not None
    assert result.page_classifications[0].routing_status == "review_required"
    assert engine.full_page_calls == 1

    calls = []
    monkeypatch.setattr(
        "app.services.pipeline_runner._process_ocr_document",
        lambda document, evidence, **kwargs: calls.append((kwargs, evidence)) or SimpleNamespace(),
    )
    resolved = process_routing_review_selection(result.routing_context, 1, "page2")

    assert resolved.group.document_family == "ruspina_reinvoice_v1"
    assert resolved.group.page_classifications[0].routing_status == "manually_resolved"
    assert len(calls) == 1
    assert calls[0][0]["ocr_engine"] is None
    assert calls[0][0]["physical_page_numbers"] == (1,)
    assert calls[0][1].raw_text == ocr_result.raw_text
    assert engine.full_page_calls == 1


@pytest.mark.parametrize("semantic_group, expected_family", [
    ("page1", "general_supplier_invoice"),
    ("page2", "ruspina_reinvoice_v1"),
    ("page3", "customs_tradenet_v1"),
])
def test_standalone_ambiguous_page_can_be_manually_assigned(semantic_group, expected_family, monkeypatch):
    calls = []
    from app.services.pipeline_runner import DossierRoutingContext
    context = DossierRoutingContext(
        document=LoadedDocument(source_file="synthetic.pdf", extension=".pdf", images=[np.zeros((16, 16, 3), dtype=np.uint8)]),
        ocr_result=_ambiguous_ocr_result(), timings={},
    )

    # Keep this a focused contract test: the selected existing family is wired into
    # processing, and no OCR engine is passed to the extraction pipeline.
    monkeypatch.setattr(
        "app.services.pipeline_runner._process_ocr_document",
        lambda document, evidence, **kwargs: calls.append((kwargs, evidence)) or SimpleNamespace(),
    )
    resolved = process_routing_review_selection(context, 1, semantic_group)

    assert resolved.group.document_family == expected_family
    assert calls[0][0]["ocr_engine"] is None


def test_routing_session_uses_retained_context_and_consumes_resolved_page(monkeypatch):
    from app.services import dossier_routing_review

    context = SimpleNamespace(ocr_result=_ambiguous_ocr_result())
    register_routing_session("synthetic-session", context)
    marker = object()
    monkeypatch.setattr(dossier_routing_review, "process_routing_review_selection", lambda *args: marker)
    try:
        assert resolve_routing_page("synthetic-session", 1, "page2") is marker
        with pytest.raises(LookupError):
            resolve_routing_page("synthetic-session", 1, "page2")
    finally:
        clear_routing_sessions_for_tests()


def test_simple_json_builder_rejects_unresolved_routing_even_without_review_list():
    dossier = SimpleNamespace(
        logical_documents=[SimpleNamespace(routing_status="review_required")],
        routing_review_items=[],
    )

    with pytest.raises(ValueError, match="Resolve ambiguous document routing"):
        build_simple_dossier_output(dossier)
