"""Build Sprint 3B table-anchor and company-candidate failure matrices."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = ROOT / "dataset" / "reports" / "multi_dataset_benchmark" / "predictions"
OUTPUT = ROOT / "analysis"


def predictions():
    for path in sorted(PREDICTIONS.rglob("*.json")):
        try:
            yield path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue


def table_row(path: Path, payload: dict) -> dict:
    response = payload.get("response", {})
    debug = response.get("extraction_debug", {}).get("table_extraction_debug", {})
    blocks = response.get("ocr_blocks", [])
    headers = debug.get("header_groups", [])
    numeric_rows = sum(1 for block in blocks if len(re.findall(r"[-+]?\d+(?:[,.]\d+)?", str(block.get("text", "")))) >= 2)
    selected = debug.get("selected_table_region")
    return {
        "dataset": payload.get("dataset_name"),
        "filename": Path(payload.get("file_path", path.name)).name,
        "visible_table_expected": "unknown",
        "ocr_blocks_count": len(blocks),
        "header_keyword_count": sum(len(item.get("keywords", [])) for item in headers),
        "header_keywords_found": ";".join(sorted({keyword for item in headers for keyword in item.get("keywords", [])})),
        "same_row_header_groups": len(headers),
        "repeated_numeric_rows": numeric_rows,
        "column_alignment_score": round(sum(item.get("confidence", 0) or 0 for item in debug.get("inferred_columns", [])) / max(1, len(debug.get("inferred_columns", []))), 3),
        "candidate_regions_count": len(debug.get("table_anchor_candidates", [])),
        "selected_region": json.dumps(selected, ensure_ascii=False) if selected else "",
        "rejection_reason": ";".join(debug.get("rejection_reasons", [])) or ("no selected table region" if not selected else ""),
    }


def company_row(path: Path, payload: dict) -> dict:
    response = payload.get("response", {})
    debug = response.get("extraction_debug", {})
    candidates = debug.get("candidates", {})
    semantic_nodes = debug.get("semantic_nodes", [])
    ocr_blocks = response.get("ocr_blocks", [])
    company_lines = [node.get("text") for node in semantic_nodes if node.get("node_type") == "company_candidate"]
    supplier_labels = [node.get("text") for node in semantic_nodes if node.get("node_type") == "supplier_label"]
    customer_labels = [node.get("text") for node in semantic_nodes if node.get("node_type") == "customer_label"]
    party_candidates = candidates.get("supplier_name", []) + candidates.get("customer_name", [])
    return {
        "dataset": payload.get("dataset_name"),
        "filename": Path(payload.get("file_path", path.name)).name,
        "ocr_header_lines": " | ".join(str(item.get("text", "")) for item in ocr_blocks[:20]),
        "semantic_types_assigned": ";".join(sorted({str(node.get("node_type")) for node in semantic_nodes})),
        "company_like_lines": "; ".join(str(value) for value in company_lines if value),
        "supplier_labels": "; ".join(str(value) for value in supplier_labels if value),
        "customer_labels": "; ".join(str(value) for value in customer_labels if value),
        "candidate_count": len(party_candidates),
        "candidate_rejection_reason": "; ".join(str(item.get("rejection_reason")) for item in party_candidates if item.get("rejection_reason")),
        "selected_supplier": response.get("detected_fields", {}).get("supplier_name"),
        "selected_customer": response.get("detected_fields", {}).get("customer_name"),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    items = list(predictions())
    write_csv(OUTPUT / "sprint3b_table_anchor_failures.csv", [table_row(path, payload) for path, payload in items])
    write_csv(OUTPUT / "sprint3b_company_candidate_failures.csv", [company_row(path, payload) for path, payload in items])
    print(f"Audited {len(items)} predictions")


if __name__ == "__main__":
    main()
