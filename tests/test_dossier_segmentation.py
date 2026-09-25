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


def _positioned(text: str, page: int, x: float, y: float, *, width: int = 1200, height: int = 1600) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.9,
        page_number=page,
        bbox=BoundingBox(x1=x, y1=y, x2=x + 70, y2=y + 22),
        page_width=width,
        page_height=height,
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


def test_customs_layout_evidence_overrides_incidental_supplier_family_text():
    result = classify_page([
        _line("SOTACIB KAIROUAN", 3),
        _line("Exportateur", 3),
        _line("Importateur", 3),
        _line("Moyen de transport", 3),
    ], 3)

    assert result.document_type == "customs_declaration"
    assert result.document_family is None


def test_customs_masthead_overrides_supplier_name_on_same_page():
    result = classify_page([
        _line("TUNISI TRADENT", 3),
        _line("SOTACIB KASSERINE CIMENT BLANC", 3),
    ], 3)

    assert result.document_type == "customs_declaration"
    assert result.document_family == "customs_tradenet_v1"


def test_noisy_tradenet_classification_uses_multiple_header_signals():
    lines = [
        _positioned("TUNETRADNTm DaMach", 3, 250, 35),
        _positioned("Exportaleur", 3, 260, 90),
        _positioned("importateut", 3, 260, 205),
        _positioned("Déclaration", 3, 650, 70),
        _positioned("123456", 3, 700, 160),
        _positioned("04-02-2025", 3, 820, 160),
    ]

    result = classify_page(lines, 3)

    assert result.document_type == "customs_declaration"
    assert result.document_family == "customs_tradenet_v1"
    assert {"tradenet_masthead", "exporter_label", "importer_label", "paired_header_values"}.issubset(result.matched_anchors)


def test_weak_tradenet_evidence_remains_unknown():
    result = classify_page([_positioned("tradnt", 1, 100, 60)], 1)

    assert result.document_type == "unknown"
    assert result.document_family is None


def test_customs_header_structure_beats_lone_invoice_family_anchor():
    lines = [
        _positioned("SOTACIB KASSERINE CIMENT BLANC", 3, 300, 250),
        _positioned("TUNETRADNTm DaMach", 3, 250, 35),
        _positioned("Exportaleur", 3, 260, 90),
        _positioned("Importateur", 3, 260, 205),
        _positioned("Dcaratoa", 3, 650, 70),
        _positioned("123456", 3, 700, 160),
        _positioned("04-02-2025", 3, 820, 160),
    ]

    result = classify_page(lines, 3)

    assert result.document_type == "customs_declaration"
    assert result.document_family == "customs_tradenet_v1"


def test_multi_signal_customs_structure_can_keep_family_unknown():
    lines = [
        _positioned("Exportateur", 2, 260, 90),
        _positioned("Importateur", 2, 260, 205),
        _positioned("Déclaration", 2, 650, 70),
        _positioned("123456", 2, 700, 160),
        _positioned("04-02-2025", 2, 820, 160),
    ]

    result = classify_page(lines, 2)

    assert result.document_type == "customs_declaration"
    assert result.document_family is None


def test_noisy_party_labels_and_partial_date_trigger_fixed_form_review():
    for exporter, importer, date_text in (
        ("Lxporhour", "InNtaleur", "01.01-033"),
        ("Exprnlour", "Imyxarintci", ".01-2023"),
    ):
        lines = [
            _positioned(exporter, 3, 250, 45),
            _positioned(importer, 3, 250, 175),
            _positioned("446028", 3, 675, 70),
            _positioned(date_text, 3, 795, 70),
            _positioned("FACTURE", 3, 220, 110),
        ]
        result = classify_page(lines, 3)
        assert result.document_type == "customs_declaration"
        assert result.document_family is None


def test_noisy_tunisian_customs_masthead_keeps_distinct_family():
    result = classify_page([
        _positioned("DOUANES TUNTSIENNES", 3, 200, 15),
        _positioned("Exportateur", 3, 250, 70),
        _positioned("Importateur", 3, 250, 185),
        _positioned("123456", 3, 670, 95),
        _positioned("04-02-2025", 3, 790, 95),
    ], 3)
    assert result.document_type == "customs_declaration"
    assert result.document_family == "customs_douanes_tunisiennes_v1"


def test_invoice_number_date_pair_without_customs_roles_stays_invoice():
    result = classify_page([
        _positioned("INVOICE", 1, 200, 40),
        _positioned("Supplier", 1, 250, 65),
        _positioned("Customer", 1, 250, 170),
        _positioned("123456", 1, 670, 95),
        _positioned("04-02-2025", 1, 790, 95),
    ], 1)
    assert result.document_type == "commercial_invoice"
    assert result.document_family is None


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
