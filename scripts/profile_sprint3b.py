"""Flatten per-document stage timings into the Sprint 3B profile CSV."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = ROOT / "dataset" / "reports" / "multi_dataset_benchmark" / "predictions"
TABLE_PREDICTIONS = ROOT / "dataset" / "reports" / "multi_dataset_benchmark" / "table_heavy" / "predictions"
OUTPUT = ROOT / "analysis" / "sprint3b_performance_profile.csv"


def main() -> None:
    rows = []
    prediction_paths = list(PREDICTIONS.rglob("*.json")) + list(TABLE_PREDICTIONS.rglob("*.json"))
    for path in sorted(prediction_paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        response = payload.get("response", {})
        timings = response.get("extraction_debug", {}).get("stage_timings", {})
        row = {"dataset": payload.get("dataset_name") or "table_heavy", "filename": path.name}
        for key, value in timings.items():
            if isinstance(value, (int, float)):
                row[key] = value
        row["ocr_cache_hits"] = timings.get("ocr_cache_hits", 0)
        row["ocr_cache_misses"] = timings.get("ocr_cache_misses", 0)
        row["duplicate_ocr_calls"] = timings.get("duplicate_ocr_calls", 0)
        rows.append(row)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {OUTPUT} ({len(rows)} documents)")


if __name__ == "__main__":
    main()
