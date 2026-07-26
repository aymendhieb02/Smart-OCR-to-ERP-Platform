from __future__ import annotations

from typing import Any

import numpy as np

from app.core.config import settings
from app.core.schemas import BoundingBox, LayoutBlock, OCRLine
from app.services.layout_model.layout_model_detector import LayoutModelDetector, LayoutRegion


LABEL_TO_BLOCK_TYPE = {
    "title": "header",
    "plain_text": "text",
    "text": "text",
    "abandon": "footer",
    "figure": "figure",
    "figure_caption": "figure_caption",
    "table": "table",
    "table_caption": "table_caption",
    "table_footnote": "footer",
    "isolate_formula": "formula",
    "formula_caption": "formula_caption",
    "header": "header",
    "footer": "footer",
}


def layout_model_enabled() -> bool:
    return bool(settings.enable_layout_model)


def detect_layout_blocks_with_model(images: list[np.ndarray], ocr_lines: list[OCRLine]) -> tuple[list[LayoutBlock], dict[str, Any]]:
    if not layout_model_enabled():
        return [], {"enabled": False, "reason": "INVOICE_OCR_ENABLE_LAYOUT_MODEL is false"}
    detector = LayoutModelDetector()
    blocks: list[LayoutBlock] = []
    detections: list[dict[str, Any]] = []
    for page, image in enumerate(images, start=1):
        detection = detector.detect(image, page=page)
        detections.append(detection.to_dict())
        if not detection.available:
            return [], {"enabled": True, "available": False, "reason": detection.model_load_error, "detections": detections}
        for region in detection.regions:
            block = _region_to_layout_block(region, ocr_lines, image)
            if block:
                blocks.append(block)
    return blocks, {"enabled": True, "available": True, "source": "doclayout_yolo", "detections": detections, "block_count": len(blocks)}


def _region_to_layout_block(region: LayoutRegion, ocr_lines: list[OCRLine], image: np.ndarray) -> LayoutBlock | None:
    bbox = BoundingBox(**region.bbox)
    inside = [
        line for line in ocr_lines
        if line.page_number == region.page and line.bbox is not None and _inside(line.bbox, bbox)
    ]
    text = "\n".join(line.text for line in sorted(inside, key=lambda item: (item.bbox.y1 if item.bbox else 0, item.bbox.x1 if item.bbox else 0)))
    height, width = image.shape[:2]
    return LayoutBlock(
        block_type=LABEL_TO_BLOCK_TYPE.get(region.label.lower().replace(" ", "_"), region.label.lower().replace(" ", "_")),
        bbox=bbox,
        confidence=region.confidence,
        text=text,
        fields=[],
        page=region.page,
        page_width=width,
        page_height=height,
        coordinate_space="original_page",
    )


def _inside(inner: BoundingBox, outer: BoundingBox) -> bool:
    x_overlap = max(0.0, min(inner.x2, outer.x2) - max(inner.x1, outer.x1))
    y_overlap = max(0.0, min(inner.y2, outer.y2) - max(inner.y1, outer.y1))
    inner_area = max(1.0, (inner.x2 - inner.x1) * (inner.y2 - inner.y1))
    return (x_overlap * y_overlap) / inner_area >= 0.45
