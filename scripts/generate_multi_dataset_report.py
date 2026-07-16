from __future__ import annotations

import argparse
import csv
import html
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "dataset" / "reports" / "multi_dataset_benchmark"
COMPLETENESS_FIELDS = [
    "has_supplier_pred",
    "has_customer_pred",
    "has_invoice_number_pred",
    "has_invoice_date_pred",
    "has_amount_ttc_pred",
    "any_line_items_pred",
]
ACCURACY_FIELDS = [
    "supplier_name_correct",
    "customer_name_correct",
    "invoice_number_correct",
    "invoice_date_correct",
    "amount_ttc_correct",
    "document_type_correct",
]


def main() -> None:
    args = parse_args()
    generate_reports(Path(args.output).resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate per-dataset and global reports for the multi-dataset benchmark.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Benchmark output directory.")
    return parser.parse_args()


def generate_reports(output_dir: Path) -> None:
    results = read_csv(output_dir / "results.csv")
    if not results:
        raise SystemExit(f"Missing or empty results file: {output_dir / 'results.csv'}")

    datasets_dir = output_dir / "datasets"
    charts_dir = output_dir / "charts"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in results:
        grouped[row.get("dataset_name") or "unknown"].append(row)

    dataset_summaries = {}
    for dataset_name, rows in grouped.items():
        dataset_dir = datasets_dir / safe_name(dataset_name)
        dataset_dir.mkdir(parents=True, exist_ok=True)
        summary = compute_dataset_summary(dataset_name, rows)
        dataset_summaries[dataset_name] = summary
        write_json(dataset_dir / "summary.json", summary)
        dataset_chart_paths = generate_dataset_charts(dataset_dir / "charts", rows)
        markdown = build_dataset_markdown(summary, dataset_chart_paths)
        (dataset_dir / "report.md").write_text(markdown, encoding="utf-8")
        (dataset_dir / "report.html").write_text(build_html_report(markdown), encoding="utf-8")

    global_summary = compute_global_summary(results, dataset_summaries)
    global_chart_paths = generate_global_charts(charts_dir, results, dataset_summaries)
    write_json(output_dir / "global_summary.json", global_summary)
    (output_dir / "global_report.md").write_text(build_global_markdown(global_summary, global_chart_paths), encoding="utf-8")
    (output_dir / "global_report.html").write_text(build_html_report(build_global_markdown(global_summary, global_chart_paths)), encoding="utf-8")


def compute_dataset_summary(dataset_name: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    successes = [row for row in rows if row.get("status") == "success"]
    errors = [row for row in rows if row.get("status") == "error"]
    times = number_list(row.get("processing_time_seconds") for row in successes)
    ocr = number_list(row.get("ocr_confidence") for row in successes)
    validation = Counter(row.get("validation_status") or "unknown" for row in successes)
    document_types = Counter(row.get("document_type_pred") or "unknown" for row in successes)
    error_categories = Counter(row.get("error_category") or "none" for row in rows if row.get("error_category"))
    missing = Counter()
    for row in successes:
        for field in COMPLETENESS_FIELDS:
            if not truthy(row.get(field)):
                missing[field] += 1

    accuracy = {}
    rows_with_labels = [row for row in successes if truthy(row.get("has_ground_truth"))]
    for field in ACCURACY_FIELDS:
        relevant = [row for row in rows_with_labels if row.get(field) not in ("", None)]
        accuracy[field] = percent(sum(1 for row in relevant if truthy(row.get(field))), len(relevant)) if relevant else None

    return {
        "dataset_name": dataset_name,
        "documents_tested": len(rows),
        "success_count": len(successes),
        "error_count": len(errors),
        "average_processing_time": round(statistics.mean(times), 3) if times else None,
        "average_ocr_confidence": round(statistics.mean(ocr), 3) if ocr else None,
        "validation_distribution": dict(validation),
        "document_type_distribution": dict(document_types),
        "completeness": {
            "supplier_found_pct": percent(sum(1 for row in successes if truthy(row.get("has_supplier_pred"))), len(successes)),
            "customer_found_pct": percent(sum(1 for row in successes if truthy(row.get("has_customer_pred"))), len(successes)),
            "invoice_number_found_pct": percent(sum(1 for row in successes if truthy(row.get("has_invoice_number_pred"))), len(successes)),
            "invoice_date_found_pct": percent(sum(1 for row in successes if truthy(row.get("has_invoice_date_pred"))), len(successes)),
            "amount_ttc_found_pct": percent(sum(1 for row in successes if truthy(row.get("has_amount_ttc_pred"))), len(successes)),
            "line_items_found_pct": percent(sum(1 for row in successes if truthy(row.get("any_line_items_pred") or row.get("has_line_items_pred"))), len(successes)),
            "validated_line_items_found_pct": percent(sum(1 for row in successes if truthy(row.get("has_validated_line_items_pred"))), len(successes)),
            "review_line_items_found_pct": percent(sum(1 for row in successes if truthy(row.get("has_review_line_items_pred"))), len(successes)),
            "any_line_items_found_pct": percent(sum(1 for row in successes if truthy(row.get("any_line_items_pred") or row.get("has_line_items_pred"))), len(successes)),
        },
        "accuracy": accuracy,
        "top_errors": error_categories.most_common(10),
        "missing_fields": {field: count for field, count in missing.items()},
        "worst_confidence_docs": top_rows(successes, "overall_confidence", reverse=False, limit=10),
        "best_confidence_docs": top_rows(successes, "overall_confidence", reverse=True, limit=10),
        "slowest_docs": top_rows(successes, "processing_time_seconds", reverse=True, limit=10),
        "has_ground_truth": any(truthy(row.get("has_ground_truth")) for row in rows),
    }


def compute_global_summary(results: list[dict[str, str]], dataset_summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    validation = Counter(row.get("validation_status") or "unknown" for row in results if row.get("status") == "success")
    missing_failures = Counter()
    for row in results:
        for field in COMPLETENESS_FIELDS:
            if row.get("status") == "success" and not truthy(row.get(field)):
                missing_failures[field] += 1

    hardest = None
    all_no_text_failures = bool(results) and all(
        row.get("status") == "error" and row.get("error_category") == "no text extracted"
        for row in results
    )
    benchmark_invalid = sum(1 for row in results if row.get("status") == "success") == 0 and all_no_text_failures
    invalid_reason = "Benchmark invalid: OCR engine unavailable or OCR extraction failed globally." if benchmark_invalid else None

    if dataset_summaries and not benchmark_invalid:
        hardest = min(
            dataset_summaries.values(),
            key=lambda summary: (
                summary["completeness"]["amount_ttc_found_pct"],
                summary["completeness"]["invoice_number_found_pct"],
            ),
        )["dataset_name"]

    return {
        "total_documents_tested": len(results),
        "success_count": sum(1 for row in results if row.get("status") == "success"),
        "failure_count": sum(1 for row in results if row.get("status") == "error"),
        "dataset_comparison": dataset_summaries,
        "validation_distribution": dict(validation),
        "fields_fail_most_often": missing_failures.most_common(),
        "hardest_dataset": hardest,
        "benchmark_invalid": benchmark_invalid,
        "invalid_reason": invalid_reason,
        "recommendations": build_recommendations(dataset_summaries, missing_failures),
    }


def build_recommendations(dataset_summaries: dict[str, dict[str, Any]], missing_failures: Counter[str]) -> list[str]:
    recommendations = []
    if missing_failures:
        worst_field, _count = missing_failures.most_common(1)[0]
        recommendations.append(f"Prioritize extraction improvements for `{worst_field}` across datasets.")
    for dataset_name, summary in dataset_summaries.items():
        if summary["error_count"]:
            recommendations.append(f"Review file handling and OCR stability for dataset `{dataset_name}`.")
        if summary["has_ground_truth"] is False:
            recommendations.append(f"Dataset `{dataset_name}` has no usable labels; treat it as completeness/performance only.")
    return recommendations


def build_dataset_markdown(summary: dict[str, Any], charts: dict[str, str]) -> str:
    note = "If a dataset does not provide ground-truth labels, the report measures completeness, confidence, validation status, and processing performance only."
    return "\n".join([
        f"# Dataset Report: {summary['dataset_name']}",
        "",
        f"> {note}",
        "",
        "## Overview",
        f"- Documents tested: {summary['documents_tested']}",
        f"- Success count: {summary['success_count']}",
        f"- Error count: {summary['error_count']}",
        f"- Average processing time: {summary['average_processing_time']}",
        f"- Average OCR confidence: {summary['average_ocr_confidence']}",
        "",
        "## Completeness",
        format_dict_list(summary["completeness"]),
        "",
        "## Accuracy",
        format_dict_list(summary["accuracy"]),
        "",
        "## Validation distribution",
        format_dict_list(summary["validation_distribution"]),
        "",
        chart_md(charts.get("validation")),
        chart_md(charts.get("missing")),
        chart_md(charts.get("ocr")),
        chart_md(charts.get("time")),
        chart_md(charts.get("accuracy")),
        "",
    ])


def build_global_markdown(summary: dict[str, Any], charts: dict[str, str]) -> str:
    note = "If a dataset does not provide ground-truth labels, the report measures completeness, confidence, validation status, and processing performance only."
    rows = ["| Dataset | Docs | Success | Errors |", "|---|---:|---:|---:|"]
    for dataset_name, dataset_summary in summary["dataset_comparison"].items():
        rows.append(
            f"| {dataset_name} | {dataset_summary['documents_tested']} | {dataset_summary['success_count']} | {dataset_summary['error_count']} |"
        )
    return "\n".join([
        "# Global Multi-Dataset Benchmark Report",
        "",
        f"> {note}",
        *([f"> {summary['invalid_reason']}", ""] if summary.get("invalid_reason") else []),
        "",
        f"- Total documents tested: {summary['total_documents_tested']}",
        f"- Success count: {summary['success_count']}",
        f"- Failure count: {summary['failure_count']}",
        f"- Hardest dataset: {summary['hardest_dataset'] or 'N/A'}",
        "",
        "## Dataset comparison",
        *rows,
        "",
        "## Validation distribution",
        format_dict_list(summary["validation_distribution"]),
        "",
        "## Fields that fail most often",
        format_pairs(summary["fields_fail_most_often"]),
        "",
        "## Recommendations",
        *[f"- {item}" for item in summary["recommendations"]],
        "",
        chart_md(charts.get("docs_by_dataset")),
        chart_md(charts.get("validation_by_dataset")),
        chart_md(charts.get("missing_rates")),
        chart_md(charts.get("accuracy_by_field")),
        chart_md(charts.get("processing_time")),
        chart_md(charts.get("ocr_confidence")),
        chart_md(charts.get("error_categories")),
        "",
    ])


def generate_dataset_charts(charts_dir: Path, rows: list[dict[str, str]]) -> dict[str, str]:
    charts_dir.mkdir(parents=True, exist_ok=True)
    plt = import_matplotlib()
    if plt is None:
        return {}
    paths = {}
    paths["validation"] = bar_chart(plt, Counter(row.get("validation_status") or "unknown" for row in rows if row.get("status") == "success"), charts_dir / "validation_status.png", "Validation status")
    missing_counter = Counter({field.replace("_pred", ""): sum(1 for row in rows if row.get("status") == "success" and not truthy(row.get(field))) for field in COMPLETENESS_FIELDS})
    paths["missing"] = bar_chart(plt, missing_counter, charts_dir / "missing_fields.png", "Missing fields")
    paths["ocr"] = histogram(plt, number_list(row.get("ocr_confidence") for row in rows), charts_dir / "ocr_confidence_histogram.png", "OCR confidence")
    paths["time"] = histogram(plt, number_list(row.get("processing_time_seconds") for row in rows), charts_dir / "processing_time_histogram.png", "Processing seconds")
    accuracy_counter = Counter()
    for field in ACCURACY_FIELDS:
        relevant = [row for row in rows if truthy(row.get("has_ground_truth")) and row.get(field) not in ("", None)]
        if relevant:
            accuracy_counter[field.replace("_correct", "")] = percent(sum(1 for row in relevant if truthy(row.get(field))), len(relevant))
    if accuracy_counter:
        paths["accuracy"] = bar_chart(plt, accuracy_counter, charts_dir / "field_accuracy.png", "Accuracy (%)", ylabel="Percent")
    return {key: str(path) for key, path in paths.items() if path}


def generate_global_charts(charts_dir: Path, results: list[dict[str, str]], dataset_summaries: dict[str, dict[str, Any]]) -> dict[str, str]:
    charts_dir.mkdir(parents=True, exist_ok=True)
    plt = import_matplotlib()
    if plt is None:
        return {}
    paths = {}
    paths["docs_by_dataset"] = bar_chart(plt, Counter(row.get("dataset_name") or "unknown" for row in results), charts_dir / "documents_by_dataset.png", "Documents by dataset")
    validation_by_dataset = Counter({name: summary["validation_distribution"].get("valid", 0) for name, summary in dataset_summaries.items()})
    paths["validation_by_dataset"] = bar_chart(plt, validation_by_dataset, charts_dir / "validation_by_dataset.png", "Valid docs by dataset")
    missing_rates = Counter({name: 100 - summary["completeness"]["amount_ttc_found_pct"] for name, summary in dataset_summaries.items()})
    paths["missing_rates"] = bar_chart(plt, missing_rates, charts_dir / "missing_rates_by_dataset.png", "Missing TTC by dataset", ylabel="Percent")
    accuracy_rates = Counter()
    for field in ACCURACY_FIELDS:
        values = [summary["accuracy"].get(field) for summary in dataset_summaries.values() if summary["accuracy"].get(field) is not None]
        if values:
            accuracy_rates[field.replace("_correct", "")] = round(statistics.mean(values), 2)
    if accuracy_rates:
        paths["accuracy_by_field"] = bar_chart(plt, accuracy_rates, charts_dir / "accuracy_by_field.png", "Accuracy by field", ylabel="Percent")
    processing = Counter({name: summary["average_processing_time"] or 0 for name, summary in dataset_summaries.items()})
    paths["processing_time"] = bar_chart(plt, processing, charts_dir / "average_processing_time.png", "Average processing time", ylabel="Seconds")
    ocr = Counter({name: summary["average_ocr_confidence"] or 0 for name, summary in dataset_summaries.items()})
    paths["ocr_confidence"] = bar_chart(plt, ocr, charts_dir / "average_ocr_confidence.png", "Average OCR confidence", ylabel="Confidence")
    errors = Counter({name: summary["error_count"] for name, summary in dataset_summaries.items()})
    paths["error_categories"] = bar_chart(plt, errors, charts_dir / "errors_by_dataset.png", "Errors by dataset")
    return {key: str(path) for key, path in paths.items() if path}


def import_matplotlib():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt
    except Exception:
        return None


def bar_chart(plt, counts: Counter, path: Path, title: str, ylabel: str = "Count") -> Path | None:
    filtered = [(str(label), value) for label, value in counts.items() if value is not None]
    if not filtered:
        return None
    labels, values = zip(*filtered)
    plt.figure(figsize=(10, 5))
    plt.bar(labels, values, color="#174ea6")
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=35, ha="right")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def histogram(plt, values: list[float], path: Path, title: str) -> Path | None:
    if not values:
        return None
    plt.figure(figsize=(10, 5))
    plt.hist(values, bins=min(20, max(5, len(set(values)))), color="#147a4b", edgecolor="white")
    plt.title(title)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def build_html_report(markdown: str) -> str:
    body = []
    for line in markdown.splitlines():
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            body.append(f"<li>{html.escape(line[2:])}</li>")
        elif line.startswith("> "):
            body.append(f"<blockquote>{html.escape(line[2:])}</blockquote>")
        elif line.startswith("![]("):
            src = line[4:-1]
            body.append(f'<img src="{html.escape(Path(src).name)}" alt="chart">')
        elif line.strip():
            body.append(f"<p>{html.escape(line)}</p>")
    return "<!doctype html><html><head><meta charset='utf-8'><style>body{font-family:Arial;margin:24px;color:#172033} img{max-width:100%;border:1px solid #d9e0ea} blockquote{background:#fff8eb;padding:12px;border-left:4px solid #a15c00}</style></head><body>" + "\n".join(body) + "</body></html>"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def chart_md(path: str | None) -> str:
    return f"![]({Path(path).name})" if path else ""


def top_rows(rows: list[dict[str, str]], column: str, *, reverse: bool, limit: int) -> list[dict[str, str]]:
    ordered = sorted(rows, key=lambda row: as_float(row.get(column)) if as_float(row.get(column)) is not None else (-1 if reverse else 10**9), reverse=reverse)
    return [
        {
            "filename": row.get("filename"),
            column: row.get(column),
            "validation_status": row.get("validation_status"),
        }
        for row in ordered[:limit]
    ]


def format_dict_list(values: dict[str, Any]) -> str:
    return "\n".join(f"- {key}: {value}" for key, value in values.items()) or "- none"


def format_pairs(values: list[tuple[str, Any]]) -> str:
    return "\n".join(f"- {key}: {value}" for key, value in values) or "- none"


def number_list(values) -> list[float]:
    return [value for raw in values if (value := as_float(raw)) is not None]


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def percent(count: int, total: int) -> float:
    return round((count / total) * 100, 2) if total else 0.0


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value).strip("_") or "dataset"


if __name__ == "__main__":
    main()
