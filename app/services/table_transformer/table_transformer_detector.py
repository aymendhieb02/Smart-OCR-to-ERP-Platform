from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.config import settings
from app.services.table_transformer.table_transformer_loader import TableDetectionLoader, TableTransformerLoader


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
    detection_count_uncapped: int = 0
    structure_regions_processed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "latency_seconds": self.latency_seconds,
            "model_loaded": self.model_loaded,
            "model_load_error": self.model_load_error,
            "cache_hit": self.cache_hit,
            "source": self.source,
            "detection_count_uncapped": self.detection_count_uncapped,
            "structure_regions_processed": self.structure_regions_processed,
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
    """Runs optional two-stage TATR detection.

    Stage 1 uses the full-page table-detection model to find table regions.
    Stage 2 crops each detected table and runs the structure-recognition model
    inside that crop. Structure boxes are remapped back to full-page coordinates
    so downstream OCR-to-cell mapping still aligns with OCR boxes.
    """

    def __init__(self, loader: TableTransformerLoader | None = None, detection_loader: TableDetectionLoader | None = None) -> None:
        self.loader = loader or TableTransformerLoader()
        self.detection_loader = detection_loader or TableDetectionLoader()

    def detect(self, image: np.ndarray, *, page: int = 1, ocr_lines: list[Any] | None = None, allow_fallback: bool = True) -> TATRDetectionResult:
        started = time.perf_counter()
        detection_load = self.detection_loader.load(download=False)
        structure_load = self.loader.load(download=False) if detection_load.available else None
        if detection_load.available and structure_load and structure_load.available:
            try:
                return self._detect_with_model(image, page=page, detection_load=detection_load, structure_load=structure_load, started=started)
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
                model_load_error=_join_errors(
                    f"detection: {detection_load.error}" if not detection_load.available else None,
                    f"structure: {structure_load.error}" if structure_load and not structure_load.available else None,
                    "structure: not loaded because detection model is unavailable" if not detection_load.available else None,
                ),
                source="geometry_fallback",
                detection_count_uncapped=len(tables),
            )
        return TATRDetectionResult(
            False,
            [],
            round(time.perf_counter() - started, 4),
            False,
            _join_errors(
                f"detection: {detection_load.error}" if not detection_load.available else None,
                f"structure: {structure_load.error}" if structure_load and not structure_load.available else None,
            ),
        )

    def _detect_with_model(self, image: np.ndarray, *, page: int, detection_load: Any, structure_load: Any, started: float) -> TATRDetectionResult:
        table_boxes = self._detect_table_boxes(image, page=page, load_result=detection_load)
        max_tables = int(settings.table_transformer_max_tables or 10)
        capped_boxes = table_boxes[:max_tables]
        tables = [self._detect_structure_in_crop(image, table=table, load_result=structure_load) for table in capped_boxes]
        return TATRDetectionResult(
            True,
            tables,
            round(time.perf_counter() - started, 4),
            True,
            source="table_transformer",
            detection_count_uncapped=len(table_boxes),
            structure_regions_processed=len(capped_boxes),
        )

    def _detect_table_boxes(self, image: np.ndarray, *, page: int, load_result: Any) -> list[TATRTable]:
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
            threshold=float(settings.table_transformer_detection_confidence or settings.table_transformer_confidence or 0.75),
            target_sizes=target_sizes,
        )[0]
        tables: list[TATRTable] = []
        for score, label, box in zip(processed.get("scores", []), processed.get("labels", []), processed.get("boxes", [])):
            label_name = load_result.model.config.id2label.get(int(label), str(int(label)))
            if "table" not in label_name.lower():
                continue
            bbox = _clamp_bbox(_box_to_dict(box), image.shape[1], image.shape[0])
            if _valid_bbox(bbox):
                tables.append(TATRTable(page=page, bbox=bbox, confidence=float(score), source="table_transformer_detection"))
        return sorted(tables, key=lambda table: table.confidence, reverse=True)

    def _detect_structure_in_crop(self, image: np.ndarray, *, table: TATRTable, load_result: Any) -> TATRTable:
        from PIL import Image
        import torch

        height, width = image.shape[:2]
        crop_box = _pad_bbox(table.bbox, width=width, height=height, padding=8)
        x1, y1, x2, y2 = [int(round(crop_box[key])) for key in ("x1", "y1", "x2", "y2")]
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return table
        pil_image = Image.fromarray(crop[:, :, ::-1] if crop.ndim == 3 else crop)
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
        rows: list[dict[str, Any]] = []
        columns: list[dict[str, Any]] = []
        headers: list[dict[str, Any]] = []
        other_boxes: list[dict[str, Any]] = []
        for score, label, box in zip(processed.get("scores", []), processed.get("labels", []), processed.get("boxes", [])):
            label_name = load_result.model.config.id2label.get(int(label), str(int(label))).lower()
            page_box = _clamp_bbox(_offset_bbox(_box_to_dict(box), x1, y1), width, height)
            if not _valid_bbox(page_box):
                continue
            entry = {"bbox": page_box, "confidence": float(score), "label": label_name, "source": "table_transformer_structure"}
            if "row" in label_name and "header" not in label_name:
                rows.append(entry)
            elif "column" in label_name and "header" not in label_name:
                columns.append(entry)
            elif "header" in label_name:
                headers.append(entry)
            elif "cell" in label_name:
                other_boxes.append(entry)
        table.rows = sorted(rows, key=lambda item: item["bbox"]["y1"])
        table.columns = sorted(columns, key=lambda item: item["bbox"]["x1"])
        table.headers = headers + other_boxes
        table.cells = _build_cells_from_rows_columns(table.rows, table.columns, default_confidence=table.confidence)
        table.source = "table_transformer"
        return table


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


def _box_to_dict(box: Any) -> dict[str, float]:
    if hasattr(box, "tolist"):
        values = box.tolist()
    else:
        values = list(box)
    x1, y1, x2, y2 = [float(value) for value in values]
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _pad_bbox(bbox: dict[str, float], *, width: int, height: int, padding: int) -> dict[str, float]:
    return _clamp_bbox(
        {
            "x1": bbox["x1"] - padding,
            "y1": bbox["y1"] - padding,
            "x2": bbox["x2"] + padding,
            "y2": bbox["y2"] + padding,
        },
        width,
        height,
    )


def _offset_bbox(bbox: dict[str, float], x_offset: float, y_offset: float) -> dict[str, float]:
    return {
        "x1": bbox["x1"] + x_offset,
        "y1": bbox["y1"] + y_offset,
        "x2": bbox["x2"] + x_offset,
        "y2": bbox["y2"] + y_offset,
    }


def _clamp_bbox(bbox: dict[str, float], width: int, height: int) -> dict[str, float]:
    return {
        "x1": max(0.0, min(float(width), float(bbox["x1"]))),
        "y1": max(0.0, min(float(height), float(bbox["y1"]))),
        "x2": max(0.0, min(float(width), float(bbox["x2"]))),
        "y2": max(0.0, min(float(height), float(bbox["y2"]))),
    }


def _valid_bbox(bbox: dict[str, float]) -> bool:
    return bbox["x2"] > bbox["x1"] and bbox["y2"] > bbox["y1"]


def _build_cells_from_rows_columns(rows: list[dict[str, Any]], columns: list[dict[str, Any]], *, default_confidence: float) -> list[TATRCell]:
    cells: list[TATRCell] = []
    for row_index, row in enumerate(sorted(rows, key=lambda item: item["bbox"]["y1"])):
        for column_index, column in enumerate(sorted(columns, key=lambda item: item["bbox"]["x1"])):
            bbox = {
                "x1": max(row["bbox"]["x1"], column["bbox"]["x1"]),
                "y1": max(row["bbox"]["y1"], column["bbox"]["y1"]),
                "x2": min(row["bbox"]["x2"], column["bbox"]["x2"]),
                "y2": min(row["bbox"]["y2"], column["bbox"]["y2"]),
            }
            if not _valid_bbox(bbox):
                continue
            confidence = min(float(row.get("confidence", default_confidence)), float(column.get("confidence", default_confidence)))
            polygon = [(bbox["x1"], bbox["y1"]), (bbox["x2"], bbox["y1"]), (bbox["x2"], bbox["y2"]), (bbox["x1"], bbox["y2"])]
            cells.append(TATRCell(row_index=row_index, column_index=column_index, bbox=bbox, polygon=polygon, confidence=confidence))
    return cells


def _join_errors(*parts: str | None) -> str | None:
    messages = [part for part in parts if part]
    return "; ".join(messages) if messages else None
