import re
from collections import defaultdict

from app.core.schemas import LineItem, OCRLine
from app.utils.helpers import parse_amount, strip_accents


PRODUCT_CODE = r"[A-Z]{2,}[A-Z0-9]*-[A-Z0-9]+"
STOP_KEYWORDS = (
    "arrete", "conditions de paiement", "coordonnees bancaires", "banque", "rib",
    "iban", "swift", "payment terms", "bank details", "thank you", "merci",
    "Ø´ÙƒØ±Ø§", "Ø§Ù„Ø¨Ù†Ùƒ", "Ø§Ù„Ø­Ø³Ø§Ø¨ Ø§Ù„Ø¨Ù†ÙƒÙŠ",
)
REJECT_KEYWORDS = (
    "rib", "iban", "swift", "banque", "bank", "email", "tel", "address",
    "adresse", "mf", "ice", "phone", "Ø±Ù‚Ù… Ø§Ù„Ù‡Ø§ØªÙ", "Ø§Ù„Ø¨Ø±ÙŠØ¯ Ø§Ù„Ø¥Ù„ÙƒØªØ±ÙˆÙ†ÙŠ",
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

def _group_blocks_by_row(blocks: list[OCRLine]) -> list[list[OCRLine]]:
    rows: dict[int, list[OCRLine]] = defaultdict(list)
    for block in blocks:
        key = round((block.bbox.y1 if block.bbox else 0) / 18)
        rows[key].append(block)
    return [sorted(values, key=lambda block: block.bbox.x1 if block.bbox else 0) for _, values in sorted(rows.items())]


def _is_table_header(lower_text: str) -> bool:
    header_words = ("description", "designation", "dÃ©signation", "qty", "qte", "quantity", "net price", "net worth", "gross", "vat", "tva")
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
        if not re.fullmatch(r"[A-Z]{1,4}|UM|VAT|TVA|No\.?|each|piece|pi[eÃ¨]ce|pcs?|unit|unite|unit[eÃ©]", text, re.IGNORECASE)
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
    match = re.search(r"\b(each|piece|pi[eÃ¨]ce|pcs?|unit|unite|unit[eÃ©])\b", row_text, re.IGNORECASE)
    return match.group(1) if match else None


def _looks_like_row_number(numeric_blocks: list[tuple[OCRLine, float]], row_blocks: list[OCRLine]) -> bool:
    if len(numeric_blocks) < 5:
        return False
    first_block, first_value = numeric_blocks[0]
    row_left = min(block.bbox.x1 for block in row_blocks if block.bbox)
    return (
        first_value is not None
        and float(first_value).is_integer()
        and first_value <= 200
        and first_block.bbox is not None
        and first_block.bbox.x1 <= row_left + 35
    )




