from app.core.schemas import BoundingBox, ExtractedInvoiceFields, OCRLine
from app.services.document_graph import build_document_graph
from app.services.field_enricher import build_expanded_fields
from app.services.field_extractor import extract_with_candidates


def line(text: str, x1: float, y1: float, x2: float, y2: float, idx: int = 0, confidence: float = 0.92) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=confidence,
        page_number=1,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        line_index=idx,
    )


def test_document_graph_exposes_hierarchical_tree_and_relations():
    blocks = [
        line("ACME DISTRIBUTION SARL", 30, 20, 240, 42, idx=1),
        line("Invoice No", 430, 20, 520, 42, idx=2),
        line("INV-100", 550, 20, 630, 42, idx=3),
        line("Description Qty Price Total", 30, 260, 650, 282, idx=4),
        line("Widget 2 10.00 20.00", 30, 300, 650, 322, idx=5),
    ]

    graph = build_document_graph(blocks)
    payload = graph.to_dict()

    assert payload["document_tree"]["node_type"] == "document"
    assert payload["document_tree"]["pages"]
    assert any(block["semantic_labels"] for block in payload["blocks"])
    relations = {edge["relation_type"] for edge in payload["edges"]}
    assert {"right_of", "same_line"} & relations


def test_party_resolver_prefers_customer_label_over_header_for_customer():
    blocks = [
        line("VITAL DISTRIBUTION", 30, 20, 220, 42, idx=1),
        line("15 Rue des Entrepreneurs", 30, 50, 230, 72, idx=2),
        line("Client", 430, 150, 500, 172, idx=3),
        line("PHARMA PLUS SARL", 430, 180, 620, 202, idx=4),
        line("Invoice No INV-55", 430, 20, 620, 42, idx=5),
    ]
    text = "\n".join(block.text for block in blocks)

    fields, _candidates, _confidences, debug = extract_with_candidates(text, blocks)

    assert fields.supplier_name == "VITAL DISTRIBUTION"
    assert fields.customer_name == "PHARMA PLUS SARL"
    assert "party_resolver" in debug
    assert debug["party_resolver"]["customer_name"]


def test_due_date_label_does_not_override_invoice_date():
    blocks = [
        line("Date", 430, 80, 500, 102, idx=1),
        line("06/05/2026", 550, 80, 660, 102, idx=2),
        line("Date d'echeance", 430, 120, 560, 142, idx=3),
        line("21/05/2026", 590, 120, 700, 142, idx=4),
    ]
    text = "\n".join(block.text for block in blocks)

    fields, _candidates, _confidences, _debug = extract_with_candidates(text, blocks)

    assert str(fields.invoice_date) == "2026-05-06"
    assert str(fields.due_date) == "2026-05-21"


def test_missing_field_confidence_is_null():
    expanded = build_expanded_fields(
        ExtractedInvoiceFields(invoice_number="INV-1", amount_ttc=None),
        candidates={},
        field_confidences={"amount_ttc": 0.8, "invoice_number": 0.9},
        extracted_text="Invoice No INV-1",
    )

    assert expanded["amount_ttc"].value is None
    assert expanded["amount_ttc"].confidence is None
    assert expanded["invoice_number"].confidence == 0.9
