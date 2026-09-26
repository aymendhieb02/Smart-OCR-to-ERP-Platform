from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import re

from app.core.schemas import BoundingBox, FieldExtractionDetail, LineItem, OCRLine
from app.utils.helpers import parse_amount, strip_accents


FAMILY = "ruspina_reinvoice_v1"
_INVOICE_HEADING = re.compile(r"^[il1]nvoice\s+n\s*[o0°º*#z]?\s*", re.IGNORECASE)
_REFERENCE = re.compile(r"^as\s*per\s*invoice\s*[:\-]?\s*(\d{6,14})\b", re.IGNORECASE)
_REFERENCE_LABEL = re.compile(r"^as\s*per\s*invoice\b", re.IGNORECASE)
_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_IDENTIFIER = re.compile(r"\b\d{6,12}\b")
_WEIGHT = re.compile(r"^\s*(\d[\d\s]*(?:[.,]\d{1,3})?)\s*(T|KG|TONNES?)?\s*$", re.IGNORECASE)
_BAGS = re.compile(r"^\s*\d[\d\s]*\s*$")
_MONEY = re.compile(r"^\s*\d[\d\s.,]*\s*(?:EUR|EURO|E0R|UR)?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class RuspinaExtraction:
    fields: dict[str, FieldExtractionDetail]
    line_items: list[LineItem]


def extract_ruspina_fields(
    lines: list[OCRLine],
    *,
    document_family: str | None,
    page_dimensions: dict[int, tuple[int, int]] | None = None,
) -> RuspinaExtraction:
    """Read the recurring re-invoice from its own positioned OCR evidence only."""
    if document_family != FAMILY:
        return RuspinaExtraction({}, [])
    grouped: dict[int, list[OCRLine]] = defaultdict(list)
    for line in lines:
        if line.bbox and line.text.strip():
            grouped[line.page_number].append(line)
    fields: dict[str, FieldExtractionDetail] = {}
    items: list[LineItem] = []
    for page, page_lines in sorted(grouped.items()):
        width, height = (page_dimensions or {}).get(page) or _page_size(page_lines)
        page_fields = _extract_page(page_lines, width, height)
        for name, detail in page_fields.items():
            if name not in fields or (detail.confidence or 0) > (fields[name].confidence or 0):
                fields[name] = detail
        page_items, table_fields = _extract_table(page_lines, width, height)
        items.extend(page_items)
        for name, detail in table_fields.items():
            fields.setdefault(name, detail)
    return RuspinaExtraction(fields, items)


def _extract_page(lines: list[OCRLine], width: int, height: int) -> dict[str, FieldExtractionDetail]:
    fields: dict[str, FieldExtractionDetail] = {}
    headings = [line for line in lines if _in_region(line, width, height, (0.23, 0.075, 0.68, 0.20))
                and _INVOICE_HEADING.search(line.text.strip())]
    if headings:
        heading = max(headings, key=lambda line: line.confidence or 0)
        heading_match = _INVOICE_HEADING.search(heading.text.strip())
        inline = _IDENTIFIER.search(heading.text[heading_match.end():]) if heading_match else None
        number_line = heading if inline else _right_value(
            heading, lines, width, height,
            lambda line: bool(_IDENTIFIER.fullmatch(line.text.strip())),
            max_x=0.65, max_gap_y=0.014,
        )
        if number_line:
            number_match = inline or _IDENTIFIER.search(number_line.text)
            if number_match:
                fields["invoice_number"] = _detail(number_match.group(0), width, height, heading, number_line)
        of_labels = [line for line in lines if _in_region(line, width, height, (0.57, 0.075, 0.70, 0.20))
                     and re.fullmatch(r"OF\s*:?", line.text.strip(), re.IGNORECASE)
                     and abs(_cy(line, height) - _cy(heading, height)) <= 0.017]
        if of_labels:
            of_label = max(of_labels, key=lambda line: line.confidence or 0)
            date_line = _right_value(of_label, lines, width, height, lambda line: bool(_parse_date(line.text)), max_x=0.82)
            if date_line:
                parsed = _parse_date(date_line.text)
                fields["invoice_date"] = _detail(
                    parsed.isoformat(), width, height, of_label, date_line, display_value=date_line.text.strip(),
                )

    for line in lines:
        if not _in_region(line, width, height, (0.20, 0.10, 0.65, 0.22)):
            continue
        reference = _REFERENCE.search(line.text.strip())
        if reference:
            fields["referenced_invoice"] = _detail(reference.group(1), width, height, line)
            break
        if _REFERENCE_LABEL.search(line.text.strip()):
            value_line = _right_value(line, lines, width, height,
                                      lambda candidate: bool(_IDENTIFIER.fullmatch(candidate.text.strip())),
                                      max_x=0.70)
            if value_line:
                fields["referenced_invoice"] = _detail(value_line.text.strip(), width, height, line, value_line)
                break

    seller = _seller_line(lines, width, height)
    if seller:
        fields["seller"] = _detail(" ".join(seller.text.split()), width, height, seller)
    buyer = _labelled_value(lines, width, height, r"^cl[il1]ent\b", (0.0, 0.12, 0.25, 0.23),
                            lambda line: _text_value(line, min_letters=5), max_x=0.55, max_gap_y=0.014)
    if buyer:
        label, value = buyer
        fields["buyer"] = _detail(" ".join(value.text.split()), width, height, label, value)
        fields["customer"] = fields["buyer"]
        fields["client"] = fields["buyer"]
    address = _labelled_value(lines, width, height, r"^add?ress?\s*[:;]?\s*$", (0.0, 0.14, 0.25, 0.25),
                              lambda line: _text_value(line, min_letters=3), max_x=0.60, max_gap_y=0.014)
    if address:
        label, value = address
        fields["address"] = _detail(" ".join(value.text.split()), width, height, label, value)

    currency_lines = [line for line in lines if re.search(r"EUR\b|EURO\b", line.text, re.IGNORECASE)
                      and _in_region(line, width, height, (0.45, 0.20, 0.99, 0.68))]
    if currency_lines:
        currency_line = max(currency_lines, key=lambda line: (
            1 if _in_region(line, width, height, (0.65, 0.25, 0.97, 0.35)) else 0,
            line.confidence or 0,
        ))
        fields["currency"] = _detail("EUR", width, height, currency_line)

    total_lines = [line for line in lines if _in_region(line, width, height, (0.76, 0.54, 0.99, 0.64))
                   and (amount := _money(line.text)) is not None and amount > 0]
    if total_lines:
        selected = max(total_lines, key=lambda line: (line.confidence or 0) + (0.02 if line.source == "regional_fallback" else 0))
        fields["total"] = _detail(
            _money(selected.text), width, height, selected,
            display_value=re.sub(r"\s*(?:EUR|EURO|E0R|UR)\s*$", "", selected.text, flags=re.IGNORECASE).strip(),
        )

    amount_words = _amount_words(lines, width, height)
    if amount_words:
        value, observations = amount_words
        fields["total_amount_words"] = _detail(value, width, height, *observations)

    logistics = (
        ("gross_weight", r"^gro[s$5]s\s*weight\b", _weight_value),
        ("net_weight", r"^net\s*weight\b", _weight_value),
        ("number_of_bags", r"^number\s*of\s*bags\b", _bag_value),
        ("delivery", r"^delivery\b", _text_value),
        ("origin", r"^origin\b", _text_value),
        ("payment", r"^payment\b", _payment_value),
        ("iban", r"^iban\s*[:;]?\s*$", _iban_value),
        ("bank", r"^bank\s*[:;]?\s*$", _bank_value),
        ("swift", r"^swift\s*[:;]?\s*$", _swift_value),
    )
    for name, pattern, parser in logistics:
        pair = _labelled_value(lines, width, height, pattern, (0.0, 0.59, 0.25, 0.85), parser,
                               max_x=0.58, max_gap_y=0.014)
        if not pair:
            continue
        label, value_line = pair
        value = parser(value_line)
        detail = _detail(value, width, height, label, value_line, display_value=value_line.text.strip())
        fields[name] = detail
        if name == "delivery":
            fields["incoterm"] = detail
    return fields


def _extract_table(lines: list[OCRLine], width: int, height: int) -> tuple[list[LineItem], dict[str, FieldExtractionDetail]]:
    headings = [line for line in lines if _in_region(line, width, height, (0.50, 0.21, 0.97, 0.29))
                and sum(char.isalpha() for char in line.text) >= 3 and _money(line.text) is None]
    if len(headings) < 2:
        return [], {}
    header_y = max(_cy(line, height) for line in headings)
    description_lines = [line for line in lines if _in_region(line, width, height, (0.05, header_y + 0.008, 0.53, header_y + 0.09))
                         and sum(char.isalpha() for char in line.text) >= 5
                         and not re.search(r"^(?:descr|discr|\$crpt|total)", _plain(line.text))]
    if not description_lines:
        return [], {}
    rows: list[list[OCRLine]] = []
    for line in sorted(description_lines, key=lambda item: _cy(item, height)):
        if rows and abs(_cy(line, height) - _cy(rows[-1][0], height)) <= 0.009:
            rows[-1].append(line)
        else:
            rows.append([line])
    items: list[LineItem] = []
    item_evidence: list[OCRLine] = []
    first_row_fields: dict[str, FieldExtractionDetail] = {}
    quantity_header = next((line for line in headings if 0.52 <= _cx(line, width) <= 0.66
                            and re.search(r"[({\[]\s*T\s*[)}\]]?", line.text, re.IGNORECASE)), None)
    for row in rows:
        row_y = _cy(row[0], height)
        description_parts = sorted(row, key=lambda line: line.bbox.x1)
        description = " ".join(" ".join(line.text.split()) for line in description_parts)
        quantity_line = _table_number(lines, width, height, row_y, (0.52, 0.66))
        unit_price_line = _table_number(lines, width, height, row_y, (0.67, 0.81))
        total_line = _table_number(lines, width, height, row_y, (0.80, 0.97))
        if not quantity_line and not unit_price_line and not total_line:
            continue
        quantity = _money(quantity_line.text) if quantity_line else None
        unit_price = _money(unit_price_line.text) if unit_price_line else None
        line_total = _money(total_line.text) if total_line else None
        unit_line = next((line for line in (quantity_line, quantity_header) if line and re.search(r"[({\[]\s*T\s*[)}\]]?|\d\s*T\b", line.text, re.IGNORECASE)), None)
        unit = "T" if unit_line else None
        evidence = [*description_parts, *[line for line in (quantity_line, unit_price_line, total_line) if line]]
        item_evidence.extend(evidence)
        box = _union_box(evidence)
        item = LineItem(
            description=description, quantity=quantity, unit=unit, unit_price=unit_price, total=line_total,
            confidence=round(min(line.confidence or 0 for line in evidence), 3), bbox=box,
            page=description_parts[0].page_number, page_width=width, page_height=height,
            coordinate_space="original_page", source="RUSPINA positioned OCR table",
        )
        items.append(item)
        if len(items) == 1:
            first_row_fields = {
                "description": _detail(description, width, height, *description_parts),
                "quantity": _detail(quantity, width, height, quantity_line) if quantity_line else None,
                "unit": _detail(unit, width, height, unit_line) if unit_line else None,
                "unit_price": _detail(unit_price, width, height, unit_price_line) if unit_price_line else None,
                "line_total": _detail(line_total, width, height, total_line) if total_line else None,
            }
            first_row_fields = {name: detail for name, detail in first_row_fields.items() if detail is not None}
    if items:
        first_row_fields["line_items"] = _detail(
            [{"description": item.description, "quantity": item.quantity, "unit": item.unit,
              "unit_price": item.unit_price, "line_total": item.total} for item in items],
            width, height, *item_evidence,
        )
    return items, first_row_fields


def _table_number(lines: list[OCRLine], width: int, height: int, row_y: float,
                  x_range: tuple[float, float]) -> OCRLine | None:
    candidates = [line for line in lines if x_range[0] <= _cx(line, width) <= x_range[1]
                  and abs(_cy(line, height) - row_y) <= 0.020 and _money(line.text) is not None]
    return max(candidates, key=lambda line: (line.confidence or 0) - abs(_cy(line, height) - row_y) * 2) if candidates else None


def _labelled_value(lines: list[OCRLine], width: int, height: int, pattern: str,
                    region: tuple[float, float, float, float], parser,
                    *, max_x: float, max_gap_y: float) -> tuple[OCRLine, OCRLine] | None:
    labels = [line for line in lines if _in_region(line, width, height, region)
              and re.search(pattern, _plain(line.text))]
    pairs = [(label, value) for label in labels
             if (value := _right_value(label, lines, width, height, lambda line: parser(line) is not None,
                                       max_x=max_x, max_gap_y=max_gap_y))]
    return max(pairs, key=lambda pair: min(pair[0].confidence or 0, pair[1].confidence or 0)) if pairs else None


def _right_value(label: OCRLine, lines: list[OCRLine], width: int, height: int, predicate,
                 *, max_x: float, max_gap_y: float = 0.014) -> OCRLine | None:
    candidates = [line for line in lines if line is not label and line.bbox
                  and line.page_number == label.page_number
                  and line.bbox.x1 >= label.bbox.x2 - width * 0.04
                  and _cx(line, width) <= max_x
                  and abs(_cy(line, height) - _cy(label, height)) <= max_gap_y
                  and predicate(line)]
    return min(candidates, key=lambda line: (abs(_cy(line, height) - _cy(label, height)),
                                         max(0, line.bbox.x1 - label.bbox.x2) / width,
                                         -(line.confidence or 0))) if candidates else None


def _seller_line(lines: list[OCRLine], width: int, height: int) -> OCRLine | None:
    footer = [line for line in lines if _in_region(line, width, height, (0.20, 0.87, 0.85, 0.98))
              and re.search(r"impor[tl]\s+(?:et\s+)?expor", _plain(line.text))]
    if footer:
        return max(footer, key=lambda line: line.confidence or 0)
    header = [line for line in lines if _in_region(line, width, height, (0.64, 0.02, 0.98, 0.11))
              and sum(char.isalpha() for char in line.text) >= 8]
    return max(header, key=lambda line: line.confidence or 0) if header else None


def _parse_date(text: str) -> date | None:
    match = _DATE.fullmatch(text.strip())
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _money(text: str) -> float | None:
    return parse_amount(text) if _MONEY.fullmatch(text.strip()) else None


def _weight_value(line: OCRLine) -> float | None:
    match = _WEIGHT.fullmatch(line.text)
    return parse_amount(match.group(1)) if match else None


def _bag_value(line: OCRLine) -> int | None:
    if not _BAGS.fullmatch(line.text):
        return None
    return int(re.sub(r"\s", "", line.text))


def _text_value(line: OCRLine, *, min_letters: int = 3) -> str | None:
    text = " ".join(line.text.split())
    return text if sum(char.isalpha() for char in text) >= min_letters and not re.search(
        r"^(?:adress|address|client|bank\s*:|iban|swift)\b", _plain(text)
    ) else None


def _payment_value(line: OCRLine) -> str | None:
    text = _text_value(line)
    return text if text and not re.search(r"\b(?:iban|swift|banque)\b", _plain(text)) else None


def _iban_value(line: OCRLine) -> str | None:
    value = " ".join(line.text.split())
    compact = re.sub(r"\s", "", value)
    return value if re.fullmatch(r"[A-Za-z]{2}\d{2}[A-Za-z0-9]{10,30}", compact) else None


def _bank_value(line: OCRLine) -> str | None:
    value = _text_value(line)
    return value if value and _plain(value) != "bank transfer" else None


def _swift_value(line: OCRLine) -> str | None:
    value = line.text.strip()
    return value if re.fullmatch(r"[A-Za-z0-9]{8}(?:[A-Za-z0-9]{3})?", value) and re.search(r"[A-Za-z]", value) else None


def _amount_words(lines: list[OCRLine], width: int, height: int) -> tuple[str, tuple[OCRLine, ...]] | None:
    labels = [line for line in lines if _in_region(line, width, height, (0.0, 0.58, 0.80, 0.68))
              and re.match(r"^total\s*amount\s*[:;]?", _plain(line.text))]
    for label in sorted(labels, key=lambda line: -(line.confidence or 0)):
        match = re.match(r"^total\s*amount\s*[:;]?\s*(.*)$", label.text.strip(), re.IGNORECASE)
        if match and _words_value(match.group(1)):
            return match.group(1).strip(), (label,)
        value = _right_value(label, lines, width, height,
                             lambda line: _words_value(line.text), max_x=0.85, max_gap_y=0.014)
        if value:
            return value.text.strip(), (label, value)
    return None


def _words_value(text: str) -> bool:
    return sum(char.isalpha() for char in text) >= 8 and _money(text) is None


def _plain(text: str) -> str:
    return strip_accents(text).casefold().strip()


def _detail(value, width: int, height: int, *observations: OCRLine, display_value: str | None = None) -> FieldExtractionDetail:
    observations = tuple({id(line): line for line in observations}.values())
    first = observations[0]
    return FieldExtractionDetail(
        value=value, display_value=display_value, normalized_value=value,
        evidence_text="\n".join(line.text for line in observations),
        confidence=round(min(line.confidence or 0 for line in observations), 3),
        bbox=_union_box(observations), page=first.page_number,
        page_width=width, page_height=height, coordinate_space="original_page",
        line_index=first.line_index,
        source="RUSPINA positioned OCR (" + ", ".join(sorted({line.source or "unknown" for line in observations})) + ")",
    )


def _union_box(lines: list[OCRLine] | tuple[OCRLine, ...]) -> BoundingBox:
    return BoundingBox(x1=min(line.bbox.x1 for line in lines), y1=min(line.bbox.y1 for line in lines),
                       x2=max(line.bbox.x2 for line in lines), y2=max(line.bbox.y2 for line in lines))


def _cx(line: OCRLine, width: int) -> float:
    return (line.bbox.x1 + line.bbox.x2) / (2 * width)


def _cy(line: OCRLine, height: int) -> float:
    return (line.bbox.y1 + line.bbox.y2) / (2 * height)


def _in_region(line: OCRLine, width: int, height: int, bounds: tuple[float, float, float, float]) -> bool:
    return bool(line.bbox and bounds[0] <= _cx(line, width) <= bounds[2] and bounds[1] <= _cy(line, height) <= bounds[3])


def _page_size(lines: list[OCRLine]) -> tuple[int, int]:
    width = max((line.page_width or 0 for line in lines), default=0) or max(line.bbox.x2 for line in lines)
    height = max((line.page_height or 0 for line in lines), default=0) or max(line.bbox.y2 for line in lines)
    return max(1, int(width)), max(1, int(height))
