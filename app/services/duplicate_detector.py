"""Deterministic duplicate indicators; this is not a fraud decision."""
from __future__ import annotations

from typing import Any


def detect_duplicates(fields, existing_documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    existing_documents = existing_documents or []
    matches = []
    for document in existing_documents:
        matched = []
        if fields.invoice_number and document.get("invoice_number") == fields.invoice_number:
            matched.append("invoice_number")
        if fields.supplier_name and document.get("supplier_name") == fields.supplier_name:
            matched.append("supplier_name")
        if fields.invoice_date and document.get("invoice_date") == fields.invoice_date:
            matched.append("invoice_date")
        if fields.amount_ttc is not None and document.get("amount_ttc") is not None and abs(float(fields.amount_ttc) - float(document["amount_ttc"])) <= 0.01:
            matched.append("amount_ttc")
        if fields.customer_name and document.get("customer_name") == fields.customer_name:
            matched.append("customer_name")
        if len(matched) >= 2:
            matches.append({"document_id": document.get("document_id"), "matched_fields": matched, "confidence": round(min(1.0, 0.45 + len(matched) * 0.11), 3)})
    return {"possible_duplicate": bool(matches), "duplicate_confidence": max((item["confidence"] for item in matches), default=0.0), "matches": matches}
