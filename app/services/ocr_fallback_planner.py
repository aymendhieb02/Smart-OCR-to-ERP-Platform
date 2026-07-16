from __future__ import annotations

import re
from typing import Any

from app.core.schemas import ExtractedInvoiceFields, OCRResult
from app.utils.helpers import strip_accents


def determine_required_fallbacks(
    *,
    fields: ExtractedInvoiceFields,
    ocr_result: OCRResult,
    extraction_debug: dict[str, Any],
) -> list[str]:
    """Plan one targeted OCR recovery pass from evidence, not from optional gaps."""
    text = strip_accents(ocr_result.raw_text or "").lower()
    regions: list[str] = []
    if _needs_header_parties(fields, text, ocr_result):
        regions.append("header_parties")
    if _needs_product_table(fields, text, extraction_debug, ocr_result):
        regions.append("line_items_table_area")
    if _needs_totals(fields, text, extraction_debug):
        regions.append("totals_bottom_right")
    return regions[:3]


def _needs_header_parties(fields: ExtractedInvoiceFields, text: str, ocr_result: OCRResult) -> bool:
    has_party_labels = bool(re.search(r"\b(?:seller|supplier|vendor|client|customer|bill to|fournisseur|acheteur)\b", text))
    missing_party = not fields.supplier_name or not fields.customer_name
    return has_party_labels and missing_party and _weak_header_evidence(ocr_result)


def _weak_header_evidence(ocr_result: OCRResult) -> bool:
    header_lines = [line for line in ocr_result.lines if line.bbox and line.bbox.y1 < 450]
    if len(header_lines) < 4:
        return True
    confidences = [line.confidence for line in header_lines if line.confidence is not None]
    return bool(confidences and sum(confidences) / len(confidences) < 0.72)


def _needs_product_table(fields: ExtractedInvoiceFields, text: str, debug: dict[str, Any], ocr_result: OCRResult) -> bool:
    has_table_evidence = bool(re.search(r"\b(?:description|designation|qty|quantity|qte|price|prix|total|vat|tva|items)\b", text))
    table_debug = debug.get("table_extraction_debug", {})
    counts = table_debug.get("counts", {})
    raw_rows = counts.get("raw_candidate_rows") or counts.get("candidate_rows") or 0
    boxed_lines = sum(1 for line in ocr_result.lines if line.bbox)
    return has_table_evidence and not fields.line_items and raw_rows == 0 and boxed_lines < max(6, len(ocr_result.lines) // 3)


def _needs_totals(fields: ExtractedInvoiceFields, text: str, debug: dict[str, Any]) -> bool:
    has_total_evidence = bool(re.search(r"\b(?:summary|total|gross|ttc|vat|tva|ht|net worth|amount due)\b", text))
    missing_triplet = fields.amount_ht is None or fields.tva_amount is None or fields.amount_ttc is None
    traces = debug.get("field_traces", {})
    selected_total = traces.get("amount_ttc", {}).get("selected_value")
    return has_total_evidence and (missing_triplet or selected_total is None)
