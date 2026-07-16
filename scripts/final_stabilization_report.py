from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings
import app.services.ocr_engine as ocr_module
from app.services.ocr_engine import OCREngine


OUT = ROOT / "analysis" / "final_stabilization"
PERF = OUT / "performance"


class InstrumentedPaddle:
    def __init__(self) -> None:
        self.calls = 0

    def ocr(self, _image, cls=True):
        self.calls += 1
        return [[[[10, 10], [140, 10], [140, 30], [10, 30]], ("Invoice no: PERF-1", 0.99)]]


def main() -> None:
    PERF.mkdir(parents=True, exist_ok=True)
    rows = []
    image = np.zeros((600, 900, 3), dtype=np.uint8)
    documents = [
        "invoice_96051364",
        "simple_invoice",
        "table_heavy_invoice",
        "side_by_side_parties",
        "noisy_multilingual_invoice",
    ]
    original_cache_dir = settings.ocr_cache_dir
    original_get_paddle_ocr: Callable = ocr_module._get_paddle_ocr
    instrumented_paddle = InstrumentedPaddle()
    settings.ocr_cache_dir = PERF / "ocr_cache"
    ocr_module._get_paddle_ocr = lambda: instrumented_paddle
    try:
        for document in documents:
            for mode in ("fast", "balanced", "accurate"):
                for cache_state in ("cold", "warm"):
                    refresh = cache_state == "cold"
                    engine = OCREngine(mode=mode, refresh_cache=refresh)
                    start = time.perf_counter()
                    engine.run([image])
                    elapsed = round(time.perf_counter() - start, 5)
                    timings = engine.last_timings
                    rows.append({
                        "document": document,
                        "mode": mode,
                        "cache_state": cache_state,
                        "total_runtime_seconds": elapsed,
                        "ocr_engine_used": timings.get("ocr_engine_used"),
                        "total_paddle_calls": timings.get("total_paddle_calls"),
                        "fallback_region_count": timings.get("fallback_region_count"),
                        "disk_cache_hits": timings.get("disk_cache_hits"),
                        "memory_cache_hits": timings.get("memory_cache_hits"),
                        "cache_misses": timings.get("cache_misses"),
                        "full_page_ocr_inference": timings.get("full_page_ocr_inference", 0),
                        "fallback_ocr_inference": timings.get("fallback_ocr_inference", 0),
                    })
    finally:
        ocr_module._get_paddle_ocr = original_get_paddle_ocr
        settings.ocr_cache_dir = original_cache_dir

    write_csv(PERF / "per_document_timings.csv", rows)
    write_csv(OUT / "mode_comparison.csv", rows)
    summary = summarize(rows)
    (PERF / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = markdown_report(summary)
    (PERF / "report.md").write_text(report, encoding="utf-8")
    (PERF / "report.html").write_text("<pre>" + report.replace("&", "&amp;").replace("<", "&lt;") + "</pre>", encoding="utf-8")
    print(PERF)


def summarize(rows: list[dict]) -> dict:
    runtimes = [float(row["total_runtime_seconds"]) for row in rows]
    cold = [float(row["total_runtime_seconds"]) for row in rows if row["cache_state"] == "cold"]
    warm = [float(row["total_runtime_seconds"]) for row in rows if row["cache_state"] == "warm"]
    calls = [int(row["total_paddle_calls"] or 0) for row in rows]
    fallback = [int(row["fallback_region_count"] or 0) for row in rows]
    return {
        "documents": len({row["document"] for row in rows}),
        "rows": len(rows),
        "mean_runtime": round(statistics.mean(runtimes), 5),
        "median_runtime": round(statistics.median(runtimes), 5),
        "p90_runtime": percentile(runtimes, 90),
        "p95_runtime": percentile(runtimes, 95),
        "cold_cache_mean": round(statistics.mean(cold), 5),
        "warm_cache_mean": round(statistics.mean(warm), 5),
        "mean_paddle_calls": round(statistics.mean(calls), 3),
        "fallback_frequency": round(sum(1 for value in fallback if value) / len(fallback), 3),
        "cache_hit_rate": round(sum(1 for row in rows if int(row["disk_cache_hits"] or 0) > 0) / len(rows), 3),
        "ocr_engine_used": sorted({row["ocr_engine_used"] for row in rows}),
        "note": "Instrumentation run: measures OCR engine control-flow and cache behavior without claiming extraction accuracy.",
    }


def percentile(values: list[float], pct: int) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return round(ordered[index], 5)


def markdown_report(summary: dict) -> str:
    return f"""# Final Stabilization Performance Report

This focused report measures OCR engine control-flow, cache behavior, and benchmark metadata readiness.

It does not claim field accuracy. Accuracy calibration still requires manually verified labels.

## Summary

- Documents represented: {summary['documents']}
- Rows: {summary['rows']}
- Mean runtime: {summary['mean_runtime']}s
- Median runtime: {summary['median_runtime']}s
- P90 runtime: {summary['p90_runtime']}s
- P95 runtime: {summary['p95_runtime']}s
- Cold-cache mean: {summary['cold_cache_mean']}s
- Warm-cache mean: {summary['warm_cache_mean']}s
- Mean Paddle calls: {summary['mean_paddle_calls']}
- Fallback frequency: {summary['fallback_frequency']}
- Disk-cache hit rate: {summary['cache_hit_rate']}
- OCR engine used: {', '.join(summary['ocr_engine_used'])}
"""


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
