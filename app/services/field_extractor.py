import re
from typing import Any

from app.core.schemas import Candidate, DocumentClassification, ExtractedInvoiceFields, LineItem, OCRLine
from app.services.line_item_extractor import extract_line_items
from app.utils.helpers import first_match, normalize_text, parse_amount, parse_date, strip_accents


AMOUNT_VALUE = r"[+-]?(?:\d{1,3}(?:[ .]\d{3})+(?:[,.]\d{2,3})?|\d{1,3}(?:[.,]\d{3}){1,2}|\d+(?:[,.]\d{1,3})?)"
AMOUNT = rf"({AMOUNT_VALUE})"
MONEY_VALUE = r"(?:[$â‚¬]\s*)?[+-]?(?:\d{1,3}(?:[ .]\d{3})+(?:[,.]\d{2,3})?|\d{1,3}(?:[.,]\d{3}){1,2}|\d+(?:[,.]\d{1,3})?)"
DATE = r"(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}|\d{4}[\/\-.]\d{1,2}[\/\-.]\d{1,2})"
PRODUCT_CODE = r"[A-Z]{2,}[A-Z0-9]*-[A-Z0-9]+"
EMAIL_PATTERN = r"[\w.\-+]+@[\w.\-]+\.\w+"
PHONE_PATTERN = r"(?:\+?\d{1,3}[\s.-]?)?(?:\d[\s.-]?){6,}"
WEBSITE_PATTERN = r"(?:https?://)?(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/\S*)?"

STOP_LINE_ITEM_KEYWORDS = (
    "arrete", "conditions de paiement", "payment terms", "coordonnees bancaires",
    "bank details", "banque", "bank", "rib", "iban", "swift", "merci",
    "thank you", "subtotal", "sous-total", "total ht", "total ttc",
)
BAD_SUPPLIER_WORDS = (
    "facture", "invoice", "client", "date", "total", "tva", "vat", "ice", "mf",
    "email", "tel", "phone", "rib", "iban", "swift", "adresse", "address",
    "ref", "reference", "tax id", "tax", "seller", "supplier", "vendor", "bill to",
)


def extract_invoice_fields(text: str) -> ExtractedInvoiceFields:
    fields, _candidates, _confidences, _debug = extract_with_candidates(text)
    return fields


def extract_with_candidates(
    text: str,
    ocr_blocks: list[OCRLine] | None = None,
    classification: DocumentClassification | None = None,
) -> tuple[ExtractedInvoiceFields, dict[str, list[Candidate]], dict[str, float], dict[str, Any]]:
    normalized = normalize_text(text)
    plain = strip_accents(normalized)
    candidates = collect_field_candidates(normalized, ocr_blocks or [], classification)
    selected = _select_best_candidates(candidates)
    fields = ExtractedInvoiceFields()

    fields.supplier_name = _candidate_value(selected, "supplier_name") or _extract_supplier_name(normalized)
    fields.supplier_address = _candidate_value(selected, "supplier_address")
    fields.supplier_phone = _candidate_value(selected, "supplier_phone")
    fields.supplier_email = _candidate_value(selected, "supplier_email")
    fields.supplier_website = _candidate_value(selected, "supplier_website")
    fields.supplier_bank_iban = _candidate_value(selected, "supplier_bank_iban")
    fields.supplier_bank_rib = _candidate_value(selected, "supplier_bank_rib")
    fields.supplier_bank_swift = _candidate_value(selected, "supplier_bank_swift")
    fields.customer_name = _candidate_value(selected, "customer_name")
    fields.customer_address = _candidate_value(selected, "customer_address")
    fields.customer_phone = _candidate_value(selected, "customer_phone")
    fields.customer_email = _candidate_value(selected, "customer_email")
    fields.invoice_number = _candidate_value(selected, "invoice_number") or _extract_invoice_number(plain)
    fields.invoice_date = parse_date(_candidate_value(selected, "invoice_date") or _extract_invoice_date(plain))
    fields.due_date = parse_date(_candidate_value(selected, "due_date") or _extract_due_date(plain))
    fields.currency = _candidate_value(selected, "currency") or _extract_currency(normalized)
    fields.amount_ht = _candidate_value(selected, "amount_ht")
    if fields.amount_ht is None:
        fields.amount_ht = _extract_amount_ht(plain)
    fields.tva_amount = _candidate_value(selected, "tva_amount")
    if fields.tva_amount is None:
        fields.tva_amount = _extract_tva_amount(plain)
    fields.amount_ttc = _candidate_value(selected, "amount_ttc")
    if fields.amount_ttc is None:
        fields.amount_ttc = _extract_amount_ttc(plain)
    fields.tax_rate = _candidate_value(selected, "tax_rate") or _extract_tax_rate(plain, fields.amount_ht, fields.tva_amount)
    fields.purchase_order_number = _candidate_value(selected, "purchase_order_number") or _extract_purchase_order(plain)
    fields.supplier_tax_id = _candidate_value(selected, "supplier_tax_id") or _extract_supplier_tax_id(plain)
    fields.customer_tax_id = _candidate_value(selected, "customer_tax_id")
    fields.line_items = extract_line_items(normalized, ocr_blocks)
    confidences = {field: round(candidate.score, 3) for field, candidate in selected.items()}
    debug = {"candidates": {field: [candidate.model_dump(mode="json") for candidate in values] for field, values in candidates.items()}}
    return fields, candidates, confidences, debug


def collect_field_candidates(
    text: str,
    ocr_blocks: list[OCRLine] | None = None,
    classification: DocumentClassification | None = None,
) -> dict[str, list[Candidate]]:
    plain = strip_accents(text)
    candidates: dict[str, list[Candidate]] = {}

    def add(field: str, value: Any, score: float, source: str, block: OCRLine | None = None) -> None:
        if value is None or value == "":
            return
        candidates.setdefault(field, []).append(Candidate(
            field=field,
            value=value,
            score=max(0.0, min(1.0, score + ((block.confidence or 0) * 0.08 if block else 0))),
            source=source,
            page=block.page_number if block else None,
            line_index=block.line_index if block else None,
            bbox=block.bbox if block else None,
        ))

    regex_fields = {
        "invoice_number": _extract_invoice_number(plain),
        "invoice_date": _extract_invoice_date(plain),
        "due_date": _extract_due_date(plain),
        "currency": _extract_currency(text),
        "amount_ht": _extract_amount_ht(plain),
        "tva_amount": _extract_tva_amount(plain),
        "amount_ttc": _extract_amount_ttc(plain),
        "purchase_order_number": _extract_purchase_order(plain),
        "supplier_tax_id": _extract_supplier_tax_id(plain),
        "supplier_name": _extract_supplier_name(text),
    }
    for field, value in regex_fields.items():
        add(field, value, 0.72, "regex")
    tax_rate = _extract_tax_rate(plain, regex_fields.get("amount_ht"), regex_fields.get("tva_amount"))
    add("tax_rate", tax_rate, 0.70, "regex")

    for line_index, line in enumerate(text.splitlines()):
        line_plain = strip_accents(line)
        _add_line_candidates(add, line, line_plain, line_index)

    _add_multiline_candidates(add, text)
    for block in ocr_blocks or []:
        block_plain = strip_accents(block.text)
        _add_line_candidates(add, block.text, block_plain, block.line_index or 0, block)

    _add_block_sequence_candidates(add, ocr_blocks or [])
    _add_spatial_date_candidates(add, ocr_blocks or [])
    _add_stacked_totals_candidates(add, text, ocr_blocks or [])
    _add_party_candidates_from_blocks(add, ocr_blocks or [])
    _add_supplier_customer_candidates(add, text)
    _score_document_type_relevance(candidates, classification)
    return candidates


def _extract_invoice_number(text: str) -> str | None:
    return first_match([
        r"\binvoice\s*(?:number|num(?:ber)?|no\.?|n[oÂ°]?|#|ref(?:erence)?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9_\-\/.]{2,})",
        r"\bfacture\s*(?:n[oÂ°]?|num(?:ero)?|number|no\.?|#|ref(?:erence)?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9_\-\/.]{2,})",
        r"\bn\s*.?\s*facture\s*[:#-]?\s*([A-Z0-9][A-Z0-9_\-\/.]{2,})",
        r"\b(?:n[oÂ°]?|numero|ref(?:erence)?)\b\s*(?:facture|invoice)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9_\-\/.]{2,})",
    ], text)


def _extract_invoice_date(text: str) -> str | None:
    return first_match([
        rf"(?:date\s*(?:de\s*)?(?:facture|invoice)|invoice\s*date|billing\s*date|issue\s*date|date\s*of\s*issue|date\s*d['â€™]?\s*(?:emission|émission)|date\s*emission|تاريخ)\s*[:#-]?\s*{DATE}",
        rf"(?:facture\s*du|issued\s*on|emitted\s*on|emise\s*le)\s*[:#-]?\s*{DATE}",
        rf"^\s*date\s*[:#-]?\s*{DATE}",
    ], text)


def _extract_due_date(text: str) -> str | None:
    return first_match([
        rf"(?:echeance|date\s*d['â€™]?\s*echeance|due\s*date|payment\s*due|payable\s*by|date\s*limite)\s*[:#-]?\s*{DATE}",
    ], text)


def _extract_currency(text: str) -> str | None:
    upper = text.upper()
    for code in ("TND", "EUR", "USD", "GBP", "MAD", "DZD", "CAD", "CHF", "AED"):
        if re.search(rf"\b{code}\b", upper):
            return code
    if re.search(r"\bEURO(?:S)?\b", upper) or "â‚¬" in text or "Ã¢â€šÂ¬" in text:
        return "EUR"
    if "$" in text:
        return "USD"
    if re.search(r"\bDT\b|\bDNT\b|\bDINAR", upper):
        return "TND"
    return None


def _extract_amount_ht(text: str) -> float | None:
    value = _amount_after_label(text, [
        r"sous\s*[- ]?\s*total\s*HT", r"total\s*HT", r"montant\s*HT",
        r"base\s*HT", r"hors\s*taxe", r"hors\s*taxes", r"net\s*HT",
        r"subtotal", r"sub\s*total", r"net\s*worth", r"net", r"amount\s*excl\.?\s*tax",
        r"amount\s*excluding\s*tax", r"taxable\s*amount", r"net\s*amount",
        r"untaxed\s*amount", r"before\s*tax",
    ])
    return parse_amount(value)


def _extract_tva_amount(text: str) -> float | None:
    value = _amount_after_label(text, [
        r"TVA\s*\(?\s*\d{1,2}(?:[,.]\d{1,2})?\s*%\s*\)?",
        r"VAT\s*\(?\s*\d{1,2}(?:[,.]\d{1,2})?\s*%\s*\)?",
        r"montant\s*TVA", r"taxe\s*sur\s*la\s*valeur\s*ajoutee",
        r"TVA", r"VAT", r"tax\s*amount", r"sales\s*tax", r"total\s*tax",
    ], prefer_last=True)
    return parse_amount(value)


def _extract_amount_ttc(text: str) -> float | None:
    value = _amount_after_label(text, [
        r"total\s*TTC", r"montant\s*TTC", r"total\s*a\s*payer",
        r"net\s*a\s*payer", r"montant\s*total", r"total\s*facture",
        r"total\s*net", r"total\s*amount", r"grand\s*total",
        r"amount\s*incl\.?\s*tax", r"amount\s*including\s*tax",
        r"balance\s*due", r"amount\s*due", r"gross\s*worth", r"gross", r"invoice\s*total",
    ], prefer_last=True)
    if value is None:
        value = first_match([
            rf"\(({AMOUNT_VALUE})\s*(?:TND|EUR|USD|GBP|MAD|DZD|DT)\)",
        ], text)
    return parse_amount(value)


def _extract_tax_rate(text: str, amount_ht: float | None, tva_amount: float | None) -> float | None:
    rate_text = first_match([
        r"(?:TVA|VAT|tax\s*rate|taux\s*(?:TVA|taxe)?|tax)\D{0,20}(\d{1,2}(?:[,.]\d{1,2})?)\s*%",
        r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%\s*(?:TVA|VAT|tax)",
    ], text)
    rate = parse_amount(rate_text)
    if rate is not None:
        return rate
    if amount_ht and tva_amount is not None and amount_ht > 0:
        return round((tva_amount / amount_ht) * 100, 2)
    return None


def _extract_purchase_order(text: str) -> str | None:
    return first_match([
        r"(?:purchase\s*order|po\s*(?:number|no\.?|#)?|bon\s*de\s*commande|commande|order\s*(?:number|no\.?)?)\s*[:#-]?\s*([A-Z0-9_\-\/.]{3,})",
    ], text)


def _extract_supplier_tax_id(text: str) -> str | None:
    return first_match([
        r"\b(?:matricule\s*fiscal|tax\s*id|taxpayer\s*id|vat\s*(?:number|no\.?)|identifiant\s*fiscal|MF|ICE|RC)\b\s*[:#-]?\s*([A-Z0-9\/\-.]{5,})",
        r"\b([0-9]{7,8}[A-Z]{1,3}[A-Z0-9\/\-.]*)\b",
    ], text)


def _extract_supplier_name(original: str) -> str | None:
    explicit = first_match([
        r"(?:fournisseur|supplier|vendor)\s*[:#-]?\s*([^\n]{2,80})",
    ], original)
    if explicit:
        return _clean_name(explicit)

    lines = [_clean_name(line) for line in original.splitlines() if _clean_name(line)]
    for index, line in enumerate(lines[:24]):
        candidate = _remove_after_keywords(line, [
            r"date\s*.*",
            r"date\s*d.?\s*echeance", r"date\s*d.?\s*Ã©chÃ©ance",
            r"invoice\s*date", r"due\s*date", r"date\s*:",
        ])
        candidate_plain = strip_accents(candidate).lower()
        next_plain = strip_accents(lines[index + 1]).lower() if index + 1 < len(lines) else ""
        if _is_supplier_candidate(candidate_plain, candidate):
            if any(word in next_plain for word in ("rue", "avenue", "street", "road", "tel", "tÃ©l", "phone", "mf", "tax", "ice")):
                return _clean_name(candidate)

    for line in lines[:12]:
        candidate = _remove_after_keywords(line, [r"date\s*:", r"due\s*date"])
        candidate_plain = strip_accents(candidate).lower()
        if _is_supplier_candidate(candidate_plain, candidate):
            return _clean_name(candidate)
    return None


def _amount_after_label(text: str, labels: list[str], prefer_last: bool = False) -> str | None:
    for line in text.splitlines():
        line_plain = strip_accents(line)
        if not any(re.search(label, line_plain, re.IGNORECASE) for label in labels):
            continue
        amounts = re.findall(AMOUNT_VALUE, line)
        amounts = [amount for amount in amounts if not _looks_like_percent_rate(line, amount)]
        if amounts:
            return amounts[-1] if prefer_last else amounts[0]

    patterns = [
        rf"{label}\s*[:#=\-â€“â€”]?\s*(?:\(?\s*\d{{1,2}}(?:[,.]\d{{1,2}})?\s*%\s*\)?\s*)?(?:[A-Z]{{3}}\s*)?({AMOUNT_VALUE})"
        for label in labels
    ]
    return first_match(patterns, text)


def _looks_like_percent_rate(line: str, amount: str) -> bool:
    return bool(re.search(rf"\b{re.escape(amount)}\s*%", line))


def _extract_line_items(text: str) -> list[LineItem]:
    items: list[LineItem] = []
    stopped = False
    for line in text.splitlines():
        if len(items) >= 20:
            break
        clean_line = _clean_name(line)
        lower = strip_accents(clean_line).lower()
        if any(keyword in lower for keyword in STOP_LINE_ITEM_KEYWORDS):
            stopped = True
        if stopped or not clean_line:
            continue
        item = _parse_line_item(clean_line)
        if item:
            items.append(item)
    return items


def _parse_line_item(line: str) -> LineItem | None:
    if re.search(r"\b(?:RIB|IBAN|SWIFT|BANQUE|BANK)\b", line, re.IGNORECASE):
        return None
    product_code = re.search(PRODUCT_CODE, line)
    if not product_code:
        return None

    before_code = line[:product_code.start()].strip(" |;:-0123456789#[]")
    after_code = line[product_code.end():]
    numbers = re.findall(AMOUNT_VALUE, after_code)
    if len(numbers) < 4:
        return None

    quantity, unit_price, tax_rate, total = numbers[-4:]
    if parse_amount(tax_rate) is None or parse_amount(tax_rate) > 100:
        return None
    return LineItem(
        description=before_code or product_code.group(0),
        quantity=parse_amount(quantity),
        unit_price=parse_amount(unit_price),
        total=parse_amount(total),
    )


def _is_supplier_candidate(candidate_plain: str, candidate: str) -> bool:
    if len(candidate) < 3 or len(candidate) > 90:
        return False
    if re.match(r"^\d", candidate):
        return False
    if re.fullmatch(r"[A-Z]{2,5}[-_/]?\d+[A-Z0-9-_/]*", candidate.strip(), re.IGNORECASE):
        return False
    if sum(char.isalpha() for char in candidate) < 4:
        return False
    if any(re.search(rf"\b{re.escape(word)}\b", candidate_plain) for word in BAD_SUPPLIER_WORDS):
        return False
    if re.search(r"\b(?:rue|avenue|street|road|route|apt|suite|city|state|zip|postal|tunisie|tunis|sfax|ariana)\b", candidate_plain):
        return False
    if re.fullmatch(r"[A-Z\s]{3,}", candidate) and len(candidate.split()) == 1:
        return False
    return bool(re.search(r"[^\W\d_]{3,}", candidate, re.UNICODE))


def _remove_after_keywords(line: str, keywords: list[str]) -> str:
    result = strip_accents(line)
    for keyword in keywords:
        result = re.split(keyword, result, maxsplit=1, flags=re.IGNORECASE)[0]
    return _clean_name(result)


def _clean_name(name: str) -> str:
    return re.sub(r"\s{2,}", " ", name).strip(" :-")


def _add_line_candidates(add, line: str, line_plain: str, line_index: int, block: OCRLine | None = None) -> None:
    labels = strip_accents(line_plain).lower()
    date_match = re.search(DATE, line_plain)
    if date_match:
        if any(key in labels for key in ("echeance", "due date", "date limite", "Ø§Ø³ØªØ­Ù‚Ø§Ù‚")):
            add("due_date", date_match.group(1), 0.80, "date near due-date label", block)
        elif "date" in labels or "Ø§Ù„ØªØ§Ø±ÙŠØ®" in labels:
            add("invoice_date", date_match.group(1), 0.78, "date near date label", block)

    if any(key in labels for key in ("facture", "invoice", "n bl", "nÂ° bl", "Ø±Ù‚Ù… Ø§Ù„ÙØ§ØªÙˆØ±Ø©")):
        add("invoice_number", _extract_invoice_number(line_plain) or _document_number_from_line(line_plain), 0.86, "number near document label", block)
    if any(key in labels for key in ("commande", "purchase order", "po number", "Ø·Ù„Ø¨ Ø´Ø±Ø§Ø¡")):
        add("purchase_order_number", _extract_purchase_order(line_plain), 0.82, "order reference label", block)

    if any(key in labels for key in ("sous-total", "total ht", "subtotal", "hors taxe", "htva")):
        add("amount_ht", _last_amount(line), 0.86, "amount near HT/subtotal label", block)
    if any(key in labels for key in ("tva", "vat", "tax amount", "montant tva", "Ø¶Ø±ÙŠØ¨Ø©")):
        add("tva_amount", _last_non_percent_amount(line), 0.82, "amount near tax label", block)
        add("tax_rate", parse_amount(first_match([r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%"], line)), 0.78, "tax rate percent", block)
    is_total_line = any(key in labels for key in ("total ttc", "montant ttc", "grand total", "amount due", "ttc", "Ø§Ù„Ø¥Ø¬Ù…Ø§Ù„ÙŠ", "Ø§Ù„Ù…Ø¬Ù…ÙˆØ¹"))
    if is_total_line:
        add("amount_ttc", _last_amount(line), 0.90, "amount near TTC/total label", block)

    currency = _extract_currency(line)
    if currency:
        add("currency", currency, 0.82 if is_total_line else 0.58, "currency near totals" if is_total_line else "currency token", block)


def _add_supplier_customer_candidates(add, text: str) -> None:
    lines = [_clean_name(line) for line in text.splitlines() if _clean_name(line)]
    _add_party_block_candidates(add, lines, "supplier", _find_first_party_line(lines, ("seller", "supplier", "vendor", "from", "bill from", "fournisseur", "vendeur", "Ø§Ù„Ù…ÙˆØ±Ø¯", "Ø§Ù„Ù…Ø²ÙˆØ¯")))
    _add_party_block_candidates(add, lines, "customer", _find_first_party_line(lines, ("client", "customer", "bill to", "billed to", "ship to", "acheteur", "livre a", "livrÃƒÂ© a", "livre ÃƒÂ ", "Ø§Ù„Ø¹Ù…ÙŠÙ„")))
    client_start = _find_first_client_line(lines)
    supplier_text = "\n".join(lines[:client_start]) if client_start is not None else "\n".join(lines[:20])
    customer_text = "\n".join(lines[client_start:client_start + 14]) if client_start is not None else ""
    supplier_tax_id = _extract_supplier_tax_id(supplier_text)
    customer_tax_id = _extract_supplier_tax_id(customer_text)
    add("supplier_tax_id", supplier_tax_id, 0.82, "supplier block tax id")
    if customer_tax_id and customer_tax_id != supplier_tax_id:
        add("customer_tax_id", customer_tax_id, 0.82, "customer/client block tax id")
    for index, line in enumerate(lines[:35]):
        plain = strip_accents(line).lower()
        if any(marker in plain for marker in ("client", "customer", "livre a", "livrÃ© a", "livre Ã ", "Ø§Ù„Ø¹Ù…ÙŠÙ„")):
            for candidate in lines[index + 1:index + 5]:
                candidate_plain = strip_accents(candidate).lower()
                if _is_supplier_candidate(candidate_plain, candidate):
                    add("customer_name", candidate, 0.80, "near customer/client label")
                    break
        if index < 18 and _is_supplier_candidate(plain, line):
            next_lines = " ".join(strip_accents(value).lower() for value in lines[index + 1:index + 4])
            score = 0.80 if any(word in next_lines for word in ("rue", "avenue", "tel", "mf", "ice", "email", "tax")) else 0.58
            add("supplier_name", line, score, "top/header company block")


def _add_party_block_candidates(add, lines: list[str], role: str, start: int | None) -> None:
    if start is None:
        return
    label_line = lines[start]
    label_remainder = re.sub(
        r"^(?:seller|supplier|vendor|from|bill\s*from|fournisseur|vendeur|client|customer|bill\s*to|billed\s*to|ship\s*to|acheteur|livre\s*a|livre\s*Ã )\s*[:#-]?\s*",
        "",
        label_line,
        flags=re.IGNORECASE,
    ).strip()
    block = ([label_remainder] if label_remainder and label_remainder != label_line else []) + lines[start + 1:start + 8]
    name = None
    address_lines: list[str] = []
    tax_id = None
    for line in block:
        plain = strip_accents(line).lower()
        if _is_party_label(plain):
            break
        if not tax_id and any(key in plain for key in ("tax id", "taxid", "matricule", "mf", "ice", "identifiant")):
            tax_id = _extract_supplier_tax_id(line)
            continue
        if any(key in plain for key in ("email", "tel", "phone", "invoice", "facture", "total")):
            continue
        if name is None and _is_company_name_line(line):
            name = line
            continue
        if name and _looks_like_address_line(line):
            address_lines.append(line)
    if name:
        add(f"{role}_name", name, 0.90, f"{role} label block")
    if address_lines:
        add(f"{role}_address", ", ".join(address_lines), 0.82, f"{role} address block")
    if tax_id:
        add(f"{role}_tax_id", tax_id, 0.88, f"{role} tax id block")


def _add_multiline_candidates(add, text: str) -> None:
    lines = [_clean_name(line) for line in text.splitlines() if _clean_name(line)]
    for index, line in enumerate(lines[:-1]):
        label = strip_accents(line).lower()
        following = " ".join(lines[index + 1:index + 3])
        if _is_invoice_date_label(label):
            date_value = first_match([DATE], line) or first_match([DATE], following)
            add("invoice_date", date_value, 0.86, "date label followed by value")
        elif any(key in label for key in ("due date", "echeance", "date limite", "payment due")):
            date_value = first_match([DATE], line) or first_match([DATE], following)
            add("due_date", date_value, 0.84, "due-date label followed by value")
        elif any(key in label for key in ("facture n", "invoice number", "invoice no", "n facture")):
            add("invoice_number", _document_number_from_line(following), 0.84, "document number label followed by value")


def _add_block_sequence_candidates(add, blocks: list[OCRLine]) -> None:
    ordered = sorted(blocks, key=lambda block: (block.page_number, block.line_index if block.line_index is not None else 10_000))
    for index, block in enumerate(ordered[:-1]):
        label = strip_accents(block.text).lower()
        next_text = " ".join(next_block.text for next_block in ordered[index + 1:index + 3])
        if _is_invoice_date_label(label):
            add("invoice_date", first_match([DATE], block.text) or first_match([DATE], next_text), 0.88, "OCR block date label followed by value", block)
        elif any(key in label for key in ("due date", "echeance", "date limite", "payment due")):
            add("due_date", first_match([DATE], block.text) or first_match([DATE], next_text), 0.86, "OCR block due-date label followed by value", block)


def _add_stacked_totals_candidates(add, text: str, blocks: list[OCRLine]) -> None:
    lines = [_clean_name(line) for line in text.splitlines() if _clean_name(line)]
    for index, line in enumerate(lines):
        label = strip_accents(line).lower()
        if label not in {"total", "totals"} and not any(key in label for key in ("net worth", "gross worth", "total htva", "total h.t")):
            continue
        window = " ".join(lines[index + 1:index + 8])
        amounts = _money_values(window)
        if len(amounts) >= 3:
            add("amount_ht", amounts[0], 0.82, "stacked totals first amount")
            add("tva_amount", amounts[1], 0.82, "stacked totals middle amount")
            add("amount_ttc", amounts[-1], 0.86, "stacked totals rightmost/gross amount")
            if amounts[0]:
                add("tax_rate", round((amounts[1] / amounts[0]) * 100, 2), 0.80, "stacked totals inferred tax rate")
            if "$" in window:
                add("currency", "USD", 0.86, "currency in stacked totals")
            return
    bottom_right = [block for block in blocks if block.bbox and block.bbox.x1 > 450 and block.bbox.y1 > 500]
    amounts = [parse_amount(block.text) for block in bottom_right if parse_amount(block.text) is not None]
    if len(amounts) >= 3:
        amounts = sorted(amounts)
        add("amount_ht", amounts[-3], 0.70, "bottom-right totals cluster")
        add("tva_amount", amounts[-2], 0.70, "bottom-right totals cluster")
        add("amount_ttc", amounts[-1], 0.76, "bottom-right totals cluster")
        if amounts[-3]:
            add("tax_rate", round((amounts[-2] / amounts[-3]) * 100, 2), 0.66, "bottom-right totals inferred tax rate")



def _add_spatial_date_candidates(add, blocks: list[OCRLine]) -> None:
    ordered = sorted([block for block in blocks if block.bbox], key=lambda block: (block.page_number, block.bbox.y1, block.bbox.x1))
    for index, block in enumerate(ordered):
        label = strip_accents(block.text).lower().strip(" :#-")
        if not (_is_invoice_date_label(label) or any(key in label for key in ("issue date", "date of issue", "date emission", "date d emission"))):
            continue
        inline = first_match([DATE], block.text)
        if inline:
            add("invoice_date", inline, 0.90, "spatial date label same OCR block", block)
            continue
        candidates = []
        for other in ordered[index + 1:index + 8]:
            if other.page_number != block.page_number:
                continue
            date_value = first_match([DATE], other.text)
            if not date_value:
                continue
            same_column = abs(other.bbox.x1 - block.bbox.x1) < 160
            right_or_below = other.bbox.x1 >= block.bbox.x1 - 20 and other.bbox.y1 >= block.bbox.y1 - 10
            if same_column or right_or_below:
                distance = abs(other.bbox.y1 - block.bbox.y1) + max(0, other.bbox.x1 - block.bbox.x2) * 0.3
                candidates.append((distance, date_value, other))
        if candidates:
            _distance, value, source_block = sorted(candidates, key=lambda item: item[0])[0]
            add("invoice_date", value, 0.89, "spatial date label nearest value", source_block)


def _add_party_candidates_from_blocks(add, blocks: list[OCRLine]) -> None:
    if not blocks:
        return
    ordered = sorted([block for block in blocks if block.bbox], key=lambda block: (block.page_number, block.bbox.y1, block.bbox.x1))
    labels = {
        "supplier": ("supplier", "seller", "vendor", "from", "bill from", "fournisseur", "vendeur", "المورد"),
        "customer": ("customer", "client", "bill to", "ship to", "acheteur", "livre a", "livré a", "العميل"),
    }
    for role, role_labels in labels.items():
        for index, block in enumerate(ordered):
            plain = strip_accents(block.text).lower().strip(" :#-")
            if not any(label in plain for label in role_labels):
                continue
            window = _party_window_after_label(ordered, index)
            _add_party_window_candidates(add, role, window)
            break


def _party_window_after_label(blocks: list[OCRLine], start_index: int) -> list[OCRLine]:
    label_block = blocks[start_index]
    if not label_block.bbox:
        return blocks[start_index + 1:start_index + 14]

    label_center_x = (label_block.bbox.x1 + label_block.bbox.x2) / 2
    page_blocks = [block for block in blocks if block.page_number == label_block.page_number and block.bbox]
    max_x = max((block.bbox.x2 for block in page_blocks), default=label_block.bbox.x2)
    column_tolerance = max(180, max_x * 0.28)
    below_same_column: list[OCRLine] = []
    for block in page_blocks:
        if block is label_block or block.bbox.y1 <= label_block.bbox.y1 + 5:
            continue
        block_center_x = (block.bbox.x1 + block.bbox.x2) / 2
        if abs(block_center_x - label_center_x) > column_tolerance:
            continue
        below_same_column.append(block)

    window: list[OCRLine] = []
    for block in sorted(below_same_column, key=lambda value: (value.bbox.y1, value.bbox.x1))[:14]:
        plain = strip_accents(block.text).lower().strip(" :#-")
        if _is_party_label(plain) or any(key in plain for key in ("invoice", "facture", "total", "subtotal", "sous-total")):
            break
        window.append(block)
    return window


def _add_party_window_candidates(add, role: str, window: list[OCRLine]) -> None:
    name = None
    address_lines: list[str] = []
    for block in window:
        line = _clean_name(block.text)
        plain = strip_accents(line).lower()
        if not line:
            continue
        if email := first_match([EMAIL_PATTERN], line):
            add(f"{role}_email", email, 0.86, f"{role} email in labeled block", block)
            continue
        if any(key in plain for key in ("tax id", "taxid", "matricule", "mf", "ice", "identifiant", "vat")):
            tax_id = _extract_supplier_tax_id(line)
            if tax_id:
                add(f"{role}_tax_id", tax_id, 0.90, f"{role} tax id in labeled block", block)
            continue
        if phone := _extract_phone(line):
            add(f"{role}_phone", phone, 0.80, f"{role} phone in labeled block", block)
            continue
        if website := _extract_website(line):
            add(f"{role}_website", website, 0.76, f"{role} website in labeled block", block)
            continue
        if any(key in plain for key in ("iban", "rib", "swift", "bic")):
            _add_bank_candidates(add, role, line, block)
            continue
        if name is None and _is_company_name_line(line):
            name = line
            add(f"{role}_name", name, 0.92, f"{role} name in labeled block", block)
            continue
        if name and _looks_like_address_line(line):
            address_lines.append(line)
    if address_lines:
        add(f"{role}_address", ", ".join(address_lines), 0.84, f"{role} address in labeled block")


def _add_bank_candidates(add, role: str, line: str, block: OCRLine | None = None) -> None:
    if iban := first_match([r"\b([A-Z]{2}\d{2}[\sA-Z0-9]{8,40})"], line):
        add(f"{role}_bank_iban", iban, 0.82, f"{role} IBAN in labeled block", block)
    if rib := first_match([r"\bRIB\s*[:\-]?\s*([A-Z0-9\s]{10,40})"], line):
        add(f"{role}_bank_rib", rib, 0.82, f"{role} RIB in labeled block", block)
    if swift := first_match([r"\b(?:SWIFT|BIC)\s*[:\-]?\s*([A-Z0-9]{6,12})"], line):
        add(f"{role}_bank_swift", swift, 0.82, f"{role} SWIFT in labeled block", block)


def _extract_phone(line: str) -> str | None:
    if not any(char.isdigit() for char in line):
        return None
    plain = strip_accents(line).lower()
    if any(key in plain for key in ("invoice", "facture", "date", "total", "iban", "rib", "ice", "mf", "tax", "vat", "matricule", "identifiant")):
        return None
    return first_match([PHONE_PATTERN], line)


def _extract_website(line: str) -> str | None:
    if "@" in line:
        return None
    return first_match([WEBSITE_PATTERN], line)
def _score_document_type_relevance(candidates: dict[str, list[Candidate]], classification: DocumentClassification | None) -> None:
    if not classification:
        return
    if classification.document_type == "delivery_note":
        for candidate in candidates.get("invoice_number", []):
            if str(candidate.value).upper().startswith(("BL", "DN")):
                candidate.score += 0.10
    if classification.document_type != "invoice":
        for field in ("amount_ht", "tva_amount", "amount_ttc", "tax_rate"):
            for candidate in candidates.get(field, []):
                candidate.score -= 0.08


def _select_best_candidates(candidates: dict[str, list[Candidate]]) -> dict[str, Candidate]:
    selected: dict[str, Candidate] = {}
    for field, values in candidates.items():
        if not values:
            continue
        if field == "currency":
            selected[field] = _select_currency_candidate(values)
        else:
            selected[field] = sorted(values, key=lambda candidate: candidate.score, reverse=True)[0]
    return selected


def _select_currency_candidate(values: list[Candidate]) -> Candidate:
    grouped: dict[str, float] = {}
    representatives: dict[str, Candidate] = {}
    for candidate in values:
        value = str(candidate.value).upper()
        context_bonus = 0.20 if "total" in candidate.source.lower() else 0.0
        grouped[value] = grouped.get(value, 0.0) + candidate.score + context_bonus
        current = representatives.get(value)
        if current is None or candidate.score > current.score:
            representatives[value] = candidate
    winner = max(grouped, key=grouped.get)
    candidate = representatives[winner]
    candidate.score = min(1.0, max(candidate.score, grouped[winner] / max(1, len(values))))
    return candidate


def _candidate_value(selected: dict[str, Candidate], field: str):
    candidate = selected.get(field)
    return candidate.value if candidate else None


def _last_amount(line: str) -> float | None:
    amounts = re.findall(AMOUNT_VALUE, line)
    return parse_amount(amounts[-1]) if amounts else None


def _last_non_percent_amount(line: str) -> float | None:
    amounts = [amount for amount in re.findall(AMOUNT_VALUE, line) if not _looks_like_percent_rate(line, amount)]
    return parse_amount(amounts[-1]) if amounts else None


def _document_number_from_line(line: str) -> str | None:
    match = re.search(r"\b((?:FAC|INV|BL|DN|AV|PO|CMD)[-_]?\d{2,}[-_/]?\d*)\b", line, re.IGNORECASE)
    return match.group(1) if match else None


def _find_first_client_line(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        plain = strip_accents(line).lower()
        if any(marker in plain for marker in ("client", "customer", "livre a", "livrÃƒÂ© a", "livre ÃƒÂ ", "Ø§Ù„Ø¹Ù…ÙŠÙ„")):
            return index
    return None


def _find_first_party_line(lines: list[str], labels: tuple[str, ...]) -> int | None:
    for index, line in enumerate(lines):
        plain = strip_accents(line).lower().rstrip(":")
        if any(label in plain for label in labels):
            return index
    return None


def _is_party_label(plain: str) -> bool:
    normalized = plain.strip().rstrip(":")
    return any(
        label == normalized or normalized.startswith(f"{label}:")
        for label in ("seller", "supplier", "vendor", "from", "bill from", "fournisseur", "vendeur", "client", "customer", "bill to", "billed to", "ship to", "acheteur", "livre a", "livre Ã ")
    )


def _is_company_name_line(line: str) -> bool:
    plain = strip_accents(line).lower()
    if not _is_supplier_candidate(plain, line):
        return False
    return not _looks_like_address_line(line)


def _looks_like_address_line(line: str) -> bool:
    plain = strip_accents(line).lower()
    return bool(
        re.search(r"\d", line)
        and (
            re.search(r"\b(street|st\.?|road|rd\.?|avenue|ave\.?|prairie|summit|apt|suite|rue|route|km|lake|north|south|east|west)\b", plain)
            or re.search(r"\b[A-Z]{2}\s+\d{5}\b", line)
        )
    )


def _is_invoice_date_label(label: str) -> bool:
    return any(key in label for key in ("date of issue", "invoice date", "date facture", "date d'emission", "date d emission", "ØªØ§Ø±ÙŠØ®")) or label == "date"


def _money_values(text: str) -> list[float]:
    values = []
    for raw in re.findall(MONEY_VALUE, text):
        value = parse_amount(raw)
        if value is not None:
            values.append(value)
    return values







