"""Generic, OCR-geometry-based table reader for supplier invoices."""
from __future__ import annotations

import re

from app.core.schemas import LineItem
from app.utils.helpers import parse_amount, strip_accents


HEADER_PATTERNS = {
    "description": (r"description", r"designation", r"d[eé]signation", r"goods description"),
    "quantity": (r"quantity", r"quant[l1i]te", r"quantit[eé]", r"\bqty\b", r"\bqte\b"),
    "unit_price": (r"unit\s*price", r"pr[i1l]x\s*unitaire", r"pr[i1l]x\s*unital(?:re|ro)"),
    "line_total": (r"line\s*total", r"total\s*price", r"montant", r"amount", r"\btotal\b"),
    "unit": (r"^unit$", r"^unit[eé]$"),
}
STOP_PATTERNS = (
    r"^total$", r"grand\s*total", r"total\s*(?:ht|ttc)", r"total\s*including",
    r"amount\s*due", r"total\s*due", r"hs\s*code", r"position\s*tarifaire",
    r"incoterm", r"origin", r"destination", r"payment", r"paiement", r"bank\s*details",
)


def extract_producer_table_items(lines: list) -> list[LineItem]:
    positioned = [line for line in lines if getattr(line, "bbox", None) and str(getattr(line, "text", "")).strip()]
    header = _find_header(positioned)
    if not header:
        return []
    page, header_y, header_bottom, columns, unit_from_header = header
    page_lines = [line for line in positioned if getattr(line, "page_number", 1) == page]
    stop_y = _table_stop_y(page_lines, header_bottom)
    height = _median_height(page_lines)
    quantity_x = columns["quantity"]
    quantity_bounds = _column_bounds(columns, "quantity")
    quantity_rows = []
    for line in page_lines:
        if _center_y(line) <= header_bottom or line.bbox.y1 >= stop_y:
            continue
        center_x = _center_x(line)
        if not quantity_bounds[0] <= center_x <= quantity_bounds[1]:
            continue
        value = _number(line.text)
        if value is None or value <= 0:
            continue
        # Skip headers, tax percentages and long identifiers; quantities are
        # accepted only inside the geometry of the quantity column.
        if "%" in line.text or len(re.sub(r"\D", "", line.text)) > 7:
            continue
        quantity_rows.append((line, value))
    quantity_rows.sort(key=lambda pair: pair[0].bbox.y1)
    if not quantity_rows:
        return []

    items = []
    for index, (quantity_line, quantity) in enumerate(quantity_rows):
        row_center = _center_y(quantity_line)
        next_center = _center_y(quantity_rows[index + 1][0]) if index + 1 < len(quantity_rows) else stop_y
        row_end = min(next_center - height * 0.45, stop_y)
        desc_bounds = (columns.get("description_left", float("-inf")), _column_bounds(columns, "description")[1])
        desc_blocks = [
            line for line in page_lines
            if line is not quantity_line and header_bottom < _center_y(line) < row_end
            and desc_bounds[0] <= _center_x(line) <= desc_bounds[1]
            and not _is_numeric(line.text) and not _is_stop(line.text)
            and not _looks_like_header(line.text)
        ]
        description = " ".join(line.text.strip(" :|-") for line in sorted(desc_blocks, key=lambda item: (item.bbox.y1, item.bbox.x1)))
        description = re.sub(r"\s+", " ", description).strip()
        if len(description) < 3 or sum(char.isalpha() for char in description) < 3:
            continue

        unit = _nearest_text(page_lines, columns.get("unit"), row_center, header_bottom, row_end)
        if not unit:
            unit = unit_from_header
        unit = _infer_metric_unit_from_packaging(description, quantity, unit)
        unit_price = _nearest_number(page_lines, columns, "unit_price", row_center, header_bottom, row_end)
        line_total = _nearest_number(page_lines, columns, "line_total", row_center, header_bottom, row_end)
        selected = [quantity_line, *desc_blocks]
        selected.extend(_matching_numeric_lines(page_lines, columns, row_center, header_bottom, row_end))
        boxes = [line.bbox for line in selected if line.bbox]
        bbox = {
            "x1": min(box.x1 for box in boxes), "y1": min(box.y1 for box in boxes),
            "x2": max(box.x2 for box in boxes), "y2": max(box.y2 for box in boxes),
        } if boxes else None
        confidences = [getattr(line, "confidence", None) for line in selected if getattr(line, "confidence", None) is not None]
        confidence = sum(confidences) / len(confidences) if confidences else 0.68
        items.append(LineItem(
            description=description,
            quantity=quantity,
            unit=unit.strip() if unit else None,
            unit_price=unit_price,
            total=line_total,
            confidence=round(confidence, 3),
            bbox=bbox,
            page=page,
            source="generic semantic table reconstruction",
        ))
    return items


def _find_header(lines: list):
    by_page = sorted(lines, key=lambda line: (getattr(line, "page_number", 1), line.bbox.y1, line.bbox.x1))
    for anchor in by_page:
        if "description" not in _header_types(anchor.text):
            continue
        height = _median_height([line for line in by_page if getattr(line, "page_number", 1) == getattr(anchor, "page_number", 1)])
        cluster = [line for line in by_page if getattr(line, "page_number", 1) == getattr(anchor, "page_number", 1)
                   and abs(_center_y(line) - _center_y(anchor)) <= max(18.0, height * 0.8)]
        columns: dict[str, float] = {}
        unit_from_header = None
        for line in cluster:
            for kind in _header_types(line.text):
                columns.setdefault(kind, _center_x(line))
            normalized = strip_accents(line.text).lower()
            if "quantity" in normalized or "quantite" in normalized or "quantlte" in normalized or "qte" in normalized:
                match = re.search(r"\b(kg|to|ton|mt|pcs?|unit[eé])\b", normalized)
                if match:
                    unit_from_header = match.group(1).upper()
        if all(key in columns for key in ("description", "quantity", "unit_price", "line_total")):
            header_center = sum(_center_y(line) for line in cluster) / len(cluster)
            description_line = next((line for line in cluster if "description" in _header_types(line.text)), anchor)
            columns["description_left"] = description_line.bbox.x1 - 60
            return (getattr(anchor, "page_number", 1), header_center,
                    header_center, columns, unit_from_header)
    return None


def _header_types(text: str) -> set[str]:
    plain = strip_accents(text).lower()
    found = set()
    for kind, aliases in HEADER_PATTERNS.items():
        if any(re.search(alias, plain, re.I) for alias in aliases):
            found.add(kind)
    return found


def _table_stop_y(lines: list, header_bottom: float) -> float:
    stops = [line.bbox.y1 for line in lines if line.bbox.y1 > header_bottom and _is_stop(line.text)]
    return min(stops) if stops else max(line.bbox.y2 for line in lines) + 1


def _is_stop(text: str) -> bool:
    plain = strip_accents(text).lower().strip(" :.-")
    return any(re.search(pattern, plain, re.I) for pattern in STOP_PATTERNS)


def _looks_like_header(text: str) -> bool:
    return bool(_header_types(text))


def _column_bounds(columns: dict[str, float], kind: str) -> tuple[float, float]:
    ordered = sorted(((name, x) for name, x in columns.items() if name in {"description", "quantity", "unit", "unit_price", "line_total"}), key=lambda item: item[1])
    index = next(i for i, (name, _x) in enumerate(ordered) if name == kind)
    left = (ordered[index - 1][1] + ordered[index][1]) / 2 if index else float("-inf")
    right = (ordered[index][1] + ordered[index + 1][1]) / 2 if index + 1 < len(ordered) else float("inf")
    return left, right


def _nearest_number(lines: list, columns: dict[str, float], kind: str, row_center: float, top: float, bottom: float) -> float | None:
    x = columns[kind]
    left, right = _column_bounds(columns, kind)
    values = []
    for line in lines:
        if _center_y(line) <= top or line.bbox.y1 >= bottom or "%" in line.text:
            continue
        if not left <= _center_x(line) <= right:
            continue
        value = _number(line.text)
        if value is None:
            continue
        values.append((abs(_center_x(line) - x) + abs(_center_y(line) - row_center) * 0.08, value))
    return min(values, key=lambda item: item[0])[1] if values else None


def _nearest_text(lines: list, x: float | None, row_center: float, top: float, bottom: float) -> str | None:
    if x is None:
        return None
    candidates = [line for line in lines if _center_y(line) > top and line.bbox.y1 < bottom
                  and abs(_center_x(line) - x) < 80 and not _is_numeric(line.text)]
    if not candidates:
        return None
    return min(candidates, key=lambda line: abs(_center_y(line) - row_center)).text


def _matching_numeric_lines(lines: list, columns: dict[str, float], row_center: float, top: float, bottom: float) -> list:
    numeric_x = [columns[key] for key in ("quantity", "unit_price", "line_total") if key in columns]
    return [line for line in lines if _center_y(line) > top and line.bbox.y1 < bottom
            and _is_numeric(line.text) and min((abs(_center_x(line) - x) for x in numeric_x), default=999) < 110]


def _number(text: str) -> float | None:
    clean = str(text or "").strip()
    if not clean or not re.fullmatch(r"[+-]?[\d\s.,]+", clean):
        return None
    return parse_amount(clean)


def _infer_metric_unit_from_packaging(description: str, quantity: float, observed_unit: str | None) -> str | None:
    """Resolve a damaged unit token only when same-row package mass confirms MT."""
    if observed_unit and observed_unit.strip().upper() not in {"N", "?", ""}:
        return observed_unit
    match = re.search(
        r"(\d[\d\s.,]*)\s*(?:bags?|sacs?)\s*/\s*(\d+(?:[,.]\d+)?)\s*kgs?\b",
        strip_accents(description), re.I,
    )
    if not match:
        return observed_unit
    packages = parse_amount(match.group(1))
    kg_per_package = parse_amount(match.group(2))
    if packages is None or kg_per_package is None:
        return observed_unit
    metric_tonnes = packages * kg_per_package / 1000
    if abs(metric_tonnes - quantity) <= max(0.001, abs(quantity) * 0.002):
        return "MT"
    return observed_unit


def _is_numeric(text: str) -> bool:
    return _number(text) is not None


def _median_height(lines: list) -> float:
    heights = sorted(line.bbox.y2 - line.bbox.y1 for line in lines if getattr(line, "bbox", None))
    return heights[len(heights) // 2] if heights else 18.0


def _center_x(line) -> float:
    return (line.bbox.x1 + line.bbox.x2) / 2


def _center_y(line) -> float:
    return (line.bbox.y1 + line.bbox.y2) / 2
