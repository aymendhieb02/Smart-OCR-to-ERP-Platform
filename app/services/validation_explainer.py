from __future__ import annotations

from app.core.schemas import ValidationExplanation, ValidationIssue, ValidationResult


FIELD_HINTS = {
    "Invoice number": "invoice_number",
    "Document date": "invoice_date",
    "Invoice date": "invoice_date",
    "Total amount TTC": "amount_ttc",
    "Amount mismatch": "amounts",
    "Tax rate": "tax_rate",
    "Currency": "currency",
    "Supplier": "supplier_name",
    "Product table": "line_items",
    "Low OCR confidence": "ocr_confidence",
}


def build_validation_explanation(validation: ValidationResult) -> ValidationExplanation:
    blocking_errors = [
        ValidationIssue(
            field=_infer_field(error),
            table="erp_fields",
            message=error,
            impact="ERP export blocked.",
            severity="blocking",
            suggested_action=_suggest_action(error),
        )
        for error in validation.errors
    ]
    warnings = [
        ValidationIssue(
            field=_infer_field(warning),
            table="erp_fields",
            message=warning,
            impact="Needs manual review.",
            severity="warning",
            suggested_action=_suggest_action(warning),
        )
        for warning in validation.warnings
    ]
    if validation.status == "valid":
        reason = "Document passed required ERP validation checks."
        suggested = "Review highlighted fields if needed, then export to ERP."
    elif blocking_errors:
        reason = "Required ERP fields are missing or inconsistent."
        suggested = "Fix the blocking errors, then re-check highlighted fields before ERP export."
    else:
        reason = "Document is extractable but has warnings that require human review."
        suggested = "Review warnings and confirm values before ERP export."
    return ValidationExplanation(
        status=validation.status,
        reason=reason,
        blocking_errors=blocking_errors,
        warnings=warnings,
        suggested_action=suggested,
    )


def _infer_field(message: str) -> str | None:
    for needle, field in FIELD_HINTS.items():
        if needle.lower() in message.lower():
            return field
    return None


def _suggest_action(message: str) -> str:
    lower = message.lower()
    if "total amount" in lower or "ttc" in lower:
        return "Select or enter the correct total amount from the totals block."
    if "date" in lower:
        return "Select or enter the correct document date from the metadata block."
    if "invoice number" in lower or "document reference" in lower:
        return "Select or enter the correct document reference from the metadata block."
    if "tax rate" in lower:
        return "Confirm the tax rate from the tax summary or product table."
    if "currency" in lower:
        return "Confirm the currency from totals, line items, or payment text."
    if "line items" in lower or "product table" in lower:
        return "Review the product lines table and correct missing rows."
    return "Review the highlighted dynamic table row and correct it if needed."
