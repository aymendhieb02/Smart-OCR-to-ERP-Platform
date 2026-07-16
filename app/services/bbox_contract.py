from __future__ import annotations

from typing import Any

from app.core.schemas import BoundingBox, DocumentPreview
from app.services.ocr_engine import normalize_paddle_bbox


def normalize_public_bbox(value: Any, *, page_width: int | None = None, page_height: int | None = None) -> BoundingBox | None:
    bbox = normalize_paddle_bbox(value)
    if not bbox:
        return None
    x1, y1, x2, y2 = bbox.x1, bbox.y1, bbox.x2, bbox.y2
    if page_width is not None:
        x1 = _clamp(x1, 0.0, float(page_width))
        x2 = _clamp(x2, 0.0, float(page_width))
    if page_height is not None:
        y1 = _clamp(y1, 0.0, float(page_height))
        y2 = _clamp(y2, 0.0, float(page_height))
    if x2 <= x1 or y2 <= y1:
        return None
    return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)


def apply_public_bbox_contract(response) -> None:
    pages = _page_dimensions(getattr(response, "document_preview", None))
    for line in getattr(response, "ocr_blocks", []) or []:
        _normalize_model_box(line, getattr(line, "page_number", None), pages)
    for line in getattr(response, "all_ocr_blocks", []) or []:
        _normalize_model_box(line, getattr(line, "page_number", None), pages)
    for block in getattr(response, "layout_blocks", []) or []:
        _normalize_model_box(block, getattr(block, "page", None), pages)
    for box in getattr(response, "field_boxes", []) or []:
        _normalize_model_box(box, getattr(box, "page", None), pages)
    for detail in (getattr(response, "expanded_fields", None) or {}).values():
        _normalize_model_box(detail, getattr(detail, "page", None), pages)
    for item in getattr(response, "all_line_items", []) or []:
        _normalize_model_box(item, getattr(item, "page", None), pages)
    for item in getattr(response, "line_items_validated", []) or []:
        _normalize_model_box(item, getattr(item, "page", None), pages)
    for item in getattr(response, "line_items_needs_review", []) or []:
        _normalize_model_box(item, getattr(item, "page", None), pages)
    for item in getattr(response, "detected_fields", None).line_items if getattr(response, "detected_fields", None) else []:
        _normalize_model_box(item, getattr(item, "page", None), pages)
    for candidates in (getattr(response, "review_candidates", None) or {}).values():
        for candidate in candidates:
            _normalize_dict_box(candidate, candidate.get("page"), pages)
    for candidates in (getattr(response, "rejected_candidates", None) or {}).values():
        for candidate in candidates:
            _normalize_dict_box(candidate, candidate.get("page"), pages)
    if getattr(response, "extraction_debug", None) is not None:
        response.extraction_debug.setdefault("bbox_trace", {})
        response.extraction_debug["bbox_trace"]["public_boxes_count"] = count_public_ocr_boxes(response)
        response.extraction_debug["bbox_trace"]["bbox_loss_stage"] = bbox_loss_stage(response)


def _page_dimensions(preview: DocumentPreview | None) -> dict[int, tuple[int, int]]:
    if not preview:
        return {}
    return {
        int(page.page): (int(page.width), int(page.height))
        for page in preview.pages
        if page.width and page.height
    }


def _normalize_model_box(model: Any, page: int | None, pages: dict[int, tuple[int, int]]) -> None:
    width, height = pages.get(int(page or 1), (None, None))
    bbox = normalize_public_bbox(getattr(model, "bbox", None), page_width=width, page_height=height)
    setattr(model, "bbox", bbox)
    if width and height:
        setattr(model, "page_width", width)
        setattr(model, "page_height", height)
    if bbox:
        setattr(model, "coordinate_space", "original_page")


def _normalize_dict_box(payload: dict[str, Any], page: int | None, pages: dict[int, tuple[int, int]]) -> None:
    width, height = pages.get(int(page or 1), (None, None))
    bbox = normalize_public_bbox(payload.get("bbox"), page_width=width, page_height=height)
    payload["bbox"] = bbox.model_dump(mode="json") if bbox else None
    if width and height:
        payload["page_width"] = width
        payload["page_height"] = height
    if bbox:
        payload["coordinate_space"] = "original_page"


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def count_public_ocr_boxes(response) -> int:
    return sum(1 for line in getattr(response, "ocr_blocks", []) or [] if getattr(line, "bbox", None) is not None)


def bbox_loss_stage(response) -> str | None:
    trace = (getattr(response, "extraction_debug", None) or {}).get("stage_timings", {})
    raw = int(trace.get("raw_boxes_count", 0) or 0)
    normalized = int(trace.get("normalized_boxes_count", 0) or 0)
    pre_api = int(trace.get("pre_api_boxes_count", 0) or 0)
    public = count_public_ocr_boxes(response)
    if raw == 0:
        return "raw_paddle_result" if trace.get("geometry_status") == "unavailable_from_engine" else None
    if normalized == 0:
        return "normalize_paddle_bbox"
    if pre_api == 0:
        return "ocrline_creation"
    if public == 0:
        return "bbox_contract_public_mapping"
    return None
