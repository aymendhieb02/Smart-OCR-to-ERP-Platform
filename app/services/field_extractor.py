import re
from datetime import datetime
from typing import Any

from app.core.schemas import Candidate, DocumentClassification, ExtractedInvoiceFields, LineItem, OCRLine
from app.services.correction_store import boost_candidates_from_memory
from app.services.confidence_normalizer import normalize_confidence
from app.services.document_layout import analyze_document_layout, build_table_extraction_debug, reconstruct_tables, group_ocr_lines
from app.services.graph_field_extractor import add_graph_field_candidates, build_graph_debug
from app.services.line_item_extractor import extract_line_items
from app.services.party_resolver import party_adjusted_score, resolve_parties
from app.services.semantic_classifier import is_company_candidate_text, is_forbidden_party_name
from app.utils.helpers import first_match, normalize_text, parse_amount, parse_date, strip_accents


AMOUNT_VALUE = r"[+-]?(?:\d[\d .]*[,.]\d{2,3}|\d{1,3}(?:[ .]\d{3})+|\d+)"
AMOUNT = rf"({AMOUNT_VALUE})"
MONEY_VALUE = r"(?:[$€£]\s*)?[+-]?(?:\d[\d .]*[,.]\d{2,3}|\d{1,3}(?:[ .]\d{3})+|\d+)"
MONTH_NAME = r"(?:jan(?:uary|vier)?|feb(?:ruary)?|fevr(?:ier)?|f[eé]vr(?:ier)?|mar(?:ch|s)?|apr(?:il)?|avril|may|mai|jun(?:e)?|juin|jul(?:y)?|juillet|aug(?:ust)?|aout|ao[uû]t|sep(?:t(?:ember)?)?|septembre|oct(?:ober|obre)?|nov(?:ember|embre)?|dec(?:ember)?|decembre|d[eé]cembre)\.?"
DATE_VALUE = rf"(?:\d{{1,2}}[\/\-.]\d{{1,2}}[\/\-.]\d{{2,4}}|\d{{4}}[\/\-.]\d{{1,2}}[\/\-.]\d{{1,2}}|{MONTH_NAME}\s+\d{{1,2}},?\s+\d{{2,4}}|\d{{1,2}}\s+{MONTH_NAME},?\s+\d{{2,4}})"
DATE = rf"({DATE_VALUE})"
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
NUMBER_LABEL = r"(?:number|num(?:e|\u00e9)ro|numero|no\.?|n\.|n\s*(?:[\u00b0\u00ba]|o)?|#)"


def extract_invoice_fields(text: str) -> ExtractedInvoiceFields:
    fields, _candidates, _confidences, _debug = extract_with_candidates(text)
    return fields


def extract_with_candidates(
    text: str,
    ocr_blocks: list[OCRLine] | None = None,
    classification: DocumentClassification | None = None,
    timing_recorder=None,
) -> tuple[ExtractedInvoiceFields, dict[str, list[Candidate]], dict[str, float], dict[str, Any]]:
    normalized = normalize_text(text)
    plain = strip_accents(normalized)
    with _timer_stage(timing_recorder, "candidate_generation"):
        candidates = collect_field_candidates(normalized, ocr_blocks or [], classification, timing_recorder=timing_recorder)
    selected = _select_best_candidates(candidates)
    with _timer_stage(timing_recorder, "supplier_extraction", part="party_resolver"):
        party_decision = resolve_parties(candidates)
    with _timer_stage(timing_recorder, "customer_extraction", part="party_resolver"):
        pass
    if party_decision.supplier:
        selected["supplier_name"] = party_decision.supplier
        candidates.setdefault("supplier_name", []).append(party_decision.supplier)
    if party_decision.customer:
        selected["customer_name"] = party_decision.customer
        candidates.setdefault("customer_name", []).append(party_decision.customer)
    _prefer_consistent_total_candidates(selected, candidates)
    _resolve_date_collisions(selected)
    _separate_party_candidates(selected)
    fields = ExtractedInvoiceFields()

    fields.supplier_name = _candidate_value(selected, "supplier_name") or (None if ocr_blocks else _extract_supplier_name(normalized))
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
    with _timer_stage(timing_recorder, "line_item_reconstruction"):
        fields.line_items = extract_line_items(normalized, ocr_blocks)
    confidences = {
        field: normalize_confidence(candidate.score, selected_value=_candidate_value(selected, field))
        for field, candidate in selected.items()
    }
    confidences = {field: value for field, value in confidences.items() if value is not None}
    with _timer_stage(timing_recorder, "document_graph", part="debug"):
        graph_debug = build_graph_debug(ocr_blocks or [])
    with _timer_stage(timing_recorder, "table_detection", part="debug"):
        table_debug = build_table_extraction_debug(ocr_blocks or [])
    debug = {"candidates": {field: [candidate.model_dump(mode="json") for candidate in values] for field, values in candidates.items()}}
    debug.update(graph_debug)
    debug["party_resolver"] = party_decision.debug
    debug["party_resolution_trace"] = party_decision.debug
    debug["party_candidate_ranking"] = party_decision.debug.get("all_ranked_candidates", [])
    debug["table_extraction_debug"] = table_debug
    debug["field_traces"] = _build_field_traces(fields, candidates, selected)
    return fields, candidates, confidences, debug


def collect_field_candidates(
    text: str,
    ocr_blocks: list[OCRLine] | None = None,
    classification: DocumentClassification | None = None,
    timing_recorder=None,
) -> dict[str, list[Candidate]]:
    plain = strip_accents(text)
    candidates: dict[str, list[Candidate]] = {}

    def add(field: str, value: Any, score: float, source: str, block: OCRLine | None = None, score_breakdown: dict[str, float] | None = None) -> None:
        if value is None or value == "":
            return
        if field in {"invoice_number", "purchase_order_number"} and not re.search(r"\d", str(value)):
            return
        if field in {"supplier_name", "customer_name"} and is_forbidden_party_name(str(value)):
            return
        final_score = max(0.0, min(1.0, score + ((block.confidence or 0) * 0.08 if block else 0)))
        candidates.setdefault(field, []).append(Candidate(
            field=field,
            value=value,
            score=final_score,
            source=source,
            page=block.page_number if block else None,
            line_index=block.line_index if block else None,
            bbox=block.bbox if block else None,
            normalized_value=value,
            confidence=final_score,
            evidence_text=block.text if block else None,
            score_breakdown=score_breakdown or {
                "layout_score": 0.25 if block and block.bbox else 0.05,
                "semantic_score": 0.05,
                "label_proximity_score": min(0.35, score * 0.35),
                "regex_score": 0.25,
                "business_validation_score": 0.10,
                "consistency_score": 0.05,
                "memory_score": 0.0,
                "penalty_score": 0.0,
            },
        ))

    with _timer_stage(timing_recorder, "metadata_extraction", source="regex"):
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
            "supplier_name": None if ocr_blocks else _extract_supplier_name(text),
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
    with _timer_stage(timing_recorder, "layout_analysis", source="candidate_generation"):
        _add_layout_aware_candidates(add, ocr_blocks or [])
    with _timer_stage(timing_recorder, "document_graph", part="candidate_generation"):
        add_graph_field_candidates(add, ocr_blocks or [])
    with _timer_stage(timing_recorder, "metadata_extraction", source="spatial_date"):
        _add_spatial_date_candidates(add, ocr_blocks or [])
    with _timer_stage(timing_recorder, "totals_extraction"):
        _add_stacked_totals_candidates(add, text, ocr_blocks or [])
        _add_summary_table_candidates(add, ocr_blocks or [])
    with _timer_stage(timing_recorder, "supplier_extraction", source="ocr_blocks"):
        _add_party_candidates_from_blocks(add, ocr_blocks or [])
        _add_safe_party_region_candidates(add, ocr_blocks or [])
    with _timer_stage(timing_recorder, "customer_extraction", source="ocr_blocks"):
        pass
    if not ocr_blocks:
        _add_supplier_customer_candidates(add, text)
    with _timer_stage(timing_recorder, "correction_loading"):
        boost_candidates_from_memory(candidates, text)
    _score_document_type_relevance(candidates, classification)
    return candidates


def _extract_invoice_number(text: str) -> str | None:
    patterns = [
        rf"\binvoice\s*(?:{NUMBER_LABEL}|reference|ref)\s*[:#-]?\s*(?P<number>[A-Z0-9][A-Z0-9_\-\/.]{{2,}})",
        rf"\bfacture\s*(?:{NUMBER_LABEL}|reference|ref)\s*[:#-]?\s*(?P<number>[A-Z0-9][A-Z0-9_\-\/.]{{2,}})",
        r"\bn\s*.?\s*facture\s*[:#-]?\s*(?P<number>[A-Z0-9][A-Z0-9_\-\/.]{2,})",
        rf"\b(?:{NUMBER_LABEL}|reference|ref)\b\s*(?:facture|invoice)?\s*[:#-]?\s*(?P<number>[A-Z0-9][A-Z0-9_\-\/.]{{2,}})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group("number").strip()
            if not re.search(r"\d", value) or value.lower() in {"umber", "number", "invoice"}:
                continue
            return value
    return None


def _extract_invoice_date(text: str) -> str | None:
    return first_match([
        rf"(?:date\s*(?:de\s*)?(?:facture|invoice)|invoice\s*date|billing\s*date|issue\s*date|issued\s*date|date\s*of\s*issue|date\s*d['’]?\s*(?:emission|émission)|date\s*emission|Ã˜ÂªÃ˜Â§Ã˜Â±Ã™Å Ã˜Â®)\s*[:#-]?\s*{DATE}",
        rf"(?:facture\s*du|issued\s*on|emitted\s*on|emise\s*le|émise\s*le|emis\s*le|émis\s*le)\s*[:#-]?\s*{DATE}",
        rf"^\s*date\s*[:#-]?\s*{DATE}",
        rf"(?:date|issued|emis|ÃƒÂ©mis)\s*$\n\s*{DATE}",
    ], text)


def _extract_due_date(text: str) -> str | None:
    return first_match([
        rf"(?:echeance|échéance|date\s*d['’]?\s*echeance|date\s*d['’]?\s*échéance|due\s*date|payment\s*due|payable\s*by|date\s*limite)\s*[:#-]?\s*{DATE}",
    ], text)


def _is_due_date_context(text: str) -> bool:
    plain = strip_accents(text).lower()
    return bool(re.search(r"\b(?:due[_\s-]*date|payment due|payable by|date limite|echeance|date d['’ ]?echeance|date decheance)\b", plain))


def _is_order_or_delivery_date_context(text: str) -> bool:
    plain = strip_accents(text).lower()
    return bool(re.search(r"\b(?:order date|purchase order date|po date|date commande|date de commande|commande|delivery date|date livraison|date de livraison|shipping date|ship date|livraison)\b", plain))


def _extract_currency(text: str) -> str | None:
    upper = text.upper()
    for code in ("TND", "EUR", "USD", "GBP", "MAD", "DZD", "CAD", "CHF", "AED"):
        if re.search(rf"\b{code}\b", upper):
            return code
    if re.search(r"\bEURO(?:S)?\b", upper) or "€" in text or "ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬" in text or "ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬" in text:
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
    if value is None:
        totals = _collect_labeled_amounts(text)
        if totals.get("amount_ttc") is not None:
            return totals["amount_ttc"]
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
        r"(?:purchase\s*order|po\s*(?:number|no\.?|#)?|bon\s*de\s*commande|ref\.?\s*commande|réf\.?\s*commande|commande|order\s*(?:number|no\.?)?)\s*[:#-]?\s*([A-Z0-9_\-\/.]{3,})",
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
            r"date\s*d.?\s*echeance", r"date\s*d.?\s*ÃƒÆ’Ã‚Â©chÃƒÆ’Ã‚Â©ance",
            r"invoice\s*date", r"due\s*date", r"date\s*:",
        ])
        candidate_plain = strip_accents(candidate).lower()
        next_plain = strip_accents(lines[index + 1]).lower() if index + 1 < len(lines) else ""
        if _is_supplier_candidate(candidate_plain, candidate):
            if any(word in next_plain for word in ("rue", "avenue", "street", "road", "tel", "tÃƒÆ’Ã‚Â©l", "phone", "mf", "tax", "ice")):
                return _clean_name(candidate)

    for line in lines[:12]:
        candidate = _remove_after_keywords(line, [r"date\s*:", r"due\s*date"])
        candidate_plain = strip_accents(candidate).lower()
        if _is_supplier_candidate(candidate_plain, candidate):
            return _clean_name(candidate)
    best = _best_company_line(lines[:18])
    if best:
        return best
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
        rf"{label}\s*[:#=\-ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â]?\s*(?:\(?\s*\d{{1,2}}(?:[,.]\d{{1,2}})?\s*%\s*\)?\s*)?(?:[A-Z]{{3}}\s*)?({AMOUNT_VALUE})"
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
    if _looks_like_table_row(labels):
        if any(key in labels for key in ("subtotal", "sous-total", "total ttc", "amount due", "grand total", "vat", "tva")):
            _add_totals_block_candidates(add, line)
        return
    date_match = re.search(DATE, line_plain)
    if date_match:
        if any(key in labels for key in ("echeance", "due date", "date limite", "ÃƒËœÃ‚Â§ÃƒËœÃ‚Â³ÃƒËœÃ‚ÂªÃƒËœÃ‚Â­Ãƒâ„¢Ã¢â‚¬Å¡ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¡")):
            add("due_date", date_match.group(1), 0.80, "date near due-date label", block)
        elif not _is_order_or_delivery_date_context(labels) and ("date" in labels or "issued" in labels or "emise" in labels or "emis" in labels or "ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾ÃƒËœÃ‚ÂªÃƒËœÃ‚Â§ÃƒËœÃ‚Â±Ãƒâ„¢Ã…Â ÃƒËœÃ‚Â®" in labels):
            add("invoice_date", date_match.group(1), 0.78, "date near date label", block)

    if any(key in labels for key in ("facture", "invoice", "n bl", "nÃƒâ€šÃ‚Â° bl", "ÃƒËœÃ‚Â±Ãƒâ„¢Ã¢â‚¬Å¡Ãƒâ„¢Ã¢â‚¬Â¦ ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾Ãƒâ„¢Ã‚ÂÃƒËœÃ‚Â§ÃƒËœÃ‚ÂªÃƒâ„¢Ã‹â€ ÃƒËœÃ‚Â±ÃƒËœÃ‚Â©")):
        add("invoice_number", _extract_invoice_number(line_plain) or _document_number_from_line(line_plain), 0.86, "number near document label", block)
    if any(key in labels for key in ("commande", "purchase order", "po number", "ÃƒËœÃ‚Â·Ãƒâ„¢Ã¢â‚¬Å¾ÃƒËœÃ‚Â¨ ÃƒËœÃ‚Â´ÃƒËœÃ‚Â±ÃƒËœÃ‚Â§ÃƒËœÃ‚Â¡")):
        add("purchase_order_number", _extract_purchase_order(line_plain) or _document_number_from_line(line_plain), 0.82, "order reference label", block)

    if any(key in labels for key in ("sous-total", "total ht", "subtotal", "hors taxe", "htva")):
        add("amount_ht", _last_amount(line), 0.86, "amount near HT/subtotal label", block)
    if any(key in labels for key in ("tva", "vat", "tax amount", "montant tva", "ÃƒËœÃ‚Â¶ÃƒËœÃ‚Â±Ãƒâ„¢Ã…Â ÃƒËœÃ‚Â¨ÃƒËœÃ‚Â©")):
        add("tva_amount", _last_non_percent_amount(line), 0.82, "amount near tax label", block)
        add("tax_rate", parse_amount(first_match([r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%"], line)), 0.78, "tax rate percent", block)
    is_total_line = any(key in labels for key in ("total ttc", "montant ttc", "grand total", "amount due", "ttc", "ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾ÃƒËœÃ‚Â¥ÃƒËœÃ‚Â¬Ãƒâ„¢Ã¢â‚¬Â¦ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾Ãƒâ„¢Ã…Â ", "ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾Ãƒâ„¢Ã¢â‚¬Â¦ÃƒËœÃ‚Â¬Ãƒâ„¢Ã¢â‚¬Â¦Ãƒâ„¢Ã‹â€ ÃƒËœÃ‚Â¹"))
    if is_total_line:
        add("amount_ttc", _last_amount(line), 0.90, "amount near TTC/total label", block)

    currency = _extract_currency(line)
    if currency:
        add("currency", currency, 0.82 if is_total_line else 0.58, "currency near totals" if is_total_line else "currency token", block)


def _add_supplier_customer_candidates(add, text: str) -> None:
    lines = [_clean_name(line) for line in text.splitlines() if _clean_name(line)]
    _add_party_block_candidates(add, lines, "supplier", _find_first_party_line(lines, ("seller", "supplier", "vendor", "from", "bill from", "fournisseur", "vendeur", "ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾Ãƒâ„¢Ã¢â‚¬Â¦Ãƒâ„¢Ã‹â€ ÃƒËœÃ‚Â±ÃƒËœÃ‚Â¯", "ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾Ãƒâ„¢Ã¢â‚¬Â¦ÃƒËœÃ‚Â²Ãƒâ„¢Ã‹â€ ÃƒËœÃ‚Â¯")))
    _add_party_block_candidates(add, lines, "customer", _find_first_party_line(lines, ("client", "customer", "bill to", "billed to", "ship to", "acheteur", "livre a", "livrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© a", "livre ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â ", "ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾ÃƒËœÃ‚Â¹Ãƒâ„¢Ã¢â‚¬Â¦Ãƒâ„¢Ã…Â Ãƒâ„¢Ã¢â‚¬Å¾")))
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
        if any(marker in plain for marker in ("client", "customer", "livre a", "livrÃƒÆ’Ã‚Â© a", "livre ÃƒÆ’Ã‚Â ", "ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾ÃƒËœÃ‚Â¹Ãƒâ„¢Ã¢â‚¬Â¦Ãƒâ„¢Ã…Â Ãƒâ„¢Ã¢â‚¬Å¾")):
            for candidate in lines[index + 1:index + 5]:
                candidate_plain = strip_accents(candidate).lower()
                if _is_supplier_candidate(candidate_plain, candidate):
                    add("customer_name", candidate, 0.80, "near customer/client label")
                    break
        if index < 18 and _is_supplier_candidate(plain, line) and is_company_candidate_text(line):
            next_lines = " ".join(strip_accents(value).lower() for value in lines[index + 1:index + 4])
            score = 0.80 if any(word in next_lines for word in ("rue", "avenue", "tel", "mf", "ice", "email", "tax", "road", "street")) else 0.58
            add("supplier_name", line, score, "top/header company block")


def _add_party_block_candidates(add, lines: list[str], role: str, start: int | None) -> None:
    if start is None:
        return
    label_line = lines[start]
    label_remainder = re.sub(
        r"^(?:seller|supplier|vendor|from|bill\s*from|fournisseur|vendeur|client|customer|bill\s*to|billed\s*to|ship\s*to|acheteur|livre\s*a|livre\s*ÃƒÆ’Ã‚Â )\s*[:#-]?\s*",
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
        if _is_order_or_delivery_date_context(label):
            continue
        if _is_invoice_date_label(label):
            date_value = first_match([DATE], line) or first_match([DATE], following)
            add("invoice_date", date_value, 0.86, "date label followed by value")
        elif any(key in label for key in ("due date", "echeance", "date limite", "payment due")):
            date_value = first_match([DATE], line) or first_match([DATE], following)
            add("due_date", date_value, 0.84, "due-date label followed by value")
        elif any(key in label for key in ("facture n", "invoice number", "invoice no", "n facture")):
            add("invoice_number", _document_number_from_line(following), 0.84, "document number label followed by value")
        elif any(key in label for key in ("purchase order", "po number", "bon de commande", "ref commande", "réf commande", "commande")):
            add("purchase_order_number", _extract_purchase_order(line) or _document_number_from_line(following), 0.84, "purchase order label followed by value")


def _add_block_sequence_candidates(add, blocks: list[OCRLine]) -> None:
    ordered = sorted(blocks, key=lambda block: (block.page_number, block.line_index if block.line_index is not None else 10_000))
    for index, block in enumerate(ordered[:-1]):
        label = strip_accents(block.text).lower()
        next_text = " ".join(next_block.text for next_block in ordered[index + 1:index + 3])
        if _is_order_or_delivery_date_context(label):
            continue
        if _is_invoice_date_label(label):
            add("invoice_date", first_match([DATE], block.text) or first_match([DATE], next_text), 0.88, "OCR block date label followed by value", block)
        elif any(key in label for key in ("due date", "echeance", "date limite", "payment due")):
            add("due_date", first_match([DATE], block.text) or first_match([DATE], next_text), 0.86, "OCR block due-date label followed by value", block)
        elif any(key in label for key in ("invoice number", "invoice no", "invoice #", "facture n", "n facture", "document number")):
            add("invoice_number", _document_number_from_line(block.text) or _document_number_from_line(next_text), 0.88, "OCR block document number label followed by value", block)
        elif any(key in label for key in ("purchase order", "po number", "bon de commande", "ref commande", "réf commande", "commande")):
            add("purchase_order_number", _extract_purchase_order(block.text) or _document_number_from_line(next_text), 0.86, "OCR block purchase order label followed by value", block)


def _add_stacked_totals_candidates(add, text: str, blocks: list[OCRLine]) -> None:
    labeled_totals = _collect_labeled_amounts(text)
    if labeled_totals.get("amount_ht") is not None:
        add("amount_ht", labeled_totals.get("amount_ht"), 0.88, "semantic totals cluster HT")
    if labeled_totals.get("tva_amount") is not None:
        add("tva_amount", labeled_totals.get("tva_amount"), 0.88, "semantic totals cluster TVA")
    if labeled_totals.get("amount_ttc") is not None:
        add("amount_ttc", labeled_totals.get("amount_ttc"), 0.92, "semantic totals cluster TTC")
    if labeled_totals.get("tax_rate") is not None:
        add("tax_rate", labeled_totals.get("tax_rate"), 0.84, "semantic totals cluster tax rate")
    if labeled_totals.get("currency") is not None:
        add("currency", labeled_totals.get("currency"), 0.84, "semantic totals cluster currency")
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


def _add_summary_table_candidates(add, blocks: list[OCRLine]) -> None:
    positioned = [block for block in blocks if block.bbox and block.text.strip()]
    if not positioned:
        return
    summary_markers = [
        block for block in positioned
        if re.search(r"\bsummary\b|\br[eé]capitulatif\b|\bsynth[eè]se\b", strip_accents(block.text).lower())
    ]
    if not summary_markers:
        return
    summary_y = min(block.bbox.y1 for block in summary_markers)
    below = [block for block in positioned if block.bbox.y1 > summary_y]
    if not below:
        return

    labels: dict[str, OCRLine] = {}
    rate_label: OCRLine | None = None
    for block in below:
        plain = strip_accents(block.text).lower().strip()
        if "net worth" in plain or "net amount" in plain or re.fullmatch(r"net", plain):
            labels["amount_ht"] = block
        elif "gross worth" in plain or "gross total" in plain:
            labels["amount_ttc"] = block
        elif re.fullmatch(r"vat|tva|tax", plain):
            labels["tva_amount"] = block
        elif "vat" in plain or "tva" in plain:
            rate_label = block

    def nearest_amount(label: OCRLine, *, percent: bool = False) -> float | None:
        label_x = (label.bbox.x1 + label.bbox.x2) / 2
        options: list[tuple[float, float]] = []
        for block in below:
            if block.bbox.y1 <= label.bbox.y1:
                continue
            text = block.text.strip()
            if percent:
                match = re.search(r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%", text)
                value = parse_amount(match.group(1)) if match else None
            else:
                if "%" in text:
                    continue
                value = parse_amount(text)
            if value is None:
                continue
            block_x = (block.bbox.x1 + block.bbox.x2) / 2
            horizontal_distance = abs(block_x - label_x)
            if horizontal_distance > 190:
                continue
            vertical_distance = block.bbox.y1 - label.bbox.y1
            options.append((horizontal_distance + vertical_distance * 0.12, value))
        return sorted(options, key=lambda item: item[0])[0][1] if options else None

    ht = nearest_amount(labels["amount_ht"]) if "amount_ht" in labels else None
    vat = nearest_amount(labels["tva_amount"]) if "tva_amount" in labels else None
    ttc = nearest_amount(labels["amount_ttc"]) if "amount_ttc" in labels else None
    rate = nearest_amount(rate_label, percent=True) if rate_label else None

    if ht is not None:
        add("amount_ht", ht, 0.96, "summary table spatial net worth", labels.get("amount_ht"))
    if vat is not None:
        add("tva_amount", vat, 0.96, "summary table spatial VAT amount", labels.get("tva_amount"))
    if ttc is not None:
        add("amount_ttc", ttc, 0.97, "summary table spatial gross worth", labels.get("amount_ttc"))
    if rate is not None:
        add("tax_rate", rate, 0.94, "summary table spatial tax rate", rate_label)
    if any("$" in block.text for block in below):
        add("currency", "USD", 0.94, "summary table currency symbol")




def _add_layout_aware_candidates(add, blocks: list[OCRLine]) -> None:
    if not blocks:
        return
    lines = group_ocr_lines(blocks)
    layout = analyze_document_layout(blocks)
    logical_blocks = layout.get("blocks", [])

    for line in lines:
        plain = strip_accents(line.text).lower()
        first_block = line.blocks[0] if line.blocks else None
        if any(label in plain for label in ("invoice no", "invoice number", "invoice #", "facture n", "n facture", "numero", "numÃƒÂ©ro")):
            value = _document_number_from_line(strip_accents(line.text))
            add("invoice_number", value, 0.92, "layout label proximity: invoice number", first_block)
        if any(label in plain for label in ("date facture", "invoice date", "issue date", "issued", "billing date", "date:", "emise", "emis")) and not _is_due_date_context(plain) and not _is_order_or_delivery_date_context(plain):
            date_value = first_match([DATE], line.text)
            add("invoice_date", date_value, 0.90, "layout label proximity: invoice date", first_block)
        if _is_due_date_context(plain):
            date_value = first_match([DATE], line.text)
            add("due_date", date_value, 0.88, "layout label proximity: due date", first_block)
        if any(label in plain for label in ("purchase order", "po number", "bon de commande", "ref commande", "réf commande", "commande")):
            add("purchase_order_number", _extract_purchase_order(strip_accents(line.text)) or _document_number_from_line(strip_accents(line.text)), 0.86, "layout label proximity: purchase order", first_block)
        currency = _extract_currency(line.text)
        if currency and any(label in plain for label in ("total", "amount", "montant", "ttc", "currency", "devise")):
            add("currency", currency, 0.86, "layout currency near amount/total", first_block)

    for block in logical_blocks:
        block_type = block.get("block_type")
        block_text = block.get("text") or ""
        if block_type == "customer":
            name = _first_party_name_after_label(block_text)
            add("customer_name", name, 0.90, "layout customer block")
            tax_id = _extract_supplier_tax_id(block_text)
            add("customer_tax_id", tax_id, 0.86, "layout customer tax id")
            address = _extract_party_address(block_text)
            add("customer_address", address, 0.82, "layout customer address")
        elif block_type == "supplier":
            name = _first_header_company_name(block_text)
            if name and is_company_candidate_text(name):
                add("supplier_name", name, 0.90, "layout supplier/header block")
            tax_id = _extract_supplier_tax_id(block_text)
            add("supplier_tax_id", tax_id, 0.84, "layout supplier tax id")
            address = _extract_party_address(block_text)
            add("supplier_address", address, 0.80, "layout supplier address")
        elif block_type == "totals":
            _add_totals_block_candidates(add, block_text)

    for table in reconstruct_tables(blocks, lines):
        line_totals = [row.get("values", {}).get("total") for row in table.rows]
        line_totals = [value for value in line_totals if value is not None]
        if line_totals:
            add("amount_ht", round(sum(line_totals), 3), 0.74, "sum of reconstructed line totals")


def _add_totals_block_candidates(add, text: str) -> None:
    for line in text.splitlines():
        plain = strip_accents(line).lower()
        if any(label in plain for label in ("subtotal", "sous-total", "total ht", "hors taxe", "htva")):
            add("amount_ht", _last_amount(line), 0.93, "totals block HT/subtotal")
        if any(label in plain for label in ("tva", "vat", "sales tax", "tax amount")):
            add("tva_amount", _last_non_percent_amount(line), 0.91, "totals block tax")
            add("tax_rate", parse_amount(first_match([r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%"], line)), 0.88, "totals block tax rate")
        if any(label in plain for label in ("total ttc", "grand total", "amount due", "total due", "net a payer", "net ÃƒÂ  payer")):
            add("amount_ttc", _last_amount(line), 0.95, "totals block TTC/amount due")


def _first_party_name_after_label(text: str) -> str | None:
    lines = [_clean_name(line) for line in text.splitlines() if _clean_name(line)]
    labels = ("client", "customer", "bill to", "facture", "acheteur", "destinataire", "livre", "livrÃƒÂ©")
    for index, line in enumerate(lines):
        plain = strip_accents(line).lower()
        if any(label in plain for label in labels):
            for candidate in lines[index + 1:index + 5]:
                if _is_company_name_line(candidate):
                    return candidate
    best = _best_company_line(lines[:6])
    if best:
        return best
    return None


def _first_header_company_name(text: str) -> str | None:
    lines = [_clean_name(line) for line in text.splitlines() if _clean_name(line)]
    return _best_company_line(lines[:8])
def _add_spatial_date_candidates(add, blocks: list[OCRLine]) -> None:
    ordered = sorted([block for block in blocks if block.bbox], key=lambda block: (block.page_number, block.bbox.y1, block.bbox.x1))
    for index, block in enumerate(ordered):
        label = strip_accents(block.text).lower().strip(" :#-")
        is_due_label = _is_due_date_context(label)
        is_invoice_label = not _is_order_or_delivery_date_context(label) and (
            _is_invoice_date_label(label) or any(key in label for key in ("issue date", "issued", "date of issue", "date emission", "date d emission", "emise", "emis"))
        )
        if not (is_invoice_label or is_due_label):
            continue
        inline = first_match([DATE], block.text)
        if inline:
            add("due_date" if is_due_label else "invoice_date", inline, 0.90 if not is_due_label else 0.88, "spatial date label same OCR block", block)
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
            add("due_date" if is_due_label else "invoice_date", value, 0.87 if is_due_label else 0.89, "spatial date label nearest value", source_block)


def _add_party_candidates_from_blocks(add, blocks: list[OCRLine]) -> None:
    if not blocks:
        return
    ordered = sorted([block for block in blocks if block.bbox], key=lambda block: (block.page_number, block.bbox.y1, block.bbox.x1))
    labels = {
        "supplier": ("supplier", "seller", "vendor", "from", "bill from", "fournisseur", "vendeur", "Ã˜Â§Ã™â€žÃ™â€¦Ã™Ë†Ã˜Â±Ã˜Â¯"),
        "customer": ("customer", "client", "bill to", "ship to", "acheteur", "livre a", "livrÃƒÂ© a", "Ã˜Â§Ã™â€žÃ˜Â¹Ã™â€¦Ã™Å Ã™â€ž"),
    }
    for role, role_labels in labels.items():
        for index, block in enumerate(ordered):
            plain = strip_accents(block.text).lower().strip(" :#-")
            if not any(label in plain for label in role_labels):
                continue
            window = _party_window_after_label(ordered, index, role)
            _add_party_window_candidates(add, role, window)
            break


def _add_safe_party_region_candidates(add, blocks: list[OCRLine]) -> None:
    """Recover header parties when graph labels are absent or rejected.

    This fallback is deliberately limited to the upper document regions and
    still applies the same company/address/table safety filters. It supplies a
    reviewable candidate; it never bypasses the quality gate.
    """
    positioned = [block for block in blocks if block.bbox and block.text.strip()]
    if not positioned:
        return
    max_x = max(block.bbox.x2 for block in positioned)
    max_y = max(block.bbox.y2 for block in positioned)
    header = [block for block in positioned if block.bbox.y1 <= max_y * 0.55]
    labels = {
        "supplier": ("supplier", "seller", "vendor", "from", "fournisseur", "vendeur"),
        "customer": ("customer", "client", "bill to", "ship to", "acheteur", "destinataire", "livre a", "livre"),
    }
    for role, role_labels in labels.items():
        label_blocks = [
            block for block in header
            if any(label in strip_accents(block.text).lower() for label in role_labels)
        ]
        for block in sorted(header, key=lambda item: (item.bbox.y1, item.bbox.x1)):
            plain = strip_accents(block.text).lower()
            if is_forbidden_party_name(block.text) or not is_company_candidate_text(block.text):
                continue
            if _looks_like_table_row(plain) or any(word in plain for word in BAD_SUPPLIER_WORDS):
                continue
            center_x = (block.bbox.x1 + block.bbox.x2) / 2
            if role == "supplier" and center_x > max_x * 0.58:
                continue
            if role == "customer" and center_x < max_x * 0.38:
                continue
            label_distances = [
                abs(block.bbox.y1 - label.bbox.y2) + abs(center_x - ((label.bbox.x1 + label.bbox.x2) / 2)) * 0.15
                for label in label_blocks
                if label.bbox and label.bbox.y1 <= block.bbox.y1
            ]
            label_distance = min(label_distances) if label_distances else 9999
            score = 0.70 if label_distance < 260 else 0.58
            source = f"safe {role} header region fallback"
            add(f"{role}_name", _clean_name(block.text), score, source, block)
            break


def _party_window_after_label(blocks: list[OCRLine], start_index: int, role: str | None = None) -> list[OCRLine]:
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
        if _is_party_label(plain):
            if role and _is_other_party_label(plain, role):
                continue
            break
        if any(key in plain for key in ("invoice", "facture", "total", "subtotal", "sous-total")):
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
    if iban := first_match([r"\b([A-Z]{2}\d{2}[ \tA-Z0-9]{8,40})"], line):
        add(f"{role}_bank_iban", re.sub(r"[^A-Z0-9]", "", iban.upper()), 0.82, f"{role} IBAN in labeled block", block)
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
        ranked_values = values
        if field in {"supplier_name", "customer_name"}:
            continue
        if field in {"invoice_date", "due_date"}:
            ranked_values = _rank_date_candidates(values, field)
            if ranked_values:
                selected[field] = ranked_values[0]
                continue
        if field in {"amount_ht", "tva_amount", "amount_ttc", "tax_rate"}:
            ranked_values = _rank_amount_candidates(values, field)
            if ranked_values:
                selected[field] = ranked_values[0]
                continue
        if field == "currency":
            selected[field] = _select_currency_candidate(ranked_values)
        else:
            selected[field] = sorted(ranked_values, key=lambda candidate: candidate.score, reverse=True)[0]
    return selected


def _best_company_line(lines: list[str]) -> str | None:
    scored: list[tuple[float, str]] = []
    for line in lines:
        if not _is_company_name_line(line):
            continue
        plain = strip_accents(line).lower()
        score = 0.6
        if re.search(r"\b(?:ltd|llc|inc|gmbh|sarl|sa|sas|bv|nv|corp|company|group)\b", plain):
            score += 0.25
        if line.upper() == line and len(line.split()) >= 2:
            score += 0.08
        if not _looks_like_address_line(line):
            score += 0.07
        scored.append((score, line))
    return sorted(scored, reverse=True)[0][1] if scored else None


def _extract_party_address(text: str) -> str | None:
    lines = [_clean_name(line) for line in text.splitlines() if _clean_name(line)]
    address_lines = [line for line in lines if _looks_like_address_line(line)]
    if not address_lines:
        return None
    return ", ".join(address_lines[:2])


def _looks_like_table_row(text: str) -> bool:
    amount_count = len(re.findall(AMOUNT_VALUE, text))
    return amount_count >= 2 and (
        re.search(PRODUCT_CODE, text, re.IGNORECASE) is not None
        or any(word in text for word in ("description", "designation", "qty", "quantity", "price", "prix", "total"))
    )


def _collect_labeled_amounts(text: str) -> dict[str, float | str | None]:
    result: dict[str, float | str | None] = {"amount_ht": None, "tva_amount": None, "amount_ttc": None, "tax_rate": None, "currency": None}
    candidates: list[tuple[str, float]] = []
    for line in text.splitlines():
        clean = _clean_name(line)
        plain = strip_accents(clean).lower()
        if not clean:
            continue
        amounts = [parse_amount(amount) for amount in re.findall(AMOUNT_VALUE, clean)]
        amounts = [amount for amount in amounts if amount is not None]
        if not amounts:
            continue
        if result["currency"] is None:
            result["currency"] = _extract_currency(clean)
        if any(word in plain for word in ("subtotal", "sous-total", "total ht", "htva", "net worth", "hors taxe")):
            result["amount_ht"] = amounts[-1]
        elif any(word in plain for word in ("tva", "vat", "sales tax", "tax amount")):
            non_percent = _last_non_percent_amount(clean)
            if non_percent is not None:
                result["tva_amount"] = non_percent
            rate = parse_amount(first_match([r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%"], clean))
            if rate is not None:
                result["tax_rate"] = rate
        elif any(word in plain for word in ("total ttc", "grand total", "amount due", "balance due", "total due", "net a payer", "invoice total")):
            result["amount_ttc"] = amounts[-1]
        if any(word in plain for word in ("total", "due", "ttc", "ht", "vat", "tva", "subtotal")):
            candidates.extend((plain, amount) for amount in amounts)
    if result["amount_ttc"] is None and candidates:
        ttc_candidates = [amount for label, amount in candidates if any(word in label for word in ("total", "due", "ttc"))]
        if ttc_candidates:
            result["amount_ttc"] = max(ttc_candidates)
    if result["amount_ht"] is not None and result["tva_amount"] is not None and result["amount_ttc"] is None:
        result["amount_ttc"] = round(float(result["amount_ht"]) + float(result["tva_amount"]), 3)
    if result["tax_rate"] is None and result["amount_ht"] and result["tva_amount"] is not None and result["amount_ht"] != 0:
        result["tax_rate"] = round((float(result["tva_amount"]) / float(result["amount_ht"])) * 100, 2)
    return result


def _party_candidate_bonus(candidate: Candidate) -> float:
    value = str(candidate.value or "")
    plain = strip_accents(value).lower()
    bonus = 0.0
    if re.search(r"\b(?:ltd|llc|inc|gmbh|sarl|sa|sas|bv|nv|corp|company|group)\b", plain):
        bonus += 0.12
    if candidate.source and any(word in candidate.source.lower() for word in ("layout", "graph", "label")):
        bonus += 0.04
    if _looks_like_address_line(value):
        bonus -= 0.18
    return bonus


def _rank_date_candidates(values: list[Candidate], field: str) -> list[Candidate]:
    ranked: list[Candidate] = []
    for candidate in values:
        text = str(candidate.value or "")
        score = candidate.score
        parsed_date = _parse_candidate_date(candidate)
        if parsed_date is None:
            score -= 0.5
        source = (candidate.source or "").lower()
        if "table" in source or "row" in source:
            score -= 0.25
        if "label" in source or "spatial" in source or "graph" in source:
            score += 0.06
        if field == "invoice_date" and any(token in source for token in ("due", "echeance", "payment due", "date limite")):
            score -= 0.35
        if field == "due_date" and any(token in source for token in ("invoice date", "issue date", "date emission")):
            score -= 0.30
        updated = candidate.model_copy(deep=True)
        if parsed_date is not None:
            updated.value = parsed_date.isoformat()
            updated.normalized_value = parsed_date.isoformat()
        updated.score = round(max(0.0, min(1.0, score)), 3)
        updated.confidence = updated.score
        ranked.append(updated)
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def _parse_candidate_date(candidate: Candidate):
    value = str(candidate.value or "")
    numeric = re.search(r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})\b", value)
    if not numeric:
        return parse_date(value)
    first = int(numeric.group(1))
    second = int(numeric.group(2))
    year = numeric.group(3)
    if first > 12 or second > 12:
        return parse_date(value)
    context = strip_accents(" ".join(str(part or "") for part in (candidate.source, candidate.evidence_text))).lower()
    french_markers = ("date facture", "facture", "echeance", "emission", "emise", "emis", "commande", "livraison")
    english_markers = ("invoice date", "issued", "issue date", "date of issue", "bill date", "due date", "payment due")
    prefer_month_first = any(marker in context for marker in english_markers) and not any(marker in context for marker in french_markers)
    if not prefer_month_first:
        return parse_date(value)
    normalized_year = year if len(year) == 4 else f"20{year}"
    try:
        return datetime.strptime(f"{first:02d}/{second:02d}/{normalized_year}", "%m/%d/%Y").date()
    except ValueError:
        return parse_date(value)


def _rank_amount_candidates(values: list[Candidate], field: str) -> list[Candidate]:
    ranked: list[Candidate] = []
    for candidate in values:
        value = parse_amount(str(candidate.value))
        if value is None:
            continue
        source = (candidate.source or "").lower()
        score = candidate.score
        if any(token in source for token in ("totals block", "document graph totals", "semantic totals", "consistent total")):
            score += 0.12
        if "sum of reconstructed line totals" in source and field == "amount_ttc":
            score -= 0.15
        if field == "amount_ttc" and any(token in source for token in ("subtotal", "ht/subtotal")):
            score -= 0.25
        if field == "amount_ht" and any(token in source for token in ("amount due", "total due", "grand total", "ttc")):
            score -= 0.25
        if field == "tax_rate" and value > 100:
            score -= 0.80
        if field != "tax_rate" and value <= 0:
            score -= 0.40
        updated = candidate.model_copy(deep=True)
        updated.score = round(max(0.0, min(1.0, score)), 3)
        updated.confidence = updated.score
        ranked.append(updated)
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def _prefer_consistent_total_candidates(selected: dict[str, Candidate], candidates: dict[str, list[Candidate]]) -> None:
    best_score: float | None = None
    best_triplet: tuple[Candidate, Candidate, Candidate] | None = None
    ht_values = candidates.get("amount_ht", [])
    tva_values = candidates.get("tva_amount", [])
    ttc_values = candidates.get("amount_ttc", [])
    for ht in ht_values:
        ht_amount = parse_amount(str(ht.value))
        if ht_amount is None:
            continue
        for tva in tva_values:
            tva_amount = parse_amount(str(tva.value))
            if tva_amount is None:
                continue
            for ttc in ttc_values:
                ttc_amount = parse_amount(str(ttc.value))
                if ttc_amount is None:
                    continue
                mismatch = abs(round(ht_amount + tva_amount, 3) - ttc_amount)
                if mismatch > max(0.05, abs(ttc_amount) * 0.002):
                    continue
                score = ht.score + tva.score + ttc.score - mismatch
                if best_score is None or score > best_score:
                    best_score = score
                    best_triplet = (ht, tva, ttc)
    if best_triplet:
        selected["amount_ht"], selected["tva_amount"], selected["amount_ttc"] = best_triplet
        ht_amount = parse_amount(str(best_triplet[0].value))
        tva_amount = parse_amount(str(best_triplet[1].value))
        if ht_amount and tva_amount is not None:
            selected["tax_rate"] = Candidate(
                field="tax_rate",
                value=round((tva_amount / ht_amount) * 100, 2),
                score=min(best_triplet[0].score, best_triplet[1].score),
                source="consistent total candidate combination",
            )


def _resolve_date_collisions(selected: dict[str, Candidate]) -> None:
    invoice = selected.get("invoice_date")
    due = selected.get("due_date")
    if not invoice or not due:
        return
    invoice_date = parse_date(str(invoice.value))
    due_date = parse_date(str(due.value))
    if not invoice_date or not due_date or invoice_date != due_date:
        return
    invoice_source = strip_accents(invoice.source or "").lower()
    due_source = strip_accents(due.source or "").lower()
    if "due" in due_source or "echeance" in due_source or "date limite" in due_source:
        if invoice.score <= due.score or "generic" in invoice_source or "date near date label" in invoice_source:
            selected.pop("invoice_date", None)
            return
    if "invoice" in invoice_source or "issue" in invoice_source or "facture" in invoice_source:
        selected.pop("due_date", None)
        return
    selected.pop("invoice_date" if invoice.score <= due.score else "due_date", None)


def _separate_party_candidates(selected: dict[str, Candidate]) -> None:
    supplier = selected.get("supplier_name")
    customer = selected.get("customer_name")
    if not supplier or not customer:
        return
    if str(supplier.value).strip().lower() != str(customer.value).strip().lower():
        return
    if supplier.score >= customer.score:
        selected.pop("customer_name", None)
    else:
        selected.pop("supplier_name", None)


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


def _build_field_traces(
    fields: ExtractedInvoiceFields,
    candidates: dict[str, list[Candidate]],
    selected: dict[str, Candidate],
) -> dict[str, Any]:
    field_values = fields.model_dump(mode="json")
    traces: dict[str, Any] = {}
    trace_fields = set(field_values) | set(candidates) | {
        "supplier_name", "customer_name", "invoice_number", "invoice_date",
        "amount_ht", "tva_amount", "amount_ttc", "tax_rate", "supplier_bank_iban",
        "line_items",
    }
    for field in sorted(trace_fields):
        if field == "line_items":
            traces[field] = {
                "selected_value": field_values.get("line_items", []),
                "selected_count": len(field_values.get("line_items", []) or []),
                "selection_reason": "table reconstruction" if field_values.get("line_items") else None,
                "null_reason": None if field_values.get("line_items") else "no safe table rows selected",
                "candidates": [],
            }
            continue
        chosen = selected.get(field)
        field_candidates = sorted(candidates.get(field, []), key=lambda item: item.score, reverse=True)
        traces[field] = {
            "selected_value": field_values.get(field),
            "selected_candidate": _candidate_trace(chosen) if chosen else None,
            "null_reason": None if field_values.get(field) is not None else _null_reason(field, field_candidates),
            "candidates": [_candidate_trace(candidate, chosen) for candidate in field_candidates[:20]],
        }
    return traces


def _candidate_trace(candidate: Candidate | None, chosen: Candidate | None = None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    is_selected = bool(chosen and candidate.value == chosen.value and candidate.source == chosen.source)
    return {
        "value": candidate.value,
        "score": normalize_confidence(candidate.score),
        "confidence": normalize_confidence(candidate.confidence if candidate.confidence is not None else candidate.score),
        "source": candidate.source,
        "bbox": candidate.bbox.model_dump(mode="json") if candidate.bbox else None,
        "page": candidate.page,
        "line_index": candidate.line_index,
        "source_text": candidate.evidence_text,
        "score_breakdown": candidate.score_breakdown,
        "selected": is_selected,
        "rejected": not is_selected,
        "rejection_reason": None if is_selected else _candidate_rejection_reason(candidate, chosen),
    }


def _candidate_rejection_reason(candidate: Candidate, chosen: Candidate | None) -> str:
    if chosen is None:
        return "no candidate selected for this field"
    if candidate.value == chosen.value:
        return "same value from lower-priority evidence"
    if candidate.score < chosen.score:
        return "lower score than selected candidate"
    return "rejected by consistency or role disambiguation"


def _null_reason(field: str, field_candidates: list[Candidate]) -> str:
    if not field_candidates:
        return "no candidates generated"
    return "all candidates rejected by ranking or quality gate"


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
        if any(marker in plain for marker in ("client", "customer", "livre a", "livrÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â© a", "livre ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â ", "ÃƒËœÃ‚Â§Ãƒâ„¢Ã¢â‚¬Å¾ÃƒËœÃ‚Â¹Ãƒâ„¢Ã¢â‚¬Â¦Ãƒâ„¢Ã…Â Ãƒâ„¢Ã¢â‚¬Å¾")):
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
        for label in ("seller", "supplier", "vendor", "from", "bill from", "fournisseur", "vendeur", "client", "customer", "bill to", "billed to", "ship to", "acheteur", "livre a", "livre ÃƒÆ’Ã‚Â ")
    )


def _is_other_party_label(plain: str, role: str) -> bool:
    normalized = plain.strip().rstrip(":")
    supplier_labels = ("seller", "supplier", "vendor", "from", "bill from", "fournisseur", "vendeur")
    customer_labels = ("client", "customer", "bill to", "billed to", "ship to", "acheteur", "livre a")
    labels = customer_labels if role == "supplier" else supplier_labels
    return any(label == normalized or normalized.startswith(f"{label}:") for label in labels)


def _is_company_name_line(line: str) -> bool:
    if is_forbidden_party_name(line) or not is_company_candidate_text(line):
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
    normalized = label.strip().rstrip(":")
    return any(key in normalized for key in ("date of issue", "invoice date", "date facture", "date d'emission", "date d emission", "issued", "issue", "emise", "emis", "ÃƒËœÃ‚ÂªÃƒËœÃ‚Â§ÃƒËœÃ‚Â±Ãƒâ„¢Ã…Â ÃƒËœÃ‚Â®")) or normalized == "date"


def _money_values(text: str) -> list[float]:
    values = []
    for raw in re.findall(MONEY_VALUE, text):
        value = parse_amount(raw)
        if value is not None:
            values.append(value)
    return values


def _timer_stage(timing_recorder, name: str, **metadata):
    if timing_recorder is None:
        return _noop_stage()
    return timing_recorder.stage(name, **metadata)


class _noop_stage:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False






