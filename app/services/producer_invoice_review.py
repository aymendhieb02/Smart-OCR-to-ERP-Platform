"""Family-scoped canonical review fields for Page 1 producer invoices."""
from __future__ import annotations

import re

from app.core.schemas import FieldExtractionDetail, ProcessInvoiceResponse

ENFIDHA_FAMILY = "ciments_enfidha_invoice_v1"
SOTACIB_FAMILIES = frozenset({"sotacib_kairouan_grey_invoice_v1", "sotacib_kasserine_white_invoice_v1"})
PRODUCER_REVIEW_FIELDS = {
    ENFIDHA_FAMILY: (
        "seller", "invoice_number", "invoice_date", "client", "client_address", "consignee",
        "currency", "total", "total_amount_words", "hs_code", "incoterm", "origin",
        "destination", "packaging", "client_rc", "consignee_address", "proforma_invoice_number",
        "proforma_invoice_date", "shipment", "payment", "bank", "iban", "swift",
        "number_of_bags", "bag_weight", "truck_count",
    ),
    **{family: (
        "seller", "invoice_number", "invoice_date", "client", "client_address", "consignee",
        "currency", "total", "total_amount_words", "hs_code", "incoterm", "origin",
        "destination", "packaging", "client_tax_id", "total_ht", "number_of_bags", "bag_weight",
        "integration_rate", "bank_account", "payment_method", "payment_terms",
    ) for family in SOTACIB_FAMILIES},
}

# Existing detector/extractor names are compatibility inputs, not display keys.
SOURCE_KEYS = {
    "seller": ("seller", "supplier_name"),
    "client": ("client", "customer_name", "buyer"),
    "client_address": ("client_address", "customer_address", "address"),
    "consignee": ("consignee",),
    "consignee_address": ("consignee_address",),
    "total": ("total", "amount_ttc"),
    "total_ht": ("total_ht", "amount_ht"),
    "hs_code": ("hs_code", "tariff_position", "position_tarifaire"),
    "incoterm": ("incoterm", "delivery"),
    "bank_account": ("bank_account", "supplier_bank_rib"),
    "iban": ("iban", "bank_iban", "supplier_bank_iban"),
    "swift": ("swift", "bank_swift", "supplier_bank_swift"),
    "number_of_bags": ("number_of_bags",),
    "bag_weight": ("bag_weight",),
}

COMMON_LABELS = {
    "invoice_number": (r"invoice\s*(?:n(?:o|°|º)|number|#)", r"facture\s*(?:n(?:o|°|º)|num[eé]ro|#)"),
    "invoice_date": (r"invoice\s*date", r"date\s*(?:de\s*)?facture", r"^date$"),
    "client": (r"^client$", r"customer", r"buyer", r"acheteur"),
    "client_address": (r"client\s*address", r"customer\s*address", r"adresse\s*(?:du\s*)?client"),
    "consignee": (r"consignee", r"destinataire"),
    "currency": (r"^currency$", r"devise"),
    "total": (r"total(?!\s*(?:ht|h\.?t\.?))\s*(?:including\s*all\s*taxes|ttc|amount)?", r"montant\s*total", r"^total$"),
    "total_amount_words": (r"amount\s*in\s*words", r"total\s*in\s*words", r"montant\s*en\s*lettres"),
    "hs_code": (r"hs\s*code", r"position\s*tarifaire"),
    "incoterm": (r"^incoterm$", r"delivery\s*term", r"conditions?\s*de\s*livraison"),
    "origin": (r"^origin$", r"origine", r"country\s*of\s*origin"),
    "destination": (r"^destination$", r"country\s*of\s*destination"),
    "packaging": (r"pack(?:ing|aging)", r"conditionnement"),
}
ENFIDHA_LABELS = {
    **COMMON_LABELS,
    "client_rc": (r"client\s*(?:r\.?c\.?|register(?:ed)?\s*number)", r"r\.?c\.?\s*(?:du\s*)?client"),
    "consignee_address": (r"consignee\s*address", r"adresse\s*du\s*destinataire"),
    "proforma_invoice_number": (r"pro\s*forma\s*(?:invoice\s*)?(?:n(?:o|°|º)|number|#)", r"facture\s*pro\s*forma\s*(?:n(?:o|°|º)|num[eé]ro|#)"),
    "proforma_invoice_date": (r"pro\s*forma\s*date", r"date\s*(?:de\s*)?facture\s*pro\s*forma"),
    "shipment": (r"^shipment$", r"exp[eé]dition"),
    "payment": (r"^payment$", r"^paiement$"),
    "bank": (r"^bank$", r"^banque$"),
    "iban": (r"^iban$",),
    "swift": (r"^(?:swift|bic)$",),
    "number_of_bags": (r"number\s*of\s*bags", r"nombre\s*de\s*sacs"),
    "bag_weight": (r"(?:weight|poids)\s*(?:per\s*)?(?:bag|sac)", r"(?:bag|sac)\s*weight"),
    "truck_count": (r"(?:number\s*of\s*)?(?:trucks?|camions?)",),
}
SOTACIB_LABELS = {
    **COMMON_LABELS,
    "client_tax_id": (r"client\s*(?:tax\s*(?:id|number)|matricule\s*fiscal)", r"matricule\s*fiscal\s*(?:du\s*)?client"),
    "total_ht": (r"total\s*(?:ht|excluding\s*tax)", r"montant\s*ht", r"total\s*h\.?t\.?"),
    "number_of_bags": (r"number\s*of\s*bags", r"nombre\s*de\s*sacs"),
    "bag_weight": (r"(?:weight|poids)\s*(?:per\s*)?(?:bag|sac)", r"(?:bag|sac)\s*weight"),
    "integration_rate": (r"integration\s*rate", r"taux\s*d.?int[eé]gration"),
    "bank_account": (r"bank\s*account(?:\s*(?:number|n°))?", r"num[eé]ro\s*de\s*compte\s*bancaire", r"compte\s*bancaire"),
    "payment_method": (r"moyen\s*de\s*r[eè]glement", r"payment\s*method"),
    "payment_terms": (r"mode\s*de\s*r[eè]glement", r"payment\s*terms"),
}


def apply_producer_review_fields(response: ProcessInvoiceResponse, family: str | None, ocr_lines: list | None = None) -> None:
    """Expose canonical display keys while retaining their source evidence."""
    allowed = PRODUCER_REVIEW_FIELDS.get(family or "")
    if not allowed or not hasattr(response, "detected_fields") or not hasattr(response, "expanded_fields"):
        return
    detected = response.detected_fields.model_dump(mode="python")
    expanded = response.expanded_fields
    labels = ENFIDHA_LABELS if family == ENFIDHA_FAMILY else SOTACIB_LABELS
    for field_name, detail in _extract_labeled_details(ocr_lines or [], labels).items():
        current = expanded.get(field_name)
        if current is None or current.value in (None, "") or current.source == "field selection":
            expanded[field_name] = detail
    for canonical in allowed:
        if canonical in expanded:
            continue
        detail = None
        for source_key in SOURCE_KEYS.get(canonical, (canonical,)):
            detail = expanded.get(source_key)
            value = detected.get(source_key)
            if detail is not None or value not in (None, ""):
                if detail is None:
                    detail = FieldExtractionDetail(value=value, source="canonical producer alias")
                break
        if detail is None:
            detail = FieldExtractionDetail(value=None, display_value="", source="not extracted")
        if detail.machine_value is None:
            detail.machine_value = detail.value
        expanded[canonical] = detail.model_copy(deep=True)


def _extract_labeled_details(lines: list, field_labels: dict[str, tuple[str, ...]]) -> dict[str, FieldExtractionDetail]:
    extracted: dict[str, FieldExtractionDetail] = {}
    for line in lines:
        text = str(getattr(line, "text", "") or "").strip()
        if not text:
            continue
        for field_name, aliases in field_labels.items():
            if field_name in extracted:
                continue
            for alias in sorted(aliases, key=len, reverse=True):
                match = re.match(rf"^\s*(?:{alias})\s*(?:[:#=\-–]\s*|\s+)(.+?)\s*$", text, re.IGNORECASE)
                if not match:
                    continue
                value = match.group(1).strip(" \t:;#-")
                if not value:
                    continue
                extracted[field_name] = FieldExtractionDetail(
                    value=value,
                    display_value=value,
                    machine_value=value,
                    confidence=getattr(line, "confidence", None),
                    bbox=getattr(line, "bbox", None),
                    page=getattr(line, "page_number", None),
                    page_width=getattr(line, "page_width", None),
                    page_height=getattr(line, "page_height", None),
                    coordinate_space=getattr(line, "coordinate_space", None),
                    line_index=getattr(line, "line_index", None),
                    source="family-labeled OCR evidence",
                    evidence_text=text,
                )
                break
    if "packaging" not in extracted:
        packaging_pattern = re.compile(r"\b(?:en\s+sac(?:s)?|bags?|sacs?|bulk|vrac)\b(?:\s+(?:of\s+)?\d{1,3}\s?kg)?", re.IGNORECASE)
        for line in lines:
            text = str(getattr(line, "text", "") or "").strip()
            match = packaging_pattern.search(text)
            if not match:
                continue
            value = match.group(0).strip()
            extracted["packaging"] = FieldExtractionDetail(
                value=value,
                display_value=value,
                machine_value=value,
                confidence=getattr(line, "confidence", None),
                bbox=getattr(line, "bbox", None),
                page=getattr(line, "page_number", None),
                page_width=getattr(line, "page_width", None),
                page_height=getattr(line, "page_height", None),
                coordinate_space=getattr(line, "coordinate_space", None),
                line_index=getattr(line, "line_index", None),
                source="family-labeled OCR evidence",
                evidence_text=text,
            )
            break
    fallback_patterns = [
        ("number_of_bags", r"(\d[\d\s,.]*)\s*(?:bags?|sacs?)\b"),
        ("bag_weight", r"\b(\d+(?:[,.]\d+)?)\s*(?:kg|g)\b"),
    ]
    if "truck_count" in field_labels:
        fallback_patterns.append(("truck_count", r"(\d[\d\s,.]*)\s*(?:trucks?|camions?)\b"))
    for field_name, pattern in fallback_patterns:
        if field_name in extracted:
            continue
        for line in lines:
            text = str(getattr(line, "text", "") or "").strip()
            context_pattern = r"truck|camion" if field_name == "truck_count" else r"bag|sac|pack|condition|\bkg\b"
            if not text or not re.search(context_pattern, text, re.IGNORECASE):
                continue
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            value = match.group(0).strip()
            extracted[field_name] = FieldExtractionDetail(
                value=value,
                display_value=value,
                machine_value=value,
                confidence=getattr(line, "confidence", None),
                bbox=getattr(line, "bbox", None),
                page=getattr(line, "page_number", None),
                page_width=getattr(line, "page_width", None),
                page_height=getattr(line, "page_height", None),
                coordinate_space=getattr(line, "coordinate_space", None),
                line_index=getattr(line, "line_index", None),
                source="family-labeled OCR evidence",
                evidence_text=text,
            )
            break
    return extracted
