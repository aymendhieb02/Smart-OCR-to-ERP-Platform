from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.config import settings
from app.services.table_transformer.table_transformer_loader import TableTransformerLoader


@dataclass
class TATRBox:
    x1: float
    y1: float
    x2: float
    y2: float

    def to_dict(self) -> dict[str, float]:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}


@dataclass
class TATRCell:
    row_index: int
    column_index: int
    bbox: dict[str, float]
    polygon: list[tuple[float, float]]
    confidence: float
    cell_type: str = "cell"


@dataclass
class TATRTable:
    page: int
    bbox: dict[str, float]
    confidence: float
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[dict[str, Any]] = field(default_factory=list)
    cells: list[TATRCell] = field(default_factory=list)
    headers: list[dict[str, Any]] = field(default_factory=list)
    source: str = "table_transformer"


@dataclass
class TATRDetectionResult:
    available: bool
    tables: list[TATRTable]
    latency_seconds: float
    model_loaded: bool
    model_load_error: str | None = None
    cache_hit: bool = False
    source: str = "table_transformer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "latency_seconds": self.latency_seconds,
            "model_loaded": self.model_loaded,
            "model_load_error": self.model_load_error,
            "cache_hit": self.cache_hit,
            "source": self.source,
            "tables": [
                {
                    "page": table.page,
                    "bbox": table.bbox,
                    "confidence": table.confidence,
                    "rows": table.rows,
                    "columns": table.columns,
                    "headers": table.headers,
                    "cells": [cell.__dict__ for cell in table.cells],
                    "source": table.source,
                }
                for table in self.tables
            ],
        }


class TableTransformerDetector:
    """Runs optional TATR structure detection.

    No OCR is performed here. When the official model is unavailable, a
    deterministic geometry-only fallback can expose candidate table regions for
    debugging while clearly marking the source as fallback.
    """

    def __init__(self, loader: TableTransformerLoader | None = None) -> None:
        self.loader = loader or TableTransformerLoader()

    def detect(self, image: np.ndarray, *, page: int = 1, ocr_lines: list[Any] | None = None, allow_fallback: bool = True) -> TATRDetectionResult:
        started = time.perf_counter()
        load_result = self.loader.load(download=False)
        if load_result.available:
            try:
                return self._detect_with_model(image, page=page, load_result=load_result, started=started)
            except Exception as exc:
                if not allow_fallback:
                    return TATRDetectionResult(False, [], round(time.perf_counter() - started, 4), True, str(exc))
        if allow_fallback:
            tables = fallback_detect_tables_from_ocr(ocr_lines or [], page=page)
            return TATRDetectionResult(
                available=False,
                tables=tables[: int(settings.table_transformer_max_tables or 10)],
                latency_seconds=round(time.perf_counter() - started, 4),
                model_loaded=False,
                model_load_error=load_result.error,
                source="geometry_fallback",
            )
        return TATRDetectionResult(False, [], round(time.perf_counter() - started, 4), False, load_result.error)

    def _detect_with_model(self, image: np.ndarray, *, page: int, load_result: Any, started: float) -> TATRDetectionResult:
        from PIL import Image
        import torch

        pil_image = Image.fromarray(image[:, :, ::-1] if image.ndim == 3 else image)
        inputs = load_result.processor(images=pil_image, return_tensors="pt")
        inputs = {key: value.to(load_result.device) for key, value in inputs.items()}
        with torch.no_grad():
            outputs = load_result.model(**inputs)
        target_sizes = torch.tensor([pil_image.size[::-1]], device=load_result.device)
        processed = load_result.processor.post_process_object_detection(
            outputs,
            threshold=float(settings.table_transformer_confidence or 0.75),
            target_sizes=target_sizes,
        )[0]
        tables: list[TATRTable] = []
        for score, label, box in zip(processed.get("scores", []), processed.get("labels", []), processed.get("boxes", [])):
            label_name = load_result.model.config.id2label.get(int(label), str(int(label)))
            if "table" not in label_name.lower():
                continue
            x1, y1, x2, y2 = [float(value) for value in box.tolist()]
            bbox = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            tables.append(TATRTable(page=page, bbox=bbox, confidence=float(score), source="table_transformer"))
        return TATRDetectionResult(True, tables[: int(settings.table_transformer_max_tables or 10)], round(time.perf_counter() - started, 4), True)


def fallback_detect_tables_from_ocr(ocr_lines: list[Any], *, page: int = 1) -> list[TATRTable]:
    positioned = [line for line in ocr_lines if getattr(line, "bbox", None)]
    if not positioned:
        return []
    page_lines = [line for line in positioned if getattr(line, "page_number", page) == page] or positioned
    header_terms = ("description", "designation", "item", "product", "qty", "qte", "quantity", "price", "prix", "total", "amount", "vat", "tva")
    header_candidates = []
    for line in page_lines:
        text = str(getattr(line, "text", "")).lower()
        hits = sum(1 for term in header_terms if term in text)
        if hits >= 2:
            header_candidates.append(line)
    if header_candidates:
        header_y = min(line.bbox.y1 for line in header_candidates)
        body = [line for line in page_lines if line.bbox.y1 >= header_y - 8]
    else:
        body = page_lines
    if len(body) < 2:
        return []
    boxes = [line.bbox for line in body if line.bbox]
    bbox = {
        "x1": min(box.x1 for box in boxes),
        "y1": min(box.y1 for box in boxes),
        "x2": max(box.x2 for box in boxes),
        "y2": max(box.y2 for box in boxes),
    }
    confidence_values = [line.confidence for line in body if line.confidence is not None]
    confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.45
    return [TATRTable(page=page, bbox=bbox, confidence=min(confidence, 0.65), source="geometry_fallback")]
