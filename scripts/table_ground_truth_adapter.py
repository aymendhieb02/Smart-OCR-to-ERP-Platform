from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.utils.helpers import parse_amount, strip_accents


GT_FAILURE_CODES = {
    "unsupported": "TABLE_GT_SCHEMA_UNSUPPORTED",
    "missing": "TABLE_GT_MISSING",
    "adapter_failed": "TABLE_GT_ADAPTER_FAILED",
    "empty": "TABLE_GT_EMPTY_RECORD_REMOVED",
    "duplicate": "TABLE_GT_DUPLICATE_RECORD_REMOVED",
    "total": "TABLE_GT_TOTAL_ROW_REMOVED",
    "tax": "TABLE_GT_TAX_ROW_REMOVED",
    "shipping": "TABLE_GT_SHIPPING_ROW_REVIEW",
    "changed": "TABLE_GT_COUNT_CHANGED_BY_ADAPTER",
    "ambiguous": "TABLE_GT_GRANULARITY_AMBIGUOUS",
    "split": "TABLE_GT_PREDICTION_SPLIT",
    "merged": "TABLE_GT_PREDICTION_MERGED",
    "zero_confirmed": "TABLE_GT_ZERO_ITEMS_CONFIRMED",
    "zero_suspect": "TABLE_GT_ZERO_ITEMS_SUSPECT",
    "serialized_failure": "TABLE_GT_SERIALIZED_PARSE_FAILURE",
    "manual": "TABLE_GT_MANUAL_REVIEW_REQUIRED",
}


@dataclass
class CanonicalGroundTruthLineItem:
    source_index: int
    description: str | None = None
    reference: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    discount: float | None = None
    tax_rate: float | None = None
    line_total_ht: float | None = None
    line_total_ttc: float | None = None
    raw_value: Any = None
    source_schema: str = "unknown"
    source_path: str | None = None
    normalization_warnings: list[str] = field(default_factory=list)
    exclusion_reason: str | None = None
    item_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalGroundTruthTable:
    raw_item_count: int = 0
    canonical_item_count: int = 0
    excluded_record_count: int = 0
    unsupported_record_count: int = 0
    duplicate_record_count: int = 0
    zero_item_document: bool = False
    explicit_zero_items: bool = False
    truth_status: str = "missing"
    items: list[CanonicalGroundTruthLineItem] = field(default_factory=list)
    source_schema: str = "missing"
    adapter_warnings: list[str] = field(default_factory=list)
    excluded_records: list[dict[str, Any]] = field(default_factory=list)
    unsupported_records: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [item.to_dict() for item in self.items]
        return payload


def adapt_table_ground_truth(label_path: str | Path | None, *, dataset_name: str = "", payload: Any = None) -> CanonicalGroundTruthTable:
    if payload is None:
        if not label_path:
            return CanonicalGroundTruthTable(adapter_warnings=[GT_FAILURE_CODES["missing"]])
        path = Path(label_path)
        if not path.exists():
            return CanonicalGroundTruthTable(adapter_warnings=[GT_FAILURE_CODES["missing"]])
        try:
            payload = _coerce_value(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return CanonicalGroundTruthTable(source_schema="unreadable", truth_status="adapter_failed", adapter_warnings=[GT_FAILURE_CODES["adapter_failed"]])
    source_path = str(label_path) if label_path else None
    try:
        if _looks_like_donut(payload):
            return adapt_donut_line_items(payload, source_path=source_path)
        if _looks_like_invoicexpert(payload, dataset_name):
            return adapt_invoicexpert_line_items(payload, source_path=source_path)
        if _looks_like_invoices_receipts(payload, dataset_name):
            return adapt_invoices_receipts_line_items(payload, source_path=source_path)
        if _looks_like_fatura2(payload, dataset_name):
            return adapt_fatura2_line_items(payload, source_path=source_path)
        if _looks_like_md_invoice(payload, dataset_name):
            return adapt_md_invoice_line_items(payload, source_path=source_path)
        return adapt_generic_line_items(payload, source_schema="generic", source_path=source_path)
    except Exception as exc:
        return CanonicalGroundTruthTable(
            source_schema="adapter_exception",
            truth_status="adapter_failed",
            adapter_warnings=[GT_FAILURE_CODES["adapter_failed"], f"{type(exc).__name__}: {exc}"],
        )


def adapt_donut_line_items(payload: Any, *, source_path: str | None = None) -> CanonicalGroundTruthTable:
    parsed = _extract_gt_parse(payload)
    if isinstance(parsed, str):
        parsed = parse_donut_serialized_tokens(parsed)
    return adapt_generic_line_items(parsed, source_schema="donut_gt_parse", source_path=source_path)


def adapt_invoicexpert_line_items(payload: Any, *, source_path: str | None = None) -> CanonicalGroundTruthTable:
    return adapt_generic_line_items(payload, source_schema="invoiceXpert", source_path=source_path)


def adapt_invoices_receipts_line_items(payload: Any, *, source_path: str | None = None) -> CanonicalGroundTruthTable:
    return adapt_generic_line_items(payload, source_schema="invoices-and-receipts", source_path=source_path)


def adapt_fatura2_line_items(payload: Any, *, source_path: str | None = None) -> CanonicalGroundTruthTable:
    parsed = payload
    if isinstance(payload, dict) and isinstance(payload.get("parsed_data"), str):
        parsed = _coerce_value(payload["parsed_data"])
        if isinstance(parsed, dict) and parsed.get("json") is not None:
            parsed = _coerce_value(parsed.get("json"))
    return adapt_generic_line_items(parsed, source_schema="FATURA2", source_path=source_path)


def adapt_md_invoice_line_items(payload: Any, *, source_path: str | None = None) -> CanonicalGroundTruthTable:
    return adapt_generic_line_items(payload, source_schema="md_invoices", source_path=source_path)


def adapt_generic_line_items(payload: Any, *, source_schema: str, source_path: str | None = None) -> CanonicalGroundTruthTable:
    containers = _find_item_containers(payload)
    if not containers:
        has_other_truth = isinstance(payload, dict) and bool(payload)
        status = "explicit_zero" if has_other_truth else "unsupported"
        warnings = [GT_FAILURE_CODES["zero_confirmed"]] if has_other_truth else [GT_FAILURE_CODES["unsupported"]]
        return CanonicalGroundTruthTable(
            raw_item_count=0,
            canonical_item_count=0,
            zero_item_document=status == "explicit_zero",
            explicit_zero_items=status == "explicit_zero",
            truth_status=status,
            source_schema=source_schema,
            adapter_warnings=warnings,
        )
    raw_records = _flatten_record_container(containers[0])
    if not raw_records:
        return CanonicalGroundTruthTable(
            raw_item_count=0,
            canonical_item_count=0,
            zero_item_document=True,
            explicit_zero_items=True,
            truth_status="explicit_zero",
            source_schema=source_schema,
            adapter_warnings=[GT_FAILURE_CODES["zero_confirmed"]],
        )
    table = CanonicalGroundTruthTable(raw_item_count=len(raw_records), source_schema=source_schema, truth_status="supported")
    seen: set[tuple[Any, ...]] = set()
    for index, raw in enumerate(raw_records):
        item = _canonicalize_record(raw, index, source_schema=source_schema, source_path=source_path)
        if item.exclusion_reason:
            table.excluded_record_count += 1
            table.excluded_records.append({"source_index": index, "reason": item.exclusion_reason, "raw_value": raw})
            table.adapter_warnings.append(item.exclusion_reason)
            continue
        key = _dedupe_key(item)
        if key in seen:
            table.duplicate_record_count += 1
            table.excluded_record_count += 1
            table.excluded_records.append({"source_index": index, "reason": GT_FAILURE_CODES["duplicate"], "raw_value": raw})
            table.adapter_warnings.append(GT_FAILURE_CODES["duplicate"])
            continue
        seen.add(key)
        table.items.append(item)
    table.canonical_item_count = len(table.items)
    table.zero_item_document = table.canonical_item_count == 0
    table.explicit_zero_items = table.raw_item_count == 0 or (table.raw_item_count > 0 and table.canonical_item_count == 0)
    if table.raw_item_count != table.canonical_item_count:
        table.adapter_warnings.append(GT_FAILURE_CODES["changed"])
    table.adapter_warnings = sorted(set(table.adapter_warnings))
    return table


def parse_donut_serialized_tokens(text: str) -> dict[str, Any]:
    cleaned = text.replace("<s>", "").replace("</s>", "")
    records: list[dict[str, str]] = []
    for chunk in re.findall(r"<s_(?:line_item|item|items|row)>(.*?)</s_(?:line_item|item|items|row)>", cleaned, flags=re.IGNORECASE | re.DOTALL):
        record: dict[str, str] = {}
        for key, value in re.findall(r"<s_([^>]+)>(.*?)</s_\1>", chunk, flags=re.IGNORECASE | re.DOTALL):
            record[_normalize_key(key)] = _strip_tokens(value)
        if record:
            records.append(record)
    if records:
        return {"line_items": records}
    fields = {}
    for key, value in re.findall(r"<s_([^>]+)>(.*?)</s_\1>", cleaned, flags=re.IGNORECASE | re.DOTALL):
        fields[_normalize_key(key)] = _strip_tokens(value)
    return fields


def compare_line_items(predicted_items: list[Any], truth_items: list[CanonicalGroundTruthLineItem]) -> dict[str, Any]:
    pred = [_normalize_predicted_item(item) for item in predicted_items]
    truth = [item for item in truth_items if not item.exclusion_reason]
    pairs: list[dict[str, Any]] = []
    used_pred: set[int] = set()
    used_truth: set[int] = set()
    scored = []
    for pi, p_item in enumerate(pred):
        for ti, t_item in enumerate(truth):
            score = _item_similarity(p_item, t_item)
            if score >= 0.62:
                scored.append((score, pi, ti))
    for score, pi, ti in sorted(scored, reverse=True):
        if pi in used_pred or ti in used_truth:
            continue
        used_pred.add(pi)
        used_truth.add(ti)
        pairs.append({"prediction_index": pi, "truth_index": ti, "score": round(score, 4), "status": "matched"})
    for pi in range(len(pred)):
        if pi not in used_pred:
            pairs.append({"prediction_index": pi, "truth_index": None, "score": 0.0, "status": "unmatched_prediction"})
    for ti in range(len(truth)):
        if ti not in used_truth:
            pairs.append({"prediction_index": None, "truth_index": ti, "score": 0.0, "status": "unmatched_truth"})
    granularity = classify_granularity(pred, truth, len(used_pred))
    return {
        "item_match_count": len(used_truth),
        "item_match_rate": round(len(used_truth) / len(truth), 4) if truth else None,
        "order_independent_row_match_rate": round(len(used_truth) / max(len(pred), len(truth)), 4) if pred or truth else 1.0,
        "amount_aware_item_match_rate": round(sum(1 for pair in pairs if pair["status"] == "matched" and pair["score"] >= 0.78) / len(truth), 4) if truth else None,
        "granularity_class": granularity,
        "pairs": pairs,
    }


def classify_granularity(pred: list[dict[str, Any]], truth: list[CanonicalGroundTruthLineItem], matched: int = 0) -> str:
    if len(pred) == len(truth):
        return "same_granularity" if matched or not pred else "unrelated_rows"
    if not pred or not truth:
        return "unrelated_rows"
    pred_total = _sum_totals(pred)
    truth_total = _sum_totals([item.to_dict() for item in truth])
    totals_close = pred_total is not None and truth_total is not None and abs(pred_total - truth_total) <= max(0.05, abs(truth_total) * 0.02)
    if len(pred) > len(truth) and (matched or totals_close):
        return "prediction_split"
    if len(pred) < len(truth) and (matched or totals_close):
        return "prediction_merged"
    return "ambiguous_granularity"


def _canonicalize_record(raw: Any, source_index: int, *, source_schema: str, source_path: str | None) -> CanonicalGroundTruthLineItem:
    if isinstance(raw, str):
        raw = _coerce_value(raw)
    if not isinstance(raw, dict):
        return CanonicalGroundTruthLineItem(source_index, raw_value=raw, source_schema=source_schema, source_path=source_path, exclusion_reason=GT_FAILURE_CODES["unsupported"])
    values = {_normalize_key(key): value for key, value in raw.items()}
    description = _first_clean(values, "description", "desc", "designation", "item", "product", "product_name", "name", "libelle", "details", "service")
    reference = _first_clean(values, "reference", "ref", "code", "sku", "product_code", "article_no")
    quantity = _first_number(values, "quantity", "qty", "qte", "quantite")
    unit = _first_clean(values, "unit", "uom", "unite")
    unit_price = _first_number(values, "unit_price", "price", "rate", "prix", "prix_unitaire")
    discount = _first_number(values, "discount", "remise")
    tax_rate = _first_number(values, "tax_rate", "vat", "tva", "tax")
    line_total_ht = _first_number(values, "line_total_ht", "total_ht", "net_amount", "net_worth", "amount_ht")
    line_total_ttc = _first_number(values, "line_total_ttc", "total", "amount", "line_total", "total_ttc", "gross_amount", "gross_worth")
    combined_text = " ".join(str(value) for value in values.values() if value not in (None, ""))
    warnings: list[str] = []
    exclusion = _exclusion_reason(description or combined_text)
    if not any(str(value).strip() for value in values.values() if value is not None):
        exclusion = GT_FAILURE_CODES["empty"]
    if not description and reference:
        description = reference
        warnings.append("description_recovered_from_reference")
    if not description and sum(char.isalpha() for char in combined_text) >= 3:
        description = _clean_text(re.sub(r"[-+]?\d[\d\s]*(?:[,.]\d+)?", " ", combined_text))
        warnings.append("description_recovered_from_raw_record")
    if not description and not line_total_ttc:
        exclusion = exclusion or GT_FAILURE_CODES["empty"]
    confidence = 0.9
    if quantity is None or unit_price is None:
        confidence -= 0.15
    if line_total_ttc is None:
        confidence -= 0.15
    if warnings:
        confidence -= 0.05
    return CanonicalGroundTruthLineItem(
        source_index=source_index,
        description=description,
        reference=reference,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        discount=discount,
        tax_rate=tax_rate,
        line_total_ht=line_total_ht,
        line_total_ttc=line_total_ttc,
        raw_value=raw,
        source_schema=source_schema,
        source_path=source_path,
        normalization_warnings=warnings,
        exclusion_reason=exclusion,
        item_confidence=round(max(0.0, confidence), 3),
    )


def _find_item_containers(payload: Any) -> list[Any]:
    aliases = {"line_items", "items", "products", "rows", "table_items", "invoice_items", "item_list", "articles", "details"}
    found = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            norm_key = _normalize_key(key)
            if norm_key in aliases and isinstance(value, (list, dict, str)):
                found.append(value)
            found.extend(_find_item_containers(value))
    elif isinstance(payload, list):
        if payload and all(isinstance(item, (dict, str)) for item in payload):
            found.append(payload)
        for item in payload:
            found.extend(_find_item_containers(item))
    elif isinstance(payload, str):
        parsed = _coerce_value(payload)
        if parsed is not payload:
            found.extend(_find_item_containers(parsed))
        elif "<s_" in payload:
            found.extend(_find_item_containers(parse_donut_serialized_tokens(payload)))
    return found


def _flatten_record_container(container: Any) -> list[Any]:
    if isinstance(container, str):
        container = _coerce_value(container)
        if isinstance(container, str) and "<s_" in container:
            container = parse_donut_serialized_tokens(container).get("line_items", [])
    if isinstance(container, dict):
        if all(not isinstance(value, (dict, list)) for value in container.values()):
            return [container]
        records = []
        for value in container.values():
            records.extend(_flatten_record_container(value))
        return records
    if isinstance(container, list):
        records = []
        for item in container:
            records.extend(_flatten_record_container(item))
        return records
    return [container]


def _extract_gt_parse(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key in ("gt_parse", "ground_truth", "target", "label", "parsed_data"):
            if key in payload:
                return _coerce_value(payload[key])
        for value in payload.values():
            found = _extract_gt_parse(value)
            if found not in (None, {}, []):
                return found
    return payload


def _looks_like_donut(payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False, default=str).lower()[:10000]
    return "gt_parse" in text or "<s_" in text


def _looks_like_invoicexpert(payload: Any, dataset_name: str) -> bool:
    return "invoicexpert" in dataset_name.lower()


def _looks_like_invoices_receipts(payload: Any, dataset_name: str) -> bool:
    return "receipts" in dataset_name.lower()


def _looks_like_fatura2(payload: Any, dataset_name: str) -> bool:
    return "fatura" in dataset_name.lower() or (isinstance(payload, dict) and "parsed_data" in payload)


def _looks_like_md_invoice(payload: Any, dataset_name: str) -> bool:
    return "md_invoice" in dataset_name.lower() or "md-invoice" in dataset_name.lower()


def _exclusion_reason(text: str) -> str | None:
    plain = _norm(text)
    if not plain:
        return GT_FAILURE_CODES["empty"]
    if any(term in plain for term in ("subtotal", "sub total", "sous total", "grand total", "total due", "amount due", "total ttc")):
        return GT_FAILURE_CODES["total"]
    if any(term in plain for term in ("tax summary", "vat summary", "sales tax", "tva")) and not any(word in plain for word in ("service", "product", "item")):
        return GT_FAILURE_CODES["tax"]
    if re.search(r"\b(vat|tax|tva)\b", plain) and re.search(r"\d", plain):
        return GT_FAILURE_CODES["tax"]
    if re.fullmatch(r"(description|item|product|quantity|qty|price|unit price|amount|total|vat|tax|tva)(\s+\w+){0,4}", plain):
        return "TABLE_GT_HEADER_ROW_REMOVED"
    if any(term in plain for term in ("iban", "swift", "rib", "bank", "payment")):
        return "TABLE_GT_PAYMENT_ROW_REMOVED"
    if any(term in plain for term in ("shipping", "handling", "delivery fee")):
        return GT_FAILURE_CODES["shipping"]
    return None


def _dedupe_key(item: CanonicalGroundTruthLineItem) -> tuple[Any, ...]:
    return (
        _norm(item.description or ""),
        _norm(item.reference or ""),
        _round_or_none(item.quantity),
        _round_or_none(item.unit_price),
        _round_or_none(item.line_total_ttc),
    )


def _normalize_predicted_item(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        item = item.model_dump(mode="json")
    if not isinstance(item, dict):
        return {}
    return {
        "description": item.get("description"),
        "reference": item.get("reference"),
        "quantity": _number(item.get("quantity")),
        "unit_price": _number(item.get("unit_price")),
        "line_total_ttc": _number(item.get("line_total_ttc") if item.get("line_total_ttc") is not None else item.get("total")),
    }


def _item_similarity(pred: dict[str, Any], truth: CanonicalGroundTruthLineItem) -> float:
    desc = _string_similarity(pred.get("description"), truth.description)
    ref = 1.0 if truth.reference and _norm(pred.get("reference") or "") == _norm(truth.reference) else 0.0
    qty = _num_match(pred.get("quantity"), truth.quantity)
    price = _num_match(pred.get("unit_price"), truth.unit_price)
    total = _num_match(pred.get("line_total_ttc"), truth.line_total_ttc)
    return desc * 0.45 + ref * 0.2 + qty * 0.12 + price * 0.1 + total * 0.13


def _string_similarity(a: Any, b: Any) -> float:
    left = _norm(a or "")
    right = _norm(b or "")
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 0.9
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _num_match(a: Any, b: Any) -> float:
    left = _number(a)
    right = _number(b)
    if left is None or right is None:
        return 0.0
    return 1.0 if abs(left - right) <= max(0.01, abs(right) * 0.005) else 0.0


def _sum_totals(items: list[dict[str, Any]]) -> float | None:
    totals = [_number(item.get("line_total_ttc") or item.get("total")) for item in items]
    totals = [value for value in totals if value is not None]
    return round(sum(totals), 3) if totals else None


def _first_clean(values: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = values.get(_normalize_key(key))
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return None


def _first_number(values: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(values.get(_normalize_key(key)))
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_amount(str(value))


def _round_or_none(value: Any) -> float | None:
    num = _number(value)
    return round(num, 4) if num is not None else None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _strip_tokens(str(value))
    text = re.sub(r"\s+", " ", text).strip(" |:-")
    return text if sum(char.isalpha() for char in text) >= 2 else None


def _strip_tokens(value: str) -> str:
    return re.sub(r"</?s_[^>]+>|</?s>|<[^>]+>", " ", str(value)).strip()


def _normalize_key(value: str) -> str:
    return re.sub(r"_+", "_", "".join(char.lower() if char.isalnum() else "_" for char in str(value))).strip("_")


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", strip_accents(str(value)).casefold()).split())


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
