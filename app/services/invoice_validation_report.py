"""Stable business validation report assembled for API and ERP review."""
from __future__ import annotations

from typing import Any


def build_invoice_validation_report(*, fields, rows, financial, confidence, readiness, warnings, errors, corrections, duplicate, fraud) -> dict[str, Any]:
    return {
        "summary": {
            "status": readiness.get("erp_ready_status"),
            "erp_ready": readiness.get("ready", False),
            "erp_ready_score": readiness.get("erp_ready_score", 0.0),
        },
        "fields": fields.model_dump(mode="json"),
        "rows": rows,
        "financial_checks": financial,
        "confidence": confidence,
        "erp_readiness": readiness,
        "warnings": warnings,
        "errors": errors,
        "suggested_corrections": corrections,
        "duplicate_detection": duplicate,
        "fraud_indicators": fraud,
    }
