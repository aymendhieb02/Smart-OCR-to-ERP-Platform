from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class UnverifiedGroundTruthError(ValueError):
    pass


ERROR_TAXONOMY = {
    "CORRECT", "MISSING_FIELD", "WRONG_VALUE", "EXTRA_FIELD",
    "WRONG_DOCUMENT_TYPE", "WRONG_DOCUMENT_FAMILY", "WRONG_PARTY_ROLE",
    "OCR_TRANSCRIPTION_ERROR", "SEMANTIC_EXTRACTION_ERROR",
    "TABLE_ROW_MISSING", "TABLE_ROW_EXTRA", "TABLE_CELL_ERROR",
    "CROSS_DOCUMENT_RELATION_ERROR", "UNKNOWN",
}


def compare_files(prediction_path: Path, ground_truth_path: Path) -> dict[str, Any]:
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    return compare_payloads(prediction, truth)


def compare_payloads(prediction: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    if truth.get("label_status") != "verified":
        raise UnverifiedGroundTruthError(
            "Ground truth is not verified: label_status must equal 'verified'. No metrics calculated."
        )
    if truth.get("human_verified") is not True:
        raise UnverifiedGroundTruthError(
            "Ground truth is not verified: human_verified must be true. No metrics calculated."
        )

    prediction_docs = {
        item["logical_document_id"]: item
        for item in prediction["dossier"]["logical_documents"]
    }
    sections: dict[str, list[dict[str, Any]]] = {
        key: [] for key in ("document", "identifiers", "parties", "financial", "table", "customs", "relations")
    }
    for review_doc in truth.get("logical_documents", []):
        document_id = review_doc["logical_document_id"]
        predicted = prediction_docs.get(document_id)
        machine_values = _prediction_values(predicted or {})
        for field, review in review_doc.get("fields", {}).items():
            expected = review.get("verified_value")
            actual = machine_values.get(field)
            section = _section_for(field)
            sections[section].append(_comparison(
                document_id, field, actual, expected, section,
                review.get("error_classification"),
            ))
        expected_rows = review_doc.get("line_items", {}).get("verified_rows")
        actual_rows = (predicted or {}).get("line_items", [])
        sections["table"].extend(_compare_rows(document_id, actual_rows, expected_rows or []))

    for relation in truth.get("relations", []):
        expected = relation.get("verified_status")
        actual = _prediction_relation_status(relation, prediction_docs)
        sections["relations"].append(
            _comparison("relations", f"{relation['left_field']}↔{relation['right_field']}", actual, expected, "relations")
        )

    summary = {
        name: {
            "compared": len(items),
            "correct": sum(item["result"] == "CORRECT" for item in items),
            "errors": sum(item["result"] != "CORRECT" for item in items),
        }
        for name, items in sections.items()
    }
    return {"verification": "verified", "summary_by_section": summary, "comparisons": sections}


def _prediction_values(document: dict[str, Any]) -> dict[str, Any]:
    fields = document.get("detected_fields", {})
    return {
        "document_type": document.get("document_type"),
        "document_family": document.get("document_family"),
        "invoice_number": fields.get("invoice_number"),
        "invoice_date": fields.get("invoice_date"),
        "seller": fields.get("supplier_name"),
        "supplier": fields.get("supplier_name"),
        "buyer": fields.get("customer_name"),
        "customer": fields.get("customer_name"),
        "currency": fields.get("currency"),
        "amount_ht": fields.get("amount_ht"),
        "tax": fields.get("tva_amount"),
        "amount_ttc": fields.get("amount_ttc"),
        "total": fields.get("amount_ttc"),
        "referenced_invoice": document.get("identifiers", {}).get("referenced_invoice"),
        "declaration_reference": document.get("identifiers", {}).get("declaration_reference"),
        "invoice_value": document.get("financial", {}).get("invoice_value"),
        **{key: document.get("parties", {}).get(key) for key in ("consignee", "exporter", "importer", "declarant")},
    }


def _section_for(field: str) -> str:
    if field in {"document_type", "document_family"}:
        return "document"
    if field in {"invoice_number", "invoice_date", "referenced_invoice", "declaration_number", "declaration_reference"}:
        return "identifiers"
    if field in {"seller", "supplier", "buyer", "customer", "consignee", "exporter", "importer", "declarant"}:
        return "parties"
    if field in {"currency", "amount_ht", "tax", "amount_ttc", "total", "invoice_value"}:
        return "financial"
    if field in {"hs_code", "gross_weight", "net_weight", "origin", "destination"}:
        return "customs"
    return "table"


def _comparison(
    document_id: str,
    field: str,
    actual: Any,
    expected: Any,
    section: str,
    reviewed_cause: str | None = None,
) -> dict[str, Any]:
    if actual == expected:
        result = "CORRECT"
    elif actual is None and expected is not None:
        result = "MISSING_FIELD"
    elif actual is not None and expected is None:
        result = "EXTRA_FIELD"
    elif field == "document_type":
        result = "WRONG_DOCUMENT_TYPE"
    elif field == "document_family":
        result = "WRONG_DOCUMENT_FAMILY"
    elif section == "parties":
        result = "WRONG_PARTY_ROLE"
    elif section == "relations":
        result = "CROSS_DOCUMENT_RELATION_ERROR"
    else:
        result = "WRONG_VALUE"
    cause = None
    if result != "CORRECT":
        cause = reviewed_cause if reviewed_cause in ERROR_TAXONOMY else "UNKNOWN"
    return {"document": document_id, "field": field, "predicted": actual, "verified": expected, "result": result, "cause": cause}


def _compare_rows(document_id: str, actual: list[Any], expected: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for index in range(max(len(actual), len(expected))):
        if index >= len(actual):
            result = "TABLE_ROW_MISSING"
        elif index >= len(expected):
            result = "TABLE_ROW_EXTRA"
        elif actual[index] == expected[index]:
            result = "CORRECT"
        else:
            result = "TABLE_CELL_ERROR"
        rows.append({"document": document_id, "field": f"line_items[{index}]", "predicted": actual[index] if index < len(actual) else None, "verified": expected[index] if index < len(expected) else None, "result": result, "cause": "UNKNOWN" if result != "CORRECT" else None})
    return rows


def _prediction_relation_status(relation: dict[str, Any], documents: dict[str, dict[str, Any]]) -> str:
    roles = {ROLE: doc for ROLE, doc in zip(("producer", "ruspina", "customs"), documents.values())}
    left_role, left_field = relation["left_field"].split(".", 1)
    right_role, right_field = relation["right_field"].split(".", 1)
    left = _prediction_values(roles.get(left_role, {})).get(left_field)
    right = _prediction_values(roles.get(right_role, {})).get(right_field)
    if left is None or right is None:
        return "unavailable"
    return "match" if left == right else "mismatch"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare dossier predictions with explicitly verified human ground truth.")
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare_files(args.prediction, args.ground_truth)
    except UnverifiedGroundTruthError as exc:
        parser.exit(2, f"STOP: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
