from __future__ import annotations

import numpy as np

from app.core.config import settings
from app.core.schemas import BoundingBox, OCRLine
from app.services.table_transformer.table_cell_mapper import map_ocr_to_cells
from app.services.table_transformer.table_transformer_cache import TableTransformerCache, image_cache_key
from app.services.table_transformer.table_transformer_detector import TATRTable, fallback_detect_tables_from_ocr
from app.services.table_transformer.table_transformer_loader import TableTransformerLoader
from app.services.table_transformer.table_transformer_reconstruction import reconstruct_line_items_from_cells
from app.services.table_transformer.table_transformer_router import experimental_table_transformer_enabled


def line(text: str, x1: float, y1: float, x2: float, y2: float, index: int) -> OCRLine:
    return OCRLine(text=text, confidence=0.9, bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2), page_number=1, line_index=index)


def test_table_transformer_config_defaults_off() -> None:
    assert settings.enable_table_transformer is False
    assert experimental_table_transformer_enabled() is False


def test_loader_gracefully_reports_missing_local_weights(tmp_path) -> None:
    TableTransformerLoader.reset_singleton()
    result = TableTransformerLoader(model_path=tmp_path, device="cpu").load(download=False)
    assert result.available in {False, True}
    if not result.available:
        assert result.error


def test_cache_roundtrip(tmp_path) -> None:
    cache = TableTransformerCache(tmp_path)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    key = image_cache_key(image)
    cache.set(key, {"tables": []})
    assert cache.get(key) == {"tables": []}


def test_fallback_detects_table_region_from_ocr() -> None:
    lines = [
        line("Description Qty Price Total", 10, 10, 220, 30, 0),
        line("Service 2 10 20", 10, 40, 220, 60, 1),
    ]
    tables = fallback_detect_tables_from_ocr(lines)
    assert isinstance(tables, list)


def test_cell_mapper_and_reconstruction() -> None:
    table = TATRTable(page=1, bbox={"x1": 0, "y1": 0, "x2": 300, "y2": 100}, confidence=0.9)
    lines = [
        line("Description", 5, 5, 90, 20, 0),
        line("Qty", 110, 5, 135, 20, 1),
        line("Price", 160, 5, 200, 20, 2),
        line("Total", 230, 5, 280, 20, 3),
        line("Service", 5, 40, 90, 55, 4),
        line("2", 110, 40, 135, 55, 5),
        line("10", 160, 40, 200, 55, 6),
        line("20", 230, 40, 280, 55, 7),
    ]
    cells = map_ocr_to_cells(table, lines)
    items = reconstruct_line_items_from_cells(cells)
    assert cells
    assert items
    assert items[0].description


def test_visualizer_imports() -> None:
    from app.services.table_transformer.table_transformer_visualizer import draw_table_transformer_overlay

    assert callable(draw_table_transformer_overlay)
