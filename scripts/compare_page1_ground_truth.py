"""Compare one verified physical Page-1 producer invoice with machine output.

This intentionally does not traverse or score RUSPINA/Page 2 or customs/Page 3.
The expected input is a page1_machine_prediction.json produced by the companion
runner, plus the project's canonical private dossier ground-truth file.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.utils.helpers import parse_date


GROUPS = {
    "IDENTIFIERS": ("invoice_number", "invoice_date", "proforma_invoice_number", "proforma_invoice_date"),
    "PARTIES": ("seller", "client", "client_address", "client_rc", "consignee", "consignee_address"),
    "FINANCIAL": ("currency", "total", "total_amount_words"),
    "PRODUCT": ("hs_code", "packaging", "number_of_bags", "bag_weight", "truck_count"),
    "LOGISTICS": ("incoterm", "origin", "destination", "shipment"),
    "PAYMENT": ("payment", "bank", "iban", "swift"),
    "LINE ITEMS": ("description", "quantity", "unit", "unit_price", "line_total"),
}
CAUSES = {
    "OCR_TRANSCRIPTION", "LABEL_ASSOCIATION", "PARTY_ROLE", "SEMANTIC_MAPPING",
    "TABLE_REGION", "TABLE_RECONSTRUCTION", "NORMALIZATION", "DISPLAY_VALUE",
    "MISSING_EXTRACTION", "FALSE_POSITIVE", "VALIDATION_ONLY", "UNKNOWN",
}


class UnverifiedGroundTruthError(ValueError):
    pass


def _text_normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    text = text.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s*-\s*", "-", text)
    return " ".join(text.split()).rstrip(" .")


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = re.sub(r"[^0-9,.-]", "", str(value).replace("\u00a0", " ").replace("\u202f", " "))
    if not text or not re.search(r"\d", text):
        return None
    # A comma followed by one or two digits is the decimal marker; otherwise
    # commas/spaces are treated as thousands separators.
    if "," in text and re.search(r",\d{1,2}$", text):
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _normal(value: Any, field: str, supplied: Any = None) -> Any:
    value = supplied if supplied is not None else value
    if value is None:
        return None
    if field in {"total", "quantity", "unit_price", "line_total", "number_of_bags", "bag_weight"}:
        return _decimal(value)
    if field == "iban":
        return re.sub(r"\s+", "", str(value)).upper()
    if field in {"invoice_date", "proforma_invoice_date"}:
        text = str(value).strip()
        parsed = parse_date(text)
        return parsed.isoformat() if parsed else text
    return _text_normalize(value)


def _result(expected: Any, predicted: Any, field: str, *, expected_normalized: Any = None,
            predicted_normalized: Any = None, presence: str | None = None) -> str:
    if presence in {"unclear", "illegible", "unavailable"}:
        return "unavailable"
    if expected is None:
        return "correct" if predicted is None else "false_positive"
    if predicted is None:
        return "missing"
    left = _normal(expected, field, expected_normalized)
    right = _normal(predicted, field, predicted_normalized)
    if left == right:
        return "correct" if str(expected) == str(predicted) else "formatting_only"
    return "wrong_value"


def _producer_document(truth: dict[str, Any]) -> dict[str, Any]:
    if truth.get("label_status") != "verified" or truth.get("human_verified") is not True:
        raise UnverifiedGroundTruthError("Page-1 ground truth must be human verified before comparison.")
    matches = [item for item in truth.get("logical_documents", [])
               if item.get("document_role") == "producer" and item.get("physical_page_numbers") == [1]]
    if len(matches) != 1:
        raise ValueError("Expected exactly one producer logical document covering physical Page 1 only.")
    return matches[0]


def _field_items(prediction: dict[str, Any], producer: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    predicted_fields = prediction.get("fields", {})
    for name, expected_record in producer.get("fields", {}).items():
        predicted_record = predicted_fields.get(name)
        if predicted_record is None:
            predicted_record = {}
        raw = predicted_record.get("raw_machine_value")
        canonical = predicted_record.get("canonical_value")
        effective = predicted_record.get("effective_machine_value")
        # Extraction scoring is pre-correction: prefer canonical machine output,
        # then uncured effective output, never the correction field.
        machine = canonical if canonical is not None else (effective if effective is not None else raw)
        expected = expected_record.get("verified_value")
        expected_norm = expected_record.get("normalized_value")
        machine_norm = predicted_record.get("normalized_value")
        predicted_display = predicted_record.get("display_value")
        result = _result(expected, machine, name, expected_normalized=expected_norm,
                         predicted_normalized=machine_norm,
                         presence=expected_record.get("presence_status"))
        if (result == "formatting_only" and expected_record.get("source_value") is not None
                and (predicted_display or str(raw or "")) == expected_record.get("source_value")):
            result = "correct"
        item = {
            "field": name,
            "expected": expected,
            "expected_display": expected_record.get("source_value"),
            "normalized_expected": expected_norm if expected_norm is not None else _normal(expected, name),
            "raw_machine_value": raw,
            "canonical_machine_value": canonical,
            "human_correction": predicted_record.get("human_correction"),
            "corrected_effective_value": (predicted_record.get("human_correction")
                                          if predicted_record.get("human_correction") is not None else machine),
            "predicted": machine,
            "predicted_display": predicted_record.get("display_value"),
            "normalized_predicted": machine_norm if machine_norm is not None else _normal(machine, name),
            "status": result,
            "root_cause": "DISPLAY_VALUE" if result == "formatting_only" else None,
            "source": predicted_record.get("source"),
            "evidence": predicted_record.get("evidence_text"),
            "page": 1,
        }
        if result not in {"correct", "formatting_only"}:
            item["root_cause"] = _cause(name, result, machine, expected_record, predicted_record, predicted_fields)
        items.append(item)
    return items


def _cause(field: str, result: str, actual: Any, expected_record: dict[str, Any],
           predicted_record: dict[str, Any], all_predictions: dict[str, Any]) -> str:
    override = expected_record.get("likely_root_cause")
    if override in CAUSES:
        return override
    if result == "false_positive":
        return "FALSE_POSITIVE"
    if result == "missing":
        return "MISSING_EXTRACTION" if predicted_record.get("evidence_text") else "UNKNOWN"
    if field in {"client", "consignee", "seller"}:
        other_values = {key: value.get("canonical_value") or value.get("effective_machine_value")
                        or value.get("raw_machine_value") for key, value in all_predictions.items()}
        if actual in {value for key, value in other_values.items() if key in {"client", "consignee", "seller"} and key != field}:
            return "PARTY_ROLE"
    for key, value in all_predictions.items():
        other = value.get("canonical_value") or value.get("effective_machine_value") or value.get("raw_machine_value")
        if key != field and actual is not None and _normal(actual, field) == _normal(other, key):
            return "SEMANTIC_MAPPING"
    if predicted_record.get("evidence_text") and _text_normalize(predicted_record["evidence_text"]) != _text_normalize(expected_record.get("source_value", "")):
        return "OCR_TRANSCRIPTION"
    return "UNKNOWN"


def _line_items(prediction: dict[str, Any], producer: dict[str, Any]) -> list[dict[str, Any]]:
    expected_rows = producer.get("line_items", {}).get("verified_rows") or []
    actual_rows = prediction.get("line_items", [])
    comparisons = []
    keys = ("description", "quantity", "unit", "unit_price", "line_total")
    for index in range(max(len(expected_rows), len(actual_rows))):
        expected_row = expected_rows[index] if index < len(expected_rows) else None
        actual_row = actual_rows[index] if index < len(actual_rows) else None
        corrections = prediction.get("line_item_human_corrections", {}).get(str(index), {})
        expected_values = (expected_row.get("verified_value", expected_row) if expected_row else {})
        for key in keys:
            expected = expected_values.get(key) if expected_row else None
            actual = actual_row.get(key) if actual_row else None
            expected_display = expected_row.get("source_values", {}).get(key) if expected_row else None
            expected_normalized = expected_row.get("normalized_values", {}).get(key) if expected_row else None
            correction = corrections.get(key, {}).get("corrected_value")
            result = "missing" if expected_row and actual_row is None else (
                "false_positive" if actual_row and expected_row is None and actual is not None else
                _result(expected, actual, key, expected_normalized=expected_normalized)
            )
            if (result == "correct" and expected_display is not None
                    and key in {"quantity", "unit_price", "line_total"}
                    and str(actual) != str(expected_display)):
                result = "formatting_only"
            comparisons.append({
                "field": f"line_items[{index}].{key}", "row_id": expected_row.get("row_id") if expected_row else None,
                "expected": expected, "expected_display": expected_display,
                "normalized_expected": expected_normalized if expected_normalized is not None else _normal(expected, key),
                "predicted": actual, "normalized_predicted": _normal(actual, key), "status": result,
                "human_correction": correction,
                "corrected_effective_value": correction if correction is not None else actual,
                "root_cause": (None if result == "correct" else "DISPLAY_VALUE" if result == "formatting_only" else
                    expected_row.get("likely_root_cause", {}).get(key, "UNKNOWN") if expected_row else "FALSE_POSITIVE"),
                "page": 1,
            })
    return comparisons


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    return {status: sum(item["status"] == status for item in items)
            for status in ("correct", "missing", "wrong_value", "false_positive", "formatting_only", "unavailable")}


def compare_page1(prediction: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    if prediction.get("physical_page") != 1 or prediction.get("document_role") != "producer":
        raise ValueError("Prediction must contain physical Page 1 producer output only.")
    producer = _producer_document(truth)
    items = _field_items(prediction, producer)
    line_items = _line_items(prediction, producer)
    # Explicitly audit legacy fields without adding them to the Page-1 success set.
    obsolete = prediction.get("legacy_field_audit", {})
    false_positives = [
        {"field": name, "predicted": value.get("value"), "evidence": value.get("evidence_text"),
         "status": "false_positive", "root_cause": "FALSE_POSITIVE", "page": 1}
        for name, value in obsolete.items()
        if value.get("value") not in (None, "") and not value.get("source_semantically_relevant", False)
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    by_name = {item["field"]: item for item in items}
    for group, fields in GROUPS.items():
        groups[group] = [by_name[field] for field in fields if field in by_name]
    groups["LINE ITEMS"] = line_items
    counts = _summary(items + line_items + false_positives)
    total = len(items) + len(line_items)
    counts["expected_fields_and_cells"] = total
    counts["exact_match_rate"] = round(counts["correct"] / total, 4) if total else 0.0
    return {
        "scope": "INV01-only verified physical Page-1 producer invoice baseline",
        "scope_pages": [1], "ground_truth_source": "canonical private dossier GT / producer section only",
        "summary": counts, "groups": groups,
        "false_positive_audit": false_positives,
        "validation": prediction.get("validation", {}),
        "notes": ["Metrics describe this one verified Page-1 reference only, not global or production accuracy."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    report = compare_page1(prediction, truth)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
