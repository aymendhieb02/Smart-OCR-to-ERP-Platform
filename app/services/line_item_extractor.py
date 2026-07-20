import re
from collections import defaultdict

from app.core.config import settings
from app.core.schemas import LineItem, OCRLine
from app.services.document_layout import group_ocr_lines, reconstruct_tables
from app.services.table_reconstruction_engine import reconstruct_line_items as reconstruct_p3_line_items
from app.utils.helpers import parse_amount, strip_accents


PRODUCT_CODE = r"[A-Z]{2,}[A-Z0-9]*-[A-Z0-9]+"
STOP_KEYWORDS = (
    "arrete", "conditions de paiement", "coordonnees bancaires", "banque", "rib",
    "iban", "swift", "payment terms", "bank details", "thank you", "merci",
    "Ã˜Â´Ã™Æ’Ã˜Â±Ã˜Â§", "Ã˜Â§Ã™â€žÃ˜Â¨Ã™â€ Ã™Æ’", "Ã˜Â§Ã™â€žÃ˜Â­Ã˜Â³Ã˜Â§Ã˜Â¨ Ã˜Â§Ã™â€žÃ˜Â¨Ã™â€ Ã™Æ’Ã™Å ",
)
REJECT_KEYWORDS = (
    "rib", "iban", "swift", "banque", "bank", "email", "tel", "address",
    "adresse", "mf", "ice", "phone", "Ã˜Â±Ã™â€šÃ™â€¦ Ã˜Â§Ã™â€žÃ™â€¡Ã˜Â§Ã˜ÂªÃ™Â", "Ã˜Â§Ã™â€žÃ˜Â¨Ã˜Â±Ã™Å Ã˜Â¯ Ã˜Â§Ã™â€žÃ˜Â¥Ã™â€žÃ™Æ’Ã˜ÂªÃ˜Â±Ã™Ë†Ã™â€ Ã™Å ",
)


def extract_line_items(text: str, blocks: list[OCRLine] | None = None) -> list[LineItem]:
    coordinate_items = extract_line_items_from_blocks(blocks or [])
    if coordinate_items:
        return coordinate_items

    items: list[LineItem] = []
    stopped = False
    for line in text.splitlines():
        clean = line.strip()
        lower = strip_accents(clean).lower()
        item = parse_line_item(clean)
        if item:
            items.append(item)
            continue
        if any(keyword in lower for keyword in STOP_KEYWORDS):
            stopped = True
        if stopped or not clean or any(keyword in lower for keyword in REJECT_KEYWORDS):
            continue
    return items


def extract_line_items_from_blocks(blocks: list[OCRLine]) -> list[LineItem]:
    if not blocks:
        return []
    positioned_blocks = [block for block in blocks if block.bbox]
    p3_result = reconstruct_p3_line_items(positioned_blocks)
    if p3_result.line_items and _table_profile_allows_result(p3_result.selected_strategy):
        return p3_result.line_items
    if (
        p3_result.selected_strategy == "UNRESOLVED"
        and p3_result.diagnostics.get("header_confirmed")
        and not p3_result.diagnostics.get("rows_reconstructed")
        and not _has_combined_table_header(positioned_blocks)
    ):
        return []
    reconstructed_items = _extract_reconstructed_table_items(positioned_blocks or blocks)
    if reconstructed_items:
        return reconstructed_items
    if not positioned_blocks:
        return []
    anchored_items = _extract_anchored_table_rows(positioned_blocks)
    if anchored_items:
        return anchored_items
    rows = _group_blocks_by_row([block for block in blocks if block.bbox])
    header_seen = False
    items: list[LineItem] = []
    pending_description: str | None = None
    for row_blocks in rows:
        row_text = " ".join(block.text for block in row_blocks)
        lower = strip_accents(row_text).lower()
        if any(keyword in lower for keyword in STOP_KEYWORDS):
            if items:
                break
            continue
        if _is_table_header(lower):
            header_seen = True
            continue
        if not header_seen and not _looks_like_coordinate_item_row(row_text):
            continue
        item = _parse_coordinate_row(row_blocks, pending_description)
        if item:
            items.append(item)
            pending_description = None
        elif header_seen and _looks_like_description_fragment(row_text):
            pending_description = f"{pending_description or ''} {row_text}".strip()
    return items


def _table_profile_allows_result(strategy: str | None) -> bool:
    profile = getattr(settings, "table_reconstruction_profile", "p3_stable")
    if profile == "p3_1_adaptive":
        return True
    return strategy in {"COLUMNAR_TABLE", "HEADERLESS_COLUMNAR"}


def parse_line_item(line: str) -> LineItem | None:
    product_code = re.search(PRODUCT_CODE, line)
    if not product_code:
        return _parse_flexible_line_item(line)
    numbers = re.findall(r"[+-]?\d+(?:[,.]\d+)?", line[product_code.end():])
    parsed = [parse_amount(value) for value in numbers]
    parsed = [value for value in parsed if value is not None]
    if len(parsed) >= 4:
        quantity, unit_price, _tax, total = parsed[-4:]
    else:
        return None
    if quantity is None or quantity <= 0 or total is None or total <= 0:
        return None
    if unit_price is not None and unit_price > 0 and abs((quantity * unit_price) - total) > max(0.1, total * 0.20):
        # OCR often drops digits in tables; keep plausible rows but reject very wild numeric rows.
        if len(parsed) <= 3:
            return None
    description = line[:product_code.start()].strip(" #0123456789.-|[]")
    line_total_ht = round(quantity * unit_price, 3) if quantity is not None and unit_price is not None else None
    tax_rate = parse_amount(str(_tax))
    tax_amount = round(total - line_total_ht, 3) if total is not None and line_total_ht is not None and total >= line_total_ht else None
    return LineItem(
        reference=product_code.group(0),
        description=description or product_code.group(0),
        quantity=quantity,
        unit_price=unit_price,
        line_total_ht=line_total_ht,
        tax_amount=tax_amount,
        tax_rate=tax_rate,
        line_total_ttc=total,
        total=total,
        confidence=0.72,
        source="regex line item",
    )



def _parse_flexible_line_item(line: str) -> LineItem | None:
    lower = strip_accents(line).lower()
    if any(keyword in lower for keyword in REJECT_KEYWORDS) or any(keyword in lower for keyword in STOP_KEYWORDS):
        return None
    numbers = re.findall(r"[+-]?\d+(?:[,.]\d+)?", line)
    parsed = [parse_amount(value) for value in numbers]
    parsed = [value for value in parsed if value is not None]
    if len(parsed) < 3:
        return None
    first_number = re.search(r"[+-]?\d", line)
    description = line[:first_number.start()].strip(" #0123456789.-|[]") if first_number else line
    description = re.sub(r"\s{2,}", " ", description).strip()
    if len(description) < 3 or sum(char.isalpha() for char in description) < 3:
        return None
    if len(parsed) >= 4:
        quantity, unit_price, tax_rate, total = parsed[-4:]
    else:
        quantity, unit_price, total = parsed[-3:]
        tax_rate = None
    if quantity <= 0 or total <= 0:
        return None
    if unit_price and abs(quantity * unit_price - total) > max(total * 0.35, 1.0) and len(parsed) < 4:
        return None
    line_total_ht = round(quantity * unit_price, 3) if unit_price is not None else None
    tax_amount = round(total - line_total_ht, 3) if line_total_ht is not None and total >= line_total_ht else None
    return LineItem(
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        line_total_ht=line_total_ht,
        tax_amount=tax_amount,
        tax_rate=tax_rate if tax_rate is not None and tax_rate <= 100 else None,
        line_total_ttc=total,
        total=total,
        confidence=0.62,
        source="flexible numeric row",
    )



def _extract_reconstructed_table_items(blocks: list[OCRLine]) -> list[LineItem]:
    if not blocks:
        return []
    tables = reconstruct_tables(blocks, group_ocr_lines(blocks))
    if not tables:
        return []
    items: list[LineItem] = []
    for table in tables[:1]:
        for row in table.rows:
            if row.get("invalid"):
                continue
            values = row.get("values", {})
            description = values.get("description")
            reference = values.get("reference")
            quantity = values.get("quantity")
            discount = values.get("discount")
            unit_price = values.get("unit_price")
            total = values.get("total")
            tax_rate = values.get("tax_rate")
            unit = values.get("unit")
            if not description:
                continue
            if unit_price is None and quantity and total is not None:
                unit_price = round(total / quantity, 3)
            line_total_ht = values.get("line_total_ht")
            if line_total_ht is None:
                line_total_ht = round(quantity * unit_price, 3) if quantity is not None and unit_price is not None else total
            if total is None and line_total_ht is not None:
                total = line_total_ht
            if quantity is None and total is None and unit_price is None:
                continue
            items.append(LineItem(
                reference=reference,
                description=description,
                quantity=quantity,
                unit=unit,
                unit_price=unit_price,
                discount=discount,
                line_total_ht=line_total_ht,
                tax_rate=tax_rate,
                line_total_ttc=total,
                total=total,
                confidence=min(row.get("confidence") or table.confidence or 0.65, 0.72) if row.get("needs_review") else (row.get("confidence") or table.confidence),
                bbox=row.get("bbox"),
                page=table.page,
                source="reconstructed table review" if row.get("needs_review") else "reconstructed table",
            ))
    return items if len(items) >= 1 else []


def _has_combined_table_header(blocks: list[OCRLine]) -> bool:
    for block in blocks:
        lower = strip_accents(block.text).lower()
        hits = sum(1 for word in ("description", "quantity", "qty", "price", "total") if word in lower)
        if hits >= 4:
            return True
    return False


def _extract_anchored_table_rows(blocks: list[OCRLine]) -> list[LineItem]:
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda block: (block.page_number, block.bbox.y1, block.bbox.x1))
    header = _detect_table_columns(ordered)
    if not header:
        return []
    header_y = header["y"]
    stop_y = _detect_table_stop_y(ordered, header_y)
    anchors = _detect_row_anchors(ordered, header_y, stop_y, header)
    if len(anchors) < 2:
        return []

    items: list[LineItem] = []
    for index, anchor in enumerate(anchors):
        next_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
        top = max(header_y + 2, anchor.bbox.y1 - 8)
        bottom = (next_anchor.bbox.y1 - 8) if next_anchor else stop_y
        band = [
            block for block in ordered
            if block.page_number == anchor.page_number
            and block.bbox.y1 >= top
            and block.bbox.y1 < bottom
            and not _is_summary_or_header_text(block.text)
        ]
        item = _parse_anchored_band(anchor, band, header)
        if item:
            items.append(item)
    return items if len(items) >= 2 else []


def _detect_table_columns(blocks: list[OCRLine]) -> dict[str, float] | None:
    candidates: list[dict[str, float]] = []
    for block in blocks:
        lower = strip_accents(block.text).lower()
        if "description" not in lower and "designation" not in lower:
            continue
        same_line = [
            other for other in blocks
            if other.page_number == block.page_number
            and abs(_center_y(other) - _center_y(block)) <= 18
        ]
        header: dict[str, float] = {"y": block.bbox.y2, "description_x": block.bbox.x1}
        for other in same_line:
            text = strip_accents(other.text).lower()
            if any(word in text for word in ("quantity", "qty", "qte", "qte livree")):
                header["quantity_x"] = _center_x(other)
            elif "price" in text or "prix" in text or "unit" in text:
                header["price_x"] = _center_x(other)
            elif "total" in text:
                header["total_x"] = _center_x(other)
        if "quantity_x" in header and ("price_x" in header or "total_x" in header):
            candidates.append(header)
    if candidates:
        return sorted(candidates, key=lambda item: item["y"])[0]
    return None


def _detect_table_stop_y(blocks: list[OCRLine], header_y: float) -> float:
    stop_words = ("subtotal", "sub total", "sous-total", "sales tax", "tax", "tva", "shipping", "total due", "total ttc", "amount due")
    stop_candidates = [
        block.bbox.y1 for block in blocks
        if block.bbox
        and block.bbox.y1 > header_y
        and any(word in strip_accents(block.text).lower() for word in stop_words)
    ]
    if stop_candidates:
        return min(stop_candidates)
    bottom_edges = [block.bbox.y2 for block in blocks if block.bbox]
    return (max(bottom_edges) + 20) if bottom_edges else header_y + 20


def _detect_row_anchors(blocks: list[OCRLine], header_y: float, stop_y: float, header: dict[str, float]) -> list[OCRLine]:
    description_x = header.get("description_x", 120)
    anchors: list[OCRLine] = []
    seen: set[tuple[int, int]] = set()
    for block in blocks:
        if block.bbox.y1 <= header_y or block.bbox.y1 >= stop_y:
            continue
        text = block.text.strip()
        if not re.fullmatch(r"0?\d{1,3}", text):
            continue
        value = int(text)
        if value <= 0 or value > 500:
            continue
        if block.bbox.x1 > description_x - 8:
            continue
        key = (block.page_number, value)
        if key in seen:
            continue
        seen.add(key)
        anchors.append(block)
    return sorted(anchors, key=lambda block: (block.page_number, block.bbox.y1, block.bbox.x1))


def _parse_anchored_band(anchor: OCRLine, band: list[OCRLine], header: dict[str, float]) -> LineItem | None:
    quantity_x = header.get("quantity_x")
    price_x = header.get("price_x")
    total_x = header.get("total_x")
    if quantity_x is None or total_x is None:
        return None
    desc_right = quantity_x - 28
    description_blocks = [
        block for block in band
        if block is not anchor
        and block.bbox.x1 > anchor.bbox.x2 + 5
        and block.bbox.x1 < desc_right
    ]
    description = _join_description_blocks(description_blocks)
    if not description:
        return None

    quantity = _nearest_numeric_value(band, quantity_x, anchor, max_distance=55, integer_preferred=True)
    unit_price = _nearest_numeric_value(band, price_x, anchor, max_distance=75) if price_x is not None else None
    total = _nearest_numeric_value(band, total_x, anchor, max_distance=75)
    if quantity is None or total is None:
        return None
    if unit_price is None and quantity:
        unit_price = round(total / quantity, 3)
    line_total_ht = round(quantity * unit_price, 3) if quantity is not None and unit_price is not None else total
    boxes = [block.bbox for block in band if block.bbox]
    bbox = {
        "x1": min(box.x1 for box in boxes),
        "y1": min(box.y1 for box in boxes),
        "x2": max(box.x2 for box in boxes),
        "y2": max(box.y2 for box in boxes),
    } if boxes else None
    confidences = [block.confidence for block in band if block.confidence is not None]
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
    return LineItem(
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        line_total_ht=line_total_ht,
        line_total_ttc=total,
        total=total,
        confidence=confidence,
        bbox=bbox,
        page=anchor.page_number,
        source="anchored table row",
    )


def _nearest_numeric_value(
    blocks: list[OCRLine],
    column_x: float,
    anchor: OCRLine,
    max_distance: float,
    integer_preferred: bool = False,
) -> float | None:
    candidates: list[tuple[float, float, OCRLine]] = []
    anchor_y = _center_y(anchor)
    for block in blocks:
        if block is anchor:
            continue
        value = _parse_money_or_number(block.text)
        if value is None:
            continue
        distance_x = abs(_center_x(block) - column_x)
        if distance_x > max_distance:
            continue
        if integer_preferred and not float(value).is_integer():
            continue
        distance_y = abs(_center_y(block) - anchor_y)
        candidates.append((distance_x + distance_y * 0.25, value, block))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def _join_description_blocks(blocks: list[OCRLine]) -> str:
    parts = []
    for block in sorted(blocks, key=lambda value: (value.bbox.y1, value.bbox.x1)):
        text = block.text.strip(" |[]")
        if not text:
            continue
        plain = strip_accents(text).lower()
        if any(keyword in plain for keyword in REJECT_KEYWORDS):
            continue
        if re.fullmatch(r"0?\d{1,3}", text):
            continue
        parts.append(text)
    description = " ".join(parts)
    description = re.sub(r"\s+", " ", description).strip()
    return description if sum(char.isalpha() for char in description) >= 3 else ""


def _parse_money_or_number(text: str) -> float | None:
    clean = text.strip()
    if not clean:
        return None
    if not re.search(r"\d", clean):
        return None
    if re.search(r"[A-Za-z]{2,}", clean) and not re.search(r"[$â‚¬Â£]|\d+[,.]\d{2}\b", clean):
        return None
    match = re.search(r"[$â‚¬Â£]?\s*[-+]?\d+(?:[,.]\d+)?", clean)
    return parse_amount(match.group(0)) if match else None


def _is_summary_or_header_text(text: str) -> bool:
    plain = strip_accents(text).lower()
    return any(word in plain for word in ("description", "quantity", "price", "prix", "total", "subtotal", "sales tax", "shipping", "tva"))


def _center_x(block: OCRLine) -> float:
    return (block.bbox.x1 + block.bbox.x2) / 2


def _center_y(block: OCRLine) -> float:
    return (block.bbox.y1 + block.bbox.y2) / 2
def _group_blocks_by_row(blocks: list[OCRLine]) -> list[list[OCRLine]]:
    rows: dict[int, list[OCRLine]] = defaultdict(list)
    for block in blocks:
        key = round((block.bbox.y1 if block.bbox else 0) / 18)
        rows[key].append(block)
    return [sorted(values, key=lambda block: block.bbox.x1 if block.bbox else 0) for _, values in sorted(rows.items())]


def _is_table_header(lower_text: str) -> bool:
    header_words = ("description", "designation", "dÃƒÂ©signation", "qty", "qte", "quantity", "net price", "net worth", "gross", "vat", "tva")
    return sum(1 for word in header_words if word in lower_text) >= 2


def _looks_like_coordinate_item_row(text: str) -> bool:
    return bool(re.search(r"\b\d+[,.]?\d*\b", text) and len(re.findall(r"[+-]?\d[\d .,]*", text)) >= 3)


def _looks_like_description_fragment(text: str) -> bool:
    lower = strip_accents(text).lower()
    return len(text) > 8 and not any(keyword in lower for keyword in REJECT_KEYWORDS)


def _parse_coordinate_row(row_blocks: list[OCRLine], pending_description: str | None = None) -> LineItem | None:
    row_text = " ".join(block.text for block in row_blocks)
    if any(keyword in strip_accents(row_text).lower() for keyword in REJECT_KEYWORDS):
        return None
    numeric_blocks = [(block, parse_amount(block.text.replace("%", ""))) for block in row_blocks]
    numeric_blocks = [(block, value) for block, value in numeric_blocks if value is not None]
    if len(numeric_blocks) < 4:
        regex_item = parse_line_item(row_text)
        if regex_item and pending_description:
            regex_item.description = f"{pending_description} {regex_item.description}".strip()
        return regex_item

    text_blocks = [block.text for block in row_blocks if parse_amount(block.text.replace("%", "")) is None]
    description_parts = [
        text for text in text_blocks
        if not re.fullmatch(r"[A-Z]{1,4}|UM|VAT|TVA|No\.?|each|piece|pi[eÃƒÂ¨]ce|pcs?|unit|unite|unit[eÃƒÂ©]", text, re.IGNORECASE)
    ]
    description = " ".join(description_parts).strip()
    if pending_description:
        description = f"{pending_description} {description}".strip()
    if not description:
        return None

    values = [value for _block, value in numeric_blocks]
    if _looks_like_row_number(numeric_blocks, row_blocks):
        values = values[1:]
    quantity = values[0]
    unit_price = values[1] if len(values) > 1 else None
    line_total_ht = values[2] if len(values) > 2 else None
    tax_rate = _extract_row_tax_rate(row_text, values)
    line_total_ttc = values[-1]
    unit = _extract_unit(row_text)
    tax_amount = round(line_total_ttc - line_total_ht, 3) if line_total_ttc is not None and line_total_ht is not None and line_total_ttc >= line_total_ht else None
    boxes = [block.bbox for block in row_blocks if block.bbox]
    bbox = {
        "x1": min(box.x1 for box in boxes),
        "y1": min(box.y1 for box in boxes),
        "x2": max(box.x2 for box in boxes),
        "y2": max(box.y2 for box in boxes),
    } if boxes else None
    confidences = [block.confidence for block in row_blocks if block.confidence is not None]
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
    return LineItem(
        description=description,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        line_total_ht=line_total_ht,
        tax_amount=tax_amount,
        tax_rate=tax_rate,
        line_total_ttc=line_total_ttc,
        total=line_total_ttc,
        confidence=confidence,
        bbox=bbox,
        page=row_blocks[0].page_number if row_blocks else None,
        source="coordinate table row",
    )


def _extract_row_tax_rate(row_text: str, values: list[float]) -> float | None:
    percent = re.search(r"(\d{1,2}(?:[,.]\d+)?)\s*%", row_text)
    if percent:
        return parse_amount(percent.group(1))
    plausible = [value for value in values if 0 <= value <= 30]
    return plausible[-1] if plausible else None


def _extract_unit(row_text: str) -> str | None:
    match = re.search(r"\b(each|piece|pi[eÃƒÂ¨]ce|pcs?|unit|unite|unit[eÃƒÂ©])\b", row_text, re.IGNORECASE)
    return match.group(1) if match else None


def _looks_like_row_number(numeric_blocks: list[tuple[OCRLine, float]], row_blocks: list[OCRLine]) -> bool:
    if len(numeric_blocks) < 5:
        return False
    first_block, first_value = numeric_blocks[0]
    left_edges = [block.bbox.x1 for block in row_blocks if block.bbox]
    if not left_edges:
        return False
    row_left = min(left_edges)
    return (
        first_value is not None
        and float(first_value).is_integer()
        and first_value <= 200
        and first_block.bbox is not None
        and first_block.bbox.x1 <= row_left + 35
    )
