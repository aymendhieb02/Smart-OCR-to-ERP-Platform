"""Propose, but never silently apply, conservative OCR corrections."""
from __future__ import annotations

import re
from typing import Any


def suggest_corrections(fields) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for field in ("invoice_number", "purchase_order_number"):
        value = getattr(fields, field, None)
        if not value:
            continue
        corrected = re.sub(r"O(?=\d)", "0", str(value))
        corrected = re.sub(r"(?<=\d)I(?=\d)", "1", corrected)
        if corrected != str(value) and any(char.isdigit() for char in corrected):
            suggestions.append({"field": field, "original": value, "corrected": corrected, "reason": "common OCR letter/digit ambiguity", "confidence": 0.62})
    if fields.tax_rate is not None and fields.tax_rate > 100:
        corrected = round(fields.tax_rate / 10, 2)
        suggestions.append({"field": "tax_rate", "original": fields.tax_rate, "corrected": corrected, "reason": "tax rate appears to contain an extra decimal digit", "confidence": 0.55})
    return suggestions
