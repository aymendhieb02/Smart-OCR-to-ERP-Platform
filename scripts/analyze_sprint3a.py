"""Audit the last benchmark predictions without re-running OCR."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = ROOT / "dataset" / "reports" / "multi_dataset_benchmark" / "predictions"
OUTPUT = ROOT / "analysis"


def load_predictions() -> list[tuple[Path, dict]]:
    found = []
    for path in sorted(PREDICTIONS.rglob("*.json")):
        try:
            found.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    return found


def line_item_row(path: Path, payload: dict) -> dict:
    response = payload.get("response", {})
    table_debug = response.get("extraction_debug", {}).get("table_extraction_debug", {})
    tables = response.get("table_candidates") or response.get("extraction_debug", {}).get("layout_analysis", {}).get("tables", []) or []
    counts = table_debug.get("counts", {})
    candidate_rows = counts.get("candidate_rows", len(table_debug.get("raw_candidate_rows", [])))
    validated = table_debug.get("validated_rows", response.get("line_items_validated") or [])
    review = table_debug.get("review_rows", response.get("line_items_needs_review") or [])
    final = table_debug.get("final_line_items", response.get("detected_fields", {}).get("line_items") or [])
    if candidate_rows and not final and not validated and not review:
        point = "D: reconstructed but not copied into final response"
    elif candidate_rows and not validated and review:
        point = "C: reconstructed but rejected for review"
    elif not tables:
        point = "A/B: table not detected or not reconstructed"
    else:
        point = "none"
    return {
        "dataset": payload.get("dataset_name"),
        "filename": Path(payload.get("file_path", path.name)).name,
        "prediction_path": str(path),
        "products_table_block_detected": any(block.get("block_type") in {"products", "products_table"} for block in response.get("layout_blocks", []) if isinstance(block, dict)),
        "table_header_detected": bool(tables),
        "inferred_columns_count": max((len(table.get("columns", {})) for table in tables if isinstance(table, dict)), default=0),
        "candidate_rows_count": candidate_rows,
        "validated_rows_count": len(validated),
        "needs_review_rows_count": len(review),
        "final_line_items_count": len(final),
        "point_rows_lost": point,
    }


def party_row(path: Path, payload: dict) -> dict:
    response = payload.get("response", {})
    debug = response.get("extraction_debug", {})
    candidates = debug.get("candidates", {})
    graph_scores = debug.get("field_scores", {})
    blocks = debug.get("document_graph", {}).get("blocks", [])
    supplier_candidates = candidates.get("supplier_name", [])
    customer_candidates = candidates.get("customer_name", [])
    selected = response.get("detected_fields", {})
    rejected = response.get("rejected_candidates", {})
    reasons = []
    for field in ("supplier_name", "customer_name"):
        reasons.extend(item.get("rejection_reason") or "" for item in rejected.get(field, []) if item.get("rejection_reason"))
    return {
        "dataset": payload.get("dataset_name"),
        "filename": Path(payload.get("file_path", path.name)).name,
        "supplier_block_exists": any(block.get("block_type") in {"supplier", "supplier_block"} for block in blocks if isinstance(block, dict)),
        "customer_block_exists": any(block.get("block_type") in {"customer", "customer_block"} for block in blocks if isinstance(block, dict)),
        "company_candidates": "; ".join(str(item.get("value")) for item in supplier_candidates + customer_candidates),
        "graph_company_candidates": len(graph_scores.get("supplier_name", [])) + len(graph_scores.get("customer_name", [])),
        "selected_supplier": selected.get("supplier_name"),
        "selected_customer": selected.get("customer_name"),
        "rejection_reason": "; ".join(sorted(set(reasons))),
        "regression_root_cause": "missing candidate" if not supplier_candidates and not customer_candidates else ("candidate rejected" if reasons else "selector or region routing"),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    predictions = load_predictions()
    write_csv(OUTPUT / "sprint3a_line_item_routing.csv", [line_item_row(path, payload) for path, payload in predictions])
    write_csv(OUTPUT / "sprint3a_party_regression.csv", [party_row(path, payload) for path, payload in predictions])
    print(f"Audited {len(predictions)} predictions")
    print(f"Wrote {OUTPUT / 'sprint3a_line_item_routing.csv'}")
    print(f"Wrote {OUTPUT / 'sprint3a_party_regression.csv'}")


if __name__ == "__main__":
    main()
