from decimal import Decimal

import numpy as np
import pytest

from app.core.schemas import BoundingBox, OCRLine, OCRResult
from app.services.ocr_engine import _map_inference_bbox_to_page
from app.services.pipeline_runner import _merge_ocr_result
from app.services.table_regions import build_tradenet_ocr_regions
from app.services.tradenet_field_extractor import extract_tradenet_fields


def _line(text, cx, cy, *, page=3, width=1200, height=1600, confidence=0.91, box_w=70, source="full_page"):
    return OCRLine(
        text=text,
        confidence=confidence,
        page_number=page,
        bbox=BoundingBox(
            x1=cx - box_w / 2,
            y1=cy - 11,
            x2=cx + box_w / 2,
            y2=cy + 11,
        ),
        page_width=width,
        page_height=height,
        coordinate_space="original_page",
        source=source,
    )


def _header(*, page=3, width=1200, height=1600, offset_x=0, offset_y=0, type_value="E", count_value="1"):
    def pt(x, y):
        return x * width + offset_x, y * height + offset_y

    values = []
    for text, x, y in (
        ("Déclaration", 0.60, 0.05),
        ("D.A.E", 0.82, 0.05),
        ("Numéro", 0.53, 0.08),
        ("123456", 0.53, 0.11),
        ("Date", 0.67, 0.08),
        ("04-02-2025", 0.67, 0.11),
        ("Numéro", 0.76, 0.08),
        ("888888", 0.76, 0.11),
        ("Date", 0.91, 0.08),
        ("05-02-2025", 0.91, 0.11),
        ("Type déclaration", 0.76, 0.09),
        (type_value, 0.76, 0.12),
        ("Nbr total articles", 0.82, 0.14),
        (count_value, 0.82, 0.17),
        ("Code", 0.42, 0.085),
        ("009988", 0.42, 0.11),
    ):
        cx, cy = pt(x, y)
        values.append(_line(text, cx, cy, page=page, width=width, height=height))
    return values


def test_extracts_declaration_fields_and_derives_code_with_provenance():
    lines = _header()

    fields = extract_tradenet_fields(lines, page_dimensions={3: (1200, 1600)})

    assert fields["declaration_number"].value == "123456"
    assert fields["declaration_date"].value == "2025-02-04"
    assert fields["declaration_date"].evidence_text == "04-02-2025"
    assert fields["declaration_type"].value == "E"
    assert fields["declaration_article_count"].value == 1
    assert fields["declaration_code"].value == "E1"
    assert fields["declaration_code"].source == "derived"
    assert fields["declaration_code"].bbox is None
    assert fields["declaration_number"].bbox == lines[3].bbox
    assert fields["declaration_number"].page == 3
    assert fields["declaration_number"].source == "TradeNet label-anchored OCR"


def test_dae_number_and_date_do_not_override_declaration_section():
    fields = extract_tradenet_fields(_header())

    assert fields["declaration_number"].value == "123456"
    assert fields["declaration_date"].value == "2025-02-04"


def test_date_token_cannot_be_selected_as_declaration_number():
    lines = _header()
    lines[3].confidence = 0.70
    lines[5].confidence = 0.99
    fields = extract_tradenet_fields(lines)
    assert fields["declaration_number"].value == "123456"
    assert fields["declaration_date"].value == "2025-02-04"


def test_competing_number_readings_prefer_complete_cell_token_at_similar_confidence():
    lines = _header()
    full_number = lines[3]
    truncated = full_number.model_copy(update={"text": "12345", "source": "regional_fallback"})
    fields = extract_tradenet_fields([*lines, truncated])
    assert fields["declaration_number"].value == "123456"

    number = _line("123456", 0.586 * 1200, 0.065 * 1600)
    truncated = number.model_copy(update={"text": "12345", "source": "regional_fallback"})
    date = _line("04.02.2025", 0.678 * 1200, 0.066 * 1600)
    fields = extract_tradenet_fields([number, truncated, date])
    assert fields["declaration_number"].value == "123456"


def test_derived_code_supports_multi_character_type():
    fields = extract_tradenet_fields(_header(type_value="IM", count_value="4"))

    assert fields["declaration_code"].value == "IM4"


def test_missing_type_or_count_does_not_fabricate_derived_code():
    missing_type = extract_tradenet_fields(_header(type_value="", count_value="1"))
    missing_count = extract_tradenet_fields(_header(type_value="C", count_value=""))

    assert missing_type["declaration_type"].value is None
    assert missing_type["declaration_code"].value is None
    assert missing_count["declaration_article_count"].value is None
    assert missing_count["declaration_code"].value is None


def test_unrelated_nearby_numeric_token_is_ignored_by_label_and_cell_geometry():
    lines = _header()
    lines.extend([
        _line("700001", 0.53 * 1200, 0.24 * 1600),
        _line("999999", 0.96 * 1200, 0.11 * 1600),
    ])

    fields = extract_tradenet_fields(lines)

    assert fields["declaration_number"].value == "123456"


def test_normalized_geometry_handles_different_page_dimensions_and_translation():
    lines = _header(width=2400, height=3200, offset_x=28, offset_y=36)

    fields = extract_tradenet_fields(lines, page_dimensions={3: (2400, 3200)})

    assert fields["declaration_number"].value == "123456"
    assert fields["declaration_type"].value == "E"
    assert fields["declaration_article_count"].value == 1
    assert fields["declaration_number"].bbox.x1 > 28
    assert fields["declaration_number"].page_width == 2400
    assert fields["declaration_number"].page_height == 3200


def test_noisy_missing_labels_use_trade_net_number_date_and_type_cells_but_do_not_guess_count():
    width, height = 2000, 2800
    lines = [
        _line("7654321", 0.50 * width, 0.086 * height, width=width, height=height),  # nearby code cell
        _line("123456", 0.586 * width, 0.065 * height, width=width, height=height),
        _line("04.02.2025", 0.678 * width, 0.067 * height, width=width, height=height),
        _line("888888", 0.84 * width, 0.065 * height, width=width, height=height),  # D.A.E number cell
        _line("05.02.2025", 0.92 * width, 0.067 * height, width=width, height=height),  # D.A.E date cell
        _line("E", 0.82 * width, 0.125 * height, width=width, height=height),
        _line("1", 0.91 * width, 0.125 * height, width=width, height=height),
    ]

    fields = extract_tradenet_fields(lines, page_dimensions={3: (width, height)})

    assert fields["declaration_number"].value == "123456"
    assert fields["declaration_number"].evidence_text == "123456"
    assert fields["declaration_date"].value == "2025-02-04"
    assert fields["declaration_date"].evidence_text == "04.02.2025"
    assert fields["declaration_type"].value == "E"
    assert fields["declaration_article_count"].value is None
    assert fields["declaration_code"].value is None
    assert fields["declaration_number"].source.endswith("label not recognized)")
    assert fields["declaration_date"].source.endswith("label not recognized)")


def test_crop_coordinate_mapping_returns_original_page_bbox():
    mapped = _map_inference_bbox_to_page(
        BoundingBox(x1=10, y1=20, x2=30, y2=40),
        {
            "natural_page_width": 1000,
            "natural_page_height": 1200,
            "inference_width": 500,
            "inference_height": 600,
            "scale_x": 2,
            "scale_y": 2,
            "crop_offset_x": 100,
            "crop_offset_y": 200,
        },
    )

    assert mapped == BoundingBox(x1=120, y1=240, x2=160, y2=280)


def test_empty_input_exposes_null_fields_without_invented_evidence():
    fields = extract_tradenet_fields([])

    assert fields["declaration_number"].value is None
    assert fields["declaration_date"].value is None
    assert fields["declaration_type"].value is None
    assert fields["declaration_article_count"].value is None
    assert fields["declaration_code"].value is None
    assert fields["declaration_code"].bbox is None


def _business_form(*, width=1200, height=1600, dx=0, dy=0):
    def cell(text, x, y, **kwargs):
        return _line(text, x * width + dx, y * height + dy, width=width, height=height, **kwargs)

    return [
        cell("Exportateur", 0.25, 0.04),
        cell("SUPPLIER TEST LTD", 0.30, 0.055),
        cell("1 TEST STREET", 0.31, 0.070),
        cell("Importateur", 0.25, 0.098),
        cell("CUSTOMER TEST LLC", 0.30, 0.111),
        cell("LIBYA", 0.24, 0.122),
        cell("Déclarant", 0.25, 0.14),
        cell("AGENT TEST", 0.30, 0.16),
        cell("PTFN", 0.63, 0.22),
        cell("12345.000", 0.67, 0.232),
        cell("Cours de conversion de la devise de facturation", 0.65, 0.278),
        cell("3.1234000", 0.65, 0.287),
        cell("Valeur douane totale (en dinars)", 0.85, 0.278),
        cell("38548.750", 0.85, 0.287),
        cell("PFN de l'article en devise de facturation", 0.84, 0.34),
        cell("88888.000", 0.84, 0.35),
        cell("Valeur en dinars", 0.67, 0.42),
        cell("99999.000", 0.67, 0.43),
        cell("Douane", 0.85, 0.43),
        cell("77777.000", 0.85, 0.44),
    ]


def _party_form(exporter_lines, importer_lines, *, width=1200, height=1600, dx=0, dy=0):
    lines = [
        _line("Exportateur", 0.25 * width + dx, 0.04 * height + dy, width=width, height=height),
        _line("Importateur", 0.25 * width + dx, 0.098 * height + dy, width=width, height=height),
        _line("Déclarant", 0.25 * width + dx, 0.14 * height + dy, width=width, height=height),
    ]
    for index, text in enumerate(exporter_lines):
        lines.append(_line(text, 0.30 * width + dx, (0.055 + index * 0.014) * height + dy, width=width, height=height))
    for index, text in enumerate(importer_lines):
        lines.append(_line(text, 0.30 * width + dx, (0.111 + index * 0.011) * height + dy, width=width, height=height))
    return lines


@pytest.mark.parametrize(("exporter_lines", "expected"), [
    (["ALPHA EXPORT"], "ALPHA EXPORT"),
    (["ALPHA EXPORT", "INDUSTRIAL ZONE NORTH"], "ALPHA EXPORT INDUSTRIAL ZONE NORTH"),
    (["ALPHA EXPORT", "INDUSTRIAL ZONE NORTH", "PORT DISTRICT"], "ALPHA EXPORT INDUSTRIAL ZONE NORTH PORT DISTRICT"),
])
def test_exporter_uses_every_meaningful_cell_line(exporter_lines, expected):
    fields = extract_tradenet_fields(_party_form(exporter_lines, ["BETA IMPORT"]))
    assert fields["exporter"].value == expected
    assert fields["exporter"].evidence_text == "\n".join(exporter_lines)


@pytest.mark.parametrize(("importer_lines", "expected"), [
    (["BETA IMPORT"], "BETA IMPORT"),
    (["BETA IMPORT", "LIBYA"], "BETA IMPORT LIBYA"),
    (["BETA IMPORT", "MARKET STREET", "LIBYA"], "BETA IMPORT MARKET STREET LIBYA"),
])
def test_importer_uses_every_meaningful_cell_line(importer_lines, expected):
    fields = extract_tradenet_fields(_party_form(["ALPHA EXPORT"], importer_lines))
    assert fields["importer"].value == expected
    assert fields["importer"].evidence_text == "\n".join(importer_lines)


def test_party_address_with_numeric_prefix_and_repeated_whitespace_is_retained():
    lines = _party_form(["ALPHA   EXPORT", "006  INDUSTRIAL   ZONE NORTH"], ["BETA IMPORT", "LIBYA"])
    fields = extract_tradenet_fields(lines)
    assert fields["exporter"].value == "ALPHA EXPORT 006 INDUSTRIAL ZONE NORTH"
    assert fields["exporter"].evidence_text == "ALPHA   EXPORT\n006  INDUSTRIAL   ZONE NORTH"


def test_party_cells_exclude_neighboring_labels_codes_dates_and_declarant():
    lines = _party_form(["ALPHA EXPORT", "NORTH DISTRICT"], ["BETA IMPORT", "LIBYA"])
    lines.extend([
        _line("Code", 0.46 * 1200, 0.068 * 1600),
        _line("12345678", 0.46 * 1200, 0.082 * 1600),
        _line("04-02-2025", 0.52 * 1200, 0.084 * 1600),
        _line("D.A.E", 0.47 * 1200, 0.116 * 1600),
        _line("AGENT TEST", 0.30 * 1200, 0.16 * 1600),
        _line("Type déclaration", 0.77 * 1200, 0.12 * 1600),
    ])
    fields = extract_tradenet_fields(lines)
    assert fields["exporter"].value == "ALPHA EXPORT NORTH DISTRICT"
    assert fields["importer"].value == "BETA IMPORT LIBYA"
    assert fields["exporter"].bbox.y2 < fields["importer"].bbox.y1
    assert "AGENT" not in fields["importer"].evidence_text


def test_noisy_ocr_alternative_of_code_label_is_excluded_by_shared_geometry():
    lines = _party_form(["ALPHA EXPORT", "NORTH DISTRICT"], ["BETA IMPORT", "LIBYA"])
    for y in (0.069, 0.115):
        label = _line("Code", 0.465 * 1200, y * 1600, box_w=65, source="tradenet_parties")
        lines.extend([label, label.model_copy(update={"text": "Oods>A", "source": "full_page", "confidence": 0.78})])
    fields = extract_tradenet_fields(lines)
    assert fields["exporter"].value == "ALPHA EXPORT NORTH DISTRICT"
    assert fields["importer"].value == "BETA IMPORT LIBYA"


def test_exporter_cell_has_no_three_line_cap():
    lines = _party_form([], ["BETA IMPORT"])
    for text, y in zip(("ALPHA EXPORT", "INDUSTRIAL ZONE", "NORTH SECTOR", "PORT DISTRICT"), (0.052, 0.063, 0.074, 0.085)):
        lines.append(_line(text, 0.30 * 1200, y * 1600))
    assert extract_tradenet_fields(lines)["exporter"].value == "ALPHA EXPORT INDUSTRIAL ZONE NORTH SECTOR PORT DISTRICT"


def test_importer_cell_stops_before_declarant_and_storage_address():
    lines = _party_form(["ALPHA EXPORT"], ["BETA IMPORT", "LIBYA"])
    lines.extend([
        _line("Déclarant", 0.25 * 1200, 0.14 * 1600),
        _line("AGENT TEST", 0.30 * 1200, 0.151 * 1600),
        _line("Adresse de stockage", 0.30 * 1200, 0.157 * 1600),
        _line("WAREHOUSE DISTRICT", 0.30 * 1200, 0.170 * 1600),
    ])
    assert extract_tradenet_fields(lines)["importer"].value == "BETA IMPORT LIBYA"


def test_duplicate_sources_and_competing_party_readings_choose_one_physical_line():
    lines = _party_form(["ALPHA EXPORT", "NORTH DISTRICT"], ["BETA IMPORT", "LIBYA"])
    company = next(line for line in lines if line.text == "ALPHA EXPORT")
    address = next(line for line in lines if line.text == "NORTH DISTRICT")
    lines.extend([
        company.model_copy(update={"source": "regional_fallback", "confidence": 0.82}),
        company.model_copy(update={"text": "ALPHA EXPO", "source": "tradenet_parties", "confidence": 0.94}),
        address.model_copy(update={"source": "tradenet_parties", "confidence": 0.88}),
    ])
    fields = extract_tradenet_fields(lines)
    assert fields["exporter"].value == "ALPHA EXPORT NORTH DISTRICT"
    assert fields["exporter"].evidence_text == "ALPHA EXPORT\nNORTH DISTRICT"
    assert len(fields["exporter"].evidence_text.splitlines()) == 2


def test_out_of_order_observations_and_same_row_fragments_use_reading_order():
    lines = _party_form([], ["BETA IMPORT"])
    lines.extend([
        _line("ZONE NORTH", 0.35 * 1200, 0.069 * 1600),
        _line("INDUSTRIAL", 0.23 * 1200, 0.069 * 1600),
        _line("ALPHA EXPORT", 0.30 * 1200, 0.055 * 1600),
    ])
    fields = extract_tradenet_fields(lines)
    assert fields["exporter"].value == "ALPHA EXPORT INDUSTRIAL ZONE NORTH"
    assert fields["exporter"].evidence_text == "ALPHA EXPORT\nINDUSTRIAL\nZONE NORTH"


def test_party_union_bbox_uses_only_selected_lines_and_keeps_page_and_source():
    lines = _party_form(["ALPHA EXPORT", "NORTH DISTRICT"], ["BETA IMPORT"])
    company = next(line for line in lines if line.text == "ALPHA EXPORT")
    address = next(line for line in lines if line.text == "NORTH DISTRICT")
    address.source = "tradenet_parties"
    lines.append(_line("FAR AWAY", 0.80 * 1200, 0.070 * 1600))
    detail = extract_tradenet_fields(lines)["exporter"]
    assert detail.bbox == BoundingBox(
        x1=min(company.bbox.x1, address.bbox.x1),
        y1=min(company.bbox.y1, address.bbox.y1),
        x2=max(company.bbox.x2, address.bbox.x2),
        y2=max(company.bbox.y2, address.bbox.y2),
    )
    assert detail.page == 3
    assert detail.page_width == 1200
    assert detail.page_height == 1600
    assert "full_page" in detail.source and "tradenet_parties" in detail.source


@pytest.mark.parametrize(("width", "height", "dx", "dy"), [
    (1200, 1600, 0, 0),
    (2400, 3200, 28, 32),
    (1000, 1400, -8, 12),
])
def test_multiline_parties_follow_normalized_page_geometry(width, height, dx, dy):
    lines = _party_form(["ALPHA EXPORT", "INDUSTRIAL ZONE"], ["BETA IMPORT", "LIBYA"], width=width, height=height, dx=dx, dy=dy)
    fields = extract_tradenet_fields(lines, page_dimensions={3: (width, height)})
    assert fields["exporter"].value == "ALPHA EXPORT INDUSTRIAL ZONE"
    assert fields["importer"].value == "BETA IMPORT LIBYA"


def test_party_roles_are_separate_and_multiline_evidence_stays_in_its_cell():
    fields = extract_tradenet_fields(_business_form())

    assert fields["exporter"].value == "SUPPLIER TEST LTD 1 TEST STREET"
    assert fields["exporter"].evidence_text == "SUPPLIER TEST LTD\n1 TEST STREET"
    assert fields["importer"].value == "CUSTOMER TEST LLC LIBYA"
    assert fields["importer"].evidence_text == "CUSTOMER TEST LLC\nLIBYA"
    assert "AGENT" not in fields["importer"].evidence_text
    assert fields["exporter"].bbox.y2 < fields["importer"].bbox.y1
    assert fields["importer"].page == 3


def test_single_line_parties_and_short_company_name_continuation():
    lines = _business_form()
    lines = [line for line in lines if line.text not in {"1 TEST STREET", "LIBYA"}]
    fields = extract_tradenet_fields(lines)
    assert fields["exporter"].value == "SUPPLIER TEST LTD"
    assert fields["importer"].value == "CUSTOMER TEST LLC"

    lines = [line for line in lines if line.text != "SUPPLIER TEST LTD"]
    lines.extend([_line("SUPPLIER", 0.30 * 1200, 0.055 * 1600), _line("TEST LTD", 0.30 * 1200, 0.069 * 1600)])
    assert extract_tradenet_fields(lines)["exporter"].value == "SUPPLIER TEST LTD"


def test_ptfn_rate_and_printed_customs_total_use_three_distinct_cells():
    fields = extract_tradenet_fields(_business_form())

    assert fields["ptfn_amount"].value == "12345.000"
    assert fields["ptfn_amount"].normalized_value == Decimal("12345.000")
    assert fields["ptfn_amount"].evidence_text == "12345.000"
    assert fields["currency_conversion_rate"].value == "3.1234000"
    assert fields["currency_conversion_rate"].evidence_text == "3.1234000"
    assert fields["customs_total_value_tnd"].value == "38548.750"
    assert fields["customs_total_value_tnd"].evidence_text == "38548.750"
    assert fields["ptfn_amount"].bbox.y1 < fields["currency_conversion_rate"].bbox.y1
    assert fields["customs_total_value_tnd"].bbox.x1 > fields["currency_conversion_rate"].bbox.x1
    assert all(fields[name].source.startswith("TradeNet") for name in ("ptfn_amount", "currency_conversion_rate", "customs_total_value_tnd"))


@pytest.mark.parametrize("field,source_text", [
    ("ptfn_amount", "52000.000"),
    ("ptfn_amount", "0.500"),
    ("ptfn_amount", "10.000"),
    ("ptfn_amount", "0.000"),
    ("currency_conversion_rate", "3.2842000"),
    ("currency_conversion_rate", "0.500"),
    ("customs_total_value_tnd", "170778.400"),
    ("customs_total_value_tnd", "10.000"),
    ("customs_total_value_tnd", "0.000"),
])
def test_financial_display_keeps_source_decimal_lexeme(field, source_text):
    lines = _business_form()
    old_text = {"ptfn_amount": "12345.000", "currency_conversion_rate": "3.1234000",
                "customs_total_value_tnd": "38548.750"}[field]
    next(line for line in lines if line.text == old_text).text = source_text
    detail = extract_tradenet_fields(lines)[field]
    assert detail.value == source_text
    assert detail.display_value == source_text
    assert detail.machine_value == source_text
    assert detail.normalized_value == Decimal(source_text)


def test_form_cells_scale_and_shift_without_fixed_raster_coordinates():
    fields = extract_tradenet_fields(_business_form(width=2400, height=3200, dx=28, dy=32), page_dimensions={3: (2400, 3200)})

    assert fields["exporter"].value == "SUPPLIER TEST LTD 1 TEST STREET"
    assert fields["importer"].value == "CUSTOMER TEST LLC LIBYA"
    assert fields["ptfn_amount"].value == "12345.000"
    assert fields["currency_conversion_rate"].value == "3.1234000"
    assert fields["customs_total_value_tnd"].value == "38548.750"
    assert fields["ptfn_amount"].page_width == 2400
    assert fields["ptfn_amount"].bbox.x1 > 28


def test_header_alignment_tracks_a_form_shifted_up_within_scan():
    def shifted(line):
        box = line.bbox
        return line.model_copy(update={"bbox": BoundingBox(x1=box.x1, y1=box.y1 - 0.024 * 1600, x2=box.x2, y2=box.y2 - 0.024 * 1600)})

    lines = [shifted(line) for line in _business_form()]
    lines.extend([
        _line("123456", 0.61 * 1200, 0.040 * 1600),
        _line("06-01-2025", 0.72 * 1200, 0.040 * 1600),
        _line("E", 0.76 * 1200, 0.073 * 1600),
    ])
    fields = extract_tradenet_fields(lines, page_dimensions={3: (1200, 1600)})
    assert fields["declaration_type"].value == "E"
    assert fields["exporter"].value == "SUPPLIER TEST LTD 1 TEST STREET"
    assert fields["ptfn_amount"].value == "12345.000"
    assert fields["currency_conversion_rate"].value == "3.1234000"
    assert fields["customs_total_value_tnd"].value == "38548.750"


def test_type_cell_accepts_short_token_but_rejects_letter_elsewhere():
    lines = _header()
    lines.extend([_line("C", 0.60 * 1200, 0.20 * 1600, confidence=1.0), _line("E", 0.40 * 1200, 0.13 * 1600, confidence=1.0)])
    assert extract_tradenet_fields(lines)["declaration_type"].value == "E"
    without_cell = [line for line in lines if not (line.text == "E" and 0.70 * 1200 < line.bbox.x1)]
    assert extract_tradenet_fields(without_cell)["declaration_type"].value is None


def test_full_page_targeted_disagreement_preserves_observations_and_chooses_cell_evidence():
    lines = _business_form()
    original = next(line for line in lines if line.text == "12345.000")
    original.text = "54321.000"
    original.confidence = 0.82
    targeted = original.model_copy(update={"text": "12345.000", "confidence": 0.96, "source": "regional_fallback"})
    merged = _merge_ocr_result(OCRResult(raw_text="", lines=lines, confidence=0.8, engine="fake", page_count=3), [targeted])
    fields = extract_tradenet_fields(merged.lines)

    assert {line.text for line in merged.lines if line.bbox == original.bbox} == {"54321.000", "12345.000"}
    assert fields["ptfn_amount"].value == "12345.000"
    assert fields["ptfn_amount"].source.endswith("regional_fallback)")


def test_dedicated_total_cell_breaks_near_confidence_tie():
    lines = _business_form()
    broad = next(line for line in lines if line.text == "38548.750")
    broad.source = "tradenet_financial"
    broad.confidence = 0.982
    narrow = broad.model_copy(update={"text": "38548.760", "confidence": 0.981, "source": "tradenet_customs_total"})
    fields = extract_tradenet_fields([*lines, narrow])
    assert fields["customs_total_value_tnd"].value == "38548.760"
    assert fields["customs_total_value_tnd"].evidence_text == "38548.760"
    assert fields["customs_total_value_tnd"].source.endswith("tradenet_customs_total)")


def test_party_ocr_prefers_complete_legible_name_over_truncated_crop():
    lines = _business_form()
    original = next(line for line in lines if line.text == "CUSTOMER TEST LLC")
    crop = original.model_copy(update={"text": "USTOMER TEST LLC", "confidence": 0.94, "source": "regional_fallback"})
    noisy = original.model_copy(update={"text": "CU$TOMER TEST", "confidence": 0.95, "source": "regional_fallback"})
    fields = extract_tradenet_fields([*lines, crop, noisy])
    assert fields["importer"].value == "CUSTOMER TEST LLC LIBYA"


def test_missing_or_weak_cell_evidence_remains_null():
    lines = [line for line in _business_form() if line.text not in {"PTFN", "12345.000", "3.1234000", "38548.750", "Exportateur", "Importateur"}]
    fields = extract_tradenet_fields(lines)
    assert all(fields[name].value is None for name in ("exporter", "importer", "ptfn_amount", "currency_conversion_rate", "customs_total_value_tnd"))

    lines.append(_line("12345.000", 0.67 * 1200, 0.232 * 1600, confidence=0.2))
    assert extract_tradenet_fields(lines)["ptfn_amount"].value is None


def test_tradenet_regions_are_narrow_crops_with_original_page_offsets():
    regions = build_tradenet_ocr_regions(np.zeros((1600, 1200, 3), dtype=np.uint8))
    assert [region.name for region in regions] == ["tradenet_declaration_header", "tradenet_parties", "tradenet_financial", "tradenet_customs_total"]
    assert all(region.image.shape[0] < 400 and region.image.shape[1] < 650 for region in regions)
    assert all(region.coordinates[:2] == (region.x_offset, region.y_offset) for region in regions)
