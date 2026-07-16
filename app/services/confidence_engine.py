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
    overall = round(sum(components[key] * value for key, value in raw_weights.items()) / total_weight, 3)
    return {
        **components,
        "weights": raw_weights,
        "overall_confidence": overall,
        "confidence_type": "uncalibrated_composite_index",
        "display_name": "Composite Confidence Index",
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
