"""Decide whether extracted business data is safe for ERP export."""
from __future__ import annotations

from typing import Any

from app.core.schemas import ExtractedInvoiceFields


def assess_erp_readiness(
    fields: ExtractedInvoiceFields,
    *,
    row_summary: dict[str, Any],
    financial: dict[str, Any],
    confidence: float,
) -> dict[str, Any]:
    missing = [name for name, value in {
        "invoice_number": fields.invoice_number,
        "supplier_name": fields.supplier_name,
        "customer_name": fields.customer_name,
        "invoice_date": fields.invoice_date,
        "currency": fields.currency,
        "amount_ttc": fields.amount_ttc,
    }.items() if value in (None, "")]
    blocking_errors = list(financial.get("financial_errors", []))
    if row_summary.get("invalid", 0):
        blocking_errors.append(f"{row_summary['invalid']} invalid line item(s)")
    score = round(max(0.0, min(1.0, confidence * 0.45 + financial.get("financial_consistency_score", 0) * 0.30 + row_summary.get("validation_score", 0) * 0.25)), 3)
    if blocking_errors:
        status = "Rejected"
    elif missing or row_summary.get("needs_review", 0) or score < 0.78 or not financial.get("financially_consistent", False):
        status = "Needs Review"
    else:
        status = "ERP Ready"
    return {
        "erp_ready_score": score,
        "erp_ready_status": status,
        "blocking_errors": blocking_errors,
        "missing_fields": missing,
        "ready": status == "ERP Ready",
    }
