from __future__ import annotations

import logging
from functools import lru_cache

import cv2
import numpy as np

from app.core.config import settings
from app.core.schemas import BoundingBox, OCRLine, OCRResult
from app.services.preprocessing import preprocess_image, preprocess_table_region
from app.services.table_regions import OCRRegion, build_ocr_regions
from app.utils.helpers import normalize_text

logger = logging.getLogger(__name__)


class OCREngine:
    def run(self, images: list[np.ndarray], embedded_text: str = "") -> OCRResult:
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

        raw_text = normalize_text("\n".join(line.text for line in lines))
        lines = _dedupe_ocr_lines(lines)
        confidence_values = [line.confidence for line in lines if line.confidence is not None]
        confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else None
        return OCRResult(
            raw_text=normalize_text("\n".join(line.text for line in lines)),
            lines=lines,
            confidence=confidence,
            engine=engine_name,
            page_count=max(len(images), 1),
        )

    def _run_paddle(self, images: list[np.ndarray]) -> list[OCRLine]:
        paddle = _get_paddle_ocr()
        lines: list[OCRLine] = []
        for page_number, image in enumerate(images, start=1):
            for region in build_ocr_regions(image):
                processed = _preprocess_for_region(region)
                try:
                    result = paddle.ocr(processed, cls=True)
                except TypeError as exc:
                    if "unexpected keyword argument 'cls'" not in str(exc):
                        raise
                    result = paddle.ocr(processed)
                for page_result in result or []:
                    for item in page_result or []:
                        if len(item) < 2:
                            continue
                        text_info = item[1]
                        if not text_info:
                            continue
                        text = normalize_text(str(text_info[0]))
                        confidence = float(text_info[1]) if len(text_info) > 1 else None
                        bbox = _paddle_bbox(item[0]) if item and item[0] else None
                        if text:
                            lines.append(OCRLine(text=text, confidence=confidence, page_number=page_number, bbox=bbox, line_index=len(lines)))
        return lines

    def _run_tesseract(self, images: list[np.ndarray]) -> list[OCRLine]:
        try:
            import pytesseract
        except ImportError:
            logger.warning("pytesseract is not installed")
            return []

        lines: list[OCRLine] = []
        for page_number, image in enumerate(images, start=1):
            for region in build_ocr_regions(image):
                processed = _preprocess_for_region(region)
                config = _tesseract_config_for_region(region.name)
                if region.name == "full_page":
                    lines.extend(_tesseract_data_lines(pytesseract, processed, page_number, config))
                else:
                    lines.extend(_tesseract_string_lines(pytesseract, processed, page_number, config))
        return lines


def _build_tesseract_line(parts: list[str], confidences: list[float], page_number: int) -> OCRLine:
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
    return OCRLine(text=" ".join(parts), confidence=confidence, page_number=page_number)


def _preprocess_for_region(region: OCRRegion) -> np.ndarray:
    if region.name == "full_page":
        return preprocess_image(region.image)
    return preprocess_table_region(region.image)


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


def _paddle_bbox(points) -> BoundingBox | None:
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return BoundingBox(x1=min(xs), y1=min(ys), x2=max(xs), y2=max(ys))
    except Exception:
        return None


@lru_cache(maxsize=1)
def _get_paddle_ocr():
    from paddleocr import PaddleOCR

    try:
        return PaddleOCR(use_angle_cls=True, lang="fr", show_log=False)
    except ValueError as exc:
        if "Unknown argument" not in str(exc):
            raise
        return PaddleOCR(use_angle_cls=True, lang="fr")
