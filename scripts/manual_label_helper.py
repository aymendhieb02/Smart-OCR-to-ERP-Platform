from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.manual_benchmark_utils import (
    DEFAULT_BENCHMARK_ROOT,
    DEFAULT_DATASETS_ROOT,
    FIELD_NAMES,
    LABEL_TEMPLATE,
    build_selection_candidates,
    create_manifest_and_blank_labels,
    ensure_benchmark_structure,
    load_manifest_documents,
    read_json,
    select_representative_documents,
    write_csv,
    write_json,
)


SELECTION_COLUMNS = [
    "source_path",
    "dataset",
    "document_type",
    "image_quality",
    "table_heavy",
    "multilingual",
    "supplier_customer_side_by_side",
    "recommended",
    "selection_reason",
]


def main() -> None:
    args = parse_args()
    benchmark_root = Path(args.benchmark_root).resolve()
    if args.prepare:
        prepare_benchmark(benchmark_root, Path(args.datasets_root).resolve(), args.target_count, args.candidate_limit)
        return
    run_labeling_session(benchmark_root, open_files=not args.no_open)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and manually verify labels for the ground-truth benchmark.")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT), help="Manual benchmark root folder.")
    parser.add_argument("--datasets-root", default=str(DEFAULT_DATASETS_ROOT), help="External datasets root used by --prepare.")
    parser.add_argument("--prepare", action="store_true", help="Create selection candidates, copy selected documents, and write blank labels.")
    parser.add_argument("--target-count", type=int, default=12, help="Number of documents copied into the fixed benchmark.")
    parser.add_argument("--candidate-limit", type=int, default=250, help="Maximum candidate rows written to selection_candidates.csv.")
    parser.add_argument("--no-open", action="store_true", help="Do not open documents with the OS default viewer during labeling.")
    return parser.parse_args()


def prepare_benchmark(benchmark_root: Path, datasets_root: Path, target_count: int, candidate_limit: int) -> None:
    ensure_benchmark_structure(benchmark_root)
    candidates = build_selection_candidates(datasets_root, limit=candidate_limit)
    write_csv(benchmark_root / "selection_candidates.csv", candidates, SELECTION_COLUMNS)
    selected = select_representative_documents(candidates, target_count=target_count)
    manifest = create_manifest_and_blank_labels(benchmark_root, selected)
    write_readme(benchmark_root)
    print(f"Selection candidates: {benchmark_root / 'selection_candidates.csv'}")
    print(f"Selected documents: {len(manifest['documents'])}")
    print(f"Manifest: {benchmark_root / 'manifest.json'}")
    print(f"Blank labels: {benchmark_root / 'labels'}")


def run_labeling_session(benchmark_root: Path, *, open_files: bool) -> None:
    documents = load_manifest_documents(benchmark_root)
    if not documents:
        raise SystemExit("No manifest documents found. Run with --prepare first.")
    pending = [doc for doc in documents if not read_json(doc.label_path).get("verified_by_human")]
    if not pending:
        print("All labels are already verified.")
        return
    print(f"{len(pending)} label(s) still require human verification.")
    for document in pending:
        label = read_json(document.label_path)
        print("\n" + "=" * 72)
        print(f"Document: {document.filename}")
        print(f"Image/PDF: {document.image_path}")
        print(f"Source: {document.source_path}")
        if open_files:
            open_document(document.image_path)
        update_label_interactively(label)
        label["filename"] = document.filename
        label["source_path"] = str(document.source_path.resolve())
        label["verified_by_human"] = confirm("Mark this label as verified by human?")
        write_json(document.label_path, label)
        print(f"Saved: {document.label_path}")


def update_label_interactively(label: dict[str, Any]) -> None:
    print("Enter values from the document. Leave blank to keep null/current value.")
    for field in FIELD_NAMES:
        current = label.get(field)
        value = input_value(field, current)
        if value != "":
            label[field] = value
    label["line_items"] = collect_line_items(label.get("line_items") or [])
    notes = input_value("notes", label.get("notes") or "")
    if notes != "":
        label["notes"] = notes


def input_value(field: str, current: Any) -> Any:
    prompt = f"{field}"
    if current not in (None, "", []):
        prompt += f" [{current}]"
    prompt += ": "
    raw = input(prompt).strip()
    if raw == "":
        return ""
    if raw.lower() in {"null", "none", "-"}:
        return None
    if field in {"amount_ht", "tax_amount", "amount_ttc", "tax_rate"}:
        return coerce_float(raw)
    return raw


def collect_line_items(existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    print("Line items: press Enter at description to finish.")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(existing):
        if not any(value not in (None, "", []) for value in row.values()):
            continue
        print(f"Existing line {index + 1}: {row}")
        if confirm("Keep this line item?"):
            rows.append(row)
    while True:
        description = input("line description: ").strip()
        if not description:
            break
        item = dict(LABEL_TEMPLATE["line_items"][0])
        item["description"] = description
        item["reference"] = empty_to_none(input("reference: ").strip())
        item["quantity"] = coerce_float(input("quantity: ").strip())
        item["unit"] = empty_to_none(input("unit: ").strip())
        item["unit_price"] = coerce_float(input("unit price: ").strip())
        item["tax_rate"] = coerce_float(input("tax rate: ").strip())
        item["line_total_ht"] = coerce_float(input("line total HT: ").strip())
        item["line_total_ttc"] = coerce_float(input("line total TTC: ").strip())
        rows.append(item)
    return rows


def confirm(question: str) -> bool:
    return input(f"{question} [y/N]: ").strip().lower() in {"y", "yes"}


def empty_to_none(value: str) -> str | None:
    return value or None


def coerce_float(value: str) -> float | None:
    if value == "":
        return None
    normalized = value.replace(" ", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        print(f"Could not parse number {value!r}; storing null.")
        return None


def open_document(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        print(f"Open manually: {path}")


def write_readme(benchmark_root: Path) -> None:
    readme = """# Manual Ground-Truth Benchmark

This folder contains a small fixed benchmark for true OCR-to-ERP extraction accuracy.

Ground truth is never auto-filled. Each label must be manually checked and must contain:

```json
"verified_by_human": true
```

Unverified labels are refused by `scripts/benchmark_manual_ground_truth.py`.

## Workflow

1. Prepare candidates and blank labels:

```powershell
python scripts/manual_label_helper.py --prepare --benchmark-root dataset/manual_ground_truth_benchmark
```

2. Manually verify labels:

```powershell
python scripts/manual_label_helper.py --benchmark-root dataset/manual_ground_truth_benchmark
```

3. Run the current pipeline:

```powershell
python scripts/benchmark_manual_ground_truth.py --benchmark-root dataset/manual_ground_truth_benchmark --run-name baseline
```

4. Compare two runs:

```powershell
python scripts/compare_manual_benchmark_runs.py --before baseline --after after_fix
```

## Important

- OCR confidence is not accuracy.
- Missing ground-truth fields are excluded from accuracy denominators.
- Labels should be verified from the document image/PDF, not copied blindly from OCR output.
"""
    (benchmark_root / "README.md").write_text(readme, encoding="utf-8")


if __name__ == "__main__":
    main()
