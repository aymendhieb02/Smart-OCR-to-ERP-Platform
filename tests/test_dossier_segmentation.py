from app.core.schemas import BoundingBox, OCRLine, OCRResult
from app.services.document_graph import build_document_graph
from app.services.dossier_segmentation import (
    PageClassification,
    classify_page,
    classify_pages,
    group_logical_documents,
)
from app.services.graph_field_extractor import build_graph_debug


def _line(text: str, page: int) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.92,
        page_number=page,
        bbox=BoundingBox(x1=10, y1=10, x2=300, y2=35),
        page_width=1000,
        page_height=1400,
        coordinate_space="original_page",
    )


def test_deterministic_family_classification_uses_page_evidence():
    cases = [
        (_line("SOCIETE DES CIMENTS D'ENFIDHA INVOICE", 1), "ciments_enfidha_invoice_v1"),
        (_line("SOTACIB KAIROUAN Facture EXW Kairouan", 2), "sotacib_kairouan_grey_invoice_v1"),
        (_line("STE TUNISO-ANDALOUSE DE CIMENT BLANC Feriana", 3), "sotacib_kasserine_white_invoice_v1"),
        (_line("RUSPINA IMPORT EXPORT AS PER INVOICE 123", 4), "ruspina_reinvoice_v1"),
        (_line("TRADENET Declaration en detail", 5), "customs_tradenet_v1"),
        (_line("DOUANES TUNISIENNES الديوانة التونسية", 6), "customs_douanes_tunisiennes_v1"),
    ]

    for line, expected_family in cases:
        result = classify_page([line], line.page_number)
        assert result.document_family == expected_family
        assert result.match_score > 0


def test_unknown_page_is_not_forced_into_known_family():
    result = classify_page([_line("Packing list continuation", 7)], 7)

    assert result.document_type == "unknown"
    assert result.document_family is None
    assert result.match_score == 0


def test_classify_pages_includes_page_without_ocr_lines():
    result = classify_pages(OCRResult(
        raw_text="Invoice",
        lines=[_line("RUSPINA IMPORT EXPORT AS PER INVOICE", 2)],
        confidence=0.9,
        engine="test",
        page_count=3,
    ))

    assert [item.page_number for item in result] == [1, 2, 3]
    assert result[0].document_family is None
    assert result[1].document_family == "ruspina_reinvoice_v1"


def test_grouping_supports_future_multi_page_logical_document():
    first = PageClassification(1, "commercial_invoice", "ruspina_reinvoice_v1", 0.9, ("ruspina",), ("start",), True)
    continuation = PageClassification(2, "commercial_invoice", "ruspina_reinvoice_v1", 0.4, (), ("continuation",), False)
    customs = PageClassification(3, "customs_declaration", "customs_tradenet_v1", 0.9, ("tradenet",), ("start",), True)

    groups = group_logical_documents([first, continuation, customs])

    assert [group.pages for group in groups] == [(1, 2), (3,)]


def test_unicode_and_ocrline_contract_remain_compatible_with_graph_consumers():
    lines = [
        _line("فاتورة رقم ١٢٣", 3),
        _line("Total TTC 100.00 EUR", 3),
    ]

    graph = build_document_graph(lines)
    debug = build_graph_debug(lines)

    assert any("فاتورة رقم ١٢٣" in node.text for node in graph.nodes)
    assert debug["document_graph"]["nodes"]
    assert lines[0].model_dump(mode="json")["coordinate_space"] == "original_page"
