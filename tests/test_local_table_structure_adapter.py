from types import SimpleNamespace

from app.core.schemas import BoundingBox, OCRLine
from scripts.local_table_structure_adapter import (
    arithmetic_diagnostics,
    canonicalize_deterministic_result,
    canonicalize_slanet_result,
    map_ocr_lines_to_cells,
    normalize_bbox,
)


def line(text: str, box: tuple[float, float, float, float], index: int) -> OCRLine:
    return OCRLine(text=text, page_number=1, line_index=index, bbox=BoundingBox(x1=box[0], y1=box[1], x2=box[2], y2=box[3]))


def synthetic_result():
    return {
        "bbox": [
            [0, 0, 100, 0, 100, 20, 0, 20],
            [100, 0, 200, 0, 200, 20, 100, 20],
            [0, 20, 100, 20, 100, 40, 0, 40],
            [100, 20, 200, 20, 200, 40, 100, 40],
        ],
        "structure": [
            "<html><body><table><tbody>",
            "<tr><td></td><td></td></tr>",
            "<tr><td", " colspan=\"1\"", "></td><td></td></tr>",
            "</tbody></table></body></html>",
        ],
        "structure_score": 0.91,
    }


def test_bbox_conversion_supports_polygon_and_mapping() -> None:
    assert normalize_bbox([10, 20, 30, 18, 32, 40, 8, 42]) == {"x1": 8.0, "y1": 18.0, "x2": 32.0, "y2": 42.0}
    assert normalize_bbox({"left": 1, "top": 2, "width": 3, "height": 4}) == {"x1": 1.0, "y1": 2.0, "x2": 4.0, "y2": 6.0}


def test_slanet_rows_columns_and_spans_are_preserved() -> None:
    table = canonicalize_slanet_result(synthetic_result(), page_number=3)
    assert table.page_number == 3
    assert table.columns == 2
    assert len(table.rows) == 2
    assert [(cell.row_index, cell.column_index) for cell in table.cells] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert table.cells[2].column_span == 1


def test_rowspan_advances_occupied_columns() -> None:
    result = {
        "bbox": [[0, 0, 10, 0, 10, 20, 0, 20], [10, 0, 20, 0, 20, 10, 10, 10], [10, 10, 20, 10, 20, 20, 10, 20]],
        "structure": ["<table><tr><td rowspan='2'></td><td></td></tr><tr><td></td></tr></table>"],
        "structure_score": 1,
    }
    table = canonicalize_slanet_result(result, page_number=1)
    assert [(cell.row_index, cell.column_index, cell.row_span) for cell in table.cells] == [(0, 0, 2), (0, 1, 1), (1, 1, 1)]


def test_ocr_mapping_preserves_unicode_and_empty_cells() -> None:
    table = canonicalize_slanet_result(synthetic_result(), page_number=1)
    diagnostics = map_ocr_lines_to_cells(table, [
        line("Désignation", (5, 2, 90, 18), 10),
        line("الكمية", (105, 2, 190, 18), 11),
        line("53.00", (105, 22, 190, 38), 12),
    ])
    assert diagnostics.assigned_ocr_line_ids == [10, 11, 12]
    assert "Désignation" in table.cells[0].text
    assert "الكمية" in table.cells[1].text
    assert table.cells[3].text == "53.00"
    assert diagnostics.empty_cell_indexes == [2]


def test_ambiguous_overlap_is_not_forced_into_a_cell() -> None:
    result = {
        "bbox": [[0, 0, 100, 0, 100, 30, 0, 30], [0, 0, 100, 0, 100, 30, 0, 30]],
        "structure": ["<table><tr><td></td><td></td></tr></table>"],
        "structure_score": 0.8,
    }
    table = canonicalize_slanet_result(result, page_number=1)
    diagnostics = map_ocr_lines_to_cells(table, [line("1000", (10, 5, 50, 20), 7)])
    assert diagnostics.ambiguous_ocr_line_ids == [7]
    assert diagnostics.assigned_ocr_line_ids == []


def test_unassigned_line_is_reported() -> None:
    table = canonicalize_slanet_result(synthetic_result(), page_number=1)
    diagnostics = map_ocr_lines_to_cells(table, [line("hors tableau", (300, 300, 400, 320), 4)])
    assert diagnostics.unassigned_ocr_line_ids == [4]


def test_deterministic_conversion_does_not_invent_row_cell_relationships() -> None:
    result = SimpleNamespace(
        columns=[SimpleNamespace(semantic_type="description")],
        cells=[SimpleNamespace(text="Article", bbox={"x1": 1, "y1": 2, "x2": 3, "y2": 4}, column_type="description", source_line_ids=[9], assignment_confidence=0.8)],
        rows=[SimpleNamespace(description="Ciment", reference=None, quantity=2, unit=None, unit_price=5, discount=None, tax_rate=None, line_total_ht=10, line_total_ttc=None)],
        regions=[SimpleNamespace(bbox={"x1": 0, "y1": 0, "x2": 20, "y2": 20}, confidence=0.7)],
        headers=[SimpleNamespace(text="Désignation")],
        unresolved_fragments=[], line_items=[object()], selected_strategy="COLUMNAR_TABLE",
    )
    table = canonicalize_deterministic_result(result, page_number=1)
    assert table.cells[0].row_index is None
    assert table.cells[0].column_index == 0
    assert table.rows[0].semantic_values["quantity"] == 2
    assert table.metadata["explicit_row_cell_relationships"] is False


def test_empty_and_malformed_model_response_is_safe() -> None:
    table = canonicalize_slanet_result({"structure": ["<table><tr><td>"], "bbox": []}, page_number=1)
    assert len(table.cells) == 1
    assert table.cells[0].bbox is None
    assert table.metadata["malformed_or_mismatched"] is True


def test_numeric_financial_diagnostics() -> None:
    table = canonicalize_deterministic_result(SimpleNamespace(
        columns=[], cells=[], regions=[], headers=[], unresolved_fragments=[], line_items=[], selected_strategy="NUMERIC_ANCHORED_ROWS",
        rows=[SimpleNamespace(description="Produit", reference=None, quantity=1000, unit=None, unit_price=53, discount=None, tax_rate=None, line_total_ht=53000, line_total_ttc=None)],
    ), page_number=1)
    assert arithmetic_diagnostics(table.rows)["exact"] == 1
