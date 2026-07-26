from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.config import settings
from app.services.layout_model.layout_model_loader import LayoutModelLoader


@dataclass
class LayoutRegion:
    page: int
    label: str
    bbox: dict[str, float]
    confidence: float
    source: str = "doclayout_yolo"

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "label": self.label,
            "bbox": self.bbox,
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass
class LayoutDetectionResult:
    available: bool
    regions: list[LayoutRegion]
    latency_seconds: float
    model_loaded: bool
    model_load_error: str | None = None
    source: str = "doclayout_yolo"
    detection_count_uncapped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "latency_seconds": self.latency_seconds,
            "model_loaded": self.model_loaded,
            "model_load_error": self.model_load_error,
            "source": self.source,
            "detection_count_uncapped": self.detection_count_uncapped,
            "regions": [region.to_dict() for region in self.regions],
        }


class LayoutModelDetector:
    def __init__(self, loader: LayoutModelLoader | None = None) -> None:
        self.loader = loader or LayoutModelLoader()

    def detect(self, image: np.ndarray, *, page: int = 1, allow_unavailable: bool = True) -> LayoutDetectionResult:
        started = time.perf_counter()
        load = self.loader.load(download=False)
        if not load.available or load.model is None:
            return LayoutDetectionResult(False, [], round(time.perf_counter() - started, 4), False, load.error)
        try:
            regions = self._detect_with_model(image, page=page, load_result=load)
            capped = regions[: int(settings.layout_model_max_regions or 40)]
            return LayoutDetectionResult(
                True,
                capped,
                round(time.perf_counter() - started, 4),
                True,
                detection_count_uncapped=len(regions),
            )
        except Exception as exc:
            if allow_unavailable:
                return LayoutDetectionResult(False, [], round(time.perf_counter() - started, 4), True, str(exc))
            raise

    def _detect_with_model(self, image: np.ndarray, *, page: int, load_result: Any) -> list[LayoutRegion]:
        rgb = image[:, :, ::-1] if image.ndim == 3 else image
        results = load_result.model.predict(
            rgb,
            imgsz=1024,
            conf=float(settings.layout_model_confidence or 0.5),
            device=load_result.device,
            verbose=False,
        )
        if not results:
            return []
        first = results[0]
        names = getattr(first, "names", {}) or getattr(load_result.model, "names", {}) or {}
        boxes = getattr(first, "boxes", None)
        if boxes is None:
            return []
        height, width = image.shape[:2]
        regions: list[LayoutRegion] = []
        xyxy = getattr(boxes, "xyxy", [])
        confs = getattr(boxes, "conf", [])
        classes = getattr(boxes, "cls", [])
        for box, confidence, cls in zip(xyxy, confs, classes):
            label = names.get(int(_scalar(cls)), str(int(_scalar(cls))))
            bbox = _clamp_bbox(_box_to_dict(box), width, height)
            if _valid_bbox(bbox):
                regions.append(LayoutRegion(page=page, label=str(label), bbox=bbox, confidence=float(_scalar(confidence))))
        return sorted(regions, key=lambda region: region.confidence, reverse=True)


def _scalar(value: Any) -> float:
    if hasattr(value, "item"):
        return float(value.item())
    return float(value)


def _box_to_dict(box: Any) -> dict[str, float]:
    if hasattr(box, "detach"):
        box = box.detach().cpu().tolist()
    elif hasattr(box, "tolist"):
        box = box.tolist()
    return {"x1": float(box[0]), "y1": float(box[1]), "x2": float(box[2]), "y2": float(box[3])}


def _clamp_bbox(bbox: dict[str, float], width: int, height: int) -> dict[str, float]:
    return {
        "x1": max(0.0, min(float(width), bbox["x1"])),
        "y1": max(0.0, min(float(height), bbox["y1"])),
        "x2": max(0.0, min(float(width), bbox["x2"])),
        "y2": max(0.0, min(float(height), bbox["y2"])),
    }


def _valid_bbox(bbox: dict[str, float]) -> bool:
    return bbox["x2"] > bbox["x1"] and bbox["y2"] > bbox["y1"]
