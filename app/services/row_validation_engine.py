"""Business validation for reconstructed invoice rows."""
from __future__ import annotations

from typing import Any

from app.core.schemas import LineItem


def validate_row(item: LineItem, *, tolerance: float = 0.05) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []
    description = (item.description or "").strip()
    if not description:
        missing.append("description")
    if item.quantity is None:
        missing.append("quantity")
    elif item.quantity <= 0:
        errors.append("quantity must be positive")
    if item.unit_price is None:
        missing.append("unit_price")
    elif item.unit_price < 0:
        errors.append("unit price cannot be negative")
    total = item.line_total_ht if item.line_total_ht is not None else item.total
    if total is None:
        missing.append("total")
    elif total < 0:
        errors.append("line total cannot be negative")
    if item.tax_rate is not None and not 0 <= item.tax_rate <= 100:
        errors.append("VAT rate must be between 0 and 100")
    if item.discount is not None and not 0 <= item.discount <= 100:
        errors.append("discount must be between 0 and 100")

    arithmetic_delta = None
    if item.quantity is not None and item.unit_price is not None and total is not None:
        expected = round(item.quantity * item.unit_price, 3)
        arithmetic_delta = round(abs(expected - total), 3)
        if arithmetic_delta > max(tolerance, abs(total) * 0.01):
            errors.append(f"quantity x unit price = {expected}, row total = {total}")
        elif arithmetic_delta > 0.01:
            warnings.append(f"small rounding difference of {arithmetic_delta}")
    if item.tax_amount is not None and item.line_total_ht is not None and item.line_total_ttc is not None:
        tax_delta = round(abs(item.line_total_ht + item.tax_amount - item.line_total_ttc), 3)
        if tax_delta > max(tolerance, abs(item.line_total_ttc) * 0.01):
            errors.append("HT + VAT does not match line TTC")

    if errors:
        status = "invalid"
    elif missing or warnings or "review" in (item.source or "").lower():
        status = "needs_review"
        if missing:
            warnings.append("missing fields: " + ", ".join(missing))
    else:
        status = "validated"
    confidence = 0.35 if errors else (0.62 if status == "needs_review" else 0.92)
    return {
        "status": status,
        "validation_reason": "valid row" if status == "validated" else ("; ".join(errors or warnings) or "requires review"),
        "warnings": warnings,
        "errors": errors,
        "missing_fields": missing,
        "arithmetic_delta": arithmetic_delta,
        "confidence": confidence,
    }


def validate_rows(items: list[LineItem], *, tolerance: float = 0.05) -> list[dict[str, Any]]:
    reports = []
    for index, item in enumerate(items, start=1):
        report = validate_row(item, tolerance=tolerance)
        report["row_index"] = index
        report["line_item"] = item.model_dump(mode="json")
        reports.append(report)
    return reports


def summarize_rows(reports: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(reports),
        "validated": sum(report["status"] == "validated" for report in reports),
        "needs_review": sum(report["status"] == "needs_review" for report in reports),
        "invalid": sum(report["status"] == "invalid" for report in reports),
        "validation_score": round(sum(report["confidence"] for report in reports) / len(reports), 3) if reports else 0.0,
    }
