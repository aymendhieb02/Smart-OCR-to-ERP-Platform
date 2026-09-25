from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
import unicodedata
from typing import Callable

from app.core.schemas import FieldExtractionDetail, OCRLine


_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})(?!\d)")
_NUMBER_RE = re.compile(r"(?<!\d)(\d[\d\s.-]{2,15}\d)(?!\d)")
_TYPE_RE = re.compile(r"\b([A-Za-z]{1,3})\b")
_COUNT_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_TYPE_LABEL_RE = re.compile(r"\btype\s*(?:de\s*)?declar(?:ation|ation)\b")
_ARTICLE_LABEL_RE = re.compile(r"\b(?:nbr(?:e)?|nb|nombre)\s*total\s*articles?\b")


def extract_tradenet_fields(
    lines: list[OCRLine],
    *,
    page_dimensions: dict[int, tuple[int, int]] | None = None,
) -> dict[str, FieldExtractionDetail]:
    """Extract fixed TradeNet declaration-header fields from positioned OCR lines."""
    grouped: dict[int, list[OCRLine]] = defaultdict(list)
    for line in lines:
        if line.bbox and line.text.strip():
            grouped[line.page_number].append(line)

    by_page: dict[int, dict[str, FieldExtractionDetail]] = {}
    for page, page_lines in grouped.items():
        dims = (page_dimensions or {}).get(page)
        if not dims:
            dims = _infer_page_dimensions(page_lines)
        result = _extract_page_fields(page_lines, dims)
        if result:
            by_page[page] = result

    output: dict[str, FieldExtractionDetail] = {}
    source_fields = ("declaration_number", "declaration_date", "declaration_type", "declaration_article_count")
    for name in source_fields:
        candidates = [fields[name] for fields in by_page.values() if name in fields]
        if candidates:
            output[name] = max(candidates, key=lambda detail: detail.confidence or 0.0)
        else:
            output[name] = FieldExtractionDetail(value=None, source="TradeNet label or associated value not detected")

    type_detail = output.get("declaration_type")
    count_detail = output.get("declaration_article_count")
    code = None
    confidence = None
    page = None
    if type_detail and count_detail and type_detail.value not in (None, "") and count_detail.value is not None:
        code = f"{type_detail.value}{count_detail.value}"
        confidence = min(type_detail.confidence or 0.0, count_detail.confidence or 0.0)
        page = type_detail.page if type_detail.page == count_detail.page else None
    output["declaration_code"] = FieldExtractionDetail(
        value=code,
        normalized_value=code,
        confidence=confidence,
        page=page,
        source="derived",
    )
    return output


def _extract_page_fields(lines: list[OCRLine], dimensions: tuple[int, int]) -> dict[str, FieldExtractionDetail]:
    width, height = dimensions
    normalized = [(line, _normalize(line.text)) for line in lines]
    declarations = [
        line for line, text in normalized
        if _is_declaration_heading(text, line, height)
    ]
    dae_headings = [
        line for line, text in normalized
        if _is_dae_heading(text, line, height)
    ]
    label_groups: dict[str, list[OCRLine]] = {"number": [], "date": [], "type": [], "count": []}
    for line, text in normalized:
        if _is_number_label(text):
            if _belongs_to_declaration_section(line, declarations, dae_headings, width, height):
                label_groups["number"].append(line)
        elif _is_date_label(text):
            if _belongs_to_declaration_section(line, declarations, dae_headings, width, height):
                label_groups["date"].append(line)
        elif _TYPE_LABEL_RE.search(text):
            label_groups["type"].append(line)
        elif _ARTICLE_LABEL_RE.search(text):
            label_groups["count"].append(line)

    fields: dict[str, FieldExtractionDetail] = {}
    definitions: tuple[tuple[str, str, Callable[[str], tuple[object, object] | None]], ...] = (
        ("declaration_number", "number", _parse_number),
        ("declaration_date", "date", _parse_date),
        ("declaration_type", "type", _parse_type),
        ("declaration_article_count", "count", _parse_count),
    )
    for field_name, label_kind, parser in definitions:
        found = _best_label_value(lines, label_groups[label_kind], parser, width, height)
        if found:
            fields[field_name] = found
    _add_normalized_header_cell_fallback(fields, lines, width, height)
    return fields


def _add_normalized_header_cell_fallback(fields: dict[str, FieldExtractionDetail], lines: list[OCRLine], width: int, height: int) -> None:
    """Use fixed TradeNet cell positions only when OCR lost that cell's label."""
    if "declaration_number" not in fields or "declaration_date" not in fields:
        number_candidates: list[tuple[float, OCRLine, str]] = []
        date_candidates: list[tuple[float, OCRLine, str]] = []
        for line in lines:
            if not line.bbox:
                continue
            x, y = _normalized_center(line, width, height)
            if not 0.035 <= y <= 0.12:
                continue
            number = _parse_number(line.text)
            if number and 0.50 <= x <= 0.70:
                number_candidates.append((x, line, str(number[0])))
            parsed_date = _parse_date(line.text)
            if parsed_date and 0.61 <= x <= 0.81:
                date_candidates.append((x, line, str(parsed_date[0])))
        pairs: list[tuple[float, OCRLine, str, OCRLine, str, str]] = []
        for date_x, date_line, date_value in date_candidates:
            for number_x, number_line, number_value in number_candidates:
                number_y = _normalized_center_y(number_line, height)
                date_y = _normalized_center_y(date_line, height)
                gap = date_x - number_x
                if 0.04 <= gap <= 0.18 and abs(number_y - date_y) <= 0.025:
                    pairs.append((gap, number_line, number_value, date_line, date_value, date_value))
        if pairs:
            _gap, number_line, number_value, date_line, date_value, normalized_date = min(pairs, key=lambda item: item[0])
            if "declaration_number" not in fields:
                fields["declaration_number"] = _geometry_detail(number_value, number_value, number_line, width, height)
            if "declaration_date" not in fields:
                fields["declaration_date"] = _geometry_detail(date_value, normalized_date, date_line, width, height)

def _geometry_detail(value: object, normalized_value: object, candidate: OCRLine, width: int, height: int) -> FieldExtractionDetail:
    detail = _detail(value, normalized_value, candidate, candidate, width, height)
    detail.confidence = round((detail.confidence or 0.0) * 0.82, 3)
    detail.source = "TradeNet normalized header cell OCR (label not recognized)"
    return detail


def _best_label_value(
    lines: list[OCRLine],
    labels: list[OCRLine],
    parser: Callable[[str], tuple[object, object] | None],
    width: int,
    height: int,
) -> FieldExtractionDetail | None:
    best: tuple[float, FieldExtractionDetail] | None = None
    for label in labels:
        parsed_inline = parser(label.text)
        if parsed_inline:
            value, normalized_value = parsed_inline
            detail = _detail(value, normalized_value, label, label, width, height)
            score = _candidate_score(label, label, 0.0, 0.0)
            if best is None or score > best[0]:
                best = score, detail
            continue
        for candidate in lines:
            if candidate is label or not candidate.bbox:
                continue
            parsed = parser(candidate.text)
            if not parsed:
                continue
            dx, dy = _relative_distance(label, candidate, width, height)
            horizontal_limit = max(0.075, _normalized_width(label, width) * 1.5)
            # A value may follow the label on the same row, or sit beneath it
            # in the same table cell. Relative distances tolerate DPI changes.
            same_row = dy <= 0.018 and candidate.bbox.x1 >= label.bbox.x1 - width * 0.01 and dx <= 0.22
            below_cell = 0.0 <= dy <= 0.065 and dx <= horizontal_limit
            if not (same_row or below_cell):
                continue
            if _normalized_center_y(candidate, height) > 0.34:
                continue
            value, normalized_value = parsed
            score = _candidate_score(label, candidate, dx, dy)
            detail = _detail(value, normalized_value, candidate, label, width, height)
            if best is None or score > best[0]:
                best = score, detail
    return best[1] if best else None


def _detail(value: object, normalized_value: object, candidate: OCRLine, label: OCRLine, width: int, height: int) -> FieldExtractionDetail:
    value_confidence = candidate.confidence if candidate.confidence is not None else 0.55
    label_confidence = label.confidence if label.confidence is not None else 0.55
    confidence = round(min(0.99, max(0.0, value_confidence * 0.8 + label_confidence * 0.2)), 3)
    return FieldExtractionDetail(
        value=value,
        normalized_value=normalized_value,
        confidence=confidence,
        bbox=candidate.bbox,
        page=candidate.page_number,
        page_width=width,
        page_height=height,
        coordinate_space="original_page",
        line_index=candidate.line_index,
        source="TradeNet label-anchored OCR",
        evidence_text=candidate.text,
    )


def _candidate_score(label: OCRLine, candidate: OCRLine, dx: float, dy: float) -> float:
    value_confidence = candidate.confidence if candidate.confidence is not None else 0.55
    label_confidence = label.confidence if label.confidence is not None else 0.55
    return value_confidence * 0.65 + label_confidence * 0.20 - dx * 0.40 - dy * 0.30


def _belongs_to_declaration_section(
    label: OCRLine,
    declarations: list[OCRLine],
    dae_headings: list[OCRLine],
    width: int,
    height: int,
) -> bool:
    if not label.bbox:
        return False
    label_x, label_y = _normalized_center(label, width, height)
    declaration_headings = [heading for heading in declarations if heading.bbox]
    dae_headings = [heading for heading in dae_headings if heading.bbox]
    if not declaration_headings:
        return False
    nearby = [heading for heading in declaration_headings + dae_headings if _normalized_center_y(heading, height) <= label_y and label_y - _normalized_center_y(heading, height) <= 0.13]
    if not nearby:
        return False
    nearest = min(nearby, key=lambda heading: abs(_normalized_center(heading, width, height)[0] - label_x) + abs(_normalized_center_y(heading, height) - label_y) * 0.35)
    return nearest in declaration_headings


def _is_declaration_heading(text: str, line: OCRLine, height: int) -> bool:
    if not line.bbox or _normalized_center_y(line, height) > 0.22:
        return False
    compact = text.replace(" ", "")
    if "dae" in compact or "type" in text:
        return False
    return text in {"declaration", "declaraton", "dcaratoa"} or text.startswith("declaration ") and len(text.split()) <= 3


def _is_dae_heading(text: str, line: OCRLine, height: int) -> bool:
    compact = text.replace(" ", "")
    return bool(line.bbox and _normalized_center_y(line, height) <= 0.22 and compact in {"dae", "da.e"})


def _is_number_label(text: str) -> bool:
    return text in {"numero", "num", "n numero", "no"} or text.startswith("numero ")


def _is_date_label(text: str) -> bool:
    return text == "date" or text.startswith("date ")


def _parse_number(text: str) -> tuple[str, str] | None:
    value_text = _after_label(text, r"(?:numero|num|n\s*[°ºo]?)")
    match = _NUMBER_RE.search(value_text if value_text is not None else text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    if not 4 <= len(digits) <= 12:
        return None
    return digits, digits


def _parse_date(text: str) -> tuple[str, str] | None:
    value_text = _after_label(text, r"date")
    match = _DATE_RE.search(value_text if value_text is not None else text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000 if year < 70 else 1900
    try:
        normalized = date(year, month, day).isoformat()
    except ValueError:
        return None
    return normalized, normalized


def _parse_type(text: str) -> tuple[str, str] | None:
    value_text = _after_label(text, r"type\s*(?:de\s*)?declar(?:ation|ation)")
    if value_text is None:
        value_text = text.strip()
    match = _TYPE_RE.fullmatch(value_text.strip(" :;.-"))
    if not match:
        return None
    value = match.group(1).upper()
    return value, value


def _parse_count(text: str) -> tuple[int, int] | None:
    value_text = _after_label(text, r"(?:nbr(?:e)?|nb|nombre)\s*total\s*articles?")
    if value_text is None:
        value_text = text.strip()
    match = _COUNT_RE.fullmatch(value_text.strip(" :;.-"))
    if not match:
        return None
    value = int(match.group(1))
    return value, value


def _after_label(text: str, pattern: str) -> str | None:
    plain = _normalize(text)
    match = re.search(pattern, plain)
    return plain[match.end():].strip(" :;.-") if match else None


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^\w]+", " ", plain, flags=re.UNICODE).strip()


def _infer_page_dimensions(lines: list[OCRLine]) -> tuple[int, int]:
    width = max((line.page_width or 0 for line in lines), default=0) or max((line.bbox.x2 for line in lines if line.bbox), default=1)
    height = max((line.page_height or 0 for line in lines), default=0) or max((line.bbox.y2 for line in lines if line.bbox), default=1)
    return max(1, int(width)), max(1, int(height))


def _normalized_center(line: OCRLine, width: int, height: int) -> tuple[float, float]:
    if not line.bbox:
        return 0.0, 0.0
    return ((line.bbox.x1 + line.bbox.x2) / 2 / width, (line.bbox.y1 + line.bbox.y2) / 2 / height)


def _normalized_center_y(line: OCRLine, height: int) -> float:
    return _normalized_center(line, 1, height)[1]


def _normalized_width(line: OCRLine, width: int) -> float:
    return (line.bbox.x2 - line.bbox.x1) / width if line.bbox else 0.0


def _relative_distance(label: OCRLine, candidate: OCRLine, width: int, height: int) -> tuple[float, float]:
    lx, ly = _normalized_center(label, width, height)
    cx, cy = _normalized_center(candidate, width, height)
    return abs(lx - cx), cy - ly
