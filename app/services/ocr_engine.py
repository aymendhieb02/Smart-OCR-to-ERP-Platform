from __future__ import annotations

import logging
import os
import time
import hashlib
import json
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from app.core.config import settings
from app.core.schemas import BoundingBox, OCRLine, OCRResult
from app.services.ocr_profiles import effective_ocr_config, ocr_configuration_hash
from app.services.preprocessing import preprocess_image, preprocess_table_region
from app.services.table_regions import OCRRegion, build_ocr_regions
from app.utils.helpers import normalize_text

logger = logging.getLogger(__name__)
PREPROCESSING_VERSION = "2026-07-final-stabilization-v1"
OCR_CACHE_SCHEMA_VERSION = 2
OCR_MODES = {"fast", "balanced", "accurate"}
_UNSUPPORTED_BBOX_SHAPES: set[str] = set()


class OCREngine:
    def __init__(self, mode: str | None = None, *, use_disk_cache: bool = True, refresh_cache: bool = False, timing_recorder=None) -> None:
        self.mode = _normalize_ocr_mode(mode or settings.ocr_mode)
        self.use_disk_cache = use_disk_cache and settings.enable_ocr_disk_cache
        self.refresh_cache = refresh_cache
        self.timing_recorder = timing_recorder
        self.last_timings: dict[str, float] = {}
        self._ocr_cache: dict[str, list[dict]] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self.memory_cache_hits = 0
        self.disk_cache_hits = 0
        self.run_cache_misses = 0
        self.total_paddle_calls = 0
        self.fallback_region_count = 0

    def run(self, images: list[np.ndarray], embedded_text: str = "") -> OCRResult:
        started = time.perf_counter()
        self._reset_run_metrics()
        lines: list[OCRLine] = []
        engine_name = "EmbeddedText"

        if embedded_text.strip():
            for index, line in enumerate(embedded_text.splitlines(), start=1):
                clean = line.strip()
                if clean:
                    lines.append(OCRLine(text=clean, confidence=1.0, page_number=index))

        if images:
            try:
                ocr_lines = self._run_paddle(images)
                if ocr_lines:
                    lines.extend(ocr_lines)
                    engine_name = "PaddleOCR"
            except Exception as exc:
                logger.warning("PaddleOCR failed: %s", exc)
                if settings.enable_tesseract_fallback:
                    fallback_lines = self._run_tesseract(images)
                    if fallback_lines:
                        lines.extend(fallback_lines)
                        engine_name = "Tesseract"

        with _timer_stage(self.timing_recorder, "ocr_postprocessing", raw_line_count=len(lines)):
            raw_text = normalize_text("\n".join(line.text for line in lines))
            lines = _dedupe_ocr_lines(lines)
            confidence_values = [line.confidence for line in lines if line.confidence is not None]
            confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else None
        self.last_timings["ocr_total"] = round(time.perf_counter() - started, 4)
        self.last_timings["ocr_engine_used"] = engine_name
        self.last_timings["ocr_mode"] = self.mode
        return OCRResult(
            raw_text=normalize_text("\n".join(line.text for line in lines)),
            lines=lines,
            confidence=confidence,
            engine=engine_name,
            page_count=max(len(images), 1),
        )

    def _run_paddle(self, images: list[np.ndarray]) -> list[OCRLine]:
        started = time.perf_counter()
        cache_info = getattr(_get_paddle_ocr, "cache_info", None)
        cache_before = cache_info() if cache_info else None
        with _timer_stage(self.timing_recorder, "ocr_engine_initialization", engine="PaddleOCR", lazy=True):
            paddle = _get_paddle_instance()
        cache_after = cache_info() if cache_info else None
        self.last_timings["paddle_initialization"] = round(time.perf_counter() - started, 4) if cache_after and cache_before and cache_after.misses > cache_before.misses else 0.0
        lines: list[OCRLine] = []
        region_timings: dict[str, float] = {}
        region_calls: dict[str, int] = {}
        duplicate_calls = 0
        for page_number, image in enumerate(images, start=1):
            for region in self._regions_for_mode(image):
                region_lines, elapsed, from_memory = self._run_paddle_region(
                    paddle,
                    region,
                    page_number,
                    source="full_page" if region.name == "full_page" else "regional_fallback",
                )
                if from_memory:
                    duplicate_calls += 1
                region_calls[region.name] = region_calls.get(region.name, 0) + 1
                region_timings[region.name] = region_timings.get(region.name, 0.0) + elapsed
                lines.extend(region_lines)
        for index, line in enumerate(lines):
            line.line_index = index
        self.last_timings["ocr_inference"] = round(time.perf_counter() - started - self.last_timings.get("paddle_initialization", 0.0), 4)
        self.last_timings["preprocessing"] = round(self.last_timings.get("preprocessing", 0.0), 4)
        self.last_timings["region_ocr_calls"] = region_calls
        self.last_timings["region_timings"] = {key: round(value, 4) for key, value in region_timings.items()}
        self.last_timings["duplicate_ocr_calls"] = duplicate_calls
        self._publish_cache_metrics()
        return lines

    def run_fallback_regions(self, images: list[np.ndarray], region_names: list[str]) -> list[OCRLine]:
        if not images or not region_names:
            return []
        started = time.perf_counter()
        paddle = _get_paddle_instance()
        requested = set(region_names)
        lines: list[OCRLine] = []
        for page_number, image in enumerate(images, start=1):
            for region in build_ocr_regions(image):
                if region.name == "full_page" or region.name not in requested:
                    continue
                region_lines, _elapsed, _from_memory = self._run_paddle_region(
                    paddle,
                    region,
                    page_number,
                    source="regional_fallback",
                )
                lines.extend(region_lines)
                self.fallback_region_count += 1
        for index, line in enumerate(lines):
            line.line_index = index
        self.last_timings["fallback_ocr_inference"] = round(self.last_timings.get("fallback_ocr_inference", 0.0) + time.perf_counter() - started, 4)
        self._publish_cache_metrics()
        return lines

    def _regions_for_mode(self, image: np.ndarray) -> list[OCRRegion]:
        regions = build_ocr_regions(image)
        if self.mode in {"fast", "balanced"}:
            full_page = [region for region in regions if region.name == "full_page"]
            return full_page or regions[:1]
        return regions

    def _run_paddle_region(self, paddle, region: OCRRegion, page_number: int, *, source: str) -> tuple[list[OCRLine], float, bool]:
        region_started = time.perf_counter()
        preprocess_started = time.perf_counter()
        with _timer_stage(self.timing_recorder, "preprocessing", region=region.name, page_number=page_number):
            processed = _preprocess_for_region(region)
        preprocess_elapsed = time.perf_counter() - preprocess_started
        preprocessing_key = "full_page_preprocessing" if region.name == "full_page" else "fallback_preprocessing"
        self.last_timings[preprocessing_key] = round(self.last_timings.get(preprocessing_key, 0.0) + preprocess_elapsed, 4)
        self.last_timings["preprocessing"] = self.last_timings.get("preprocessing", 0.0) + preprocess_elapsed

        with _timer_stage(self.timing_recorder, "ocr_cache_lookup", region=region.name, page_number=page_number):
            cache_key = _ocr_cache_key(processed, region.image, region.name, page_number, self.mode, region.coordinates)
            cached_items = self._ocr_cache.get(cache_key)
            from_memory = cached_items is not None
            if cached_items is not None and not _cache_has_usable_geometry(cached_items):
                self.last_timings["ocr_cache_source"] = "memory_rejected_zero_bbox"
                self.last_timings["disk_cache_invalidated_reason"] = "memory cache had text but zero bboxes"
                self._ocr_cache.pop(cache_key, None)
                cached_items = None
                from_memory = False
            if cached_items is not None:
                self.cache_hits += 1
                self.memory_cache_hits += 1
                self.last_timings["ocr_cache_source"] = "memory"
            else:
                cached_items = self._read_disk_cache(cache_key)
                if cached_items is not None:
                    self.cache_hits += 1
                    self.disk_cache_hits += 1
                    self._ocr_cache[cache_key] = cached_items
                    self.last_timings["ocr_cache_source"] = "disk"
        if cached_items is None:
            inference_started = time.perf_counter()
            with _timer_stage(self.timing_recorder, "ocr_execution", region=region.name, page_number=page_number, engine="PaddleOCR"):
                result = _run_paddle_prediction(paddle, processed)
            inference_elapsed = time.perf_counter() - inference_started
            inference_key = "full_page_ocr_inference" if region.name == "full_page" else "fallback_ocr_inference"
            self.last_timings[inference_key] = round(self.last_timings.get(inference_key, 0.0) + inference_elapsed, 4)
            raw_items = list(_iter_paddle_items(result, page_number=page_number))
            self._accumulate_stage_count("raw_paddle_items_count", len(raw_items))
            self._accumulate_stage_count("raw_boxes_count", _items_with_bbox(raw_items))
            cached_items = [
                _item_with_page_bbox(item, region, page_number, processed, source)
                for item in raw_items
            ]
            for index, item in enumerate(cached_items):
                item["line_index"] = index
            self._accumulate_stage_count("normalized_boxes_count", _items_with_bbox(cached_items))
            self.last_timings["geometry_status"] = "available" if _items_with_bbox(cached_items) else "unavailable_from_engine"
            self.last_timings["ocr_cache_source"] = "fresh"
            self._ocr_cache[cache_key] = cached_items
            self._write_disk_cache(cache_key, cached_items, region, processed)
            self.cache_misses += 1
            self.run_cache_misses += 1
            self.total_paddle_calls += 1

        lines = []
        for item in cached_items:
            text = normalize_text(str(item.get("text") or ""))
            if not text:
                continue
            lines.append(OCRLine(
                text=text,
                confidence=item.get("confidence"),
                page_number=item.get("page_number") or page_number,
                bbox=_bbox_from_cache(item.get("bbox")),
                line_index=len(lines),
                source=item.get("source") or source,
            ))
        self._accumulate_stage_count("ocrline_boxes_count", sum(1 for line in lines if line.bbox))
        self.last_timings["pre_api_boxes_count"] = self.last_timings.get("ocrline_boxes_count", 0)
        return lines, time.perf_counter() - region_started, from_memory

    def _accumulate_stage_count(self, key: str, value: int) -> None:
        self.last_timings[key] = int(self.last_timings.get(key, 0) or 0) + int(value)

    def _run_tesseract(self, images: list[np.ndarray]) -> list[OCRLine]:
        try:
            import pytesseract
        except ImportError:
            logger.warning("pytesseract is not installed")
            return []

        lines: list[OCRLine] = []
        for page_number, image in enumerate(images, start=1):
            for region in self._regions_for_mode(image):
                processed = _preprocess_for_region(region)
                config = _tesseract_config_for_region(region.name)
                if region.name == "full_page":
                    lines.extend(_tesseract_data_lines(pytesseract, processed, page_number, config))
                else:
                    lines.extend(_tesseract_string_lines(pytesseract, processed, page_number, config))
        return lines

    def _read_disk_cache(self, cache_key: str) -> list[dict] | None:
        if not self.use_disk_cache or self.refresh_cache:
            return None
        try:
            path = settings.ocr_cache_dir / f"{cache_key}.json"
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            schema_version = payload.get("schema_version")
            self.last_timings["disk_cache_schema_version"] = schema_version
            if schema_version != OCR_CACHE_SCHEMA_VERSION:
                self.last_timings["disk_cache_invalidated_reason"] = f"incompatible schema {schema_version}"
                return None
            items = payload.get("items")
            if not isinstance(items, list):
                self.last_timings["disk_cache_invalidated_reason"] = "items missing or not a list"
                return None
            normalized_items = [_cache_item(item) for item in items if isinstance(item, dict) and item.get("text")]
            self.last_timings["cached_lines_count"] = len(normalized_items)
            self.last_timings["cached_lines_with_bbox"] = sum(1 for item in normalized_items if item.get("bbox"))
            self.last_timings["cached_boxes_count"] = self.last_timings["cached_lines_with_bbox"]
            if normalized_items and self.last_timings["cached_lines_with_bbox"] == 0:
                self.last_timings["disk_cache_invalidated_reason"] = "cache had text but zero bboxes"
                self.last_timings["ocr_cache_source"] = "disk_rejected_zero_bbox"
                return None
            return normalized_items
        except Exception as exc:
            logger.debug("OCR disk cache read failed: %s", exc)
            return None

    def _write_disk_cache(self, cache_key: str, items: list[dict], region: OCRRegion, processed: np.ndarray) -> None:
        if not self.use_disk_cache:
            return
        try:
            with _timer_stage(self.timing_recorder, "cache_write", region=region.name):
                settings.ocr_cache_dir.mkdir(parents=True, exist_ok=True)
                payload = {
                    "schema_version": OCR_CACHE_SCHEMA_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "engine": "PaddleOCR",
                    "ocr_mode": self.mode,
                    "ocr_cache_fingerprint": _paddle_fingerprint(),
                    "preprocessing_version": PREPROCESSING_VERSION,
                    "region": region.name,
                    "region_coordinates": region.coordinates,
                    "image_shape": list(processed.shape),
                    "coordinate_mapping": _coordinate_mapping(region, processed),
                    "geometry_status": "available" if _items_with_bbox(items) else "unavailable_from_engine",
                    "items_count": len(items),
                    "items_with_bbox": _items_with_bbox(items),
                    "items": [_cache_item(item) for item in items],
                }
                (settings.ocr_cache_dir / f"{cache_key}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.debug("OCR disk cache write failed: %s", exc)

    def _reset_run_metrics(self) -> None:
        ocr_config = effective_ocr_config()
        self.last_timings = {
            "ocr_mode": self.mode,
            "ocr_profile": ocr_config["ocr_profile"],
            "detector_model": ocr_config["detector"],
            "recognizer_model": ocr_config["recognizer"],
            "cpu_threads": ocr_config["cpu_threads"],
            "input_max_side": ocr_config["input_max_side"],
            "preprocessing_profile": ocr_config["preprocessing_profile"],
            "mkldnn": ocr_config["enable_mkldnn"],
            "gpu": ocr_config["use_gpu"],
            "ocr_engine_used": None,
            "total_paddle_calls": 0,
            "fallback_region_count": 0,
            "memory_cache_hits": 0,
            "disk_cache_hits": 0,
            "cache_misses": 0,
            "disk_cache_hit": False,
            "disk_cache_schema_version": None,
            "disk_cache_invalidated_reason": None,
            "cached_lines_count": 0,
            "cached_lines_with_bbox": 0,
            "ocr_cache_source": None,
            "raw_paddle_items_count": 0,
            "raw_boxes_count": 0,
            "normalized_boxes_count": 0,
            "ocrline_boxes_count": 0,
            "pre_api_boxes_count": 0,
            "cached_boxes_count": 0,
            "public_boxes_count": 0,
            "bbox_loss_stage": None,
            "geometry_status": None,
        }
        self.memory_cache_hits = 0
        self.disk_cache_hits = 0
        self.run_cache_misses = 0
        self.total_paddle_calls = 0
        self.fallback_region_count = 0

    def _publish_cache_metrics(self) -> None:
        self.last_timings["ocr_cache_hits"] = self.cache_hits
        self.last_timings["ocr_cache_misses"] = self.cache_misses
        self.last_timings["memory_cache_hits"] = self.memory_cache_hits
        self.last_timings["disk_cache_hits"] = self.disk_cache_hits
        self.last_timings["cache_misses"] = self.run_cache_misses
        self.last_timings["disk_cache_hit"] = self.disk_cache_hits > 0
        self.last_timings["total_paddle_calls"] = self.total_paddle_calls
        self.last_timings["fallback_region_count"] = self.fallback_region_count


def _build_tesseract_line(parts: list[str], confidences: list[float], page_number: int) -> OCRLine:
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
    return OCRLine(text=" ".join(parts), confidence=confidence, page_number=page_number)


def _preprocess_for_region(region: OCRRegion) -> np.ndarray:
    ocr_config = effective_ocr_config()
    profile = ocr_config["preprocessing_profile"]
    max_side = ocr_config["input_max_side"]
    if region.name == "full_page":
        processed = preprocess_image(region.image, profile=profile, max_side=max_side)
    else:
        processed = preprocess_table_region(region.image, profile=profile, max_side=max_side)
    return _ensure_color_image(processed)


def _normalize_ocr_mode(mode: str) -> str:
    normalized = (mode or "balanced").strip().lower()
    return normalized if normalized in OCR_MODES else "balanced"


def _ocr_cache_key(
    image: np.ndarray,
    source_image: np.ndarray,
    region_name: str,
    page_number: int,
    mode: str,
    coordinates: tuple[int, int, int, int] | None,
) -> str:
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    source_digest = hashlib.sha256(source_image.tobytes()).hexdigest()
    payload = {
        "sha256": digest,
        "source_sha256": source_digest,
        "page_number": page_number,
        "ocr_mode": mode,
        "region_name": region_name,
        "coordinates": coordinates,
        "shape": list(image.shape),
        "preprocessing_version": PREPROCESSING_VERSION,
        "ocr_cache_schema_version": OCR_CACHE_SCHEMA_VERSION,
        "model": _paddle_fingerprint(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _paddle_fingerprint() -> str:
    ocr_config = effective_ocr_config()
    payload = {
        "lang": "en",
        "enable_mkldnn": ocr_config["enable_mkldnn"],
        "cpu_threads": ocr_config["cpu_threads"],
        "use_gpu": ocr_config["use_gpu"],
        "ocr_version": settings.paddle_ocr_version,
        "det_model": ocr_config["detector"],
        "rec_model": ocr_config["recognizer"],
        "det_limit_side_len": settings.paddle_text_det_limit_side_len,
        "input_max_side": ocr_config["input_max_side"],
        "rec_batch_size": settings.paddle_text_recognition_batch_size,
        "preprocessing_profile": ocr_config["preprocessing_profile"],
        "orientation": False,
        "unwarping": False,
        "textline_orientation": False,
    }
    return json.dumps(payload, sort_keys=True)


def _item_with_page_bbox(item: dict, region: OCRRegion, page_number: int, processed: np.ndarray, source: str) -> dict:
    payload = dict(item)
    payload["page_number"] = payload.get("page_number") or page_number
    payload["source"] = payload.get("source") or source
    bbox = _bbox_from_cache(payload.get("bbox"))
    if bbox:
        mapping = _coordinate_mapping(region, processed)
        bbox = _map_inference_bbox_to_page(bbox, mapping)
    payload["bbox"] = bbox.model_dump(mode="json") if bbox else None
    return payload


def _cache_item(item: dict) -> dict:
    bbox = _bbox_from_cache(item.get("bbox"))
    return {
        "text": item.get("text"),
        "confidence": item.get("confidence"),
        "page_number": item.get("page_number"),
        "bbox": bbox.model_dump(mode="json") if bbox else None,
        "source": item.get("source"),
        "line_index": item.get("line_index"),
    }


def _items_with_bbox(items: list[dict]) -> int:
    return sum(1 for item in items if isinstance(item, dict) and _bbox_from_cache(item.get("bbox")) is not None)


def _cache_has_usable_geometry(items: list[dict]) -> bool:
    if not items:
        return True
    text_items = [item for item in items if isinstance(item, dict) and item.get("text")]
    if not text_items:
        return True
    return _items_with_bbox(text_items) > 0


def _coordinate_mapping(region: OCRRegion, processed: np.ndarray) -> dict[str, float | int]:
    natural_height, natural_width = region.image.shape[:2]
    inference_height, inference_width = processed.shape[:2]
    scale_x = natural_width / max(float(inference_width), 1.0)
    scale_y = natural_height / max(float(inference_height), 1.0)
    return {
        "natural_page_width": natural_width,
        "natural_page_height": natural_height,
        "inference_width": inference_width,
        "inference_height": inference_height,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "crop_offset_x": region.x_offset,
        "crop_offset_y": region.y_offset,
    }


def _map_inference_bbox_to_page(bbox: BoundingBox, mapping: dict[str, float | int]) -> BoundingBox | None:
    natural_width = float(mapping["natural_page_width"])
    natural_height = float(mapping["natural_page_height"])
    scale_x = float(mapping["scale_x"])
    scale_y = float(mapping["scale_y"])
    offset_x = float(mapping["crop_offset_x"])
    offset_y = float(mapping["crop_offset_y"])
    x1 = _clamp(bbox.x1 * scale_x, 0.0, natural_width) + offset_x
    y1 = _clamp(bbox.y1 * scale_y, 0.0, natural_height) + offset_y
    x2 = _clamp(bbox.x2 * scale_x, 0.0, natural_width) + offset_x
    y2 = _clamp(bbox.y2 * scale_y, 0.0, natural_height) + offset_y
    if x2 <= x1 or y2 <= y1:
        return None
    return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _bbox_from_cache(value) -> BoundingBox | None:
    if value is None:
        return None
    if isinstance(value, BoundingBox):
        return value
    if isinstance(value, dict):
        try:
            return BoundingBox(**value)
        except Exception:
            return None
    return None


def _ensure_color_image(image: np.ndarray) -> np.ndarray:
    if image is None:
        return image
    if len(image.shape) == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if len(image.shape) == 3 and image.shape[2] == 1:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image


def _tesseract_config_for_region(region_name: str) -> str:
    if region_name == "full_page":
        return "--oem 3 --psm 11"
    if "table" in region_name or "totals" in region_name:
        return "--oem 3 --psm 6 -c preserve_interword_spaces=1"
    return "--oem 3 --psm 6"


def _tesseract_data_lines(pytesseract, image: np.ndarray, page_number: int, config: str) -> list[OCRLine]:
    try:
        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT,
            lang="fra+eng",
            config=config,
            timeout=20,
        )
    except RuntimeError as exc:
        logger.warning("Tesseract data OCR timed out or failed: %s", exc)
        return []
    lines: list[OCRLine] = []
    current_line: list[str] = []
    current_conf: list[float] = []
    last_key: tuple[int, int, int] | None = None
    line_boxes: list[BoundingBox] = []
    for index, text in enumerate(data.get("text", [])):
        clean = normalize_text(text)
        if not clean:
            continue
        key = (
            data["block_num"][index],
            data["par_num"][index],
            data["line_num"][index],
        )
        if last_key is not None and key != last_key and current_line:
            lines.append(_build_tesseract_line_with_bbox(current_line, current_conf, line_boxes, page_number, len(lines)))
            current_line, current_conf, line_boxes = [], [], []
        last_key = key
        current_line.append(clean)
        line_boxes.append(BoundingBox(
            x1=float(data["left"][index]),
            y1=float(data["top"][index]),
            x2=float(data["left"][index] + data["width"][index]),
            y2=float(data["top"][index] + data["height"][index]),
        ))
        try:
            conf = float(data["conf"][index])
            if conf >= 0:
                current_conf.append(conf / 100)
        except (ValueError, TypeError):
            pass
    if current_line:
        lines.append(_build_tesseract_line_with_bbox(current_line, current_conf, line_boxes, page_number, len(lines)))
    return lines


def _tesseract_string_lines(pytesseract, image: np.ndarray, page_number: int, config: str) -> list[OCRLine]:
    try:
        text = pytesseract.image_to_string(image, lang="fra+eng", config=config, timeout=12)
    except RuntimeError as exc:
        logger.warning("Tesseract region OCR timed out or failed: %s", exc)
        return []
    return [
        OCRLine(text=clean, confidence=None, page_number=page_number)
        for line in text.splitlines()
        if (clean := normalize_text(line))
    ]


def _dedupe_ocr_lines(lines: list[OCRLine]) -> list[OCRLine]:
    unique: list[OCRLine] = []
    seen: set[str] = set()
    for line in lines:
        key = normalize_text(line.text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(line)
    return unique


def _build_tesseract_line_with_bbox(
    parts: list[str],
    confidences: list[float],
    boxes: list[BoundingBox],
    page_number: int,
    line_index: int,
) -> OCRLine:
    line = _build_tesseract_line(parts, confidences, page_number)
    line.line_index = line_index
    if boxes:
        line.bbox = BoundingBox(
            x1=min(box.x1 for box in boxes),
            y1=min(box.y1 for box in boxes),
            x2=max(box.x2 for box in boxes),
            y2=max(box.y2 for box in boxes),
        )
    return line


def normalize_paddle_bbox(value) -> BoundingBox | None:
    if isinstance(value, BoundingBox):
        return _valid_bbox(value.x1, value.y1, value.x2, value.y2)
    value = _plain_paddle_value(value)
    if value is None:
        return None

    if isinstance(value, dict):
        direct = _bbox_from_xyxy_dict(value)
        if direct:
            return direct
        for key in ("box", "bbox", "points", "poly", "dt_poly", "dt_polys", "rec_box", "rec_boxes"):
            if key in value:
                bbox = normalize_paddle_bbox(value.get(key))
                if bbox:
                    return bbox
        _log_unsupported_bbox(value)
        return None

    if isinstance(value, (list, tuple)):
        if _looks_like_flat_rect(value):
            return _bbox_from_rect_values(value[:4])
        if _looks_like_polygon(value):
            return _bbox_from_polygon(value)
        if value:
            bbox = normalize_paddle_bbox(value[0])
            if bbox:
                return bbox
        _log_unsupported_bbox(value)
        return None

    _log_unsupported_bbox(value)
    return None


def _plain_paddle_value(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if hasattr(value, "keys") and hasattr(value, "__getitem__") and not isinstance(value, dict):
        try:
            return {key: value[key] for key in value.keys()}
        except Exception:
            return value
    if hasattr(value, "tolist") and not isinstance(value, (list, tuple, dict, str, bytes)):
        try:
            return value.tolist()
        except Exception:
            return value
    return value


def _bbox_from_xyxy_dict(value: dict) -> BoundingBox | None:
    key_sets = (
        ("x1", "y1", "x2", "y2"),
        ("left", "top", "right", "bottom"),
    )
    for keys in key_sets:
        if all(key in value for key in keys):
            return _bbox_from_rect_values([value[key] for key in keys])
    if all(key in value for key in ("x", "y", "width", "height")):
        try:
            x = float(value["x"])
            y = float(value["y"])
            width = float(value["width"])
            height = float(value["height"])
        except (TypeError, ValueError):
            return None
        return _valid_bbox(x, y, x + width, y + height)
    return None


def _looks_like_flat_rect(value) -> bool:
    return len(value) >= 4 and all(_is_number(item) for item in value[:4])


def _looks_like_polygon(value) -> bool:
    if not value or not isinstance(value, (list, tuple)):
        return False
    return all(isinstance(point, (list, tuple)) and len(point) >= 2 and _is_number(point[0]) and _is_number(point[1]) for point in value)


def _bbox_from_polygon(points) -> BoundingBox | None:
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (TypeError, ValueError):
        return None
    return _valid_bbox(min(xs), min(ys), max(xs), max(ys))


def _bbox_from_rect_values(values) -> BoundingBox | None:
    try:
        x1, y1, x2, y2 = [float(item) for item in values[:4]]
    except (TypeError, ValueError):
        return None
    return _valid_bbox(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _valid_bbox(x1: float, y1: float, x2: float, y2: float) -> BoundingBox | None:
    values = (x1, y1, x2, y2)
    if not all(math.isfinite(value) for value in values):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)


def _is_number(value) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and math.isfinite(float(value))


def _log_unsupported_bbox(value) -> None:
    shape = getattr(value, "shape", None)
    fingerprint = f"{type(value).__name__}:{shape}:{repr(value)[:120]}"
    if fingerprint in _UNSUPPORTED_BBOX_SHAPES:
        return
    _UNSUPPORTED_BBOX_SHAPES.add(fingerprint)
    logger.debug("Unsupported PaddleOCR bbox shape: type=%s shape=%s value=%r", type(value).__name__, shape, repr(value)[:180])


def _paddle_bbox(points) -> BoundingBox | None:
    return normalize_paddle_bbox(points)


def _iter_paddle_items(result, page_number: int | None = None):
    if result is None:
        return
    if hasattr(result, "keys") and hasattr(result, "__getitem__") and not isinstance(result, dict):
        result = {key: result[key] for key in result.keys()}
    if isinstance(result, dict):
        rec_texts = result.get("rec_texts")
        if isinstance(rec_texts, (list, tuple)) and rec_texts:
            rec_scores = result.get("rec_scores") or []
            rec_boxes = result.get("rec_boxes")
            if rec_boxes is None or len(rec_boxes) == 0:
                rec_boxes = result.get("dt_polys")
            if rec_boxes is None:
                rec_boxes = []
            for idx, text in enumerate(rec_texts):
                bbox = _extract_paddle_bbox(rec_boxes[idx]) if idx < len(rec_boxes) else None
                confidence = None
                if idx < len(rec_scores):
                    try:
                        confidence = float(rec_scores[idx])
                    except (TypeError, ValueError):
                        confidence = None
                if text:
                    yield {
                        "text": text,
                        "confidence": confidence,
                        "bbox": bbox,
                        "page_number": page_number,
                    }
            for key in ("res", "result", "results", "data", "ocr_result"):
                nested = result.get(key)
                if nested is not None:
                    for item in _iter_paddle_items(nested, page_number=page_number):
                        yield item
            return
        text = _extract_paddle_text(result)
        if text:
            yield {
                "text": text,
                "confidence": _extract_paddle_confidence(result),
                "bbox": _extract_paddle_bbox(result),
                "page_number": page_number,
            }
        for key in ("res", "result", "results", "data", "ocr_result"):
            nested = result.get(key)
            if nested is not None:
                for item in _iter_paddle_items(nested, page_number=page_number):
                    yield item
        return
    if isinstance(result, (list, tuple)):
        if _looks_like_paddle_item(result):
            text = _extract_paddle_text(result)
            if text:
                yield {
                    "text": text,
                    "confidence": _extract_paddle_confidence(result),
                    "bbox": _extract_paddle_bbox(result),
                    "page_number": page_number,
                }
            return
        for idx, item in enumerate(result):
            nested_page = page_number
            if nested_page is None and isinstance(item, (list, tuple)) and item and _looks_like_page_collection(item):
                nested_page = idx + 1
            for normalized in _iter_paddle_items(item, page_number=nested_page):
                yield normalized


def _looks_like_page_collection(value) -> bool:
    if not isinstance(value, (list, tuple)) or not value:
        return False
    return any(_looks_like_paddle_item(item) for item in value if isinstance(item, (list, tuple, dict)))


def _looks_like_paddle_item(value) -> bool:
    if isinstance(value, dict):
        return _extract_paddle_text(value) is not None
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return False
    if _paddle_bbox(value[0]) is not None and _extract_paddle_text(value) is not None:
        return True
    if len(value) >= 3 and isinstance(value[1], str):
        return True
    return False


def _extract_paddle_text(value) -> str | None:
    if isinstance(value, dict):
        for key in ("text", "transcription", "rec_text", "label", "value"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        for key in ("rec", "texts"):
            item = value.get(key)
            if isinstance(item, (list, tuple)) and item:
                first = item[0]
                if isinstance(first, str) and first.strip():
                    return first
        return None
    if isinstance(value, (list, tuple)):
        if len(value) >= 2:
            text_info = value[1]
            if isinstance(text_info, (list, tuple)) and text_info:
                first = text_info[0]
                if isinstance(first, str) and first.strip():
                    return first
            if isinstance(text_info, str) and text_info.strip():
                return text_info
        if len(value) >= 3 and isinstance(value[2], str) and value[2].strip():
            return value[2]
    return None


def _extract_paddle_confidence(value) -> float | None:
    try:
        if isinstance(value, dict):
            for key in ("score", "confidence", "rec_score"):
                item = value.get(key)
                if item is not None:
                    return float(item)
            rec = value.get("rec")
            if isinstance(rec, (list, tuple)) and len(rec) > 1:
                return float(rec[1])
        elif isinstance(value, (list, tuple)):
            if len(value) >= 2 and isinstance(value[1], (list, tuple)) and len(value[1]) > 1:
                return float(value[1][1])
            if len(value) >= 3 and isinstance(value[2], (int, float)):
                return float(value[2])
    except (TypeError, ValueError):
        return None
    return None


def _extract_paddle_bbox(value) -> BoundingBox | None:
    return normalize_paddle_bbox(value)


@lru_cache(maxsize=4)
def _get_paddle_ocr(_config_hash: str | None = None):
    ocr_config = effective_ocr_config()
    os.environ["FLAGS_use_mkldnn"] = "1" if ocr_config["enable_mkldnn"] else "0"
    if ocr_config["cpu_threads"]:
        os.environ["CPU_NUM"] = str(ocr_config["cpu_threads"])
        os.environ["OMP_NUM_THREADS"] = str(ocr_config["cpu_threads"])
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import PaddleOCR

    try:
        kwargs = {
            "lang": "en",
            "enable_mkldnn": ocr_config["enable_mkldnn"],
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        }
        optional = {
            "cpu_threads": ocr_config["cpu_threads"],
            "ocr_version": settings.paddle_ocr_version,
            "text_detection_model_name": ocr_config["detector"],
            "text_recognition_model_name": ocr_config["recognizer"],
            "text_det_limit_side_len": settings.paddle_text_det_limit_side_len,
            "text_recognition_batch_size": settings.paddle_text_recognition_batch_size,
        }
        kwargs.update({key: value for key, value in optional.items() if value is not None})
        if ocr_config["use_gpu"]:
            kwargs["use_gpu"] = True
        return PaddleOCR(**kwargs)
    except ValueError as exc:
        if "Unknown argument" not in str(exc):
            raise
        return PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )


def _get_paddle_instance():
    try:
        return _get_paddle_ocr(ocr_configuration_hash())
    except TypeError as exc:
        if "positional" not in str(exc) and "argument" not in str(exc):
            raise
        return _get_paddle_ocr()


def _run_paddle_prediction(paddle, processed):
    if hasattr(paddle, "predict"):
        return list(paddle.predict(processed))
    try:
        return paddle.ocr(processed, cls=True)
    except TypeError as exc:
        if "unexpected keyword argument 'cls'" not in str(exc):
            raise
        return paddle.ocr(processed)


def _timer_stage(timing_recorder, name: str, **metadata):
    if timing_recorder is None:
        return _noop_stage()
    return timing_recorder.stage(name, **metadata)


class _noop_stage:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False
