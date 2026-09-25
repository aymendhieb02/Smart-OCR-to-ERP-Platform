from app.core.schemas import BoundingBox, OCRLine
from app.services.ocr_engine import _map_inference_bbox_to_page
from app.services.tradenet_field_extractor import extract_tradenet_fields


def _line(text, cx, cy, *, page=3, width=1200, height=1600, confidence=0.91, box_w=70):
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
        ("Type déclaration", 0.69, 0.14),
        (type_value, 0.69, 0.17),
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


def test_noisy_missing_labels_use_trade_net_number_date_cells_but_do_not_guess_type_or_count():
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
    assert fields["declaration_type"].value is None
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
