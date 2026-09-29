"""Family-scoped canonical review fields for Page 1 producer invoices."""
from __future__ import annotations

import re

from app.core.schemas import BoundingBox, FieldExtractionDetail, ProcessInvoiceResponse
from app.utils.helpers import parse_amount, parse_date
from app.services.table_regions import build_label_value_region

ENFIDHA_FAMILY = "ciments_enfidha_invoice_v1"
SOTACIB_FAMILIES = frozenset({"sotacib_kairouan_grey_invoice_v1", "sotacib_kasserine_white_invoice_v1"})
GENERAL_PRODUCER_FAMILY = "general_supplier_invoice"
PRODUCER_COMMON_FIELDS = (
    "seller", "invoice_number", "invoice_date", "client", "client_address", "consignee",
    "currency", "total", "total_amount_words", "hs_code", "incoterm", "origin",
    "destination", "payment", "packaging",
)
SOTACIB_EXTENSION_FIELDS = (
    "client_tax_id", "total_ht", "number_of_bags", "bag_weight", "integration_rate",
    "bank_account", "payment_method", "payment_terms",
)
PRODUCER_REVIEW_FIELDS = {
    GENERAL_PRODUCER_FAMILY: PRODUCER_COMMON_FIELDS,
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
    "total": ("total",),
    "total_ht": ("total_ht",),
    "hs_code": ("hs_code", "tariff_position", "position_tarifaire"),
    "incoterm": ("incoterm", "delivery"),
    "bank_account": ("bank_account", "supplier_bank_rib"),
    "iban": ("iban", "bank_iban", "supplier_bank_iban"),
    "swift": ("swift", "bank_swift", "supplier_bank_swift"),
    "number_of_bags": ("number_of_bags",),
    "bag_weight": ("bag_weight",),
}

COMMON_LABELS = {
    "invoice_number": (r"invoice\s*(?:number|n(?:o|°|º)?|#)", r"facture\s*(?:num[eé]ro|n(?:o|°|º)?|#)"),
    "invoice_date": (r"invoice\s*date", r"date\s*(?:de\s*)?facture", r"^date\b"),
    "client": (r"^cl[l1i]ent$", r"^customer$", r"^buyer$", r"^bill\s*to$", r"^acheteur$"),
    "client_address": (r"client\s*address", r"customer\s*address", r"adresse\s*(?:du\s*)?client", r"^adresse$", r"^adress$", r"^address$"),
    "consignee": (r"consignee", r"dest(?:in|ln)ata(?:ire|lre)"),
    "currency": (r"^currency$", r"devise"),
    "total": (r"total\s*(?:including\s*(?:all\s*)?tax(?:es)?|ttc|amount|due|price)", r"grand\s*total", r"montant\s*total", r"^total$"),
    "total_amount_words": (r"amount\s*in\s*words", r"total\s*in\s*words", r"montant\s*en\s*lettres", r".*amounts?\s*to\s*", r".*\bsomme\s*(?:de)?\s*"),
    "hs_code": (r"hs\s*code", r"position\s*tarifaire"),
    "incoterm": (r"^incoterm$", r"delivery\s*term", r"conditions?\s*de\s*livraison"),
    "origin": (r"^origin$", r"origine", r"country\s*of\s*origin"),
    "destination": (r"^destination$", r"country\s*of\s*destination"),
    "packaging": (r"pack(?:ing|aging)", r"conditionnement", r"emballage"),
}
ENFIDHA_LABELS = {
    **COMMON_LABELS,
    "client_rc": (r"client\s*(?:r\.?c\.?|register(?:ed)?\s*number)", r"r\.?c\.?\s*(?:du\s*)?client"),
    "consignee_address": (r"consignee\s*address", r"adresse\s*du\s*destinataire"),
    "proforma_invoice_number": (r"pro\s*forma\s*(?:invoice\s*)?(?:n(?:o|°|º)|number|#)", r"facture\s*pro\s*forma\s*(?:n(?:o|°|º)|num[eé]ro|#)"),
    "proforma_invoice_date": (r"pro\s*forma\s*date", r"date\s*(?:de\s*)?facture\s*pro\s*forma"),
    "shipment": (r"^shipment$", r"exp[eé]dition"),
    "payment": (r"^payment$", r"^paiement$"),
    "bank": (r"^bank$", r"^banque$", r"bank\s*details"),
    "iban": (r"^iban$",),
    "swift": (r"^(?:swift|bic)(?:\s*code)?$",),
    "number_of_bags": (r"number\s*of\s*bags", r"nombre\s*de\s*sacs"),
    "bag_weight": (r"(?:weight|poids)\s*(?:per\s*)?(?:bag|sac)", r"(?:bag|sac)\s*weight"),
    "truck_count": (r"(?:number\s*of\s*)?(?:trucks?|camions?)",),
}
_PROFORMA_REFERENCE_LINE = re.compile(
    r"\bconform\s+to\s+(?:the\s+)?pro\s*forma\s+invoice\s*"
    r"(?:n(?:o|[°º])?\s*[:#.]?\s*|number\s*[:#.]?\s*|#\s*)?"
    r"(?P<number>[A-Z0-9][A-Z0-9/-]{0,30})\s+"
    r"(?:du|of|dated)\s+"
    r"(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b",
    re.IGNORECASE,
)
_PROFORMA_REFERENCE_ANCHOR = re.compile(
    r"\bconform\b.{0,35}\bto\b.{0,20}\bpro\s*forma\b.{0,24}\binvoice\b",
    re.IGNORECASE,
)
_PROFORMA_REFERENCE_NUMBER = re.compile(r"^[A-Z0-9]+(?:[/-][A-Z0-9]+)*$", re.IGNORECASE)
_PROFORMA_REFERENCE_DATE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")
SOTACIB_LABELS = {
    **COMMON_LABELS,
    "client_tax_id": (r"client\s*(?:tax\s*(?:id|number)|matricule\s*fiscal)", r"matricule\s*fiscal\s*(?:du\s*)?client", r"matricule\s*fiscale?(?:\s*[?d]*)?"),
    "total_ht": (r"total\s*(?:ht|excluding\s*tax)", r"montant\s*ht", r"total\s*h\.?t\.?"),
    "number_of_bags": (r"number\s*of\s*bags", r"nombre\s*de\s*sacs"),
    "bag_weight": (r"(?:weight|poids)\s*(?:per\s*)?(?:bag|sac)", r"(?:bag|sac)\s*weight"),
    "integration_rate": (r"integration\s*rate", r"taux\s*d.?int[eé]gration"),
    "bank_account": (r"bank\s*account(?:\s*(?:number|n°))?", r"num[eé]ro\s*(?:de\s*)?compte\s*bancaire", r"compte\s*bancaire"),
    "payment_method": (r"moyen\s*de\s*r[eè]glement", r"payment\s*method"),
    "payment_terms": (r"mode\s*de\s*r[eè]glement", r"payment\s*terms"),
}


def recover_sotacib_total_ht(
    lines: list,
    family: str | None,
    images: list,
    ocr_engine,
    *,
    physical_page_numbers: tuple[int, ...] | list[int] | None = None,
) -> tuple[FieldExtractionDetail | None, list, dict]:
    """Read only the value cell beside an explicit SOTACIB Total HT label."""
    debug = {"attempted": False, "region": None, "reason": "not_applicable"}
    if family not in SOTACIB_FAMILIES:
        return None, [], debug

    existing = _extract_labeled_details(lines, SOTACIB_LABELS).get("total_ht")
    if existing and existing.value not in (None, ""):
        debug["reason"] = "trustworthy_label_value_already_present"
        return existing, [], debug

    label = next((line for line in lines if _is_bare_total_ht_label(line)), None)
    if label is None:
        debug["reason"] = "explicit_total_ht_label_not_found"
        return None, [], debug
    if not images or not callable(getattr(ocr_engine, "run_targeted_region", None)):
        debug["reason"] = "targeted_ocr_unavailable"
        return None, [], debug
    label_page = int(getattr(label, "page_number", 1) or 1)
    page_numbers = tuple(physical_page_numbers or range(1, len(images) + 1))
    try:
        image_index = page_numbers.index(label_page)
    except ValueError:
        debug["reason"] = "label_page_image_unavailable"
        return None, [], debug
    if image_index >= len(images) or not isinstance(getattr(label, "bbox", None), BoundingBox):
        debug["reason"] = "label_geometry_unavailable"
        return None, [], debug

    image = images[image_index]
    region = build_label_value_region(
        image, label.bbox, name="sotacib_total_ht_value_cell",
        page_width=getattr(label, "page_width", None),
        page_height=getattr(label, "page_height", None),
    )
    if region is None:
        debug["reason"] = "value_cell_region_unavailable"
        return None, [], debug
    debug.update({
        "attempted": True,
        "reason": "explicit_label_value_missing",
        "region": region.name,
        "region_bbox": {"x1": region.x_offset, "y1": region.y_offset,
                        "x2": region.coordinates[2], "y2": region.coordinates[3]},
        "physical_page": label_page,
    })
    recovered = ocr_engine.run_targeted_region(image, region, page_number=label_page) or []
    candidates = []
    label_center_y = (label.bbox.y1 + label.bbox.y2) / 2
    label_height = max(12.0, label.bbox.y2 - label.bbox.y1)
    region_box = region.coordinates
    for line in recovered:
        text = str(getattr(line, "text", "") or "").strip()
        if not _is_money_value(text):
            continue
        box = getattr(line, "bbox", None)
        if not isinstance(box, BoundingBox):
            continue
        if getattr(line, "page_number", None) != label_page:
            continue
        center_x = (box.x1 + box.x2) / 2
        center_y = (box.y1 + box.y2) / 2
        if center_x <= label.bbox.x2 or abs(center_y - label_center_y) > max(label_height * 2.0, 36.0):
            continue
        if not (region_box[0] <= center_x <= region_box[2] and region_box[1] <= center_y <= region_box[3]):
            continue
        amount = parse_amount(re.sub(r"[^\d,.+\-]", "", text))
        if amount is None:
            continue
        candidates.append((line, amount))

    distinct = {amount for _line, amount in candidates}
    if len(distinct) != 1:
        debug["reason"] = "no_unambiguous_money_value_in_target_cell"
        return None, recovered, debug
    selected = max(candidates, key=lambda pair: float(getattr(pair[0], "confidence", 0) or 0))[0]
    amount = candidates[0][1]
    detail = _detail(selected, amount, "regional_fallback (SOTACIB Total HT value cell)")
    detail.display_value = selected.text.strip()
    debug.update({"reason": "regional_value_recovered", "confidence": selected.confidence,
                  "evidence_text": selected.text, "evidence_bbox": selected.bbox.model_dump(mode="json")})
    return detail, recovered, debug


def _is_bare_total_ht_label(line) -> bool:
    text = _plain_line(line).strip()
    return bool(re.fullmatch(r"(?:total\s*h\.?t\.?|montant\s*h\.?t\.?)\s*[:#=\-–]?", text, re.I))


def _is_money_value(text: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:(?:eur|tnd|€)\s*)?[+-]?\d[\d\s.,]*(?:\s*(?:eur|tnd|€))?\s*", text, re.I))

GENERIC_EXTENSION_LABELS = {
    "total_ht": (r"total\s*(?:ht|h\.?t\.?|excluding\s*tax)", r"montant\s*ht"),
    "payment": (r"^payment$", r"^paiement$"),
    "incoterm": (r"^incoterm$", r"delivery\s*term", r"conditions?\s*de\s*livraison"),
    "origin": (r"^origin$", r"origine", r"country\s*of\s*origin"),
    "destination": (r"^destination$", r"country\s*of\s*destination"),
    "packaging": (r"pack(?:ing|aging)", r"conditionnement", r"emballage"),
    "number_of_bags": (r"number\s*of\s*bags", r"nombre\s*de\s*sacs", r"colisage"),
    "bag_weight": (r"(?:weight|poids)\s*(?:per\s*)?(?:bag|sac)", r"(?:bag|sac)\s*weight"),
    "payment_method": (r"moyen\s*de\s*r[eè]glement", r"payment\s*method"),
    "payment_terms": (r"mode\s*de\s*r[eè]glement", r"payment\s*terms"),
    "integration_rate": (r"integration\s*rate", r"taux\s*d.?int[eé]gration"),
    "client_tax_id": (r"client\s*(?:tax\s*(?:id|number)|matricule\s*fiscal)", r"matricule\s*fiscale?(?:\s*[?d]*)?"),
    "client_rc": (r"client\s*(?:r\.?c\.?|registration\s*(?:number|no))", r"^r\.?c\.?$"),
    "bank": (r"^bank$", r"^banque$", r"bank\s*details"),
    "iban": (r"^iban$",),
    "swift": (r"^(?:swift|bic)(?:\s*code)?$",),
    "bank_account": (r"(?:bank\s*)?account(?:\s*(?:number|no|n°))?", r"num[eé]ro\s*(?:de\s*)?compte\s*bancaire"),
    "consignee_address": (r"consignee\s*address", r"adresse\s*du\s*destinataire"),
    "proforma_invoice_number": (r"pro\s*forma\s*(?:invoice\s*)?(?:n(?:o|°|º)?|number|#)",),
    "proforma_invoice_date": (r"pro\s*forma\s*date",),
    "shipment": (r"^shipment$", r"exp[eé]dition"),
    "hs_code": (r"hs\s*code", r"tariff\s*code", r"position\s*tarifaire"),
}


def apply_producer_review_fields(response: ProcessInvoiceResponse, family: str | None, ocr_lines: list | None = None) -> None:
    """Expose canonical display keys while retaining their source evidence."""
    family_key = family if family in PRODUCER_REVIEW_FIELDS else GENERAL_PRODUCER_FAMILY
    allowed = PRODUCER_REVIEW_FIELDS.get(family_key)
    if not allowed or not hasattr(response, "detected_fields") or not hasattr(response, "expanded_fields"):
        return
    detected = response.detected_fields.model_dump(mode="python")
    expanded = response.expanded_fields
    labels = _labels_for_family(family_key)
    semantic = _extract_labeled_details(ocr_lines or [], labels)
    semantic.update(_extract_geometric_parties(ocr_lines or [], semantic))
    merge_producer_semantics(expanded, semantic, family_key)
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


def merge_producer_semantics(
    expanded_fields: dict[str, FieldExtractionDetail],
    producer_semantics: dict[str, FieldExtractionDetail],
    family: str | None,
) -> None:
    """Publish family-extracted canonical values ahead of legacy response data.

    Human corrections and explicit template enhancements remain authoritative;
    otherwise the producer semantic value replaces a generic field with the
    same presentation key.
    """
    family_key = family if family in PRODUCER_REVIEW_FIELDS else GENERAL_PRODUCER_FAMILY
    allowed = set(PRODUCER_REVIEW_FIELDS.get(family_key, ()))
    for field_name, detail in producer_semantics.items():
        if field_name not in allowed or detail is None or detail.value in (None, ""):
            continue
        current = expanded_fields.get(field_name)
        source = str(getattr(current, "source", "") or "").casefold()
        if current is not None and current.value not in (None, "") and any(
            marker in source for marker in ("human correction", "manual correction", "review correction", "known-template", "template enhancement")
        ):
            continue
        expanded_fields[field_name] = detail.model_copy(deep=True)


def sanitize_legacy_producer_financial_fields(fields, lines: list | None) -> None:
    """Remove legacy numeric picks unless a nearby OCR label proves their meaning."""
    lines = lines or []
    has_ht = _has_nearby_labeled_amount(lines, (r"total\s*(?:ht|h\.?t\.?)", r"montant\s*ht", r"subtotal\s*ht"))
    has_ttc = _has_nearby_labeled_amount(lines, (r"total\s*(?:ttc|including\s*(?:all\s*)?tax(?:es)?)", r"grand\s*total", r"amount\s*due", r"total\s*due"))
    has_tax_amount = _has_nearby_labeled_amount(lines, (r"(?:montant\s*)?(?:tva|vat|tax)\s*(?:amount|montant)?",))
    has_tax_rate = any(
        re.search(r"(?:tax|vat|tva)\s*(?:rate|taux)|taux\s*(?:de\s*)?(?:tva|taxe)", _plain_line(line), re.I)
        and not re.search(r"integration|int[eé]gration", _plain_line(line), re.I)
        for line in lines
    )
    if not has_ht:
        fields.amount_ht = None
    if not has_ttc:
        fields.amount_ttc = None
    if not has_tax_amount:
        fields.tva_amount = None
    if not has_tax_rate:
        fields.tax_rate = None
    if not any(re.search(r"purchase\s*order|bon\s*de\s*commande|\bpo\s*(?:no|number|#)", _plain_line(line), re.I) for line in lines):
        fields.purchase_order_number = None


def prepare_producer_fields(fields, lines: list | None, family: str | None) -> dict[str, FieldExtractionDetail]:
    """Apply generic semantic field evidence before quality/financial validation."""
    labels = _labels_for_family(family or GENERAL_PRODUCER_FAMILY)
    semantic = _extract_labeled_details(lines or [], labels)
    semantic.update(_extract_geometric_parties(lines or [], semantic))
    for canonical, target in (("seller", "supplier_name"), ("client", "customer_name"),
                              ("client_address", "customer_address"), ("invoice_number", "invoice_number"),
                              ("invoice_date", "invoice_date")):
        detail = semantic.get(canonical)
        if detail is None or not detail.value:
            continue
        if target == "invoice_date":
            parsed = parse_date(str(detail.display_value or detail.value))
            if parsed:
                fields.invoice_date = parsed
        else:
            setattr(fields, target, str(detail.display_value or detail.value).strip())
    sanitize_legacy_producer_financial_fields(fields, lines)
    return semantic


def _labels_for_family(family: str) -> dict[str, tuple[str, ...]]:
    if family == ENFIDHA_FAMILY:
        return ENFIDHA_LABELS
    if family in SOTACIB_FAMILIES:
        return SOTACIB_LABELS
    return {**COMMON_LABELS, **GENERIC_EXTENSION_LABELS}


def _extract_geometric_parties(lines: list, existing: dict[str, FieldExtractionDetail]) -> dict[str, FieldExtractionDetail]:
    result: dict[str, FieldExtractionDetail] = {}
    positioned = [line for line in lines if isinstance(getattr(line, "bbox", None), BoundingBox)]
    if not positioned:
        return result
    page_height = max((getattr(line, "page_height", None) or line.bbox.y2 for line in positioned), default=0)
    page_width = max((getattr(line, "page_width", None) or line.bbox.x2 for line in positioned), default=0)
    consignee = existing.get("consignee")
    # On unlabeled party blocks, the customer is the business-name line in the
    # upper party region immediately before a separately labeled consignee.
    if consignee and consignee.bbox and page_height:
        limit = consignee.bbox.y1
        candidates = [line for line in positioned if line.bbox.y1 < limit and line.bbox.y1 / page_height > 0.16
                      and line.bbox.y1 / page_height < 0.55 and _looks_like_party(line.text)]
        candidates = [line for line in candidates if not re.search(r"invoice|facture|date|shipment|payment|address|adress|rc\s*n", line.text, re.I)]
        if candidates and not _usable_party(existing.get("client")):
            line = max(candidates, key=lambda item: (item.bbox.y1, _party_score(item)))
            result["client"] = _detail(line, line.text.strip(), "generic party-block geometry")
    client = result.get("client") or existing.get("client")
    if client and client.bbox:
        # Registration numbers belong to the nearby client block, not a
        # visually prominent seller registration in the page masthead.
        if "client_rc" not in existing:
            for line in positioned:
                text = str(line.text or "").strip()
                if not re.search(r"\b(?:r\.?c\.?|registration\s*(?:number|no))\b", text, re.I):
                    continue
                if line.bbox.y1 < client.bbox.y1 or (consignee and consignee.bbox and line.bbox.y1 >= consignee.bbox.y1):
                    continue
                value = re.split(r"[:#=]", text, maxsplit=1)[-1].strip(" :#-")
                if value and re.search(r"\d", value):
                    result["client_rc"] = _detail(line, value, "generic client-block registration label")
                    break
        # A separate address line following the consignee label is the
        # consignee address; it is never copied into client_address.
        if consignee and consignee.bbox and "consignee_address" not in existing:
            for line in positioned:
                if line.bbox.y1 <= consignee.bbox.y1 or line.bbox.y1 - consignee.bbox.y2 > 100:
                    continue
                if not re.match(r"\s*(?:address|adress|adresse)\b", str(line.text or ""), re.I):
                    continue
                value = re.sub(r"^\s*(?:address|adress|adresse)\s*[:#=\-–]?\s*", "", str(line.text or ""), flags=re.I).strip()
                source_line = line
                if not value:
                    paired = _nearest_labeled_value(lines, line, "consignee_address")
                    if paired:
                        value, source_line = paired
                if value:
                    result["consignee_address"] = _detail(source_line, value, "generic consignee address geometry")
                    break
    if not _usable_party(existing.get("seller")):
        candidates = [line for line in positioned if page_height and line.bbox.y1 / page_height <= 0.20 and _looks_like_party(line.text)]
        candidates = [line for line in candidates if not re.search(r"email|tel|fax|www|capital|capital|adresse|address", line.text, re.I)]
        if candidates:
            line = max(candidates, key=lambda item: (
                _party_score(item)[0] + (0.3 if re.search(r"\b(?:sa|sarl|ste|societe|company|inc)\b", item.text, re.I) else 0),
                _party_score(item)[1], _party_score(item)[2],
            ))
            result["seller"] = _detail(line, line.text.strip(), "generic issuer-header geometry")
    # Use an explicitly labeled buyer/client address when one exists; if a
    # consignee follows, do not silently reuse the consignee address as client.
    return result


def _has_nearby_labeled_amount(lines: list, aliases: tuple[str, ...]) -> bool:
    numeric = re.compile(r"(?<![A-Za-z])(?:[$€£]\s*)?\d[\d\s.,]*(?:\s?(?:EUR|TND|USD|€))?(?![A-Za-z])", re.I)
    for label in lines:
        text = _plain_line(label)
        if not any(re.search(alias, text, re.I) for alias in aliases):
            continue
        if numeric.search(str(getattr(label, "text", "") or "")):
            return True
        box = getattr(label, "bbox", None)
        if not box:
            continue
        height = max(12, box.y2 - box.y1)
        for value in lines:
            value_text = str(getattr(value, "text", "") or "")
            value_box = getattr(value, "bbox", None)
            if value is label or not value_box or not numeric.fullmatch(value_text.strip()):
                continue
            if value_box.y1 <= box.y2 + 1.5 * height and value_box.y2 >= box.y1 - 1.5 * height and value_box.x1 >= box.x1:
                return True
    return False


def _plain_line(line) -> str:
    from app.utils.helpers import strip_accents
    return strip_accents(str(getattr(line, "text", "") or "")).lower()


def _normalize_payment_transcription(value: str) -> str:
    """Repair only clear OCR variants of the printed 'bank transfer' method."""
    match = re.match(
        r"^(?P<prefix>\s*bank)(?P<gap>\s*)(?P<method>(?:t|ty)?ransfer(?:t)?)(?P<suffix>\b.*)$",
        value,
        re.IGNORECASE,
    )
    if not match or match.group("method").casefold() == "transfer":
        return value
    method = match.group("method")
    if method.isupper():
        normalized_method = "TRANSFER"
    elif method[:1].isupper():
        normalized_method = "Transfer"
    else:
        normalized_method = "transfer"
    return f"{match.group('prefix')} {normalized_method}{match.group('suffix')}"


def recover_enfidha_proforma_reference(
    lines: list,
    family: str | None,
    images: list,
    ocr_engine,
    *,
    physical_page_numbers: tuple[int, ...] | list[int] | None = None,
) -> tuple[dict[str, FieldExtractionDetail], list, dict]:
    """Extract a split proforma reference, using one label-relative crop only if needed."""
    debug = {"attempted": False, "reason": "not_applicable", "region": None, "physical_page": None}
    if family != ENFIDHA_FAMILY:
        return {}, [], debug

    found = _proforma_reference_details(lines)
    required = {"proforma_invoice_number", "proforma_invoice_date"}
    if required.issubset(found):
        debug["reason"] = "reference_present_in_existing_ocr"
        return found, [], debug

    anchor = next((line for line in lines if _PROFORMA_REFERENCE_ANCHOR.search(_plain_line(line))), None)
    if anchor is None:
        debug["reason"] = "proforma_reference_label_not_found"
        return found, [], debug
    if not images or not callable(getattr(ocr_engine, "run_targeted_region", None)):
        debug["reason"] = "targeted_ocr_unavailable"
        return found, [], debug

    page_numbers = tuple(physical_page_numbers or range(1, len(images) + 1))
    try:
        image_index = page_numbers.index(int(getattr(anchor, "page_number", 1) or 1))
    except ValueError:
        debug["reason"] = "label_page_image_unavailable"
        return found, [], debug
    if image_index >= len(images) or not isinstance(getattr(anchor, "bbox", None), BoundingBox):
        debug["reason"] = "label_geometry_unavailable"
        return found, [], debug

    from app.services.table_regions import build_label_value_region

    region = build_label_value_region(
        images[image_index], anchor.bbox, name="producer_proforma_reference",
        page_width=getattr(anchor, "page_width", None), page_height=getattr(anchor, "page_height", None),
    )
    if region is None:
        debug["reason"] = "reference_value_region_unavailable"
        return found, [], debug
    page_number = int(getattr(anchor, "page_number", 1) or 1)
    debug.update({"attempted": True, "reason": "reference_value_missing", "region": region.name,
                  "physical_page": page_number})
    targeted_lines = ocr_engine.run_targeted_region(images[image_index], region, page_number=page_number) or []
    found.update({name: detail for name, detail in _proforma_reference_details([*lines, *targeted_lines]).items()
                  if name not in found})
    debug["reason"] = "reference_recovered" if required.issubset(found) else "targeted_reference_incomplete"
    debug["added_lines"] = len(targeted_lines)
    return found, targeted_lines, debug


def _proforma_reference_details(lines: list) -> dict[str, FieldExtractionDetail]:
    labels = [line for line in lines if _PROFORMA_REFERENCE_ANCHOR.search(_plain_line(line))
              and isinstance(getattr(line, "bbox", None), BoundingBox)]
    for label in labels:
        text = str(getattr(label, "text", "") or "").strip()
        inline = _PROFORMA_REFERENCE_LINE.search(text)
        if inline:
            result = {}
            raw_number = inline.group("number").strip(" .,:;#-")
            raw_date = inline.group("date")
            if raw_number:
                result["proforma_invoice_number"] = _proforma_detail(label, label, raw_number, raw_number)
            parsed_date = parse_date(raw_date)
            if parsed_date:
                result["proforma_invoice_date"] = _proforma_detail(
                    label, label, parsed_date.isoformat(), raw_date,
                )
            if result:
                return result

        page_lines = [line for line in lines if getattr(line, "page_number", None) == getattr(label, "page_number", None)
                      and isinstance(getattr(line, "bbox", None), BoundingBox)]
        width = float(getattr(label, "page_width", 0) or max((line.bbox.x2 for line in page_lines), default=0))
        height = float(getattr(label, "page_height", 0) or max((line.bbox.y2 for line in page_lines), default=0))
        if width <= 0 or height <= 0:
            continue
        label_box = label.bbox
        label_y = (label_box.y1 + label_box.y2) / 2
        vertical_tolerance = max(18.0, (label_box.y2 - label_box.y1) * 1.6)
        candidates = [line for line in lines if line is not label and
                      getattr(line, "page_number", None) == getattr(label, "page_number", None) and
                      isinstance(getattr(line, "bbox", None), BoundingBox) and
                      line.bbox.x1 >= label_box.x2 - width * 0.005 and
                      abs((line.bbox.y1 + line.bbox.y2) / 2 - label_y) <= vertical_tolerance]
        candidates.sort(key=lambda line: (line.bbox.x1, abs((line.bbox.y1 + line.bbox.y2) / 2 - label_y)))

        number_pair = None
        date_pair = None
        for line in candidates:
            candidate_text = str(getattr(line, "text", "") or "").strip().strip(" .,:;#")
            if not candidate_text:
                continue
            raw_date_match = _PROFORMA_REFERENCE_DATE.search(candidate_text)
            if raw_date_match:
                parsed_date = parse_date(raw_date_match.group(0))
                if parsed_date and date_pair is None:
                    date_pair = (line, raw_date_match.group(0), parsed_date)
                continue
            if (number_pair is None and _PROFORMA_REFERENCE_NUMBER.fullmatch(candidate_text)
                    and re.search(r"\d", candidate_text)
                    and not re.fullmatch(r"(?:du|of|dated)", candidate_text, re.IGNORECASE)):
                number_pair = (line, candidate_text)

        result = {}
        if number_pair:
            result["proforma_invoice_number"] = _proforma_detail(
                label, number_pair[0], number_pair[1], number_pair[1],
            )
        if date_pair:
            result["proforma_invoice_date"] = _proforma_detail(
                label, date_pair[0], date_pair[2].isoformat(), date_pair[1],
            )
        if result:
            return result
    return {}


def _proforma_detail(label, value_line, value: str, raw_value: str) -> FieldExtractionDetail:
    observations = [label] if value_line is label else [label, value_line]
    boxes = [line.bbox for line in observations if isinstance(getattr(line, "bbox", None), BoundingBox)]
    detail = FieldExtractionDetail(
        value=value, display_value=raw_value, machine_value=raw_value, canonical_value=value,
        normalized_value=value, confidence=min((getattr(line, "confidence", None) or 0.0) for line in observations),
        bbox=BoundingBox(x1=min(box.x1 for box in boxes), y1=min(box.y1 for box in boxes),
                         x2=max(box.x2 for box in boxes), y2=max(box.y2 for box in boxes)) if boxes else None,
        page=getattr(label, "page_number", None), page_width=getattr(label, "page_width", None),
        page_height=getattr(label, "page_height", None), coordinate_space=getattr(label, "coordinate_space", None),
        line_index=getattr(label, "line_index", None), source="label-relative proforma invoice reference",
        evidence_text="\n".join(str(getattr(line, "text", "") or "").strip() for line in observations),
    )
    return detail


def _looks_like_party(text: str) -> bool:
    value = str(text or "").strip()
    return len(value) >= 5 and sum(char.isalpha() for char in value) >= 4 and not re.search(r"\d{5,}", value)


def _usable_party(detail: FieldExtractionDetail | None) -> bool:
    value = str(getattr(detail, "value", "") or "").strip()
    return bool(value and not re.fullmatch(r"shipment|payment|origin|destination|invoice|facture|client|customer|buyer", value, re.I))


def _party_score(line) -> tuple[float, float, float]:
    value = str(getattr(line, "text", "") or "").strip()
    box = line.bbox
    caps = sum(char.isupper() for char in value) / max(1, sum(char.isalpha() for char in value))
    return (caps, float(getattr(line, "confidence", 0) or 0), float(box.x2 - box.x1))


def _detail(line, value: str, source: str) -> FieldExtractionDetail:
    return FieldExtractionDetail(value=value, display_value=str(value), machine_value=value,
        confidence=getattr(line, "confidence", None), bbox=getattr(line, "bbox", None),
        page=getattr(line, "page_number", None), page_width=getattr(line, "page_width", None),
        page_height=getattr(line, "page_height", None), coordinate_space=getattr(line, "coordinate_space", None),
        line_index=getattr(line, "line_index", None), source=source, evidence_text=getattr(line, "text", None))


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
                exact_label = alias.startswith("^") and alias.endswith("$")
                alias_body = alias.lstrip("^").rstrip("$")
                if exact_label:
                    pattern = rf"^\s*(?:{alias_body})\s*(?:[:#=\-–]\s*(.*?))?\s*$"
                else:
                    pattern = rf"^\s*(?:{alias_body})\s*(?:[:#=\-–]\s*|\s+)?(.*?)\s*$"
                match = re.match(pattern, text, re.IGNORECASE)
                if not match:
                    continue
                value = (match.group(1) or "").strip(" \t:;#-")
                paired_line = None
                if not value or re.fullmatch(r"[!?.:]+", value) or (field_name == "client_tax_id" and re.fullmatch(r"[?d]+", value, re.I)):
                    paired_value = _nearest_labeled_value(lines, line, field_name)
                    if paired_value:
                        value, paired_line = paired_value
                value = value.strip(" \t:;#-!")
                if not value:
                    continue
                if field_name in {"total", "total_ht"}:
                    amount = parse_amount(re.sub(r"[^\d,.+\-]", "", value))
                    if amount is None:
                        continue
                    evidence_line = paired_line or line
                    evidence_source = str(getattr(evidence_line, "source", "") or "")
                    source = (
                        "regional_fallback (SOTACIB Total HT value cell)"
                        if field_name == "total_ht" and evidence_source == "regional_fallback"
                        else "generic semantic total label" if field_name == "total"
                        else "generic semantic Total HT label"
                    )
                    extracted[field_name] = _detail(evidence_line, amount, source)
                    extracted[field_name].display_value = value
                    extracted[field_name].evidence_text = f"{text} {getattr(evidence_line, 'text', '')}".strip()
                else:
                    evidence_line = paired_line or line
                    canonical_value = _normalize_payment_transcription(value) if field_name == "payment" else value
                    extracted[field_name] = _detail(evidence_line, canonical_value, "generic semantic label")
                    if canonical_value != value:
                        extracted[field_name].machine_value = value
                        extracted[field_name].canonical_value = canonical_value
                        extracted[field_name].normalized_value = canonical_value
                        extracted[field_name].display_value = canonical_value
                    extracted[field_name].evidence_text = (
                        text if evidence_line is line
                        else f"{text} {getattr(evidence_line, 'text', '')}".strip()
                    )
                break
    if "proforma_invoice_number" in field_labels or "proforma_invoice_date" in field_labels:
        for line in lines:
            match = _PROFORMA_REFERENCE_LINE.search(str(getattr(line, "text", "") or ""))
            if not match:
                continue
            if "proforma_invoice_number" in field_labels and "proforma_invoice_number" not in extracted:
                number = match.group("number").strip(" .,:;#-")
                if number:
                    extracted["proforma_invoice_number"] = _detail(
                        line, number, "generic proforma invoice reference label",
                    )
            if "proforma_invoice_date" in field_labels and "proforma_invoice_date" not in extracted:
                raw_date = match.group("date")
                parsed_date = parse_date(raw_date)
                if parsed_date:
                    detail = _detail(line, parsed_date.isoformat(), "generic proforma invoice reference label")
                    detail.display_value = raw_date
                    detail.machine_value = raw_date
                    detail.canonical_value = parsed_date.isoformat()
                    detail.normalized_value = parsed_date.isoformat()
                    extracted["proforma_invoice_date"] = detail
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
        ("number_of_bags", r"(?P<value>\d[\d\s,.]*)\s*(?:bags?|sacs?)\b"),
        ("bag_weight", r"\b(?P<value>\d+(?:[,.]\d+)?)\s*(?:kgs?|g(?:rams?)?)\b"),
    ]
    if "truck_count" in field_labels:
        fallback_patterns.append(("truck_count", r"(?P<value>\d[\d\s,.]*)\s*(?:trucks?|camions?)\b"))
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
            value = match.group("value").strip()
            display_value = match.group(0).strip()
            extracted[field_name] = FieldExtractionDetail(
                value=value,
                display_value=display_value,
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


def _nearest_labeled_value(lines: list, label, field_name: str | None = None) -> tuple[str, object] | None:
    box = getattr(label, "bbox", None)
    page = getattr(label, "page_number", None)
    if not box:
        return None
    height = max(12.0, box.y2 - box.y1)
    candidates = []
    for line in lines:
        other = getattr(line, "bbox", None)
        text = str(getattr(line, "text", "") or "").strip()
        if line is label or not other or getattr(line, "page_number", page) != page or not text:
            continue
        if re.fullmatch(r"[\w .'-]{1,30}\s*[:#=]", text):
            continue
        if re.match(r"\s*:?\s*(?:invoice|facture|date|client|customer|buyer|adresse|address|consignee|dest|total|hs\s*code|payment|paiement|origin|destination|incoterm|shipment|taux|position|packaging|emballage|matricule|fiscal|r\.?c\.?)\b", text, re.I):
            continue
        center_gap_y = abs(((box.y1 + box.y2) / 2) - ((other.y1 + other.y2) / 2))
        if center_gap_y > max(38.0, height * 1.8) or other.x1 < box.x1 - 12:
            continue
        right_bias = -((other.x1 + other.x2) / 2) * 0.035 if field_name == "total" else 0
        score = center_gap_y + abs(other.x1 - box.x2) * 0.01 + right_bias
        if field_name == "total_amount_words" and ((other.y1 + other.y2) / 2) <= ((box.y1 + box.y2) / 2):
            continue
        candidates.append((score, text, line))
    if not candidates:
        return None
    if field_name == "total":
        _score, text, line = max(candidates, key=lambda item: (item[2].bbox.x1 + item[2].bbox.x2, -item[0]))
    else:
        _score, text, line = min(candidates, key=lambda item: item[0])
    return text, line
