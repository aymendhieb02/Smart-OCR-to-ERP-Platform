from datetime import date
import re

from app.core.config import settings
from app.core.schemas import DocumentClassification, ExtractedInvoiceFields, OCRResult, ValidationResult


REASONABLE_TAX_RATES = {0, 7, 10, 13, 19, 20}


def validate_invoice(
    fields: ExtractedInvoiceFields,
    ocr_result: OCRResult | None = None,
    document_type: str | DocumentClassification = "invoice",
) -> ValidationResult:
    if isinstance(document_type, DocumentClassification):
        document_type = document_type.document_type
    errors: list[str] = []
    warnings: list[str] = []
    confidence = ocr_result.confidence if ocr_result else None

    if document_type == "unknown":
        warnings.append("Document type is unknown")

    if document_type in {"invoice", "credit_note", "delivery_note"} and not fields.invoice_number:
        warnings.append("Document reference is missing")
    if not fields.invoice_date:
        warnings.append("Document date is missing or invalid")
    elif fields.invoice_date > date.today():
        warnings.append("Document date is in the future")

    if document_type in {"invoice", "credit_note", "receipt"}:
        if fields.amount_ttc is None:
            warnings.append("Total amount TTC is missing")
        elif document_type != "credit_note" and fields.amount_ttc <= 0:
            errors.append("Total amount TTC must be positive")

    if fields.amount_ht is not None and fields.tva_amount is not None and fields.amount_ttc is not None:
        expected = round(fields.amount_ht + fields.tva_amount, 3)
        mismatch = abs(expected - fields.amount_ttc)
        if mismatch > max(0.05, abs(fields.amount_ttc) * 0.001):
            errors.append(
                f"Amount mismatch: HT + TVA = {expected}, but TTC = {fields.amount_ttc}"
            )
        elif mismatch > 0.01:
            warnings.append(f"Small amount rounding difference: HT + TVA = {expected}, TTC = {fields.amount_ttc}")
    elif document_type == "invoice":
        warnings.append("One or more amount fields are missing, total consistency could not be fully checked")

    if document_type == "invoice" and fields.tax_rate is None:
        warnings.append("Tax rate is missing")
    elif fields.tax_rate is not None and min(abs(fields.tax_rate - rate) for rate in REASONABLE_TAX_RATES) > 0.5:
        warnings.append(f"Suspicious tax rate: {fields.tax_rate}%")

    if not fields.supplier_name and not fields.supplier_tax_id:
        warnings.append("Supplier identity could not be detected")
    if document_type == "invoice" and not fields.currency:
        warnings.append("Currency could not be detected")
    if document_type == "invoice" and ocr_result and not fields.line_items and _has_product_table_text(ocr_result.raw_text):
        warnings.append("Product table text was detected but no line items were parsed")
    if confidence is not None and confidence < settings.low_confidence_threshold:
        warnings.append(f"Low OCR confidence: {confidence}")

    if errors:
        status = "invalid"
    elif warnings or document_type == "unknown" or (confidence is not None and confidence < 0.75):
        status = "needs_review"
    else:
        status = "valid"

    return ValidationResult(is_valid=status == "valid", status=status, errors=errors, warnings=warnings, confidence=confidence)


def _has_product_table_text(text: str) -> bool:
    if not text:
        return False
    has_headers = any(keyword in text.lower() for keyword in ("designation", "dÃ©signation", "code produit", "product code", "qte", "qty"))
    product_row_count = len(re.findall(r"\b[A-Z]{2,}[A-Z0-9]*-[A-Z0-9]+\b\s+\d+", text))
    return has_headers or product_row_count >= 2


