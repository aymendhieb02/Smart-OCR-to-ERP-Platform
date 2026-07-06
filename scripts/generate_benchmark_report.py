from __future__ import annotations

import argparse
import csv
import html
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "dataset" / "reports" / "benchmark_8000"


def main() -> None:
    args = parse_args()
    report_dir = Path(args.output).resolve()
    results_csv = report_dir / "results.csv"
    error_csv = report_dir / "error_analysis.csv"
    if not results_csv.exists():
        raise SystemExit(f"Missing results.csv: {results_csv}")
    rows = read_csv(results_csv)
    error_rows = read_csv(error_csv) if error_csv.exists() else []
    charts_dir = report_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    chart_paths = generate_charts(rows, error_rows, charts_dir)
    metrics = compute_report_metrics(rows, error_rows)
    markdown = build_markdown_report(metrics, rows, error_rows, chart_paths)
    (report_dir / "report.md").write_text(markdown, encoding="utf-8")
    (report_dir / "report.html").write_text(build_html_report(markdown), encoding="utf-8")
    (report_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report written to {report_dir / 'report.md'}")
    print(f"HTML report written to {report_dir / 'report.html'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate benchmark reports and charts.")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_DIR), help="Benchmark report directory.")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def generate_charts(rows: list[dict[str, str]], error_rows: list[dict[str, str]], charts_dir: Path) -> dict[str, str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return {}

    charts = {}
    charts["validation_status"] = bar_chart(
        plt,
        Counter(row.get("validation_status") or "error" for row in rows),
        "Validation Status Distribution",
        charts_dir / "validation_status_distribution.png",
    )
    charts["document_type"] = bar_chart(
        plt,
        Counter(row.get("document_type") or "unknown" for row in rows if row.get("status") == "success"),
        "Document Type Distribution",
        charts_dir / "document_type_distribution.png",
    )
    charts["processing_time"] = histogram(
        plt,
        [as_float(row.get("processing_time_seconds")) for row in rows],
        "Processing Time Histogram",
        "Seconds",
        charts_dir / "processing_time_histogram.png",
    )
    charts["ocr_confidence"] = histogram(
        plt,
        [as_float(row.get("ocr_confidence")) for row in rows],
        "OCR Confidence Distribution",
        "Confidence",
        charts_dir / "ocr_confidence_distribution.png",
    )
    missing = missing_rates(rows)
    charts["missing_fields"] = bar_chart(
        plt,
        Counter({key.replace("_missing_pct", ""): value for key, value in missing.items()}),
        "Missing Fields (%)",
        charts_dir / "missing_fields_bar_chart.png",
        ylabel="Missing %",
    )
    charts["errors"] = bar_chart(
        plt,
        Counter(row["category"] for row in error_rows if row.get("count")),
        "Errors by Category",
        charts_dir / "errors_by_category.png",
    )
    charts["batch"] = bar_chart(
        plt,
        Counter(row.get("batch") or "unknown" for row in rows),
        "Files Processed by Batch",
        charts_dir / "files_processed_by_batch.png",
    )
    charts["line_items"] = histogram(
        plt,
        [as_float(row.get("line_items_count")) for row in rows if row.get("status") == "success"],
        "Line Items Count Distribution",
        "Line items",
        charts_dir / "line_items_count_distribution.png",
    )
    return {key: str(path.relative_to(charts_dir.parent)) for key, path in charts.items() if path}


def bar_chart(plt, counts: Counter, title: str, path: Path, ylabel: str = "Count") -> Path | None:
    if not counts:
        return None
    labels, values = zip(*counts.most_common())
    plt.figure(figsize=(10, 5))
    plt.bar([str(label) for label in labels], values, color="#174ea6")
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=35, ha="right")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def histogram(plt, values: list[float | None], title: str, xlabel: str, path: Path) -> Path | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    plt.figure(figsize=(10, 5))
    plt.hist(clean, bins=min(30, max(5, len(set(clean)))), color="#147a4b", edgecolor="white")
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def compute_report_metrics(rows: list[dict[str, str]], error_rows: list[dict[str, str]]) -> dict[str, Any]:
    total = len(rows)
    success = [row for row in rows if row.get("status") == "success"]
    failed = [row for row in rows if row.get("status") == "error"]
    times = numbers(row.get("processing_time_seconds") for row in rows)
    ocr_conf = numbers(row.get("ocr_confidence") for row in success)
    overall_conf = numbers(row.get("overall_confidence") for row in success)
    validation_counts = Counter(row.get("validation_status") or "error" for row in rows)
    type_counts = Counter(row.get("document_type") or "unknown" for row in success)
    batch_counts = Counter(row.get("batch") or "unknown" for row in rows)
    line_counts = numbers(row.get("line_items_count") for row in success)
    return {
        "total_files": total,
        "successful_files": len(success),
        "failed_files": len(failed),
        "valid_pct": percent(validation_counts.get("valid", 0), total),
        "needs_review_pct": percent(validation_counts.get("needs_review", 0), total),
        "invalid_pct": percent(validation_counts.get("invalid", 0), total),
        "average_processing_time": round(statistics.mean(times), 3) if times else None,
        "median_processing_time": round(statistics.median(times), 3) if times else None,
        "throughput_documents_per_hour": round(3600 / statistics.mean(times), 2) if times and statistics.mean(times) else None,
        "average_ocr_confidence": round(statistics.mean(ocr_conf), 3) if ocr_conf else None,
        "average_overall_confidence": round(statistics.mean(overall_conf), 3) if overall_conf else None,
        "line_items_extracted_pct": percent(sum(1 for row in success if truthy(row.get("has_line_items"))), len(success)),
        "validation_counts": dict(validation_counts),
        "document_type_counts": dict(type_counts),
        "batch_counts": dict(batch_counts),
        "missing_rates": missing_rates(success),
        "line_item_average": round(statistics.mean(line_counts), 2) if line_counts else None,
        "top_error_categories": top_error_categories(error_rows),
        "has_ground_truth_labels": any(Path(row.get("prediction_json", "")).exists() and prediction_has_label(row.get("prediction_json", "")) for row in success),
    }


def build_markdown_report(metrics: dict[str, Any], rows: list[dict[str, str]], error_rows: list[dict[str, str]], charts: dict[str, str]) -> str:
    success = [row for row in rows if row.get("status") == "success"]
    note = (
        "True field accuracy requires manually verified ground-truth labels. Without labels, this report measures "
        "extraction completeness, confidence, validation status, and processing performance."
    )
    parts = [
        "# Large-Scale OCR-to-ERP Benchmark on 8,000 Documents",
        "",
        f"> {note}",
        "",
        "> Chart PNGs are generated when `matplotlib` is installed in the project environment.",
        "",
        "## Dataset overview",
        "",
        f"- Processed documents: {metrics['total_files']}",
        f"- Successful: {metrics['successful_files']}",
        f"- Failed: {metrics['failed_files']}",
        f"- Files by batch: {format_counts(metrics['batch_counts'])}",
        "",
        chart_md(charts.get("batch")),
        "## Processing performance",
        "",
        f"- Average processing time: {fmt(metrics['average_processing_time'])} seconds",
        f"- Median processing time: {fmt(metrics['median_processing_time'])} seconds",
        f"- Throughput: {fmt(metrics['throughput_documents_per_hour'])} documents/hour",
        "",
        chart_md(charts.get("processing_time")),
        "## Document classification distribution",
        "",
        format_counts_table(metrics["document_type_counts"]),
        "",
        chart_md(charts.get("document_type")),
        "## Validation status distribution",
        "",
        f"- Valid: {metrics['valid_pct']}%",
        f"- Needs review: {metrics['needs_review_pct']}%",
        f"- Invalid: {metrics['invalid_pct']}%",
        "",
        chart_md(charts.get("validation_status")),
        "## Extraction completeness",
        "",
        f"- Missing TTC rate: {metrics['missing_rates'].get('amount_ttc_missing_pct', 0)}%",
        f"- Line items extracted in {metrics['line_items_extracted_pct']}% of successful documents",
        "",
        chart_md(charts.get("missing_fields")),
        "## Missing field analysis",
        "",
        format_counts_table(metrics["missing_rates"]),
        "",
        "## Line item extraction analysis",
        "",
        f"- Average line items per successful document: {fmt(metrics['line_item_average'])}",
        "",
        chart_md(charts.get("line_items")),
        "## OCR confidence analysis",
        "",
        f"- Average OCR confidence: {fmt_pct(metrics['average_ocr_confidence'])}",
        f"- Average overall confidence: {fmt_pct(metrics['average_overall_confidence'])}",
        "",
        chart_md(charts.get("ocr_confidence")),
        "## Error analysis",
        "",
        format_counts_table(metrics["top_error_categories"]),
        "",
        chart_md(charts.get("errors")),
        "## ERP safety analysis",
        "",
        "- `valid` = can be exported automatically.",
        "- `needs_review` = requires human verification.",
        "- `invalid` = blocked from ERP export.",
        "- Blocking uncertain data is safer than exporting wrong data.",
        "",
        "## Top examples",
        "",
        "### 10 best documents by confidence",
        rows_table(top_by(success, "overall_confidence", reverse=True, limit=10), ["filename", "overall_confidence", "validation_status", "document_type"]),
        "",
        "### 10 worst documents by confidence",
        rows_table(top_by(success, "overall_confidence", reverse=False, limit=10), ["filename", "overall_confidence", "validation_status", "document_type"]),
        "",
        "### 10 slowest documents",
        rows_table(top_by(rows, "processing_time_seconds", reverse=True, limit=10), ["filename", "processing_time_seconds", "validation_status", "document_type"]),
        "",
        "### 10 documents with most line items",
        rows_table(top_by(success, "line_items_count", reverse=True, limit=10), ["filename", "line_items_count", "validation_status", "document_type"]),
        "",
        "### 10 common validation problems",
        format_counts_table(metrics["top_error_categories"], limit=10),
        "",
        "## Limitations",
        "",
        "- This benchmark does not calculate true field accuracy unless manually verified labels exist.",
        "- OCR quality depends on scan resolution, skew, language, and document layout complexity.",
        "- Completeness metrics indicate whether a field was found, not whether every value is correct.",
        "",
        "## Recommendations before production",
        "",
        "- Add ground-truth labels for a representative manual review sample.",
        "- Review common failure categories and improve extraction rules for the highest-impact errors.",
        "- Keep automatic ERP export limited to `valid` documents with high confidence.",
        "- Use `needs_review` as a human-in-the-loop queue, not as an automatic export state.",
    ]
    return "\n".join(part for part in parts if part is not None)


def build_html_report(markdown: str) -> str:
    body_lines = []
    in_list = False
    for line in markdown.splitlines():
        if line.startswith("# "):
            body_lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body_lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            body_lines.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("![]("):
            src = line[4:-1]
            body_lines.append(f'<img src="{html.escape(src)}" alt="chart">')
        elif line.startswith("|"):
            body_lines.append(markdown_table_to_html(line))
        elif line.startswith("- "):
            if not in_list:
                body_lines.append("<ul>")
                in_list = True
            body_lines.append(f"<li>{html.escape(line[2:])}</li>")
        else:
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            if line.startswith("> "):
                body_lines.append(f"<blockquote>{html.escape(line[2:])}</blockquote>")
            elif line.strip():
                body_lines.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        body_lines.append("</ul>")
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Large-Scale OCR-to-ERP Benchmark</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 32px; color: #172033; line-height: 1.5; }
    h1, h2, h3 { color: #174ea6; }
    img { max-width: 100%; border: 1px solid #d9e0ea; border-radius: 8px; margin: 12px 0 24px; }
    blockquote { background: #fff8eb; border-left: 4px solid #a15c00; padding: 12px 16px; }
    table { border-collapse: collapse; width: 100%; margin: 12px 0 24px; }
    th, td { border: 1px solid #d9e0ea; padding: 8px; text-align: left; }
    th { background: #f8fafc; }
  </style>
</head>
<body>
""" + "\n".join(body_lines) + "\n</body>\n</html>\n"


def markdown_table_to_html(line: str) -> str:
    # Tables are already present in markdown. Keep them readable in preformatted form for simple HTML generation.
    return f"<pre>{html.escape(line)}</pre>"


def missing_rates(rows: list[dict[str, str]]) -> dict[str, float]:
    checks = {
        "invoice_number_missing_pct": "has_invoice_number",
        "invoice_date_missing_pct": "has_invoice_date",
        "amount_ttc_missing_pct": "has_amount_ttc",
        "supplier_missing_pct": "has_supplier",
        "customer_missing_pct": "has_customer",
        "line_items_missing_pct": "has_line_items",
    }
    return {
        output: percent(sum(1 for row in rows if not truthy(row.get(column))), len(rows))
        for output, column in checks.items()
    }


def top_error_categories(error_rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {}
    for row in error_rows:
        if row.get("count"):
            try:
                counts[row["category"]] = int(float(row["count"]))
            except ValueError:
                pass
    return counts


def chart_md(path: str | None) -> str | None:
    return f"![]({path.replace(chr(92), '/')})" if path else None


def format_counts(counts: dict[str, Any]) -> str:
    return ", ".join(f"{key}: {value}" for key, value in counts.items()) or "none"


def format_counts_table(counts: dict[str, Any], limit: int | None = None) -> str:
    items = list(counts.items())
    if limit:
        items = items[:limit]
    lines = ["| Metric | Value |", "|---|---|"]
    lines.extend(f"| {key} | {value} |" for key, value in items)
    return "\n".join(lines)


def rows_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    if not rows:
        return "No rows."
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def top_by(rows: list[dict[str, str]], column: str, *, reverse: bool, limit: int) -> list[dict[str, str]]:
    return sorted(rows, key=lambda row: as_float(row.get(column)) if as_float(row.get(column)) is not None else (-1 if reverse else 10**9), reverse=reverse)[:limit]


def numbers(values) -> list[float]:
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


def fmt(value: Any) -> str:
    return "N/A" if value is None else str(value)


def fmt_pct(value: Any) -> str:
    return "N/A" if value is None else f"{round(float(value) * 100, 2)}%"


def truthy(value: Any) -> bool:
    return str(value).lower() in {"true", "1", "yes"}


def prediction_has_label(path: str) -> bool:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return "label_comparison" in payload
    except Exception:
        return False


if __name__ == "__main__":
    main()
