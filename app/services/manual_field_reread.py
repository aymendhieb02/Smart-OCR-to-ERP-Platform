from __future__ import annotations

import re
import time

import numpy as np

from app.core.schemas import (
    BoundingBox,
    RuspinaAddressRereadContext,
    RuspinaAddressRereadResponse,
)
from app.services.ruspina_field_extractor import FAMILY as RUSPINA_FAMILY, extract_ruspina_fields
from app.services.table_regions import build_ruspina_address_region


def reread_ruspina_address(
    image: np.ndarray,
    context: RuspinaAddressRereadContext,
    ocr_engine,
) -> RuspinaAddressRereadResponse:
    """Return a one-region address proposal without changing review state."""
    started = time.perf_counter()
    if (context.semantic_group, context.document_family, context.field) != (
        "page2", RUSPINA_FAMILY, "address",
    ):
        raise ValueError("Targeted reread is available only for the RUSPINA Page-2 address")
    if image is None or image.size == 0:
        raise ValueError("The selected source page could not be rendered")
    if context.page < 1 or context.page_width is None or context.page_height is None or context.field_bbox is None:
        raise ValueError("Address page geometry is unavailable; keep the current value")

    labels = [
        line for line in context.ocr_blocks
        if line.page_number == context.page and line.bbox
        and re.fullmatch(r"\s*address\s*[:;]?\s*", line.text, re.IGNORECASE)
    ]
    if context.label_line_index is not None:
        indexed_label = next((
            line for line in context.ocr_blocks
            if line.page_number == context.page and line.line_index == context.label_line_index and line.bbox
        ), None)
        if indexed_label is not None:
            labels.insert(0, indexed_label)
    field_box = context.field_bbox
    label = min(
        labels,
        key=lambda line: abs(
            ((line.bbox.y1 + line.bbox.y2) - (field_box.y1 + field_box.y2)) / 2
        ),
        default=None,
    )
    if label is None:
        raise ValueError("Address label evidence is unavailable; keep the current value")

    region = build_ruspina_address_region(
        image, label.bbox, field_box,
        page_width=context.page_width,
        page_height=context.page_height,
    )
    if region is None:
        raise ValueError("A safe address-row crop could not be derived; keep the current value")

    # Deliberately call only the regional method. This path never invokes run().
    reread_lines = ocr_engine.run_targeted_region(image, region, page_number=context.page) or []
    regional_evidence = [label, *[
        line for line in reread_lines
        if line.page_number == context.page and line.bbox
    ]]
    extracted = extract_ruspina_fields(
        regional_evidence,
        document_family=RUSPINA_FAMILY,
        page_dimensions={context.page: (context.page_width, context.page_height)},
    )
    reread_detail = extracted.fields.get("address")
    reread_value = None
    reread_confidence = None
    reread_source = "targeted regional OCR"
    if reread_detail:
        reread_value = reread_detail.display_value or reread_detail.value
        reread_confidence = reread_detail.confidence
        reread_source = reread_detail.source or reread_source
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    x1, y1, x2, y2 = region.coordinates
    return RuspinaAddressRereadResponse(
        field="address",
        current_value=context.current_value,
        reread_value=str(reread_value).strip() if reread_value is not None else None,
        current_confidence=context.current_confidence,
        reread_confidence=reread_confidence,
        current_source=context.current_source,
        reread_source=reread_source,
        page=context.page,
        reread_bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        reread_performed=True,
        regional_ocr_calls=1,
        full_page_ocr_calls=0,
        latency_ms=latency_ms,
    )
