from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class OCRRegion:
    name: str
    image: np.ndarray
    x_offset: int = 0
    y_offset: int = 0
    coordinates: tuple[int, int, int, int] | None = None


def build_ocr_regions(image: np.ndarray) -> list[OCRRegion]:
    """Return high-value invoice zones for a second OCR pass."""
    h, w = image.shape[:2]
    regions = [
        OCRRegion("full_page", image, 0, 0, (0, 0, w, h)),
        _region(image, "header_parties", 0.00, 0.00, 1.00, 0.42),
        _region(image, "line_items_table_area", 0.03, 0.36, 0.97, 0.62),
        _region(image, "totals_bottom_right", 0.52, 0.58, 0.97, 0.78),
        _region(image, "totals_and_payment_area", 0.45, 0.55, 0.98, 0.82),
    ]
    return _dedupe_regions(regions)


def build_label_value_region(
    image: np.ndarray,
    label_bbox,
    *,
    name: str,
    page_width: int | None = None,
    page_height: int | None = None,
) -> OCRRegion | None:
    """Build a narrow right-hand value-cell crop anchored to an OCR label."""
    height, width = image.shape[:2]
    if height < 1 or width < 1 or label_bbox is None:
        return None
    scale_x = width / float(page_width) if page_width else 1.0
    scale_y = height / float(page_height) if page_height else 1.0
    label_x2 = float(label_bbox.x2) * scale_x
    label_y1 = float(label_bbox.y1) * scale_y
    label_y2 = float(label_bbox.y2) * scale_y
    label_height = max(1.0, label_y2 - label_y1)

    # Value cells for this label are to the right on the same row. Keep the
    # crop bounded by the page edge and a small vertical band around the label.
    left = max(0, min(width - 1, int(round(label_x2 + max(8.0, width * 0.008)))))
    right = max(left + 1, min(width, int(round(width * 0.985))))
    # Extra vertical margin keeps descenders/decimal punctuation inside the
    # crop while remaining tied to the label's own OCR geometry.
    half_band = max(label_height * 2.5, height * 0.018)
    center_y = (label_y1 + label_y2) / 2
    top = max(0, int(round(center_y - half_band)))
    bottom = min(height, int(round(center_y + half_band)))
    if right - left < width * 0.08 or bottom <= top:
        return None
    return OCRRegion(name, image[top:bottom, left:right], left, top, (left, top, right, bottom))


def build_ruspina_address_region(image: np.ndarray, label_bbox, field_bbox, *, page_width: int, page_height: int) -> OCRRegion | None:
    """Crop the detected RUSPINA address row, retaining its label as context."""
    height, width = image.shape[:2]
    if min(height, width, page_width, page_height) < 1 or label_bbox is None or field_bbox is None:
        return None
    scale_x = width / float(page_width)
    scale_y = height / float(page_height)
    label = (
        float(label_bbox.x1) * scale_x, float(label_bbox.y1) * scale_y,
        float(label_bbox.x2) * scale_x, float(label_bbox.y2) * scale_y,
    )
    field = (
        float(field_bbox.x1) * scale_x, float(field_bbox.y1) * scale_y,
        float(field_bbox.x2) * scale_x, float(field_bbox.y2) * scale_y,
    )
    label_center_y = (label[1] + label[3]) / 2
    field_center_y = (field[1] + field[3]) / 2
    if abs(label_center_y - field_center_y) > height * 0.035:
        return None

    row_top = min(label[1], field[1])
    row_bottom = max(label[3], field[3])
    row_height = max(1.0, row_bottom - row_top)
    vertical_padding = max(row_height * 1.2, height * 0.012)
    top = max(0, int(round(row_top - vertical_padding)))
    bottom = min(height, int(round(row_bottom + vertical_padding)))
    max_band = max(int(round(height * 0.075)), int(round(row_height * 2.5)))
    if bottom - top > max_band:
        center_y = (row_top + row_bottom) / 2
        top = max(0, int(round(center_y - max_band / 2)))
        bottom = min(height, top + max_band)

    left = max(0, int(round(label[0] - width * 0.012)))
    right = min(width, int(round(width * 0.985)))
    if right - left < width * 0.12 or bottom <= top:
        return None
    return OCRRegion("ruspina_address", image[top:bottom, left:right], left, top, (left, top, right, bottom))


def build_tradenet_ocr_regions(image: np.ndarray) -> list[OCRRegion]:
    """Small, normalized crops for the recurring TradeNet declaration form."""
    return [
        _region(image, "tradenet_declaration_header", 0.48, 0.00, 0.98, 0.165),
        _region(image, "tradenet_parties", 0.16, 0.00, 0.64, 0.19),
        _region(image, "tradenet_financial", 0.55, 0.19, 0.98, 0.335),
        _region(image, "tradenet_customs_total", 0.76, 0.235, 0.98, 0.315),
    ]


def build_ruspina_ocr_regions(image: np.ndarray) -> list[OCRRegion]:
    """Small direct-tone crop for the quantity unit in RUSPINA table headers."""
    return [_region(image, "ruspina_table_header_unit", 0.48, 0.22, 0.67, 0.30)]


def _region(image: np.ndarray, name: str, x1: float, y1: float, x2: float, y2: float) -> OCRRegion:
    h, w = image.shape[:2]
    left = max(0, min(w - 1, int(w * x1)))
    top = max(0, min(h - 1, int(h * y1)))
    right = max(left + 1, min(w, int(w * x2)))
    bottom = max(top + 1, min(h, int(h * y2)))
    return OCRRegion(name, image[top:bottom, left:right], left, top, (left, top, right, bottom))


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
