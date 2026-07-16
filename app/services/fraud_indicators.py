"""Simple risk indicators. They never assert that fraud occurred."""
from __future__ import annotations

from typing import Any


def detect_fraud_indicators(fields, *, financial: dict[str, Any], duplicate: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    indicators: list[str] = []
    if fields.amount_ttc is not None and fields.amount_ttc < 0:
        indicators.append("negative total")
    if financial.get("financial_errors"):
        indicators.append("VAT or total consistency mismatch")
    if duplicate.get("possible_duplicate"):
        indicators.append("possible duplicate invoice")
    if fields.supplier_tax_id and fields.customer_tax_id and fields.supplier_tax_id == fields.customer_tax_id:
        indicators.append("supplier and customer tax identifiers match")
    if fields.currency is None:
        indicators.append("currency missing")
    if validation.get("missing_fields"):
        indicators.append("mandatory fields missing")
    score = round(min(1.0, len(indicators) * 0.16), 3)
    return {"fraud_score": score, "fraud_indicators": indicators, "disclaimer": "Indicators only; this system does not claim fraud."}
