from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.manual_benchmark_utils import DEFAULT_BENCHMARK_ROOT, FIELD_NAMES, html_table, read_csv, read_json, write_json


def main() -> None:
    args = parse_args()
    benchmark_root = Path(args.benchmark_root).resolve()
    before_root = benchmark_root / "reports" / "runs" / args.before
    after_root = benchmark_root / "reports" / "runs" / args.after
    before_summary = read_json(before_root / "summary.json")
    after_summary = read_json(after_root / "summary.json")
    before_rows = read_csv(before_root / "results.csv")
    after_rows = read_csv(after_root / "results.csv")
    comparison = compare_runs(args.before, args.after, before_summary, after_summary, before_rows, after_rows)
    output_root = benchmark_root / "reports" / "comparisons"
    output_root.mkdir(parents=True, exist_ok=True)
    output_json = output_root / f"{args.before}_vs_{args.after}.json"
    output_html = output_root / f"{args.before}_vs_{args.after}.html"
    write_json(output_json, comparison)
    output_html.write_text(render_html(comparison), encoding="utf-8")
    print(f"Comparison JSON: {output_json}")
    print(f"Comparison HTML: {output_html}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two manual ground-truth benchmark runs.")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT), help="Manual benchmark root folder.")
    parser.add_argument("--before", required=True, help="Before run ID.")
    parser.add_argument("--after", required=True, help="After run ID.")
    return parser.parse_args()


def compare_runs(before_id: str, after_id: str, before_summary: dict[str, Any], after_summary: dict[str, Any], before_rows: list[dict[str, str]], after_rows: list[dict[str, str]]) -> dict[str, Any]:
    field_deltas = []
    for field in FIELD_NAMES:
        before_accuracy = before_summary["field_metrics"].get(field, {}).get("accuracy")
        after_accuracy = after_summary["field_metrics"].get(field, {}).get("accuracy")
        field_deltas.append({
            "field": field,
            "before": before_accuracy,
            "after": after_accuracy,
            "delta": delta(after_accuracy, before_accuracy),
        })
    before_line = before_summary["line_item_metrics"]
    after_line = after_summary["line_item_metrics"]
    before_doc = before_summary["document_metrics"]
    after_doc = after_summary["document_metrics"]
    regressions = document_regressions(before_rows, after_rows)
    return {
        "before": before_id,
        "after": after_id,
        "field_accuracy_delta": field_deltas,
        "line_item_f1_delta": delta(after_line.get("row_f1"), before_line.get("row_f1")),
        "document_perfect_rate_delta": delta(after_doc.get("fully_correct_document_rate"), before_doc.get("fully_correct_document_rate")),
        "erp_safety_delta": {
            "false_erp_ready_count_delta": (after_doc.get("false_erp_ready_count") or 0) - (before_doc.get("false_erp_ready_count") or 0),
            "erp_ready_and_actually_correct_rate_delta": delta(after_doc.get("erp_ready_and_actually_correct_rate"), before_doc.get("erp_ready_and_actually_correct_rate")),
        },
        "processing_time_delta": delta(after_summary.get("processing_time", {}).get("average_seconds"), before_summary.get("processing_time", {}).get("average_seconds")),
        "regressions_by_document": regressions,
    }


def delta(after: Any, before: Any) -> float | None:
    if after is None or before is None:
        return None
    return round(float(after) - float(before), 4)


def boolish(value: Any) -> bool | None:
    if value in (True, "True", "true", "1", 1):
        return True
    if value in (False, "False", "false", "0", 0):
        return False
    return None


def document_regressions(before_rows: list[dict[str, str]], after_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    before_by_file = {row["filename"]: row for row in before_rows}
    regressions = []
    for after in after_rows:
        before = before_by_file.get(after["filename"])
        if not before:
            continue
        before_perfect = boolish(before.get("fully_correct_document"))
        after_perfect = boolish(after.get("fully_correct_document"))
        before_false_ready = boolish(before.get("false_erp_ready"))
        after_false_ready = boolish(after.get("false_erp_ready"))
        before_f1 = to_float(before.get("line_item_f1"))
        after_f1 = to_float(after.get("line_item_f1"))
        reasons = []
        if before_perfect is True and after_perfect is False:
            reasons.append("document stopped being fully correct")
        if before_false_ready is False and after_false_ready is True:
            reasons.append("new false ERP Ready")
        if before_f1 is not None and after_f1 is not None and after_f1 < before_f1:
            reasons.append("line-item F1 decreased")
        if reasons:
            regressions.append({
                "filename": after["filename"],
                "reasons": reasons,
                "before_line_item_f1": before_f1,
                "after_line_item_f1": after_f1,
                "before_prediction": before.get("prediction_path"),
                "after_prediction": after.get("prediction_path"),
            })
    return regressions


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def render_html(comparison: dict[str, Any]) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Manual Benchmark Comparison</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; margin: 14px 0 28px; }}
    th, td {{ border: 1px solid #d9e0ea; padding: 8px; text-align: left; font-size: 13px; }}
    th {{ background: #f6f8fb; }}
  </style>
</head>
<body>
  <h1>{comparison['before']} vs {comparison['after']}</h1>
  <h2>Field Accuracy Delta</h2>
  {html_table(comparison['field_accuracy_delta'], ['field', 'before', 'after', 'delta'])}
  <h2>Key Deltas</h2>
  {html_table([{
      'line_item_f1_delta': comparison['line_item_f1_delta'],
      'document_perfect_rate_delta': comparison['document_perfect_rate_delta'],
      'processing_time_delta': comparison['processing_time_delta'],
      **comparison['erp_safety_delta'],
  }], ['line_item_f1_delta', 'document_perfect_rate_delta', 'erp_ready_and_actually_correct_rate_delta', 'false_erp_ready_count_delta', 'processing_time_delta'])}
  <h2>Regressions</h2>
  {html_table(comparison['regressions_by_document'], ['filename', 'reasons', 'before_line_item_f1', 'after_line_item_f1', 'before_prediction', 'after_prediction'])}
</body>
</html>
"""


if __name__ == "__main__":
    main()
