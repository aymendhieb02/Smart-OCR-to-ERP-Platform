from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_PERFORMANCE_ROOT = Path("dataset") / "reports" / "performance" / "latest"


def write_performance_reports(results: list[dict[str, Any]], output_dir: Path | None = None) -> dict[str, Path]:
    output = output_dir or DEFAULT_PERFORMANCE_ROOT
    output.mkdir(parents=True, exist_ok=True)
    jsonl_path = output / "per_document_timings.jsonl"
    csv_path = output / "per_document_timings.csv"
    summary_path = output / "timing_summary.json"
    report_path = output / "timing_report.md"

    jsonl_path.write_text(
        "\n".join(json.dumps(result, ensure_ascii=False, default=str) for result in results) + ("\n" if results else ""),
        encoding="utf-8",
    )
    _write_csv(csv_path, results)
    summary = build_timing_summary(results)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report_path.write_text(_render_markdown(summary, results), encoding="utf-8")
    return {
        "jsonl": jsonl_path,
        "csv": csv_path,
        "summary": summary_path,
        "report": report_path,
    }


def build_timing_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    stage_values: dict[str, list[float]] = {}
    percent_values: dict[str, list[float]] = {}
    statuses = Counter(str(result.get("validation_status") or "unknown") for result in results)
    success_count = sum(1 for result in results if result.get("success"))
    for result in results:
        for stage, seconds in (result.get("stages") or {}).items():
            stage_values.setdefault(stage, []).append(float(seconds or 0))
        for stage, percentage in (result.get("stage_percentages") or {}).items():
            percent_values.setdefault(stage, []).append(float(percentage or 0))
    totals = [float(result.get("total_seconds") or 0) for result in results]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents": len(results),
        "success_count": success_count,
        "failure_count": len(results) - success_count,
        "average_total_seconds": round(mean(totals), 6) if totals else 0.0,
        "max_total_seconds": round(max(totals), 6) if totals else 0.0,
        "validation_distribution": dict(statuses),
        "stages": {
            stage: {
                "count": len(values),
                "total_seconds": round(sum(values), 6),
                "average_seconds": round(mean(values), 6),
                "max_seconds": round(max(values), 6),
                "average_percent": round(mean(percent_values.get(stage, [0.0])), 3),
            }
            for stage, values in sorted(stage_values.items())
        },
        "slowest_documents": sorted(
            [
                {
                    "document": result.get("document"),
                    "total_seconds": result.get("total_seconds"),
                    "validation_status": result.get("validation_status"),
                    "success": result.get("success"),
                }
                for result in results
            ],
            key=lambda item: float(item.get("total_seconds") or 0),
            reverse=True,
        )[:10],
    }


def _write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    stages = sorted({stage for result in results for stage in (result.get("stages") or {})})
    percent_fields = [f"{stage}_pct" for stage in stages]
    metadata_fields = [
        "document_id", "page_count", "input_type", "image_dimensions", "ocr_engine", "ocr_mode",
        "cache_hit", "ocr_blocks", "layout_blocks", "candidate_count", "extracted_lines",
        "validation_status", "error_type",
    ]
    fieldnames = ["document", "success", "total_seconds", *metadata_fields, *stages, *percent_fields]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            metadata = result.get("metadata") or {}
            row = {
                "document": result.get("document"),
                "success": result.get("success"),
                "total_seconds": result.get("total_seconds"),
                "document_id": result.get("document_id"),
                "page_count": metadata.get("page_count"),
                "input_type": metadata.get("input_type"),
                "image_dimensions": json.dumps(metadata.get("image_dimensions") or [], ensure_ascii=False),
                "ocr_engine": metadata.get("ocr_engine"),
                "ocr_mode": metadata.get("ocr_mode"),
                "cache_hit": metadata.get("cache_hit"),
                "ocr_blocks": metadata.get("ocr_blocks"),
                "layout_blocks": metadata.get("layout_blocks"),
                "candidate_count": metadata.get("candidate_count"),
                "extracted_lines": metadata.get("extracted_lines"),
                "validation_status": result.get("validation_status"),
                "error_type": result.get("error_type"),
            }
            row.update(result.get("stages") or {})
            row.update({f"{stage}_pct": value for stage, value in (result.get("stage_percentages") or {}).items()})
            writer.writerow(row)


def _render_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# OCR-to-ERP Performance Timing Report",
        "",
        f"Generated at: `{summary.get('generated_at')}`",
        "",
        "## Overview",
        "",
        f"- Documents measured: {summary.get('documents', 0)}",
        f"- Success count: {summary.get('success_count', 0)}",
        f"- Failure count: {summary.get('failure_count', 0)}",
        f"- Average total seconds: {summary.get('average_total_seconds', 0)}",
        f"- Max total seconds: {summary.get('max_total_seconds', 0)}",
        "",
        "## Stage Summary",
        "",
        "| Stage | Count | Total Seconds | Average Seconds | Max Seconds | Average % |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for stage, payload in summary.get("stages", {}).items():
        lines.append(
            f"| `{stage}` | {payload.get('count')} | {payload.get('total_seconds')} | "
            f"{payload.get('average_seconds')} | {payload.get('max_seconds')} | {payload.get('average_percent')} |"
        )
    lines.extend([
        "",
        "## Slowest Documents",
        "",
        "| Document | Total Seconds | Status | Success |",
        "|---|---:|---|---|",
    ])
    for item in summary.get("slowest_documents", []):
        lines.append(f"| `{item.get('document')}` | {item.get('total_seconds')} | {item.get('validation_status')} | {item.get('success')} |")
    lines.extend([
        "",
        "## Notes",
        "",
        "- Stage percentages are calculated against `total_pipeline` when present.",
        "- Local full paths are not exported; document names are sanitized to filenames.",
        "- These measurements are instrumentation only. No algorithmic optimization is applied here.",
    ])
    if not results:
        lines.append("- No documents were measured.")
    return "\n".join(lines) + "\n"

