from __future__ import annotations

import re
from typing import Any

from app.core.schemas import Candidate, ExtractedInvoiceFields, FieldBox, FieldExtractionDetail
from app.utils.helpers import normalize_text, parse_amount


EXTRA_PATTERNS = {
    "supplier_email": r"([\w.\-+]+@[\w.\-]+\.\w+)",
    "phone_number": r"((?:\+?\d{1,3}[\s.-]?)?(?:\d[\s.-]?){6,})",
    "bank_rib": r"\bRIB\s*[:\-]?\s*([A-Z0-9\s]{10,40})",
    "bank_iban": r"\bIBAN\s*[:\-]?\s*([A-Z]{2}\d{2}[\sA-Z0-9]{8,40})",
    "bank_swift": r"\bSWIFT\s*[:\-]?\s*([A-Z0-9]{6,12})",
    "client_reference": r"(?:ref\.?\s*client|réf\.?\s*client|customer\s*ref)\s*[:\-]?\s*([A-Z0-9_\-/]+)",
    "discount_amount": r"(?:remise|discount)\s*[:\-]?\s*([+-]?\d+(?:[,.]\d{1,3})?)",
    "payment_terms": r"((?:paiement|payment|reglement|règlement)[^\n]{3,120})",
}


def build_expanded_fields(
    fields: ExtractedInvoiceFields,
    candidates: dict[str, list[Candidate]],
    field_confidences: dict[str, float],
    extracted_text: str,
) -> dict[str, FieldExtractionDetail]:
    expanded: dict[str, FieldExtractionDetail] = {}
    for field_name, value in fields.model_dump(mode="json").items():
        if field_name == "line_items":
            continue
        candidate = _best_candidate(candidates.get(field_name, []))
        expanded[field_name] = FieldExtractionDetail(
            value=value,
            confidence=field_confidences.get(field_name),
            bbox=candidate.bbox if candidate else None,
            page=candidate.page if candidate else None,
            line_index=candidate.line_index if candidate else None,
            source=candidate.source if candidate else "field selection",
        )

    for field_name, pattern in EXTRA_PATTERNS.items():
        match = re.search(pattern, extracted_text, re.IGNORECASE | re.MULTILINE)
        if not match:
            continue
        value: Any = match.group(1).strip()
        if field_name == "discount_amount":
            value = parse_amount(value)
        expanded[field_name] = FieldExtractionDetail(
            value=value,
            confidence=0.62,
            source="expanded regex",
        )

    expanded["raw_text_length"] = FieldExtractionDetail(
        value=len(normalize_text(extracted_text)),
        confidence=1.0,
        source="metadata",
    )
    return expanded


def build_field_boxes(expanded_fields: dict[str, FieldExtractionDetail]) -> list[FieldBox]:
    boxes = []
    for field_name, detail in expanded_fields.items():
        if detail.bbox is None:
            continue
        boxes.append(FieldBox(
            field=field_name,
            value=detail.value,
            confidence=detail.confidence,
            bbox=detail.bbox,
            page=detail.page,
            source=detail.source,
        ))
    return boxes


def _best_candidate(candidates: list[Candidate]) -> Candidate | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)[0]
