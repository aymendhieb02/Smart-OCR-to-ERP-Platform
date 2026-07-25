from __future__ import annotations

from app.core.config import settings
from app.core.schemas import LineItem, OCRLine
from app.services.table_transformer.table_cell_mapper import map_ocr_to_cells
from app.services.table_transformer.table_transformer_cache import TableTransformerCache, image_cache_key
from app.services.table_transformer.table_transformer_detector import TableTransformerDetector
from app.services.table_transformer.table_transformer_reconstruction import reconstruct_line_items_from_cells


def experimental_table_transformer_enabled() -> bool:
    return bool(settings.enable_table_transformer)


def reconstruct_with_table_transformer(image, ocr_lines: list[OCRLine], *, page: int = 1, use_cache: bool = True) -> tuple[list[LineItem], dict]:
    if not experimental_table_transformer_enabled():
        return [], {"enabled": False, "reason": "INVOICE_OCR_ENABLE_TABLE_TRANSFORMER is false"}
    cache = TableTransformerCache()
    key = image_cache_key(image, page=page)
    if use_cache:
        cached = cache.get(key)
        if cached:
            return [], {"enabled": True, "cache_hit": True, "detection": cached, "reason": "cached detection loaded; reconstruction skipped in router"}
    detection = TableTransformerDetector().detect(image, page=page, ocr_lines=ocr_lines)
    detection_dict = detection.to_dict()
    if use_cache:
        cache.set(key, detection_dict)
    items: list[LineItem] = []
    for table in detection.tables:
        cells = map_ocr_to_cells(table, [line for line in ocr_lines if line.page_number == table.page])
        items.extend(reconstruct_line_items_from_cells(cells))
    return items, {"enabled": True, "cache_hit": False, "detection": detection_dict, "line_item_count": len(items)}
