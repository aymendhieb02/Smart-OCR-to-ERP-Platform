from __future__ import annotations

import itertools
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any


CATEGORY_BY_CODE: dict[str, str] = {
    "EXECUTION_EXCEPTION": "execution",
    "EXECUTION_TIMEOUT": "execution",
    "EXECUTION_INTERRUPTED": "execution",
    "DOCUMENT_TYPE_UNKNOWN": "document_classification",
    "DOCUMENT_TYPE_MISMATCH": "document_classification",
    "MISSING_SUPPLIER": "required_field",
    "MISSING_CUSTOMER": "required_field",
    "MISSING_INVOICE_NUMBER": "required_field",
    "MISSING_INVOICE_DATE": "required_field",
    "MISSING_CURRENCY": "required_field",
    "MISSING_AMOUNT_TTC": "required_field",
    "MISSING_LINE_ITEMS": "required_field",
    "PARTY_LABEL_ONLY": "party",
    "PARTY_TABLE_HEADER": "party",
    "PARTY_ADDRESS_ONLY": "party",
    "PARTY_POSTAL_CODE_ONLY": "party",
    "PARTY_LOOKS_LIKE_SENTENCE": "party",
    "PARTY_NAME_TOO_LONG": "party",
    "PARTY_LOW_COMPANY_PLAUSIBILITY": "party",
    "PARTY_SUPPLIER_CUSTOMER_COLLISION": "party",
    "PARTY_STRICT_FORMAT_MISMATCH": "party",
    "PARTY_ADDRESS_INCLUDED_IN_GROUND_TRUTH": "party",
    "PARTY_LEGAL_SUFFIX_ONLY_DIFFERENCE": "party",
    "PARTY_PARTIAL_NAME_MATCH": "party",
    "PARTY_CANONICAL_MATCH": "party",
    "PARTY_AMBIGUOUS_MATCH": "party",
    "PARTY_TRUE_MISMATCH": "party",
    "PARTY_GROUND_TRUTH_UNSUPPORTED": "party",
    "PARTY_PREDICTION_MISSING": "party",
    "PARTY_TRUTH_MISSING": "party",
    "INVOICE_NUMBER_YEAR_ONLY": "invoice_number",
    "INVOICE_NUMBER_DATE_LIKE": "invoice_number",
    "INVOICE_NUMBER_TOTAL_LIKE": "invoice_number",
    "INVOICE_NUMBER_PO_NUMBER_CONFUSION": "invoice_number",
    "INVOICE_NUMBER_LOW_LABEL_PROXIMITY": "invoice_number",
    "INVOICE_DATE_MISSING_LABEL_CONTEXT": "date",
    "INVOICE_DATE_FUTURE_IMPLAUSIBLE": "date",
    "INVOICE_DATE_YEAR_ONLY": "date",
    "DUE_DATE_CONFUSED_AS_INVOICE_DATE": "date",
    "CURRENCY_NOT_FOUND": "currency",
    "CURRENCY_AMBIGUOUS": "currency",
    "CURRENCY_SYMBOL_CODE_CONFLICT": "currency",
    "TTC_LOOKS_LIKE_YEAR": "totals",
    "TTC_IMPLAUSIBLY_SMALL": "totals",
    "TTC_IMPLAUSIBLY_LARGE": "totals",
    "TTC_NOT_NEAR_TOTAL_LABEL": "totals",
    "TOTALS_INCONSISTENT": "totals",
    "TAX_INCONSISTENT": "totals",
    "HT_TTC_CONFUSION": "totals",
    "NO_TABLE_DETECTED": "line_items",
    "TABLE_HEADER_NOT_FOUND": "line_items",
    "TABLE_ROWS_NOT_RECONSTRUCTED": "line_items",
    "NO_VALIDATED_LINE_ITEMS": "line_items",
    "LINE_ITEM_ARITHMETIC_MISMATCH": "line_items",
    "LINE_ITEM_DESCRIPTION_INVALID": "line_items",
    "LINE_ITEM_COLUMNS_MISALIGNED": "line_items",
    "HIGH_CONFIDENCE_INVALID_EXTRACTION": "confidence",
    "OCR_CONFIDENCE_NOT_SEMANTIC_CONFIDENCE": "confidence",
    "REQUIRED_FIELDS_MISSING_WITH_HIGH_CONFIDENCE": "confidence",
}

MESSAGE_BY_CODE = {
    "EXECUTION_EXCEPTION": "Execution raised an exception.",
    "EXECUTION_TIMEOUT": "Execution exceeded the timeout budget.",
    "DOCUMENT_TYPE_UNKNOWN": "Document type could not be classified.",
    "MISSING_SUPPLIER": "Missing required field: supplier.",
    "MISSING_CUSTOMER": "Missing required field: customer.",
    "MISSING_INVOICE_NUMBER": "Missing required field: invoice number.",
    "MISSING_INVOICE_DATE": "Missing required field: invoice date.",
    "MISSING_CURRENCY": "Missing required field: currency.",
    "MISSING_AMOUNT_TTC": "Missing required field: total TTC.",
    "MISSING_LINE_ITEMS": "Missing required field: line items.",
    "PARTY_LABEL_ONLY": "Party candidate is a label-only value.",
    "PARTY_TABLE_HEADER": "Party candidate looks like a table header.",
    "PARTY_LOOKS_LIKE_SENTENCE": "Party candidate looks like a full OCR sentence.",
    "PARTY_NAME_TOO_LONG": "Party candidate is unusually long.",
    "PARTY_LOW_COMPANY_PLAUSIBILITY": "Party candidate has low company-name plausibility.",
    "PARTY_SUPPLIER_CUSTOMER_COLLISION": "Supplier and customer resolve to the same value.",
    "PARTY_STRICT_FORMAT_MISMATCH": "Party differs only by formatting under benchmark comparison.",
    "PARTY_ADDRESS_INCLUDED_IN_GROUND_TRUTH": "Ground truth includes address/contact text beyond the company name.",
    "PARTY_LEGAL_SUFFIX_ONLY_DIFFERENCE": "Party differs only by legal suffix.",
    "PARTY_PARTIAL_NAME_MATCH": "Party is a meaningful partial company-name match.",
    "PARTY_CANONICAL_MATCH": "Party matches after canonical company-name normalization.",
    "PARTY_AMBIGUOUS_MATCH": "Party comparison is ambiguous.",
    "PARTY_TRUE_MISMATCH": "Party comparison indicates a genuine mismatch.",
    "PARTY_GROUND_TRUTH_UNSUPPORTED": "Party ground-truth schema is unsupported.",
    "PARTY_PREDICTION_MISSING": "Party prediction is missing.",
    "PARTY_TRUTH_MISSING": "Party ground truth is missing.",
    "INVOICE_NUMBER_YEAR_ONLY": "Invoice number looks like a year only.",
    "INVOICE_NUMBER_DATE_LIKE": "Invoice number looks like a date.",
    "CURRENCY_NOT_FOUND": "Currency was not found.",
    "TTC_LOOKS_LIKE_YEAR": "Extracted total resembles a year.",
    "TTC_IMPLAUSIBLY_SMALL": "Extracted total is implausibly small.",
    "TTC_IMPLAUSIBLY_LARGE": "Extracted total is implausibly large.",
    "TOTALS_INCONSISTENT": "Extracted totals are inconsistent.",
    "TAX_INCONSISTENT": "Extracted tax is inconsistent with total.",
    "HT_TTC_CONFUSION": "HT amount appears greater than TTC.",
    "NO_TABLE_DETECTED": "No product table was detected.",
    "TABLE_ROWS_NOT_RECONSTRUCTED": "Table rows were not reconstructed.",
    "NO_VALIDATED_LINE_ITEMS": "No validated line items were produced.",
    "HIGH_CONFIDENCE_INVALID_EXTRACTION": "High confidence but extraction is invalid.",
    "OCR_CONFIDENCE_NOT_SEMANTIC_CONFIDENCE": "OCR confidence should not be treated as semantic accuracy.",
    "REQUIRED_FIELDS_MISSING_WITH_HIGH_CONFIDENCE": "Required fields are missing despite high confidence.",
}


@dataclass(frozen=True)
class FailureAnalysis:
    failure_codes: list[str]
    failure_categories: list[str]
    failure_details: dict[str, Any]
    primary_failure_code: str | None
    failure_count: int
    validation_failure_reasons: list[str]


def analyze_failure(row: dict[str, Any]) -> FailureAnalysis:
    codes: list[str] = []
    details: dict[str, Any] = {}
    execution = str(row.get("execution_status") or row.get("status") or "")
    extraction = str(row.get("extraction_status") or row.get("validation_status") or "")

    if execution == "failed":
        codes.append("EXECUTION_EXCEPTION")
        details["execution_error"] = row.get("execution_error_message") or row.get("error_message")
    if execution == "timeout" or _truthy(row.get("exceeded_timeout_budget")):
        codes.append("EXECUTION_TIMEOUT")
    if execution == "interrupted":
        codes.append("EXECUTION_INTERRUPTED")
    if execution != "completed":
        return _analysis(codes, details)

    document_type = str(row.get("document_type_pred") or "").lower()
    if document_type in ("", "unknown", "other"):
        codes.append("DOCUMENT_TYPE_UNKNOWN")

    missing = _missing_fields(row)
    codes.extend(_missing_code(field) for field in missing)
    if missing:
        details["missing_required_fields"] = missing

    supplier = row.get("supplier_name_pred")
    customer = row.get("customer_name_pred")
    for field_name, value in (("supplier_name", supplier), ("customer_name", customer)):
        party_codes = _party_codes(value)
        if party_codes:
            details.setdefault("party_fields", {})[field_name] = party_codes
            codes.extend(party_codes)
    if supplier and customer and _norm_text(supplier) == _norm_text(customer):
        codes.append("PARTY_SUPPLIER_CUSTOMER_COLLISION")

    invoice_number = str(row.get("invoice_number_pred") or "").strip()
    if invoice_number:
        if re.fullmatch(r"(?:19|20)\d{2}", invoice_number):
            codes.append("INVOICE_NUMBER_YEAR_ONLY")
        if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", invoice_number):
            codes.append("INVOICE_NUMBER_DATE_LIKE")
        if _float_or_none(invoice_number) is not None and row.get("amount_ttc_pred") not in (None, ""):
            if abs(float(_float_or_none(invoice_number) or 0) - float(_float_or_none(row.get("amount_ttc_pred")) or 0)) <= 0.01:
                codes.append("INVOICE_NUMBER_TOTAL_LIKE")

    amount_codes = _amount_codes(row)
    if amount_codes:
        codes.extend(amount_codes)
        details["amount_ttc"] = amount_codes

    if int(_float_or_none(row.get("line_items_count_pred")) or 0) <= 0:
        codes.extend(["NO_TABLE_DETECTED", "TABLE_ROWS_NOT_RECONSTRUCTED"])
    if int(_float_or_none(row.get("validated_line_items_count_pred")) or 0) <= 0 and int(_float_or_none(row.get("line_items_count_pred")) or 0) > 0:
        codes.append("NO_VALIDATED_LINE_ITEMS")

    suspicious = _coerce_list(row.get("suspicious_field_codes"))
    codes.extend(_map_suspicious_code(code) for code in suspicious)

    confidence = _float_or_none(row.get("overall_confidence"))
    high_conf = confidence is not None and confidence >= 0.85
    if high_conf and extraction in {"invalid", "needs_review"}:
        codes.append("HIGH_CONFIDENCE_INVALID_EXTRACTION")
    if high_conf and missing:
        codes.append("REQUIRED_FIELDS_MISSING_WITH_HIGH_CONFIDENCE")
    if high_conf and (missing or suspicious or extraction == "invalid"):
        codes.append("OCR_CONFIDENCE_NOT_SEMANTIC_CONFIDENCE")

    if extraction == "invalid" and not codes:
        codes.append("HIGH_CONFIDENCE_INVALID_EXTRACTION" if high_conf else "TOTALS_INCONSISTENT")

    return _analysis(codes, details)


def failure_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    code_counts = Counter()
    category_counts = Counter()
    dataset_counts: dict[str, Counter] = {}
    field_counts = Counter()
    cooccurring = Counter()
    high_conf_invalid = 0
    erp_blocking = Counter()
    for row in rows:
        codes = _coerce_list(row.get("failure_codes"))
        code_counts.update(codes)
        categories = _coerce_list(row.get("failure_categories"))
        category_counts.update(categories)
        dataset_counts.setdefault(str(row.get("dataset_name") or "unknown"), Counter()).update(codes)
        for field in _coerce_list(row.get("missing_required_fields")):
            field_counts[field] += 1
        for first, second in itertools.combinations(sorted(set(codes)), 2):
            cooccurring[f"{first}+{second}"] += 1
        if row.get("extraction_status") == "invalid" and _truthy(row.get("confidence_warning")):
            high_conf_invalid += 1
        erp_blocking.update(_coerce_list(row.get("erp_blocking_reasons")))
    return {
        "count_by_failure_code": dict(code_counts.most_common()),
        "count_by_category": dict(category_counts.most_common()),
        "count_by_dataset": {dataset: dict(counter.most_common()) for dataset, counter in sorted(dataset_counts.items())},
        "count_by_field": dict(field_counts.most_common()),
        "top_cooccurring_failure_pairs": dict(cooccurring.most_common(20)),
        "high_confidence_invalid_count": high_conf_invalid,
        "erp_blocking_reason_distribution": dict(erp_blocking.most_common()),
    }


def _analysis(codes: list[str], details: dict[str, Any]) -> FailureAnalysis:
    unique = sorted(set(code for code in codes if code))
    categories = sorted(set(CATEGORY_BY_CODE.get(code, "unknown") for code in unique))
    primary = unique[0] if unique else None
    return FailureAnalysis(
        failure_codes=unique,
        failure_categories=categories,
        failure_details=details,
        primary_failure_code=primary,
        failure_count=len(unique),
        validation_failure_reasons=[MESSAGE_BY_CODE.get(code, code.replace("_", " ").title()) for code in unique],
    )


def _missing_fields(row: dict[str, Any]) -> list[str]:
    fields = {
        "supplier": row.get("supplier_name_pred"),
        "customer": row.get("customer_name_pred"),
        "invoice_number": row.get("invoice_number_pred"),
        "invoice_date": row.get("invoice_date_pred"),
        "currency": row.get("currency_pred"),
        "amount_ttc": row.get("amount_ttc_pred"),
    }
    missing = [field for field, value in fields.items() if value in (None, "", [])]
    if int(_float_or_none(row.get("line_items_count_pred")) or 0) <= 0:
        missing.append("line_items")
    return missing


def _missing_code(field: str) -> str:
    return {
        "supplier": "MISSING_SUPPLIER",
        "customer": "MISSING_CUSTOMER",
        "invoice_number": "MISSING_INVOICE_NUMBER",
        "invoice_date": "MISSING_INVOICE_DATE",
        "currency": "MISSING_CURRENCY",
        "amount_ttc": "MISSING_AMOUNT_TTC",
        "line_items": "MISSING_LINE_ITEMS",
    }[field]


def _party_codes(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    text = str(value).strip()
    compact = _norm_text(text)
    labels = {"ship to", "bill to", "customer", "client", "supplier", "vendor", "seller"}
    table = {"unit price", "unit prico", "quantity", "description", "total", "amount", "vat", "tva"}
    codes = []
    if compact in labels:
        codes.append("PARTY_LABEL_ONLY")
    if compact in table or any(word in compact.split() for word in table):
        codes.append("PARTY_TABLE_HEADER")
    if re.fullmatch(r"\d{4,6}(?:[- ]?\d{2,6})?", compact):
        codes.append("PARTY_POSTAL_CODE_ONLY")
    if len(text) > 80:
        codes.append("PARTY_NAME_TOO_LONG")
    if text.count(" ") > 12 or any(mark in text for mark in (". ", ": ", "; ")):
        codes.append("PARTY_LOOKS_LIKE_SENTENCE")
    company_tokens = {"inc", "ltd", "llc", "sarl", "sa", "sas", "corp", "company", "distribution", "pharma", "medical", "trading"}
    if not any(token in compact.split() for token in company_tokens) and len(compact.split()) <= 2:
        codes.append("PARTY_LOW_COMPANY_PLAUSIBILITY")
    return codes


def _amount_codes(row: dict[str, Any]) -> list[str]:
    amount = _float_or_none(row.get("amount_ttc_pred"))
    if amount is None:
        return []
    codes = []
    if 1900 <= amount <= 2100 and float(amount).is_integer():
        codes.append("TTC_LOOKS_LIKE_YEAR")
    if 0 < amount < 1:
        codes.append("TTC_IMPLAUSIBLY_SMALL")
    if len(str(int(abs(amount)))) >= 8:
        codes.append("TTC_IMPLAUSIBLY_LARGE")
    tva = _float_or_none(row.get("tva_amount_pred"))
    ht = _float_or_none(row.get("amount_ht_pred"))
    if tva is not None and tva > amount:
        codes.append("TAX_INCONSISTENT")
    if ht is not None and ht > amount:
        codes.append("HT_TTC_CONFUSION")
    if tva is not None and ht is not None and abs((ht + tva) - amount) > max(0.05, amount * 0.01):
        codes.append("TOTALS_INCONSISTENT")
    return codes


def _map_suspicious_code(code: str) -> str:
    return {
        "PARTY_IS_LABEL_ONLY": "PARTY_LABEL_ONLY",
        "PARTY_IS_TABLE_HEADER": "PARTY_TABLE_HEADER",
        "PARTY_LOOKS_LIKE_SENTENCE": "PARTY_LOOKS_LIKE_SENTENCE",
        "PARTY_NAME_TOO_LONG": "PARTY_NAME_TOO_LONG",
        "PARTY_LOW_COMPANY_PLAUSIBILITY": "PARTY_LOW_COMPANY_PLAUSIBILITY",
        "TTC_LOOKS_LIKE_YEAR": "TTC_LOOKS_LIKE_YEAR",
        "TTC_IMPLAUSIBLY_LARGE": "TTC_IMPLAUSIBLY_LARGE",
        "TVA_GREATER_THAN_TTC": "TAX_INCONSISTENT",
        "HT_GREATER_THAN_TTC": "HT_TTC_CONFUSION",
    }.get(str(code), str(code))


def _norm_text(value: Any) -> str:
    return " ".join("".join(char.lower() if char.isalnum() else " " for char in str(value)).split())


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _coerce_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            import ast

            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [part.strip() for part in text.split(";") if part.strip()]
    return [value]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
