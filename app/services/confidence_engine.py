"""Weighted confidence composition for business decisions."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class ConfidenceWeights:
    ocr: float = 0.18
    layout: float = 0.12
    table: float = 0.14
    fields: float = 0.20
    financial: float = 0.16
    validation: float = 0.12
    erp: float = 0.08


def calculate_confidence(
    *,
    ocr: float | None,
    layout: float | None,
    table: float | None,
    fields: float | None,
    financial: float | None,
    validation: float | None,
    erp: float | None = None,
    weights: ConfidenceWeights | None = None,
    validation_status: str | None = None,
    missing_required_fields: list[str] | None = None,
    erp_ready: bool | None = None,
) -> dict[str, Any]:
    weights = weights or ConfidenceWeights()
    components = {
        "ocr_confidence": _bounded(ocr, 0.0),
        "layout_confidence": _bounded(layout, 0.0),
        "table_confidence": _bounded(table, 0.0),
        "field_confidence": _bounded(fields, 0.0),
        "financial_confidence": _bounded(financial, 0.0),
        "validation_confidence": _bounded(validation, 0.0),
        "erp_confidence": _bounded(erp, 1.0),
    }
    raw_weights = {"ocr_confidence": weights.ocr, "layout_confidence": weights.layout, "table_confidence": weights.table, "field_confidence": weights.fields, "financial_confidence": weights.financial, "validation_confidence": weights.validation, "erp_confidence": weights.erp}
    total_weight = sum(raw_weights.values())
    raw_overall = round(sum(components[key] * value for key, value in raw_weights.items()) / total_weight, 3)
    overall, calibration = _calibrate_overall(
        raw_overall,
        validation_status=validation_status,
        missing_required_fields=missing_required_fields or [],
        erp_ready=erp_ready,
        components=components,
    )
    return {
        **components,
        "weights": raw_weights,
        "raw_overall_confidence": raw_overall,
        "overall_confidence": overall,
        "confidence_type": "calibrated_business_confidence" if calibration["applied"] else "uncalibrated_composite_index",
        "display_name": "Business Confidence" if calibration["applied"] else "Composite Confidence Index",
        "calibration": calibration,
    }


def _bounded(value: float | None, default: float) -> float:
    if value is None:
        return default
    try:
        numeric = float(value)
        if math.isnan(numeric):
            return default
        return round(max(0.0, min(1.0, numeric)), 3)
    except (TypeError, ValueError):
        return default


def _calibrate_overall(
    overall: float,
    *,
    validation_status: str | None,
    missing_required_fields: list[str],
    erp_ready: bool | None,
    components: dict[str, float],
) -> tuple[float, dict[str, Any]]:
    caps: list[tuple[float, str]] = []
    status = (validation_status or "").lower()
    if status == "invalid":
        caps.append((0.59, "invalid extraction"))
    elif status == "needs_review":
        caps.append((0.79, "needs review"))
    if missing_required_fields:
        caps.append((0.69, "missing required fields"))
    if erp_ready is False:
        caps.append((0.84, "not ERP ready"))
    if components.get("financial_confidence", 1.0) < 0.5:
        caps.append((0.74, "weak financial consistency"))
    if components.get("validation_confidence", 1.0) < 0.5:
        caps.append((0.74, "weak validation confidence"))
    if not caps:
        return overall, {"applied": False, "cap": None, "reasons": []}
    cap = min(value for value, _reason in caps)
    reasons = [reason for value, reason in caps if value == cap or value <= overall]
    return round(min(overall, cap), 3), {"applied": True, "cap": cap, "reasons": reasons}
