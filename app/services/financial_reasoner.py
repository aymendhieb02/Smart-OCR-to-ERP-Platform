"""Financial consistency checks kept separate from OCR extraction."""
from __future__ import annotations

from typing import Any

from app.core.schemas import ExtractedInvoiceFields, LineItem


def reason_financials(
    fields: ExtractedInvoiceFields,
    line_items: list[LineItem],
    *,
    shipping: float | None = None,
    discount: float | None = None,
    stamp_tax: float | None = None,
    tolerance: float = 0.05,
    document_type: str = "invoice",
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    if fields.amount_ht is not None and fields.tva_amount is not None and fields.amount_ttc is not None:
        expected = round(fields.amount_ht + fields.tva_amount + (shipping or 0) + (stamp_tax or 0) - (discount or 0), 3)
        delta = round(abs(expected - fields.amount_ttc), 3)
        checks["ht_vat_adjustments_to_ttc"] = {
            "expected": expected,
            "actual": fields.amount_ttc,
            "delta": delta,
            "shipping": shipping,
            "discount": discount,
            "stamp_tax": stamp_tax,
            "passed": delta <= max(tolerance, abs(fields.amount_ttc) * 0.005),
        }
        if not checks["ht_vat_adjustments_to_ttc"]["passed"]:
            errors.append(f"HT + VAT + shipping + stamp tax - discount = {expected}, TTC = {fields.amount_ttc}")
        checks["ht_vat_shipping_discount_to_ttc"] = checks["ht_vat_adjustments_to_ttc"]
    else:
        warnings.append("insufficient totals for complete financial check")

    line_totals = [item.line_total_ht if item.line_total_ht is not None else item.total for item in line_items]
    line_totals = [value for value in line_totals if value is not None]
    if line_totals and fields.amount_ht is not None:
        line_sum = round(sum(line_totals), 3)
        delta = round(abs(line_sum - fields.amount_ht), 3)
        checks["line_sum_to_ht"] = {"expected": fields.amount_ht, "actual": line_sum, "delta": delta, "passed": delta <= max(tolerance, abs(fields.amount_ht) * 0.02)}
        if not checks["line_sum_to_ht"]["passed"]:
            warnings.append(f"line totals sum to {line_sum}, HT is {fields.amount_ht}")
    elif not line_items:
        warnings.append("no line totals available")

    if fields.amount_ttc is not None and fields.amount_ttc < 0 and document_type != "credit_note":
        errors.append("negative TTC on a non-credit invoice")
    if fields.tva_amount is not None and fields.tva_amount < 0 and document_type != "credit_note":
        errors.append("negative VAT on a non-credit invoice")
    line_rates = sorted({float(item.tax_rate) for item in line_items if item.tax_rate is not None})
    if len(line_rates) > 1:
        checks["mixed_vat_rates"] = {"rates": line_rates, "passed": True}
        warnings.append("multiple VAT rates detected; invoice-level tax rate should be reviewed")
    score = 0.35 if errors else (0.68 if warnings else 0.95)
    financially_consistent = bool(checks) and not errors and not any(not check.get("passed", True) for check in checks.values()) and not any("insufficient totals" in warning for warning in warnings)
    return {
        "financial_consistency_score": score,
        "financially_consistent": financially_consistent,
        "financial_errors": errors,
        "financial_warnings": warnings,
        "checks": checks,
        "tolerance": tolerance,
    }
