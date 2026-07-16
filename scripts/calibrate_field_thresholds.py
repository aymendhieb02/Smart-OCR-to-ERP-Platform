from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.manual_benchmark_utils import DEFAULT_BENCHMARK_ROOT, FIELD_NAMES, read_csv, safe_json_default, write_csv, write_json


THRESHOLDS = [round(value / 100, 2) for value in range(50, 100, 5)]


def main() -> None:
    args = parse_args()
    benchmark_root = Path(args.benchmark_root).resolve()
    run_root = benchmark_root / "reports" / "runs" / args.run_name
    results_path = run_root / "results.csv"
    output_root = benchmark_root / "reports" / "calibration" / args.run_name
    output_root.mkdir(parents=True, exist_ok=True)

    rows = read_csv(results_path)
    if not rows:
        summary = {
            "status": "not_enough_data",
            "reason": f"No result rows found at {results_path}",
            "note": "This script only reports suggested thresholds; it never modifies production settings.",
        }
        write_json(output_root / "threshold_summary.json", summary)
        print(output_root / "threshold_summary.json")
        return

    examples = collect_examples(rows)
    threshold_rows = []
    recommendations = {}
    for field, field_examples in examples.items():
        metrics = [score_threshold(field, field_examples, threshold) for threshold in THRESHOLDS]
        threshold_rows.extend(metrics)
        usable = [item for item in metrics if item["support"] >= args.min_support]
        if usable:
            best = sorted(usable, key=lambda item: (item["f1"], item["precision"], item["recall"]), reverse=True)[0]
            recommendations[field] = {
                "suggested_threshold": best["threshold"],
                "precision": best["precision"],
                "recall": best["recall"],
                "f1": best["f1"],
                "support": best["support"],
                "note": "Review manually before changing production thresholds.",
            }
        else:
            recommendations[field] = {
                "suggested_threshold": None,
                "support": len(field_examples),
                "note": f"Need at least {args.min_support} labeled examples.",
            }

    write_csv(output_root / "threshold_grid.csv", threshold_rows, [
        "field",
        "threshold",
        "support",
        "selected_count",
        "true_positive",
        "false_positive",
        "false_negative",
        "precision",
        "recall",
        "f1",
    ])
    summary = {
        "status": "ok" if any(item.get("suggested_threshold") is not None for item in recommendations.values()) else "not_enough_data",
        "run_root": str(run_root),
        "results_path": str(results_path),
        "fields": recommendations,
        "note": "Uncalibrated confidence threshold analysis only. This script does not edit app settings or extraction code.",
    }
    write_json(output_root / "threshold_summary.json", summary)
    (output_root / "README.md").write_text(markdown_summary(summary), encoding="utf-8")
    print(output_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze manually verified benchmark results and suggest field confidence thresholds.")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT), help="Manual ground-truth benchmark root.")
    parser.add_argument("--run-name", default="final_baseline", help="Run folder under reports/runs.")
    parser.add_argument("--min-support", type=int, default=10, help="Minimum labeled examples before recommending a threshold.")
    return parser.parse_args()


def collect_examples(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    examples: dict[str, list[dict[str, Any]]] = {field: [] for field in FIELD_NAMES}
    for row in rows:
        if row.get("status") != "success":
            continue
        prediction = load_prediction(row.get("prediction_path"))
        for field in FIELD_NAMES:
            applicable = parse_bool(row.get(f"{field}_applicable"))
            correct = parse_bool(row.get(f"{field}_correct"))
            if applicable is not True or correct is None:
                continue
            examples[field].append({
                "correct": correct,
                "confidence": extract_field_confidence(prediction, field),
                "filename": row.get("filename"),
            })
    return examples


def load_prediction(path_value: Any) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(str(path_value))
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def extract_field_confidence(prediction: dict[str, Any], field: str) -> float | None:
    expanded = prediction.get("expanded_fields") or {}
    if isinstance(expanded.get(field), dict):
        value = expanded[field].get("confidence")
        parsed = parse_float(value)
        if parsed is not None:
            return parsed
    confidences = prediction.get("field_confidences") or {}
    return parse_float(confidences.get(field))


def score_threshold(field: str, examples: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    with_confidence = [item for item in examples if item["confidence"] is not None]
    selected = [item for item in with_confidence if float(item["confidence"]) >= threshold]
    tp = sum(1 for item in selected if item["correct"])
    fp = sum(1 for item in selected if not item["correct"])
    fn = sum(1 for item in with_confidence if item["correct"] and float(item["confidence"]) < threshold)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
    return {
        "field": field,
        "threshold": threshold,
        "support": len(with_confidence),
        "selected_count": len(selected),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def parse_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 1 else None


def markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Field Threshold Calibration",
        "",
        "This report analyzes confidence thresholds from manually verified benchmark results.",
        "",
        "It does not modify production settings.",
        "",
        "| Field | Suggested threshold | Support | F1 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for field, item in summary["fields"].items():
        lines.append(f"| {field} | {item.get('suggested_threshold')} | {item.get('support')} | {item.get('f1')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
