from datetime import date

import numpy as np

from app.core.schemas import BoundingBox, ExtractedInvoiceFields, OCRLine
from app.services.producer_invoice_review import (
    ENFIDHA_FAMILY,
    GENERAL_PRODUCER_FAMILY,
    PRODUCER_COMMON_FIELDS,
    SOTACIB_FAMILIES,
    _extract_labeled_details,
    _labels_for_family,
    apply_producer_review_fields,
    prepare_producer_fields,
    recover_sotacib_total_ht,
    sanitize_legacy_producer_financial_fields,
)
from app.services.producer_table_reader import extract_producer_table_items
from app.services.validator import validate_invoice


def line(text, x1, y1, x2, y2, index, *, page=1, confidence=0.94):
    return OCRLine(
        text=text,
        confidence=confidence,
        page_number=page,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        source="synthetic fixture",
    )


def test_unknown_supplier_uses_generic_semantics_and_common_review_contract():
    lines = [
        line("SUPPLIER_TEST", 200, 20, 430, 45, 0),
        line("Invoice Number: INV-TEST-001", 80, 90, 360, 112, 1),
        line("Date", 80, 130, 135, 150, 2),
        line("14/03/2025", 80, 154, 180, 175, 3),
        line("Buyer: CUSTOMER_TEST", 80, 205, 300, 228, 4),
        line("Buyer address: CUSTOMER_TEST ADDRESS", 80, 233, 430, 256, 5),
        line("Consignee: RECEIVER_TEST", 600, 205, 850, 228, 6),
        line("HS CODE: HS-TEST-001", 80, 290, 300, 310, 7),
        line("Grand Total", 80, 400, 220, 422, 8),
        line("1,234.56 EUR", 870, 400, 1010, 422, 9),
    ]
    fields = ExtractedInvoiceFields()
    semantic = prepare_producer_fields(fields, lines, None)
    assert fields.supplier_name == "SUPPLIER_TEST"
    assert fields.customer_name == "CUSTOMER_TEST"
    assert fields.invoice_number == "INV-TEST-001"
    assert fields.invoice_date == date(2025, 3, 14)
    assert semantic["total"].value == 1234.56
    assert semantic["consignee"].value == "RECEIVER_TEST"
    from types import SimpleNamespace
    response = SimpleNamespace(detected_fields=fields, expanded_fields={})
    apply_producer_review_fields(response, None, lines)
    assert set(PRODUCER_COMMON_FIELDS) <= set(response.expanded_fields)


def test_client_party_labels_do_not_promote_shipment_payment_or_destination():
    lines = [
        line("Client:", 80, 100, 145, 122, 0),
        line("CUSTOMER_TEST", 220, 100, 420, 122, 1),
        line("Shipment: PARTIAL", 80, 140, 300, 162, 2),
        line("Payment: BANK TRANSFER", 80, 180, 350, 202, 3),
        line("Destination: CUSTOMER_TEST DESTINATION", 80, 220, 470, 242, 4),
    ]
    semantic = _extract_labeled_details(lines, _labels_for_family(None))
    assert semantic["client"].value == "CUSTOMER_TEST"
    assert semantic.get("shipment").value == "PARTIAL"
    assert semantic.get("payment").value == "BANK TRANSFER"
    assert semantic.get("destination").value == "CUSTOMER_TEST DESTINATION"
    assert semantic["client"].value not in {semantic["shipment"].value, semantic["payment"].value, semantic["destination"].value}


def test_legacy_amount_values_are_cleared_without_matching_financial_semantics():
    fields = ExtractedInvoiceFields(amount_ht=71.25, amount_ttc=42.0, tax_rate=17.0, purchase_order_number="PO-TEST-001")
    lines = [
        line("Unit Price EUR/To", 100, 100, 300, 122, 0),
        line("71,25", 700, 100, 760, 122, 1),
        line("Packaging: 42kg", 100, 150, 280, 172, 2),
        line("Taux d'intégration: 17%", 100, 200, 330, 222, 3),
        line("Company RC N 987654321", 100, 600, 470, 622, 4),
    ]
    sanitize_legacy_producer_financial_fields(fields, lines)
    assert fields.amount_ht is None
    assert fields.amount_ttc is None
    assert fields.tax_rate is None
    assert fields.purchase_order_number is None
    validation = validate_invoice(fields, None, "invoice", producer_invoice=True, producer_total=1234.56)
    assert not any("tax rate" in warning.lower() or "suspicious tax" in warning.lower() for warning in validation.warnings)


def test_true_tax_rate_semantics_remain_validatable_but_integration_rate_is_not_tax():
    fields = ExtractedInvoiceFields(tax_rate=17.0)
    lines = [line("Taux d'intégration: 17%", 100, 100, 320, 122, 0)]
    sanitize_legacy_producer_financial_fields(fields, lines)
    assert fields.tax_rate is None
    validation = validate_invoice(fields, None, "invoice", producer_invoice=True, producer_total=1234.56, producer_tax_applicable=False)
    assert "Suspicious tax rate: 17.0%" not in validation.warnings
    assert not any("Tax rate is missing" in warning for warning in validation.warnings)


def _table_fixture(*, footer=True):
    blocks = [
        line("Description article", 100, 100, 290, 122, 0),
        line("Quantité (To)", 400, 100, 520, 122, 1),
        line("Prix unitaire EUR/To", 620, 100, 830, 122, 2),
        line("Montant EUR", 900, 100, 1030, 122, 3),
        line("CEMENT_TEST PRODUCT SAC 25KG", 100, 140, 380, 162, 4),
        line("12", 440, 140, 475, 162, 5),
        line("25,00", 700, 140, 760, 162, 6),
        line("300,00", 930, 140, 1020, 162, 7),
        line("Total", 100, 175, 170, 197, 8),
        line("300,00", 930, 175, 1020, 197, 9),
    ]
    if footer:
        blocks.append(line("Company RC N 987654321", 700, 600, 1050, 622, 10))
    return blocks


def test_generic_table_maps_french_headers_and_excludes_footer_rc_number():
    rows = extract_producer_table_items(_table_fixture())
    assert len(rows) == 1
    row = rows[0]
    assert row.description == "CEMENT_TEST PRODUCT SAC 25KG"
    assert row.quantity == 12
    assert row.unit == "TO"
    assert row.unit_price == 25
    assert row.total == 300
    assert "987654321" not in row.description


def test_generic_table_infers_metric_tonnes_only_when_bag_mass_matches_quantity():
    blocks = [
        line("Description article", 100, 100, 290, 122, 0),
        line("Quantité", 400, 100, 500, 122, 1),
        line("Unité", 530, 100, 590, 122, 2),
        line("Prix unitaire EUR/To", 650, 100, 850, 122, 3),
        line("Montant EUR", 900, 100, 1030, 122, 4),
        line("CEMENT_TEST PRODUCT 120 Bags/25 kgs", 100, 140, 380, 162, 5),
        line("3", 440, 140, 475, 162, 6),
        line("N", 550, 140, 575, 162, 7),
        line("140,00", 700, 140, 760, 162, 8),
        line("420,00", 930, 140, 1020, 162, 9),
        line("Grand Total", 100, 175, 210, 197, 10),
    ]
    [row] = extract_producer_table_items(blocks)
    assert row.quantity == 3
    assert row.unit == "MT"


def test_generic_table_does_not_infer_metric_tonnes_when_packaging_mass_disagrees():
    inferred = extract_producer_table_items([
        line("Description article", 100, 100, 290, 122, 0),
        line("Quantité", 400, 100, 500, 122, 1),
        line("Unité", 530, 100, 590, 122, 2),
        line("Prix unitaire EUR/To", 650, 100, 850, 122, 3),
        line("Montant EUR", 900, 100, 1030, 122, 4),
        line("CEMENT_TEST PRODUCT 120 Bags/25 kgs", 100, 140, 380, 162, 5),
        line("2.5", 440, 140, 475, 162, 6),
        line("N", 550, 140, 575, 162, 7),
        line("140,00", 700, 140, 760, 162, 8),
        line("420,00", 930, 140, 1020, 162, 9),
        line("Grand Total", 100, 175, 210, 197, 10),
    ])
    assert inferred[0].unit == "N"


def test_table_reader_supports_multiple_rows_inside_detected_region():
    blocks = _table_fixture(footer=False)
    blocks.remove(blocks[-2])
    blocks.remove(blocks[-1])
    blocks.extend([
        line("CEMENT_TEST SECOND PRODUCT", 100, 185, 380, 207, 8),
        line("2", 440, 185, 475, 207, 9),
        line("10,00", 700, 185, 760, 207, 10),
        line("20,00", 930, 185, 1020, 207, 11),
        line("Grand Total", 100, 225, 210, 247, 12),
    ])
    rows = extract_producer_table_items(blocks)
    assert [(row.quantity, row.unit_price, row.total) for row in rows] == [(12, 25, 300), (2, 10, 20)]


def test_sotacib_additive_fields_map_integration_packaging_and_payment_separately():
    from types import SimpleNamespace

    family = next(iter(SOTACIB_FAMILIES))
    lines = [
        line("SOTACIB", 300, 20, 470, 44, 0),
        line("Client:", 80, 100, 145, 122, 1),
        line("CUSTOMER_TEST", 220, 100, 420, 122, 2),
        line("Matricule fiscale?d", 80, 130, 270, 152, 3),
        line("TAX-TEST-001", 220, 130, 330, 152, 4),
        line("Total HT:", 800, 180, 920, 202, 5),
        line("Taux d'intégration: 17%", 80, 250, 310, 272, 6),
        line("Emballage: EN SAC DE 50KG", 80, 300, 380, 322, 7),
        line("Moyen de règlement: CHEQUE", 80, 350, 400, 372, 8),
        line("Mode de règlement: AU COMPTANT", 80, 380, 420, 402, 9),
    ]
    response = SimpleNamespace(detected_fields=ExtractedInvoiceFields(amount_ht=90.0, tax_rate=89.0, amount_ttc=50.0), expanded_fields={})
    apply_producer_review_fields(response, family, lines)
    assert response.expanded_fields["client"].value == "CUSTOMER_TEST"
    assert response.expanded_fields["client_tax_id"].value == "TAX-TEST-001"
    assert response.expanded_fields["total_ht"].value is None
    assert response.expanded_fields["integration_rate"].value == "17%"
    assert response.expanded_fields["packaging"].value == "EN SAC DE 50KG"
    assert response.expanded_fields["payment_method"].value == "CHEQUE"
    assert response.expanded_fields["payment_terms"].value == "AU COMPTANT"


class _TargetedOcrStub:
    def __init__(self, lines):
        self.lines = lines
        self.calls = []

    def run_targeted_region(self, image, region, *, page_number):
        self.calls.append((image, region, page_number))
        return self.lines


def _total_ht_label(page=1, text="Total HT:"):
    return line(text, 600, 400, 700, 420, 0, page=page)


def _regional_total(text="12.345,67", *, page=1, confidence=0.93, bbox=None):
    return OCRLine(
        text=text,
        confidence=confidence,
        page_number=page,
        line_index=70,
        bbox=bbox or BoundingBox(x1=800, y1=400, x2=900, y2=420),
        source="regional_fallback",
        coordinate_space="original_page",
    )


def test_sotacib_total_ht_does_not_run_when_full_page_has_value():
    from app.services.producer_invoice_review import SOTACIB_FAMILIES

    family = next(iter(SOTACIB_FAMILIES))
    source = line("Total HT: 32.500,00 EUR", 600, 400, 900, 420, 0)
    engine = _TargetedOcrStub([_regional_total()])
    detail, extra, debug = recover_sotacib_total_ht([source], family, [], engine)
    assert detail is not None and detail.value == 32500
    assert detail.display_value == "32.500,00 EUR"
    assert extra == []
    assert not engine.calls
    assert debug["reason"] == "trustworthy_label_value_already_present"


def test_sotacib_total_ht_missing_value_uses_one_label_relative_region():
    from app.services.producer_invoice_review import SOTACIB_FAMILIES

    family = next(iter(SOTACIB_FAMILIES))
    image = np.zeros((800, 1000, 3), dtype=np.uint8)
    regional = _regional_total(page=3)
    engine = _TargetedOcrStub([regional])
    detail, extra, debug = recover_sotacib_total_ht(
        [_total_ht_label(page=3)], family, [image], engine, physical_page_numbers=[3],
    )
    assert len(engine.calls) == 1
    _called_image, region, page_number = engine.calls[0]
    assert page_number == 3
    assert region.name == "sotacib_total_ht_value_cell"
    assert region.image.shape[0] < image.shape[0] * 0.15
    assert region.image.shape[1] < image.shape[1] * 0.4
    assert detail is not None and detail.value == 12345.67
    assert detail.display_value == "12.345,67"
    assert detail.source.startswith("regional_fallback")
    assert detail.page == 3
    assert detail.bbox == regional.bbox
    assert extra == [regional]
    assert debug["attempted"] is True
    assert debug["reason"] == "regional_value_recovered"


def test_sotacib_total_ht_ignores_noisy_non_money_fallback_output():
    from app.services.producer_invoice_review import SOTACIB_FAMILIES

    family = next(iter(SOTACIB_FAMILIES))
    noisy = _regional_total("R.C. N 987654321")
    engine = _TargetedOcrStub([noisy])
    detail, extra, debug = recover_sotacib_total_ht(
        [_total_ht_label()], family, [np.zeros((800, 1000, 3), dtype=np.uint8)], engine,
    )
    assert detail is None
    assert extra == [noisy]
    assert debug["attempted"] is True
    assert debug["reason"] == "no_unambiguous_money_value_in_target_cell"


def test_unknown_supplier_never_runs_sotacib_total_ht_fallback_or_infers_money():
    lines = [
        _total_ht_label(text="Total HT:"),
        line("Grand Total: 12.345,67 EUR", 600, 500, 900, 520, 1),
        line("12.345,67", 900, 140, 990, 162, 2),
    ]
    engine = _TargetedOcrStub([_regional_total()])
    detail, extra, debug = recover_sotacib_total_ht(
        lines, None, [np.zeros((800, 1000, 3), dtype=np.uint8)], engine,
    )
    assert detail is None
    assert extra == []
    assert not engine.calls
    assert debug["reason"] == "not_applicable"


def test_sotacib_total_or_line_total_without_total_ht_label_is_never_copied():
    from app.services.producer_invoice_review import SOTACIB_FAMILIES

    family = next(iter(SOTACIB_FAMILIES))
    for lines in (
        [line("Line total: 12.345,67", 600, 400, 900, 420, 0)],
        [line("Total: 12.345,67 EUR", 600, 400, 900, 420, 0)],
    ):
        engine = _TargetedOcrStub([_regional_total()])
        detail, extra, debug = recover_sotacib_total_ht(
            lines, family, [np.zeros((800, 1000, 3), dtype=np.uint8)], engine,
        )
        assert detail is None
        assert extra == []
        assert not engine.calls
        assert debug["reason"] == "explicit_total_ht_label_not_found"


def test_page_one_frontend_uses_canonical_common_fields_for_general_invoices():
    from pathlib import Path

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'commercial_invoice: { labelKey: "dossier.document_supplier_invoice", fields: [...GENERAL_PRODUCER_REVIEW_FIELDS, ...PRODUCER_OPTIONAL_EXTENSION_FIELDS]' in app_js
    assert "const presentExtensions = PRODUCER_OPTIONAL_EXTENSION_FIELDS.filter" in app_js
    assert 'commercial_invoice: [' in app_js
    for field in PRODUCER_COMMON_FIELDS:
        assert f'"{field}"' in app_js
    assert 'document_family: presentation.key === "commercial_invoice" ? "general_supplier_invoice"' in app_js
    assert '"supplier_tax_id", "tva_amount", "amount_ttc", "tax_rate", "purchase_order_number"' not in app_js
