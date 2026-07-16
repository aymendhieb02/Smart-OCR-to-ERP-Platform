from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from dateutil import parser as date_parser

NORMALIZED_EMPTY = {
    "document_type": None,
    "supplier_name": None,
    "customer_name": None,
    "invoice_number": None,
    "invoice_date": None,
    "amount_ttc": None,
    "line_items": [],
}

KEY_ALIASES = {
    "invoice_number": {"invoice_number", "invoice_no", "invoice_id", "invoice_num", "factura_numero", "document_number", "number", "invoice ref", "reference"},
    "supplier_name": {"supplier_name", "vendor_name", "seller_name", "issuer_name", "company", "company_name", "from", "seller", "vendor", "supplier", "issuer"},
    "customer_name": {"customer_name", "client_name", "buyer_name", "receiver_name", "bill_to", "client", "customer", "buyer", "receiver"},
    "invoice_date": {"invoice_date", "date", "issue_date", "document_date", "date_of_issue"},
    "amount_ttc": {"total", "total_amount", "amount_total", "grand_total", "total_ttc", "amount_ttc", "invoice_total", "total_gross_worth", "gross_total", "montant_ttc"},
    "document_type": {"document_type", "type", "category", "label"},
    "line_items": {"line_items", "items", "products", "rows", "table_items"},
}


def load_ground_truth(label_path: str | Path | None) -> dict[str, Any]:
    normalized = dict(NORMALIZED_EMPTY)
    if not label_path:
        return normalized
    path = Path(label_path)
    if not path.exists():
        return normalized

    payload = _coerce_value(path.read_text(encoding="utf-8", errors="ignore"))
    if not isinstance(payload, (dict, list)):
        return normalized

    flat_pairs = list(_walk_pairs(payload))
    by_key = {}
    for key, value, _trail in flat_pairs:
        by_key.setdefault(_normalize_key(key), []).append(value)

    for field, aliases in KEY_ALIASES.items():
        value = _pick_first_value(by_key, aliases)
        if value is None:
            continue
        if field == "invoice_date":
            normalized[field] = _normalize_date(value)
        elif field == "amount_ttc":
            normalized[field] = _normalize_amount(value)
        elif field == "line_items":
            normalized[field] = _normalize_line_items(value)
        else:
            normalized[field] = _clean_scalar(value)

    parsed_specific = _extract_from_nested_structures(payload)
    for key, value in parsed_specific.items():
        if normalized.get(key) in (None, [], "") and value not in (None, [], ""):
            normalized[key] = value

    return normalized


def _extract_from_nested_structures(payload: Any) -> dict[str, Any]:
    normalized = dict(NORMALIZED_EMPTY)
    if not isinstance(payload, dict):
        return normalized

    if isinstance(payload.get("parsed_data"), str):
        parsed_data = _coerce_value(payload["parsed_data"])
        if isinstance(parsed_data, dict):
            json_blob = _coerce_value(parsed_data.get("json"))
            if isinstance(json_blob, dict):
                header = json_blob.get("header") or {}
                summary = json_blob.get("summary") or {}
                normalized["invoice_number"] = _clean_scalar(header.get("invoice_no") or header.get("invoice_number"))
                normalized["invoice_date"] = _normalize_date(header.get("invoice_date"))
                normalized["supplier_name"] = _clean_scalar(header.get("seller") or header.get("supplier_name"))
                normalized["customer_name"] = _clean_scalar(header.get("client") or header.get("customer_name"))
                normalized["amount_ttc"] = _normalize_amount(summary.get("total_gross_worth") or summary.get("total_ttc"))
                normalized["line_items"] = _normalize_line_items(json_blob.get("items"))
                normalized["document_type"] = "invoice"
    if normalized["document_type"] is None and isinstance(payload.get("label"), (int, float)):
        label_value = int(payload["label"])
        label_map = {
            0: "invoice",
            1: "invoice",
            2: "receipt",
            3: "delivery_note",
            4: "purchase_order",
        }
        normalized["document_type"] = label_map.get(label_value)
    return normalized


def _walk_pairs(value: Any, trail: tuple[str, ...] = ()) -> list[tuple[str, Any, tuple[str, ...]]]:
    pairs: list[tuple[str, Any, tuple[str, ...]]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            pairs.append((str(key), item, trail + (str(key),)))
            pairs.extend(_walk_pairs(item, trail + (str(key),)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            pairs.extend(_walk_pairs(item, trail + (str(index),)))
    elif isinstance(value, str):
        parsed = _coerce_value(value)
        if parsed is not value:
            pairs.extend(_walk_pairs(parsed, trail))
    return pairs


def _pick_first_value(values_by_key: dict[str, list[Any]], aliases: set[str]) -> Any:
    for alias in aliases:
        bucket = values_by_key.get(_normalize_key(alias), [])
        for value in bucket:
            cleaned = _clean_scalar(value)
            if cleaned not in (None, "", [], {}):
                return value
    return None


def _normalize_key(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in str(value)).strip("_")


def _clean_scalar(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return value
    return None


def _normalize_date(value: Any) -> str | None:
    cleaned = _clean_scalar(value)
    if cleaned in (None, ""):
        return None
    try:
        return date_parser.parse(str(cleaned), dayfirst=False, fuzzy=True).date().isoformat()
    except Exception:
        try:
            return date_parser.parse(str(cleaned), dayfirst=True, fuzzy=True).date().isoformat()
        except Exception:
            return None


def _normalize_amount(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    digits = "".join(char for char in text if char.isdigit() or char in ",.-")
    if not digits:
        return None
    if digits.count(",") and digits.count("."):
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif digits.count(",") and not digits.count("."):
        digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def _normalize_line_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(item)
    return normalized


def _coerce_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(text)
        except Exception:
            continue
    return value
