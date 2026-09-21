import numpy as np

from app.core.schemas import BoundingBox, OCRLine
from scripts.local_table_structure_adapter import CanonicalCell, CanonicalRow, CanonicalTable, map_ocr_lines_to_cells
from scripts.table_crop_benchmark_adapter import (
    CropContract,
    crop_to_page_bbox,
    dense_ocr_regions,
    make_crop,
    ocr_lines_in_crop,
    page_to_crop_bbox,
    reconstruct_semantic_candidates,
    remap_table_to_page,
)


def ocr(text, box, index=0):
    return OCRLine(text=text, page_number=1, line_index=index, bbox=BoundingBox(x1=box[0], y1=box[1], x2=box[2], y2=box[3]))


def test_crop_bbox_and_padding_are_clamped() -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    crop, contract = make_crop(image, {"x1": 10, "y1": 10, "x2": 110, "y2": 60}, padding_percent=0.05, source="test")
    assert contract.bbox == {"x1": 5.0, "y1": 7.0, "x2": 115.0, "y2": 63.0}
    assert crop.shape[:2] == (56, 110)


def test_page_crop_coordinate_round_trip() -> None:
    contract = CropContract(1000, 1200, {"x1": 100, "y1": 200, "x2": 500, "y2": 600}, 400, 400)
    page = {"x1": 120, "y1": 240, "x2": 200, "y2": 300}
    assert crop_to_page_bbox(page_to_crop_bbox(page, contract), contract) == page


def test_scaled_crop_coordinate_round_trip() -> None:
    contract = CropContract(1000, 1200, {"x1": 100, "y1": 200, "x2": 500, "y2": 600}, 200, 200, scale_x=2, scale_y=2)
    page = {"x1": 120, "y1": 240, "x2": 200, "y2": 300}
    assert crop_to_page_bbox(page_to_crop_bbox(page, contract), contract) == page


def test_ocr_inclusion_exclusion_and_edge_overlap() -> None:
    contract = CropContract(500, 500, {"x1": 100, "y1": 100, "x2": 300, "y2": 300}, 200, 200)
    lines = [
        ocr("inside", (120, 120, 180, 140), 1),
        ocr("outside", (310, 310, 350, 330), 2),
        ocr("edge", (80, 120, 120, 140), 3),
    ]
    assert [line.line_index for line in ocr_lines_in_crop(lines, contract)] == [1, 3]


def test_slanet_cell_bbox_remaps_to_page_space() -> None:
    contract = CropContract(500, 500, {"x1": 100, "y1": 200, "x2": 300, "y2": 400}, 200, 200)
    cell = CanonicalCell(0, 0, 1, 1, {"x1": 5, "y1": 10, "x2": 50, "y2": 60})
    table = CanonicalTable(1, "SLANet_plus", dict(cell.bbox), [CanonicalRow(0, [cell])], 1, [cell], [], 0.9)
    remap_table_to_page(table, contract)
    assert cell.bbox == {"x1": 105.0, "y1": 210.0, "x2": 150.0, "y2": 260.0}


def test_ambiguous_mapping_remains_explicit_after_remap() -> None:
    contract = CropContract(500, 500, {"x1": 100, "y1": 100, "x2": 300, "y2": 300}, 200, 200)
    cells = [CanonicalCell(0, index, 1, 1, {"x1": 0, "y1": 0, "x2": 100, "y2": 50}) for index in range(2)]
    table = CanonicalTable(1, "SLANet_plus", None, [CanonicalRow(0, cells)], 2, cells, [], 0.9)
    remap_table_to_page(table, contract)
    diagnostics = map_ocr_lines_to_cells(table, [ocr("53.00", (120, 110, 160, 130), 9)])
    assert diagnostics.ambiguous_ocr_line_ids == [9]


def test_semantic_mapping_preserves_french_arabic_and_numeric_values() -> None:
    header = [
        CanonicalCell(0, 0, 1, 1, None, text="Désignation"),
        CanonicalCell(0, 1, 1, 1, None, text="Quantité"),
        CanonicalCell(0, 2, 1, 1, None, text="Unité"),
        CanonicalCell(0, 3, 1, 1, None, text="Prix unitaire"),
        CanonicalCell(0, 4, 1, 1, None, text="Total"),
    ]
    body = [
        CanonicalCell(1, 0, 1, 1, None, text="إسمنت Portland"),
        CanonicalCell(1, 1, 1, 1, None, text="1000"),
        CanonicalCell(1, 2, 1, 1, None, text="T"),
        CanonicalCell(1, 3, 1, 1, None, text="53.00"),
        CanonicalCell(1, 4, 1, 1, None, text="53000.00"),
    ]
    table = CanonicalTable(1, "SLANet_plus", None, [CanonicalRow(0, header), CanonicalRow(1, body)], 5, header + body, [], 0.9)
    candidates = reconstruct_semantic_candidates(table)
    assert candidates[0].complete is True
    assert candidates[0].values["description"] == "إسمنت Portland"
    assert candidates[0].financial_status == "consistent"


def test_dense_region_uses_geometry_not_document_identity() -> None:
    lines = [
        ocr("Description", (10, 10, 100, 25), 1), ocr("Quantity", (200, 10, 260, 25), 2),
        ocr("Unit Price", (300, 10, 380, 25), 3), ocr("Total", (420, 10, 480, 25), 4),
        ocr("Produit", (10, 40, 100, 55), 5), ocr("1000", (200, 40, 250, 55), 6),
        ocr("53", (300, 40, 340, 55), 7), ocr("53000", (420, 40, 480, 55), 8),
    ]
    regions = dense_ocr_regions(lines, 500, 700)
    assert regions
    assert regions[0]["y1"] <= 10 and regions[0]["y2"] >= 55
