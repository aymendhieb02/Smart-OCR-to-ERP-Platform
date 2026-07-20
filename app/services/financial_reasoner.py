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
    adjustments = _normalize_adjustments(shipping=shipping, discount=discount, stamp_tax=stamp_tax)
    shipping = adjustments["shipping"]
    discount = adjustments["discount"]
    stamp_tax = adjustments["stamp_tax"]
    if fields.amount_ht is not None and fields.tva_amount is not None and fields.amount_ttc is not None:
        expected = round(fields.amount_ht + fields.tva_amount + shipping + stamp_tax - discount, 3)
        delta = round(abs(expected - fields.amount_ttc), 3)
        checks["ht_vat_adjustments_to_ttc"] = {
            "expected": expected,
            "actual": fields.amount_ttc,
            "delta": delta,
            **adjustments,
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
        "explanation": _build_financial_explanation(checks, errors, warnings, adjustments),
    }


def _normalize_adjustments(*, shipping: float | None, discount: float | None, stamp_tax: float | None) -> dict[str, float]:
    return {
        "shipping": round(float(shipping or 0), 3),
        "discount": round(abs(float(discount or 0)), 3),
        "stamp_tax": round(float(stamp_tax or 0), 3),
    }


def _build_financial_explanation(checks: dict[str, Any], errors: list[str], warnings: list[str], adjustments: dict[str, float]) -> str:
    if errors:
        return "Financial reconciliation failed: " + "; ".join(errors)
    if checks.get("ht_vat_adjustments_to_ttc", {}).get("passed"):
        parts = ["HT + VAT"]
        if adjustments["shipping"]:
            parts.append("+ shipping")
        if adjustments["stamp_tax"]:
            parts.append("+ stamp duty")
        if adjustments["discount"]:
            parts.append("- discount")
        return "Financial reconciliation passed using " + " ".join(parts)
    if warnings:
        return "Financial reconciliation requires review: " + "; ".join(warnings)
    return "Financial reconciliation completed"
