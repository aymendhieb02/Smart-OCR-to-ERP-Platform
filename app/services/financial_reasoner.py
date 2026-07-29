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
    field_consistency = verify_amount_field_consistency(
        fields,
        shipping=shipping,
        discount=discount,
        stamp_tax=stamp_tax,
        tolerance=tolerance,
    )
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

    line_ttc_totals = [item.line_total_ttc for item in line_items]
    line_ttc_totals = [value for value in line_ttc_totals if value is not None]
    if line_ttc_totals and fields.amount_ttc is not None:
        line_ttc_sum = round(sum(line_ttc_totals), 3)
        delta = round(abs(line_ttc_sum - fields.amount_ttc), 3)
        checks["line_sum_to_ttc"] = {
            "expected": fields.amount_ttc,
            "actual": line_ttc_sum,
            "delta": delta,
            "passed": delta <= max(tolerance, abs(fields.amount_ttc) * 0.001),
        }
        if not checks["line_sum_to_ttc"]["passed"]:
            warnings.append(f"line TTC totals sum to {line_ttc_sum}, TTC is {fields.amount_ttc}")

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
        "field_consistency": field_consistency,
        "tolerance": tolerance,
        "explanation": _build_financial_explanation(checks, errors, warnings, adjustments),
    }


def verify_amount_field_consistency(
    fields: ExtractedInvoiceFields,
    *,
    shipping: float | None = None,
    discount: float | None = None,
    stamp_tax: float | None = None,
    tolerance: float = 0.05,
    rate_tolerance: float = 0.5,
) -> dict[str, dict[str, Any]]:
    """Explain cross-field math consistency for invoice amount fields."""
    adjustments = _normalize_adjustments(shipping=shipping, discount=discount, stamp_tax=stamp_tax)
    ht = _float_or_none(fields.amount_ht)
    vat = _float_or_none(fields.tva_amount)
    ttc = _float_or_none(fields.amount_ttc)
    rate = _float_or_none(fields.tax_rate)
    result = {
        field: {
            "status": "insufficient_data",
            "expected_value": None,
            "message": "Not enough related amount fields to verify this value.",
        }
        for field in ("amount_ht", "tva_amount", "amount_ttc", "tax_rate")
    }

    expected_ttc = None
    identity_passed = None
    if ht is not None and vat is not None and ttc is not None:
        expected_ttc = round(ht + vat + adjustments["shipping"] + adjustments["stamp_tax"] - adjustments["discount"], 3)
        identity_delta = round(abs(expected_ttc - ttc), 3)
        identity_tolerance = max(tolerance, abs(ttc) * 0.005)
        identity_passed = identity_delta <= identity_tolerance
        message = (
            f"HT + VAT = {expected_ttc:.2f}, matching TTC {ttc:.2f}."
            if identity_passed
            else f"HT + VAT ({expected_ttc:.2f}) does not equal TTC ({ttc:.2f})."
        )
        for field in ("amount_ht", "tva_amount", "amount_ttc"):
            result[field] = {
                "status": "consistent" if identity_passed else "inconsistent",
                "expected_value": None,
                "message": message,
                "delta": identity_delta,
                "tolerance": round(identity_tolerance, 3),
            }
        result["amount_ttc"]["expected_value"] = expected_ttc
        result["amount_ht"]["expected_value"] = round(ttc - vat - adjustments["shipping"] - adjustments["stamp_tax"] + adjustments["discount"], 3)
        result["tva_amount"]["expected_value"] = round(ttc - ht - adjustments["shipping"] - adjustments["stamp_tax"] + adjustments["discount"], 3)

    rate_passed = None
    if ht is not None and vat is not None and rate is not None and abs(ht) > 0.000001:
        implied_rate = round((vat / ht) * 100, 3)
        rate_delta = round(abs(implied_rate - rate), 3)
        rate_passed = rate_delta <= rate_tolerance
        result["tax_rate"] = {
            "status": "consistent" if rate_passed else "inconsistent",
            "expected_value": implied_rate,
            "message": (
                f"VAT / HT implies tax rate {implied_rate:.2f}%, matching {rate:.2f}%."
                if rate_passed
                else f"VAT / HT implies tax rate {implied_rate:.2f}%, not {rate:.2f}%."
            ),
            "delta": rate_delta,
            "tolerance": rate_tolerance,
        }
        if identity_passed is None:
            result["amount_ht"] = {
                "status": "consistent" if rate_passed else "inconsistent",
                "expected_value": round(vat / (rate / 100), 3) if abs(rate) > 0.000001 else None,
                "message": result["tax_rate"]["message"],
            }
            result["tva_amount"] = {
                "status": "consistent" if rate_passed else "inconsistent",
                "expected_value": round(ht * rate / 100, 3),
                "message": result["tax_rate"]["message"],
            }

    if ht is not None and vat is not None and ttc is not None and abs(ht - ttc) <= 0.01 and abs(vat) > 0.5:
        guarded_expected = expected_ttc if expected_ttc is not None else round(ht + vat, 3)
        result["amount_ttc"].update({
            "status": "inconsistent",
            "expected_value": guarded_expected,
            "message": (
                f"TTC is nearly equal to HT ({ttc:.2f}) even though VAT is nonzero ({vat:.2f}). "
                f"Expected TTC is {guarded_expected:.2f}."
            ),
            "severity": "high",
            "guard": "ht_equals_ttc_with_nonzero_vat",
        })

    if identity_passed is False and rate_passed is True:
        expected_ht = result["amount_ht"].get("expected_value")
        expected_vat = result["tva_amount"].get("expected_value")
        for field in ("amount_ht", "tva_amount", "tax_rate"):
            result[field]["status"] = "consistent"
        result["amount_ht"]["expected_value"] = expected_ht
        result["tva_amount"]["expected_value"] = expected_vat
        result["amount_ttc"].update({
            "status": "inconsistent",
            "expected_value": expected_ttc,
            "message": f"HT and VAT agree with tax rate; expected TTC is {expected_ttc:.2f}, not {ttc:.2f}.",
            "outlier": True,
        })
    elif identity_passed is False and ht is not None and ttc is not None and rate is not None and abs(ht) > 0.000001:
        implied_vat_from_rate = round(ht * rate / 100, 3)
        implied_vat_from_ttc = round(ttc - ht - adjustments["shipping"] - adjustments["stamp_tax"] + adjustments["discount"], 3)
        if abs(implied_vat_from_ttc - implied_vat_from_rate) <= max(tolerance, abs(implied_vat_from_ttc) * 0.005):
            result["tva_amount"].update({
                "status": "inconsistent",
                "expected_value": implied_vat_from_ttc,
                "message": f"HT, TTC and tax rate imply VAT {implied_vat_from_ttc:.2f}, not {vat:.2f}." if vat is not None else "VAT is missing.",
                "outlier": True,
            })
            result["amount_ht"]["status"] = "consistent"
            result["amount_ttc"]["status"] = "consistent"
            result["tax_rate"]["status"] = "consistent"

    if ttc is not None and vat is not None:
        result["amount_ht"]["implied_ht"] = round(ttc - vat - adjustments["shipping"] - adjustments["stamp_tax"] + adjustments["discount"], 3)
    if ttc is not None and rate is not None and rate > -99.999:
        result["amount_ht"]["implied_ht_from_rate"] = round(ttc / (1 + rate / 100), 3)
    if expected_ttc is not None:
        result["amount_ttc"]["implied_ttc"] = expected_ttc

    return result


def _normalize_adjustments(*, shipping: float | None, discount: float | None, stamp_tax: float | None) -> dict[str, float]:
    return {
        "shipping": round(float(shipping or 0), 3),
        "discount": round(abs(float(discount or 0)), 3),
        "stamp_tax": round(float(stamp_tax or 0), 3),
    }


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
