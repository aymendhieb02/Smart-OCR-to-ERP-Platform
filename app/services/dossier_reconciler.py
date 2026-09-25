"""Read-only relationships between independently extracted dossier documents."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Sequence

from app.core.schemas import DossierRelationship


RELATIONS = (
    ("producer_invoice_reference", "producer", "invoice_number", "ruspina", "referenced_invoice"),
    ("producer_total_customs_ptfn", "producer", "amount_ttc", "customs", "ptfn_amount"),
    ("producer_seller_customs_exporter", "producer", "supplier_name", "customs", "exporter"),
    ("producer_buyer_customs_importer", "producer", "customer_name", "customs", "importer"),
    ("producer_ruspina_origin", "producer", "origin", "ruspina", "origin"),
)
_CURRENCIES = {"EUR": "EUR", "EURO": "EUR", "EUROS": "EUR", "USD": "USD", "TND": "TND"}


def reconcile_dossier(documents: Sequence[object]) -> tuple[DossierRelationship, ...]:
    """Observe relationships without changing document fields, validation, or ERP readiness."""
    roles: dict[str, list[object]] = {"producer": [], "ruspina": [], "customs": []}
    for document in documents:
        group = document.group
        if group.document_family == "ruspina_reinvoice_v1":
            roles["ruspina"].append(document)
        elif group.document_type == "customs_declaration":
            roles["customs"].append(document)
        elif group.document_type == "commercial_invoice":
            roles["producer"].append(document)
    results = []
    for relation_type, left_role, left_field, right_role, right_field in RELATIONS:
        left_doc = roles[left_role][0] if len(roles[left_role]) == 1 else None
        right_doc = roles[right_role][0] if len(roles[right_role]) == 1 else None
        left_value, left_confidence = _field(left_doc, left_field)
        right_value, right_confidence = _field(right_doc, right_field)
        if left_doc is None or right_doc is None:
            status, reason = "unavailable", "A required document is missing or its role is ambiguous."
        elif left_value is None or right_value is None:
            status, reason = "unavailable", "A required independently extracted field is missing."
        elif relation_type == "producer_invoice_reference":
            left_id, right_id = _identifier(left_value), _identifier(right_value)
            status, reason = _equality(left_id, right_id, "identifier")
        elif relation_type == "producer_total_customs_ptfn":
            producer_currency = _currency(_field(left_doc, "currency")[0])
            customs_currency = _ptfn_currency(right_doc)
            if producer_currency is None or customs_currency is None:
                status, reason = "unavailable", "Comparable currency evidence is missing."
            elif producer_currency != customs_currency:
                status, reason = "unavailable", "Producer and PTFN currencies are incompatible."
            else:
                status, reason = _equality(_decimal(left_value), _decimal(right_value), "amount")
        elif relation_type in {"producer_seller_customs_exporter", "producer_buyer_customs_importer"}:
            status, reason = _party_comparison(left_value, right_value)
        else:
            status, reason = _equality(_country(left_value), _country(right_value), "origin")
        results.append(DossierRelationship(
            type=relation_type, status=status,
            left_document_id=left_doc.group.group_id if left_doc else None,
            left_field=left_field, left_value=left_value,
            right_document_id=right_doc.group.group_id if right_doc else None,
            right_field=right_field, right_value=right_value,
            reason=reason,
            confidence=min(c for c in (left_confidence, right_confidence) if c is not None)
            if left_confidence is not None and right_confidence is not None else None,
        ))
    return tuple(results)


def _field(document: object | None, name: str) -> tuple[object | None, float | None]:
    if document is None:
        return None, None
    response = document.response
    detail = getattr(response, "expanded_fields", {}).get(name)
    if detail is not None and detail.value is not None:
        return detail.value, detail.confidence
    value = getattr(getattr(response, "detected_fields", None), name, None)
    return value, getattr(response, "field_confidences", {}).get(name) if value is not None else None


def _identifier(value: object) -> str | None:
    text = re.sub(r"[\s\-./]", "", str(value)).casefold()
    return text if len(text) >= 5 and text.isalnum() else None


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value).replace(" ", ""))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _currency(value: object) -> str | None:
    return _CURRENCIES.get(str(value).upper().strip()) if value is not None else None


def _ptfn_currency(document: object) -> str | None:
    detail = document.response.expanded_fields.get("ptfn_amount")
    if detail is None or detail.bbox is None or detail.page is None:
        return None
    width = detail.page_width or 1190
    height = detail.page_height or 1684
    amount_y = (detail.bbox.y1 + detail.bbox.y2) / 2
    nearby = set()
    for line in document.response.all_ocr_blocks:
        if line.page_number != detail.page or line.bbox is None:
            continue
        currency = _currency(line.text)
        if currency and 0 <= detail.bbox.x1 - line.bbox.x2 <= width * 0.13 \
                and abs((line.bbox.y1 + line.bbox.y2) / 2 - amount_y) <= height * 0.018:
            nearby.add(currency)
    return next(iter(nearby)) if len(nearby) == 1 else None


def _normal_text(value: object) -> str:
    plain = unicodedata.normalize("NFKD", str(value).casefold())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def _country(value: object) -> str | None:
    text = _normal_text(value)
    return text if len(text) >= 3 else None


def _party_comparison(left: object, right: object) -> tuple[str, str]:
    a, b = _normal_text(left), _normal_text(right)
    if len(a) < 8 or len(b) < 8 or len(a.split()) < 2 or len(b.split()) < 2:
        return "unavailable", "Party identity is too short to compare safely."
    if a == b or (min(len(a), len(b)) >= 10 and (a in b or b in a)):
        return "match", "Strong normalized party identity is embedded or equal."
    if any(char.isdigit() for char in str(left) + str(right)) or ":" in str(left) + str(right) \
            or len(a.split()) > 6 or len(b.split()) > 6:
        return "unavailable", "Party evidence contains OCR noise or a longer address block."
    a_tokens, b_tokens = set(a.split()), set(b.split())
    if not (a_tokens & b_tokens):
        return "mismatch", "Clear party names have no shared identity tokens."
    return "unavailable", "Party names overlap but do not establish a reliable identity."


def _equality(left: object | None, right: object | None, kind: str) -> tuple[str, str]:
    if left is None or right is None:
        return "unavailable", f"A {kind} cannot be normalized safely."
    return ("match", f"Normalized {kind}s agree.") if left == right else ("mismatch", f"Normalized {kind}s differ.")
