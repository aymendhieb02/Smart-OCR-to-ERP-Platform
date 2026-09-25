from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
import unicodedata
from typing import Callable

from app.core.schemas import BoundingBox, FieldExtractionDetail, OCRLine


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
    """Extract business fields from positioned TradeNet form observations."""
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
    source_fields = (
        "declaration_number", "declaration_date", "declaration_type",
        "exporter", "importer", "ptfn_amount", "currency_conversion_rate",
        "customs_total_value_tnd", "declaration_article_count",
    )
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
    vertical_shift = _form_vertical_shift(lines, width, height)
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
        found = _best_label_value(lines, label_groups[label_kind], parser, width, height, prefer_complete_number=field_name == "declaration_number")
        if field_name == "declaration_type" and found and not _in_cell(found.bbox, width, height, _shift_cell((0.69, 0.078, 0.88, 0.137), vertical_shift)):
            found = None
        if found:
            fields[field_name] = found
    _add_normalized_header_cell_fallback(fields, lines, width, height)
    _add_declaration_type_cell(fields, lines, width, height, vertical_shift)
    for role in ("exporter", "importer"):
        party = _extract_party(lines, role, width, height, vertical_shift)
        if party:
            fields[role] = party
    for name in ("ptfn_amount", "currency_conversion_rate", "customs_total_value_tnd"):
        amount = _extract_financial_cell(lines, name, width, height, vertical_shift)
        if amount:
            fields[name] = amount
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
            if number and 0.54 <= x <= 0.70:
                number_candidates.append((x, line, str(number[0])))
            parsed_date = _parse_date(line.text)
            if parsed_date and 0.61 <= x <= 0.81:
                date_candidates.append((x, line, str(parsed_date[0])))
        pairs: list[tuple[float, OCRLine, str, OCRLine, str]] = []
        for date_x, date_line, date_value in date_candidates:
            for number_x, number_line, number_value in number_candidates:
                number_y = _normalized_center_y(number_line, height)
                date_y = _normalized_center_y(date_line, height)
                gap = date_x - number_x
                if 0.04 <= gap <= 0.18 and abs(number_y - date_y) <= 0.025:
                    score = (
                        _line_quality(number_line) + _line_quality(date_line)
                        + min(len(number_value), 8) * 0.07
                        - abs(number_y - date_y) * 3
                        - abs(gap - 0.10) * 2
                    )
                    pairs.append((score, number_line, number_value, date_line, date_value))
        if pairs:
            _score, number_line, number_value, date_line, date_value = max(pairs, key=lambda item: item[0])
            if "declaration_number" not in fields:
                fields["declaration_number"] = _geometry_detail(number_value, number_value, number_line, width, height)
            if "declaration_date" not in fields:
                fields["declaration_date"] = _geometry_detail(date_value, date_value, date_line, width, height)

def _geometry_detail(value: object, normalized_value: object, candidate: OCRLine, width: int, height: int) -> FieldExtractionDetail:
    detail = _detail(value, normalized_value, candidate, candidate, width, height)
    detail.confidence = round((detail.confidence or 0.0) * 0.82, 3)
    detail.source = "TradeNet normalized header cell OCR (label not recognized)"
    return detail


def _in_cell(box: BoundingBox | None, width: int, height: int, bounds: tuple[float, float, float, float]) -> bool:
    if box is None:
        return False
    x = (box.x1 + box.x2) / (2 * width)
    y = (box.y1 + box.y2) / (2 * height)
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def _shift_cell(bounds: tuple[float, float, float, float], shift: float) -> tuple[float, float, float, float]:
    return bounds[0], bounds[1] + shift, bounds[2], bounds[3] + shift


def _form_vertical_shift(lines: list[OCRLine], width: int, height: int) -> float:
    """Align form cells to the printed declaration number/date row, not scan margins."""
    numbers = []
    dates = []
    for line in lines:
        if not line.bbox:
            continue
        x, y = _normalized_center(line, width, height)
        if y > 0.12:
            continue
        if 0.50 <= x <= 0.70 and _parse_number(line.text):
            numbers.append((x, y, line))
        if 0.62 <= x <= 0.84 and (
            _parse_date(line.text)
            or re.search(r"[./-]?\d{1,2}[./-]\d{2,4}", line.text)
        ):
            dates.append((x, y, line))
    pairs = [
        (number_y, _line_quality(number) + _line_quality(date_line) - abs(number_y - date_y) * 3)
        for number_x, number_y, number in numbers
        for date_x, date_y, date_line in dates
        if 0.04 <= date_x - number_x <= 0.18 and abs(number_y - date_y) <= 0.025
    ]
    if not pairs:
        return 0.0
    number_y, _score = max(pairs, key=lambda pair: pair[1])
    return max(-0.045, min(0.035, number_y - 0.064))


def _line_quality(line: OCRLine) -> float:
    # A narrow crop adds useful detail, but a low-confidence crop must not
    # automatically replace a clearer full-page observation.
    is_crop = (line.source or "").startswith("tradenet_") or line.source == "regional_fallback"
    return (line.confidence or 0.0) + (0.06 if is_crop else 0.0)


def _select_party_row(lines: list[OCRLine]) -> OCRLine:
    def score(line: OCRLine) -> float:
        text = line.text.strip()
        return (
            (line.confidence or 0.0)
            + (0.025 if line.source == "regional_fallback" or (line.source or "").startswith("tradenet_") else 0.0)
            + min(len(text), 60) * 0.002
            - text.count("$") * 0.12
            - text.count("#") * 0.05
        )

    best = max(lines, key=score)
    complete = [
        line for line in lines
        if len(line.text.strip()) > len(best.text.strip())
        and best.text.strip().casefold() in line.text.strip().casefold()
        and (line.confidence or 0.0) >= (best.confidence or 0.0) - 0.05
    ]
    return max(complete, key=score) if complete else best


def _add_declaration_type_cell(fields: dict[str, FieldExtractionDetail], lines: list[OCRLine], width: int, height: int, vertical_shift: float) -> None:
    if "declaration_type" in fields:
        return
    cell = _shift_cell((0.69, 0.078, 0.88, 0.137), vertical_shift)
    candidates = [
        line for line in lines
        if _in_cell(line.bbox, width, height, cell)
        and (line.confidence or 0.0) >= 0.48
        and re.fullmatch(r"[A-Za-z]", line.text.strip())
    ]
    if not candidates:
        return
    selected = max(candidates, key=_line_quality)
    value = selected.text.strip().upper()
    detail = _detail(value, value, selected, selected, width, height)
    detail.confidence = round((detail.confidence or 0.0) * 0.82, 3)
    detail.source = f"TradeNet declaration-type cell OCR ({selected.source or 'unknown'})"
    fields["declaration_type"] = detail


def _party_label(text: str, role: str) -> bool:
    normalized = _normalize(text)
    if len(normalized) > 18:
        return False
    return normalized.startswith(("expor", "expr", "lxpor")) if role == "exporter" else normalized.startswith(("impor", "imyx", "inn"))


def _extract_party(lines: list[OCRLine], role: str, width: int, height: int, vertical_shift: float) -> FieldExtractionDetail | None:
    label_box = _shift_cell((0.18, 0.015, 0.48, 0.088) if role == "exporter" else (0.18, 0.075, 0.48, 0.145), vertical_shift)
    labels = [line for line in lines if _in_cell(line.bbox, width, height, label_box) and _party_label(line.text, role)]
    if not labels:
        return None
    label = max(labels, key=_line_quality)
    label_y = _normalized_center_y(label, height)
    lower_limit = (0.106 if role == "exporter" else 0.166) + vertical_shift
    if role == "exporter":
        next_labels = [
            _normalized_center_y(line, height) for line in lines
            if _party_label(line.text, "importer") and line.bbox and _normalized_center_y(line, height) > label_y
        ]
        if next_labels:
            lower_limit = min(lower_limit, min(next_labels) - 0.005)
    else:
        declarant_labels = [
            _normalized_center_y(line, height) for line in lines
            if line.bbox and _normalize(line.text).startswith("declarant")
            and _normalized_center_y(line, height) > label_y
        ]
        if declarant_labels:
            lower_limit = min(lower_limit, min(declarant_labels) - 0.005)
    candidates = []
    for line in lines:
        if not line.bbox or line is label or not _in_cell(line.bbox, width, height, (0.16, label_y + 0.006, 0.49, lower_limit)):
            continue
        normalized = _normalize(line.text)
        if (sum(char.isalpha() for char in normalized) < 5
                or sum(char.isdigit() for char in normalized) > 2
                or _party_label(line.text, "exporter") or _party_label(line.text, "importer")
                or normalized.startswith(("declar", "code", "numero", "date", "facture"))
                or (line.confidence or 0.0) < 0.45):
            continue
        candidates.append(line)
    if not candidates:
        return None
    # A full-page and a cropped reading of the same printed line are
    # alternatives, not separate address lines.
    rows: list[list[OCRLine]] = []
    for line in sorted(candidates, key=lambda item: _normalized_center_y(item, height)):
        if rows and abs(_normalized_center_y(line, height) - _normalized_center_y(rows[-1][0], height)) <= 0.006:
            rows[-1].append(line)
        else:
            rows.append([line])
    selected_lines = [_select_party_row(row) for row in rows[:3]]
    first = selected_lines[0]
    value_lines = [first.text.strip()]
    if len(first.text.strip()) < 18 and len(selected_lines) > 1:
        continuation = selected_lines[1].text.strip()
        if not re.match(r"^\d|(?i:^(?:rue|avenue|av\.|libye|libya|tunisie|tunis)\b)", continuation):
            value_lines.append(continuation)
    value = " ".join(value_lines)
    box = BoundingBox(
        x1=min(line.bbox.x1 for line in selected_lines),
        y1=min(line.bbox.y1 for line in selected_lines),
        x2=max(line.bbox.x2 for line in selected_lines),
        y2=max(line.bbox.y2 for line in selected_lines),
    )
    sources = ", ".join(sorted({line.source or "unknown" for line in selected_lines}))
    return FieldExtractionDetail(
        value=value, normalized_value=value,
        evidence_text="\n".join(line.text for line in selected_lines),
        confidence=round(min(line.confidence or 0.0 for line in selected_lines), 3),
        bbox=box, page=first.page_number, page_width=width, page_height=height,
        coordinate_space="original_page", line_index=first.line_index,
        source=f"TradeNet {role} cell OCR ({sources})",
    )


_FINANCIAL_CELLS = {
    "ptfn_amount": ((0.60, 0.215, 0.81, 0.27), (0.60, 0.225, 0.82, 0.282), "ptfn"),
    "currency_conversion_rate": ((0.57, 0.267, 0.82, 0.32), (0.58, 0.275, 0.80, 0.329), "conversion"),
    "customs_total_value_tnd": ((0.78, 0.267, 0.97, 0.32), (0.79, 0.275, 0.97, 0.329), "valeur douane totale"),
}


def _parse_amount(text: str) -> float | None:
    match = re.fullmatch(r"\s*[$€]?\s*(\d{1,9}[.,]\d{2,7})\s*", text)
    return float(match.group(1).replace(",", ".")) if match else None


def _extract_financial_cell(lines: list[OCRLine], name: str, width: int, height: int, vertical_shift: float) -> FieldExtractionDetail | None:
    label_bounds, value_bounds, label_text = _FINANCIAL_CELLS[name]
    label_bounds = _shift_cell(label_bounds, vertical_shift)
    value_bounds = _shift_cell(value_bounds, vertical_shift)
    labels = [
        line for line in lines
        if _in_cell(line.bbox, width, height, label_bounds) and label_text in _normalize(line.text)
    ]
    candidates: list[tuple[float, OCRLine, float]] = []
    for line in lines:
        if not _in_cell(line.bbox, width, height, value_bounds) or (line.confidence or 0.0) < 0.45:
            continue
        value = _parse_amount(line.text)
        if value is None:
            continue
        x, y = _normalized_center(line, width, height)
        associated = any(
            abs(x - _normalized_center(label, width, height)[0]) <= 0.15
            and 0 <= y - _normalized_center_y(label, height) <= 0.05
            for label in labels
        )
        # The form cell remains usable when its small printed label was not
        # recognized. A candidate outside this cell never qualifies.
        score = _line_quality(line) + (0.08 if associated else 0.0)
        if name == "customs_total_value_tnd" and line.source == "tradenet_customs_total":
            score += 0.025
        candidates.append((score, line, value))
    if not candidates:
        return None
    _score, selected, value = max(candidates, key=lambda item: item[0])
    detail = _detail(value, value, selected, selected, width, height)
    detail.source = f"TradeNet {name} cell OCR ({selected.source or 'unknown'})"
    if not labels:
        detail.confidence = round((detail.confidence or 0.0) * 0.82, 3)
    return detail


def _best_label_value(
    lines: list[OCRLine],
    labels: list[OCRLine],
    parser: Callable[[str], tuple[object, object] | None],
    width: int,
    height: int,
    *,
    prefer_complete_number: bool = False,
) -> FieldExtractionDetail | None:
    best: tuple[float, FieldExtractionDetail] | None = None
    for label in labels:
        parsed_inline = parser(label.text)
        if parsed_inline:
            value, normalized_value = parsed_inline
            detail = _detail(value, normalized_value, label, label, width, height)
            score = _candidate_score(label, label, 0.0, 0.0)
            if prefer_complete_number:
                score += min(len(str(value)), 12) * 0.004
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
            if prefer_complete_number:
                score += min(len(str(value)), 12) * 0.004
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
    if _DATE_RE.search(text) or re.search(r"[./-]?\d{1,2}[./-]\d{4}", text):
        return None
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
