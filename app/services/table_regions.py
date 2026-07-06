from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class OCRRegion:
    name: str
    image: np.ndarray


def build_ocr_regions(image: np.ndarray) -> list[OCRRegion]:
    """Return high-value invoice zones for a second OCR pass."""
    h, w = image.shape[:2]
    regions = [
        OCRRegion("full_page", image),
        OCRRegion("line_items_table_area", _crop(image, 0.03, 0.36, 0.97, 0.62)),
        OCRRegion("totals_bottom_right", _crop(image, 0.52, 0.58, 0.97, 0.78)),
        OCRRegion("totals_and_payment_area", _crop(image, 0.45, 0.55, 0.98, 0.82)),
    ]
    return _dedupe_regions(regions)


def _crop(image: np.ndarray, x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
    h, w = image.shape[:2]
    left = max(0, min(w - 1, int(w * x1)))
    top = max(0, min(h - 1, int(h * y1)))
    right = max(left + 1, min(w, int(w * x2)))
    bottom = max(top + 1, min(h, int(h * y2)))
    return image[top:bottom, left:right]


def _detect_rectangular_regions(image: np.ndarray, height: int, width: int) -> list[OCRRegion]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    edges = cv2.Canny(gray, 60, 180)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[tuple[int, int, int, int]] = []
    page_area = height * width
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < page_area * 0.015 or area > page_area * 0.45:
            continue
        if w < width * 0.20 or h < height * 0.04:
            continue
        if y < height * 0.25:
            continue
        candidates.append((x, y, w, h))

    candidates = sorted(candidates, key=lambda box: box[2] * box[3], reverse=True)[:3]
    regions: list[OCRRegion] = []
    for idx, (x, y, w, h) in enumerate(candidates, start=1):
        pad_x = int(w * 0.03)
        pad_y = int(h * 0.08)
        left = max(0, x - pad_x)
        top = max(0, y - pad_y)
        right = min(width, x + w + pad_x)
        bottom = min(height, y + h + pad_y)
        regions.append(OCRRegion(f"detected_table_{idx}", image[top:bottom, left:right]))
    return regions


def _dedupe_regions(regions: list[OCRRegion]) -> list[OCRRegion]:
    seen: set[tuple[int, int]] = set()
    unique: list[OCRRegion] = []
    for region in regions:
        h, w = region.image.shape[:2]
        key = (round(w / 20), round(h / 20))
        if key in seen or h < 30 or w < 80:
            continue
        seen.add(key)
        unique.append(region)
    return unique
