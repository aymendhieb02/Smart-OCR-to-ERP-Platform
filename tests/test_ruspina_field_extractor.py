from __future__ import annotations

import pytest
import numpy as np

from app.core.schemas import BoundingBox, OCRLine, OCRResult
from app.services.file_loader import LoadedDocument
from app.services.pipeline_runner import process_dossier_file
from app.services.ruspina_field_extractor import extract_ruspina_fields
from scripts.dossier_ground_truth import serialize_machine_output


def _line(text, x, y, *, width=1190, height=1684, dx=0, dy=0, box_width=100, confidence=0.94, page=2, source="full_page"):
    cx, cy = x * width + dx, y * height + dy
    return OCRLine(
        text=text, confidence=confidence, page_number=page,
        bbox=BoundingBox(x1=cx - box_width / 2, y1=cy - 10, x2=cx + box_width / 2, y2=cy + 10),
        page_width=width, page_height=height, coordinate_space="original_page", source=source,
    )


def _form(*, width=1190, height=1684, dx=0, dy=0, invoice_heading="INVOICE N* 202500001",
          quantity_header="Quantity (T)", row_total="12 500.00EUR"):
    def item(text, x, y, **kwargs):
        return _line(text, x, y, width=width, height=height, dx=dx, dy=dy, **kwargs)

    return [
        item("DAMAGED HEADER", 0.79, 0.05, confidence=0.55),
        item(invoice_heading, 0.44, 0.125, box_width=290),
        item("OF", 0.62, 0.125, box_width=35),
        item("05/02/2025", 0.73, 0.125),
        item("AS PER INVOICE: 7700000012", 0.37, 0.15, box_width=260),
        item("Client:", 0.08, 0.175, box_width=70),
        item("BETA CUSTOMER LLC", 0.25, 0.175, box_width=180),
        item("Adress:", 0.08, 0.195, box_width=75),
        item("CITY-COUNTRY", 0.25, 0.195),
        item("DESCRIPTION OF GOODS", 0.23, 0.255, box_width=200),
        item(quantity_header, 0.57, 0.255, box_width=100),
        item("Unit Price EUR", 0.73, 0.255, box_width=110),
        item("Total Price", 0.86, 0.255, box_width=100),
        item("SYNTHETIC GOODS GRADE A HS 12345", 0.30, 0.282, box_width=420),
        item("250.000", 0.57, 0.282, box_width=80),
        item("50.00EUR", 0.73, 0.282, box_width=90),
        item(row_total, 0.86, 0.282, box_width=120),
        item("12 500.00EUR", 0.86, 0.59, box_width=120),
        item("TOTAL AMOUNT: TWELVE THOUSAND FIVE HUNDRED EURO", 0.36, 0.63, box_width=440),
        item("GROSS WEIGHT:", 0.10, 0.66, box_width=150),
        item("250T", 0.31, 0.66),
        item("NET WEIGHT:", 0.10, 0.68, box_width=140),
        item("240T", 0.31, 0.68),
        item("NUMBER OF BAGS:", 0.10, 0.70, box_width=160),
        item("5 000", 0.31, 0.70),
        item("DELIVERY:", 0.10, 0.72),
        item("EX-WORKS", 0.31, 0.72),
        item("ORIGIN:", 0.10, 0.74),
        item("TESTLAND", 0.31, 0.74),
        item("PAYMENT:", 0.10, 0.76),
        item("BANK TRANSFER", 0.31, 0.76),
        item("IBAN:", 0.10, 0.78),
        item("TN9999999999999999999999", 0.35, 0.78, box_width=230),
        item("BANK:", 0.10, 0.80),
        item("TEST BANK", 0.31, 0.80),
        item("SWIFT:", 0.10, 0.82),
        item("TSTBTNTT", 0.31, 0.82),
        item("ALPHA IMPORT EXPORT", 0.45, 0.925, box_width=210, confidence=0.98),
        item("National ID: 123456789 - Tel: 999999999", 0.45, 0.95, box_width=440),
    ]


def _extract(lines, *, width=1190, height=1684, family="ruspina_reinvoice_v1"):
    return extract_ruspina_fields(lines, document_family=family, page_dimensions={2: (width, height)})


@pytest.mark.parametrize("heading", [
    "INVOICE N* 202500001", "INVOICE N° 202500001", "INVOICE NO 202500001",
    "INVOICE N 202500001", "lNVOICE N z 202500001",
])
def test_invoice_heading_ocr_variants_preserve_own_number(heading):
    result = _extract(_form(invoice_heading=heading))
    assert result.fields["invoice_number"].value == "202500001"
    assert result.fields["invoice_number"].evidence_text == heading


def test_split_invoice_number_after_heading_and_date_after_of():
    lines = _form(invoice_heading="lNVOICE N z")
    number = _line("202500001", 0.58, 0.125)
    lines.append(number)
    result = _extract(lines)
    assert result.fields["invoice_number"].value == "202500001"
    assert result.fields["invoice_number"].bbox.x2 >= number.bbox.x2
    assert result.fields["invoice_date"].value == "2025-02-05"
    assert "OF\n05/02/2025" == result.fields["invoice_date"].evidence_text


def test_referenced_invoice_remains_distinct_from_own_number_and_phone_iban():
    result = _extract(_form())
    assert result.fields["invoice_number"].value == "202500001"
    assert result.fields["referenced_invoice"].value == "7700000012"
    assert result.fields["referenced_invoice"].evidence_text == "AS PER INVOICE: 7700000012"
    assert "123456789" not in (result.fields["invoice_number"].value, result.fields["referenced_invoice"].value)


def test_split_referenced_invoice_uses_same_row_value_not_own_number():
    lines = _form()
    label = next(line for line in lines if line.text.startswith("AS PER INVOICE"))
    label.text = "AS PER INVOICE:"
    lines.append(_line("7700000012", 0.55, 0.15))
    result = _extract(lines)
    assert result.fields["referenced_invoice"].value == "7700000012"
    assert result.fields["referenced_invoice"].evidence_text == "AS PER INVOICE:\n7700000012"
    assert result.fields["invoice_number"].value == "202500001"


def test_seller_prefers_clear_local_footer_and_buyer_uses_client_row_not_address():
    result = _extract(_form())
    assert result.fields["seller"].value == "ALPHA IMPORT EXPORT"
    assert result.fields["buyer"].value == "BETA CUSTOMER LLC"
    assert result.fields["customer"].value == "BETA CUSTOMER LLC"
    assert "CITY-COUNTRY" not in result.fields["buyer"].evidence_text
    assert result.fields["client"].value == "BETA CUSTOMER LLC"
    assert result.fields["address"].value == "CITY-COUNTRY"
    assert result.fields["address"].bbox != result.fields["client"].bbox


def test_seller_can_use_top_letterhead_without_footer_but_never_invents_a_name():
    lines = [line for line in _form() if line.text != "ALPHA IMPORT EXPORT"]
    assert _extract(lines).fields["seller"].value == "DAMAGED HEADER"


def test_currency_total_and_table_prices_have_separate_printed_evidence():
    result = _extract(_form())
    assert result.fields["currency"].value == "EUR"
    assert "EUR" in result.fields["currency"].evidence_text
    assert result.fields["total"].value == 12500.0
    assert result.fields["total"].display_value == "12 500.00"
    assert result.fields["total"].evidence_text == "12 500.00EUR"
    assert result.fields["unit_price"].value == 50.0
    assert result.fields["line_total"].value == 12500.0
    assert result.fields["total_amount_words"].value == "TWELVE THOUSAND FIVE HUNDRED EURO"
    assert result.fields["total"].value != result.fields["total_amount_words"].value


def test_amount_words_split_label_and_value_are_associated_by_row_not_ocr_order():
    lines = _form()
    label = next(line for line in lines if line.text.startswith("TOTAL AMOUNT:"))
    label.text = "TOTAL AMOUNT:"
    label.bbox.x1 = 0.10 * 1190
    label.bbox.x2 = 0.25 * 1190
    lines.append(_line("TWELVE THOUSAND FIVE HUNDRED EURO", 0.48, 0.63, box_width=320))
    result = _extract(list(reversed(lines)))
    assert result.fields["total_amount_words"].value == "TWELVE THOUSAND FIVE HUNDRED EURO"
    assert result.fields["total_amount_words"].evidence_text.startswith("TOTAL AMOUNT:\n")


def test_unrelated_money_line_outside_total_cell_cannot_override_total():
    lines = _form()
    lines.append(_line("99 999.00EUR", 0.86, 0.37, confidence=1.0))
    assert _extract(lines).fields["total"].value == 12500.0


def test_gross_net_and_bag_count_are_independent_labelled_values():
    result = _extract(_form())
    assert result.fields["gross_weight"].value == 250.0
    assert result.fields["gross_weight"].display_value == "250T"
    assert result.fields["gross_weight"].evidence_text == "GROSS WEIGHT:\n250T"
    assert result.fields["net_weight"].value == 240.0
    assert result.fields["net_weight"].display_value == "240T"
    assert result.fields["net_weight"].evidence_text == "NET WEIGHT:\n240T"
    assert result.fields["number_of_bags"].value == 5000
    assert result.fields["number_of_bags"].display_value == "5 000"


def test_equal_gross_and_net_are_still_sourced_from_distinct_lines():
    lines = _form()
    net = next(line for line in lines if line.text == "240T")
    net.text = "250T"
    result = _extract(lines)
    assert result.fields["gross_weight"].value == result.fields["net_weight"].value == 250.0
    assert result.fields["gross_weight"].bbox != result.fields["net_weight"].bbox


def test_absent_net_and_noninteger_bag_reading_remain_null():
    lines = [line for line in _form() if line.text != "240T"]
    bags = next(line for line in lines if line.text == "5 000")
    bags.text = "5000.400"
    result = _extract(lines)
    assert "net_weight" not in result.fields
    assert "number_of_bags" not in result.fields


def test_delivery_origin_payment_and_incoterm_do_not_use_bank_iban():
    result = _extract(_form())
    assert result.fields["delivery"].value == "EX-WORKS"
    assert result.fields["incoterm"].value == "EX-WORKS"
    assert result.fields["origin"].value == "TESTLAND"
    assert result.fields["payment"].value == "BANK TRANSFER"
    assert "IBAN" not in result.fields["payment"].evidence_text
    assert result.fields["iban"].value == "TN9999999999999999999999"
    assert result.fields["bank"].value == "TEST BANK"
    assert result.fields["swift"].value == "TSTBTNTT"
    assert result.fields["bank"].value != result.fields["payment"].value
    assert result.fields["iban"].value != result.fields["swift"].value
    assert result.fields["invoice_number"].value != result.fields["swift"].value


def test_missing_or_invalid_payment_details_are_not_invented():
    lines = [line for line in _form() if line.text not in {"TEST BANK", "TSTBTNTT", "TN9999999999999999999999"}]
    result = _extract(lines)
    assert not {"iban", "bank", "swift"} & result.fields.keys()
    assert result.fields["payment"].value == "BANK TRANSFER"


def test_lower_row_values_are_selected_by_geometry_even_when_ocr_order_is_reversed():
    result = _extract(list(reversed(_form())))
    for name, expected in {"address": "CITY-COUNTRY", "iban": "TN9999999999999999999999",
                           "bank": "TEST BANK", "swift": "TSTBTNTT"}.items():
        assert result.fields[name].value == expected
        assert result.fields[name].page == 2
        assert result.fields[name].bbox is not None
        assert "full_page" in result.fields[name].source


def test_single_table_row_uses_columns_and_preserves_source_description():
    lines = list(reversed(_form()))  # OCR order is not table order.
    result = _extract(lines)
    assert len(result.line_items) == 1
    row = result.line_items[0]
    assert row.description == "SYNTHETIC GOODS GRADE A HS 12345"
    assert row.quantity == 250.0
    assert row.unit == "T"
    assert row.unit_price == 50.0
    assert row.total == 12500.0
    assert row.line_total_ht is None and row.line_total_ttc is None
    assert result.fields["description"].evidence_text == "SYNTHETIC GOODS GRADE A HS 12345"
    assert result.fields["quantity"].evidence_text == "250.000"
    assert result.fields["unit"].evidence_text == "Quantity (T)"
    assert result.fields["line_total"].evidence_text == "12 500.00EUR"


def test_unit_is_not_guessed_when_quantity_header_and_row_lack_it():
    result = _extract(_form(quantity_header="Quantity ("))
    assert result.line_items[0].unit is None
    assert "unit" not in result.fields


def test_printed_line_total_is_not_replaced_by_arithmetic_or_footer_total():
    result = _extract(_form(row_total="11 500.00EUR"))
    assert result.line_items[0].total == 11500.0
    assert result.fields["total"].value == 12500.0


@pytest.mark.parametrize(("width", "height", "dx", "dy"), [
    (1190, 1684, 0, 0), (2380, 3368, 22, 28), (1000, 1400, -8, 10),
])
def test_normalized_geometry_preserves_values_bbox_page_confidence_and_source(width, height, dx, dy):
    lines = _form(width=width, height=height, dx=dx, dy=dy)
    result = _extract(lines, width=width, height=height)
    assert result.fields["invoice_number"].value == "202500001"
    assert result.fields["buyer"].value == "BETA CUSTOMER LLC"
    assert result.fields["address"].value == "CITY-COUNTRY"
    assert result.fields["swift"].value == "TSTBTNTT"
    assert result.line_items[0].quantity == 250.0
    assert result.fields["invoice_number"].bbox is not None
    assert result.fields["invoice_number"].page == 2
    assert result.fields["invoice_number"].page_width == width
    assert result.fields["invoice_number"].page_height == height
    assert result.fields["invoice_number"].confidence == 0.94
    assert "full_page" in result.fields["invoice_number"].source
    assert result.line_items[0].page == 2


def test_dedicated_extractor_does_not_run_for_other_families():
    result = _extract(_form(), family="producer_invoice_v1")
    assert result.fields == {}
    assert result.line_items == []


def test_dossier_pipeline_scopes_ruspina_fields_to_its_logical_document(monkeypatch, tmp_path):
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"synthetic test fixture")
    images = [np.zeros((1684, 1190, 3), dtype=np.uint8) for _ in range(2)]
    monkeypatch.setattr("app.services.pipeline_runner.load_document", lambda *args, **kwargs: LoadedDocument(
        source_file=source.name, extension=".pdf", images=images,
    ))

    class Engine:
        mode = "fast"
        last_timings = {"total_paddle_calls": 0}
        run_calls = 0

        def run(self, _images, _embedded_text=""):
            self.run_calls += 1
            lines = [_line("PRODUCER TEST FACTURE", 0.3, 0.1, page=1), *_form()]
            return OCRResult(raw_text="\n".join(line.text for line in lines), lines=lines,
                             confidence=0.9, engine="synthetic", page_count=2)

    engine = Engine()
    result = process_dossier_file(source, ocr_engine=engine)
    assert engine.run_calls == 1
    assert len(result.relationships) == 5
    ruspina_doc = next(item for item in result.logical_documents if item.group.document_family == "ruspina_reinvoice_v1")
    response = ruspina_doc.response
    assert response.detected_fields.invoice_number == "202500001"
    assert response.detected_fields.customer_name == "BETA CUSTOMER LLC"
    assert response.expanded_fields["referenced_invoice"].value == "7700000012"
    assert response.expanded_fields["client"].value == "BETA CUSTOMER LLC"
    assert response.expanded_fields["address"].value == "CITY-COUNTRY"
    assert response.expanded_fields["total_amount_words"].value == "TWELVE THOUSAND FIVE HUNDRED EURO"
    assert response.expanded_fields["iban"].value == "TN9999999999999999999999"
    assert response.expanded_fields["bank"].value == "TEST BANK"
    assert response.expanded_fields["swift"].value == "TSTBTNTT"
    assert response.expanded_fields["total"].value == 12500.0
    assert response.detected_fields.amount_ht is None
    assert response.detected_fields.tva_amount is None
    assert response.detected_fields.amount_ttc is None
    assert len(response.all_line_items) == 1
    assert response.all_line_items[0].page == 2
    assert all("referenced_invoice" not in item.response.expanded_fields for item in result.logical_documents if item is not ruspina_doc)

    snapshot = serialize_machine_output(result, source_path=source, pipeline_commit="synthetic")
    saved = next(item for item in snapshot["dossier"]["logical_documents"] if item["document_family"] == "ruspina_reinvoice_v1")
    assert saved["identifiers"]["referenced_invoice"] == "7700000012"
    assert saved["parties"]["seller"] == "ALPHA IMPORT EXPORT"
    assert saved["parties"]["buyer"] == "BETA CUSTOMER LLC"
    assert saved["financial"]["total"] == 12500.0
    assert saved["line_items"][0]["quantity"] == 250.0
