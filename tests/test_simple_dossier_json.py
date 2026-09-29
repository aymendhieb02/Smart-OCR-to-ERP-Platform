from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.core.schemas import DocumentPreview, FieldExtractionDetail, LineItem, OCRLine, OCRResult, PreviewPage
from app.services.file_loader import LoadedDocument
from app.services.dossier_segmentation import classify_page, semantic_group_for_document
from app.services.pipeline_runner import process_dossier_file
from app.services.simple_dossier_json import (
    RUSPINA_FIELDS,
    TRADENET_FIELDS,
    build_simple_dossier_output,
)


PRODUCER = (
    "Invoice number INV-TEST-001",
    "Date 01/02/2026",
    "Supplier SUPPLIER_TEST",
    "Customer CUSTOMER_TEST",
    "Total 1000.00 EUR",
)
RUSPINA = (
    "RUSPINA INVOICE",
    "Invoice number INV-RE-TEST-001",
    "Date 02/03/2026",
    "As Per Invoice INV-TEST-001",
    "Client CUSTOMER_TEST",
    "Gross Weight 1000T",
    "Net Weight 900T",
    "Number of Bags 200",
    "Delivery EX-WORKS",
    "Payment BANK TRANSFER",
)
CUSTOMS = (
    "TRADENET Declaration en detail",
    "Exporter SUPPLIER_TEST",
    "Importer RUSPINA IMPORT EXPORT",
    "Declaration 123456 04-02-2025",
)
UNKNOWN = ("Packing list continuation", "RUSPINA IMPORT EXPORT")


def _page_lines(lines: tuple[str, ...], page: int) -> list[OCRLine]:
    return [OCRLine(text=text, confidence=0.95, page_number=page) for text in lines]


def _result_for_pages(pages: tuple[tuple[str, ...], ...]):
    documents = []
    for page_number, lines in enumerate(pages, start=1):
        classification = classify_page(_page_lines(lines, page_number), page_number)
        group = SimpleNamespace(
            group_id=f"logical_document_{page_number}",
            pages=(page_number,),
            document_type=classification.document_type,
            document_family=classification.document_family,
            page_classifications=(classification,),
        )
        response = SimpleNamespace(
            detected_fields={"invoice_number": f"INV-TEST-{page_number:03d}"},
            expanded_fields={},
            all_line_items=[],
        )
        documents.append(SimpleNamespace(group=group, response=response))
    return SimpleNamespace(logical_documents=tuple(documents))


def _field_map(group) -> dict[str, object]:
    return {item.name: item.value for item in group.fields}


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ((PRODUCER,), {"page1"}),
        ((RUSPINA,), {"page2"}),
        ((CUSTOMS,), {"page3"}),
        ((PRODUCER, RUSPINA), {"page1", "page2"}),
        ((PRODUCER, CUSTOMS), {"page1", "page3"}),
        ((RUSPINA, CUSTOMS), {"page2", "page3"}),
        ((CUSTOMS, PRODUCER, RUSPINA), {"page1", "page2", "page3"}),
        ((RUSPINA, PRODUCER), {"page1", "page2"}),
    ],
    ids=("producer-only", "ruspina-only", "customs-only", "producer-ruspina", "producer-customs", "ruspina-customs", "all-reordered", "ruspina-before-producer"),
)
def test_semantic_groups_are_content_routed_and_order_independent(pages, expected):
    output = build_simple_dossier_output(_result_for_pages(pages)).model_dump(mode="json")

    assert set(output) == {"page1", "page2", "page3"}
    populated = {name for name, group in output.items() if group["fields"]}
    assert populated == expected
    for name in set(output) - expected:
        assert output[name] == {"fields": []}


def test_ruspina_client_name_does_not_classify_a_producer_invoice_as_page2():
    lines = _page_lines((
        "Invoice N° INV-TEST-001",
        "Date 01/02/2026",
        "Supplier SUPPLIER_TEST",
        "Client RUSPINA IMPORT EXPORT",
        "Total 1000.00 EUR",
    ), 1)

    classification = classify_page(lines, 1)

    assert classification.document_family != "ruspina_reinvoice_v1"
    assert semantic_group_for_document(
        classification.document_type, classification.document_family, classification.matched_anchors,
    ) == "page1"


def test_customs_importer_named_ruspina_stays_page3():
    classification = classify_page(_page_lines(CUSTOMS, 1), 1)

    assert classification.document_family == "customs_tradenet_v1"
    assert semantic_group_for_document(
        classification.document_type, classification.document_family, classification.matched_anchors,
    ) == "page3"


def test_unknown_document_does_not_silently_route_to_page1():
    classification = classify_page(_page_lines(UNKNOWN, 1), 1)

    assert semantic_group_for_document(
        classification.document_type, classification.document_family, classification.matched_anchors,
    ) is None
    output = build_simple_dossier_output(_result_for_pages((UNKNOWN,))).model_dump(mode="json")
    assert all(group == {"fields": []} for group in output.values())


def test_simple_json_has_exact_contract_order_nulls_and_line_items():
    pages = _result_for_pages((PRODUCER, RUSPINA, CUSTOMS))
    producer_response = pages.logical_documents[0].response
    producer_response.expanded_fields = {
        "invoice_number": FieldExtractionDetail(value="INV-CORRECTED-001", machine_value="INV-RAW-001", source="human correction"),
        "seller": FieldExtractionDetail(value="SUPPLIER_CANONICAL", machine_value="SUPPLIER_RAW", canonical_value="SUPPLIER_CANONICAL", source="template enhancement"),
        "total": FieldExtractionDetail(value=1000.0, machine_value="1000.00", source="OCR"),
    }
    producer_response.all_line_items = [LineItem(
        description="PRODUCT_TEST", quantity=2, unit="TEST_UNIT", unit_price=500,
        line_total_ht=1000, tax_amount=200, line_total_ttc=1200,
    )]
    ruspina_response = pages.logical_documents[1].response
    ruspina_response.expanded_fields = {
        "invoice_number": FieldExtractionDetail(value="INV-RE-TEST-001", source="RUSPINA positioned OCR"),
    }
    customs_response = pages.logical_documents[2].response
    customs_response.expanded_fields = {
        "declaration_number": FieldExtractionDetail(value="123456", source="TradeNet label"),
    }

    output = build_simple_dossier_output(pages).model_dump(mode="json")

    assert list(output) == ["page1", "page2", "page3"]
    for group in output.values():
        assert all(set(item) == {"name", "value"} for item in group["fields"])
    producer = _field_map(build_simple_dossier_output(pages).page1)
    assert list(producer)[:3] == ["seller", "invoice_number", "invoice_date"]
    assert producer["seller"] == "SUPPLIER_CANONICAL"
    assert producer["invoice_number"] == "INV-CORRECTED-001"
    assert producer["invoice_date"] is None
    assert producer["total"] == 1000.0
    assert producer["line_items"] == [{
        "description": "PRODUCT_TEST", "quantity": 2.0, "unit": "TEST_UNIT",
        "unit_price": 500.0, "line_total": 1000.0,
    }]
    assert "tax_amount" not in producer["line_items"][0]
    assert "line_total_ttc" not in producer["line_items"][0]
    assert list(_field_map(build_simple_dossier_output(pages).page2)) == [*RUSPINA_FIELDS, "line_items"]
    assert list(_field_map(build_simple_dossier_output(pages).page3)) == list(TRADENET_FIELDS)
    assert _field_map(build_simple_dossier_output(pages).page3)["declaration_number"] == "123456"


def test_simple_line_item_does_not_relabel_a_ttc_only_value_as_line_total():
    pages = _result_for_pages((PRODUCER,))
    pages.logical_documents[0].response.all_line_items = [LineItem(
        description="PRODUCT_TEST", quantity=1, unit="BOX", unit_price=10,
        total=12, line_total_ttc=12,
    )]

    item = _field_map(build_simple_dossier_output(pages).page1)["line_items"][0]

    assert item["line_total"] is None


def test_page1_canonical_client_wins_over_legacy_customer_and_missing_stays_null():
    pages = _result_for_pages((PRODUCER,))
    response = pages.logical_documents[0].response
    response.detected_fields = {
        "invoice_number": "INV-TEST-001",
        "customer_name": "SUPPLIER_ADDRESS_TEST",
    }
    response.expanded_fields = {
        "client": FieldExtractionDetail(value="CUSTOMER_TEST", source="generic semantic label"),
        "invoice_number": FieldExtractionDetail(value="INV-TEST-001", source="generic semantic label"),
    }

    producer = _field_map(build_simple_dossier_output(pages).page1)

    assert producer["client"] == "CUSTOMER_TEST"

    response.expanded_fields["client"] = FieldExtractionDetail(
        value="CUSTOMER_CORRECTED_TEST", canonical_value="CUSTOMER_TEST", source="human correction",
    )
    producer = _field_map(build_simple_dossier_output(pages).page1)
    assert producer["client"] == "CUSTOMER_CORRECTED_TEST"

    response.expanded_fields["client"] = FieldExtractionDetail(value=None, source="not extracted")
    producer = _field_map(build_simple_dossier_output(pages).page1)
    assert producer["client"] is None


def test_line_item_corrections_serialize_current_effective_cells():
    pages = _result_for_pages((PRODUCER,))
    response = pages.logical_documents[0].response
    response.all_line_items = [LineItem(
        description="PRODUCT_CORRECTED", quantity=3, unit="BOX", unit_price=25, total=75,
    )]

    output = build_simple_dossier_output(pages)

    assert _field_map(output.page1)["line_items"] == [{
        "description": "PRODUCT_CORRECTED", "quantity": 3.0, "unit": "BOX",
        "unit_price": 25.0, "line_total": 75.0,
    }]


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ((PRODUCER,), {"page1"}),
        ((RUSPINA,), {"page2"}),
        ((CUSTOMS,), {"page3"}),
        ((PRODUCER, RUSPINA), {"page1", "page2"}),
        ((PRODUCER, CUSTOMS), {"page1", "page3"}),
        ((RUSPINA, CUSTOMS), {"page2", "page3"}),
        ((CUSTOMS, PRODUCER, RUSPINA), {"page1", "page2", "page3"}),
    ],
    ids=("one-producer-page", "one-ruspina-page", "one-customs-page", "two-producer-ruspina-pages", "two-producer-customs-pages", "two-ruspina-customs-pages", "three-reordered-pages"),
)
def test_dossier_pipeline_accepts_one_two_three_pages_and_ocr_runs_once(monkeypatch, tmp_path, pages, expected):
    loaded = LoadedDocument(
        source_file="synthetic.pdf", extension=".pdf",
        images=[np.zeros((20, 20, 3), dtype=np.uint8) for _ in pages],
    )
    preview = DocumentPreview(
        source_file="synthetic.pdf",
        pages=[PreviewPage(page=i, url=f"/synthetic/{i}.png", width=20, height=20) for i in range(1, len(pages) + 1)],
    )
    monkeypatch.setattr("app.services.pipeline_runner.load_document", lambda *args, **kwargs: loaded)
    monkeypatch.setattr("app.services.pipeline_runner.generate_document_preview", lambda document: preview)
    monkeypatch.setattr(
        "app.services.pipeline_runner._process_ocr_document",
        lambda document, ocr_result, **kwargs: SimpleNamespace(
            expanded_fields={},
            detected_fields=SimpleNamespace(invoice_number=None, supplier_name=None, customer_name=None, currency=None, origin=None),
            field_confidences={},
            all_ocr_blocks=ocr_result.lines,
        ),
    )

    class Engine:
        mode = "balanced"
        last_timings = {}

        def __init__(self):
            self.run_calls = 0

        def run(self, images, embedded_text=""):
            self.run_calls += 1
            lines = [
                OCRLine(text=text, confidence=0.95, page_number=page)
                for page, content in enumerate(pages, start=1)
                for text in content
            ]
            return OCRResult(
                raw_text="\n".join(line.text for line in lines), lines=lines,
                confidence=0.95, engine="synthetic", page_count=len(images),
            )

        def run_fallback_regions(self, images, region_names, *, page_numbers=None):
            return []

    engine = Engine()
    result = process_dossier_file(tmp_path / "synthetic.pdf", ocr_engine=engine)
    output = build_simple_dossier_output(result).model_dump(mode="json")

    assert engine.run_calls == 1
    assert result.page_count == len(pages)
    assert [item.group.pages for item in result.logical_documents] == [(i,) for i in range(1, len(pages) + 1)]
    assert {name for name, group in output.items() if group["fields"]} == expected
