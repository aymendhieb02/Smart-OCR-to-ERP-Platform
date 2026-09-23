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
            item = _comparison(
                document_id, field, actual, expected, section,
                review.get("error_classification"),
                presence_status=review.get("presence_status"),
            )
            item["page"] = review.get("source_page")
            item["evidence"] = _prediction_evidence(predicted or {}, review.get("source_page"))
            sections[section].append(item)
        expected_rows = review_doc.get("line_items", {}).get("verified_rows")
        actual_rows = (predicted or {}).get("line_items", [])
        sections["table"].extend(_compare_rows(
            document_id, actual_rows, expected_rows or [],
            page=review_doc.get("physical_page_numbers"),
        ))

    for relation in truth.get("relations", []):
        expected = relation.get("verified_status")
        actual = _prediction_relation_status(relation, prediction_docs)
        item = _comparison("relations", f"{relation['left_field']}↔{relation['right_field']}", actual, expected, "relations")
        item["page"] = relation.get("source_pages")
        item["evidence"] = relation.get("notes")
        sections["relations"].append(item)

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
    parties = document.get("parties", {})
    identifiers = document.get("identifiers", {})
    financial = document.get("financial", {})
    expanded = document.get("expanded_fields", {})
    rows = document.get("line_items") or fields.get("line_items") or []
    first_row = rows[0] if rows else {}
    def first(*values: Any) -> Any:
        return next((value for value in values if value is not None), None)
    def expanded_value(key: str) -> Any:
        value = expanded.get(key)
        return value.get("value") if isinstance(value, dict) and "value" in value else value
    return {
        "document_type": document.get("document_type"),
        "document_family": document.get("document_family"),
        "invoice_number": first(identifiers.get("invoice_number"), fields.get("invoice_number")),
        "invoice_date": first(identifiers.get("invoice_date"), fields.get("invoice_date")),
        "declaration_number": identifiers.get("declaration_number"),
        "declaration_date": identifiers.get("declaration_date"),
        "seller": first(parties.get("seller"), parties.get("supplier"), fields.get("supplier_name")),
        "supplier": first(parties.get("supplier"), fields.get("supplier_name")),
        "buyer": first(parties.get("buyer"), parties.get("customer"), fields.get("customer_name")),
        "customer": first(parties.get("customer"), fields.get("customer_name")),
        "currency": first(financial.get("currency"), fields.get("currency")),
        "amount_ht": first(financial.get("amount_ht"), fields.get("amount_ht")),
        "tax": first(financial.get("tax"), fields.get("tva_amount")),
        "amount_ttc": first(financial.get("amount_ttc"), fields.get("amount_ttc")),
        "total": first(financial.get("total"), financial.get("amount_ttc"), fields.get("amount_ttc")),
        "referenced_invoice": identifiers.get("referenced_invoice"),
        "declaration_reference": identifiers.get("declaration_reference"),
        "invoice_value": financial.get("invoice_value"),
        **{key: first(parties.get(key), fields.get(key), expanded_value(key)) for key in ("consignee", "exporter", "importer", "declarant")},
        **{key: first(fields.get(key), expanded_value(key), document.get(key)) for key in (
            "hs_code", "incoterm", "origin", "destination", "payment", "gross_weight",
            "net_weight", "number_of_bags", "delivery",
        )},
        **{key: first(first_row.get(key), fields.get(key)) for key in (
            "description", "quantity", "unit", "unit_price",
        )},
        "line_total": first(first_row.get("line_total"), first_row.get("line_total_ht"), first_row.get("line_total_ttc"), first_row.get("total")),
    }


def _section_for(field: str) -> str:
    if field in {"document_type", "document_family"}:
        return "document"
    if field in {"invoice_number", "invoice_date", "referenced_invoice", "declaration_number", "declaration_date", "declaration_reference"}:
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
    presence_status: str | None = None,
) -> dict[str, Any]:
    if presence_status in {"unclear", "illegible"}:
        result = "UNKNOWN"
    elif presence_status == "absent":
        result = "CORRECT" if actual is None and expected is None else ("EXTRA_FIELD" if expected is None else "MISSING_FIELD")
    elif actual == expected:
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


def _compare_rows(document_id: str, actual: list[Any], expected: list[Any], page: Any = None) -> list[dict[str, Any]]:
    rows = []
    for index in range(max(len(actual), len(expected))):
        if index >= len(actual):
            result = "TABLE_ROW_MISSING"
        elif index >= len(expected):
            result = "TABLE_ROW_EXTRA"
        elif _row_values(actual[index]) == _row_values(expected[index]):
            result = "CORRECT"
        else:
            result = "TABLE_CELL_ERROR"
        rows.append({"document": document_id, "field": f"line_items[{index}]", "predicted": _row_values(actual[index]) if index < len(actual) else None, "verified": _row_values(expected[index]) if index < len(expected) else None, "result": result, "cause": "UNKNOWN" if result != "CORRECT" else None, "page": page, "evidence": None})
    return rows


def _row_values(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    keys = ("reference", "description", "quantity", "unit", "unit_price")
    values = {key: row[key] for key in keys if key in row and row[key] is not None}
    line_total = next((row[key] for key in ("line_total", "line_total_ht", "line_total_ttc", "total") if row.get(key) is not None), None)
    if line_total is not None:
        values["line_total"] = line_total
    return values


def _prediction_evidence(document: dict[str, Any], source_page: Any) -> list[dict[str, Any]]:
    pages = set(source_page if isinstance(source_page, list) else ([source_page] if isinstance(source_page, int) else []))
    return [
        {"page": item.get("physical_page"), "text": item.get("text")}
        for item in document.get("evidence", [])
        if not pages or item.get("physical_page") in pages
    ]


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
    parser.add_argument("--output", type=Path, help="Optional local JSON report path.")
    args = parser.parse_args()
    try:
        result = compare_files(args.prediction, args.ground_truth)
    except UnverifiedGroundTruthError as exc:
        parser.exit(2, f"STOP: {exc}\n")
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
