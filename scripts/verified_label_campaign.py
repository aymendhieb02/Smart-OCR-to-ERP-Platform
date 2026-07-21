from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.manual_benchmark_utils import (
    DEFAULT_BENCHMARK_ROOT,
    accuracy_claims_allowed_for_labels,
    load_manifest_documents,
    read_json,
    validate_verified_label_quality,
    write_json,
)


SELECTED_IDS = [
    "01_batch1-0001",
    "02_batch1-0002",
    "03_batch1-0003",
    "04_test-00000-of-00001_000000",
    "05_test-00000-of-00001_000001",
    "06_test-00000-of-00001_000002",
    "07_test-00000-of-00001-af2d92d1cee28514_000000",
    "08_test-00000-of-00001-af2d92d1cee28514_000001",
    "10_test-00000-of-00001_000003",
    "11_test-00000-of-00001_000004",
]

COVERAGE_PLAN = {
    "01_batch1-0001": ["easy_control", "supplier_customer_problem"],
    "02_batch1-0002": ["supplier_customer_problem"],
    "03_batch1-0003": ["supplier_customer_problem"],
    "04_test-00000-of-00001_000000": ["table_heavy", "totals_or_vat_issue", "multilingual"],
    "05_test-00000-of-00001_000001": ["table_heavy", "totals_or_vat_issue", "multilingual"],
    "06_test-00000-of-00001_000002": ["table_heavy"],
    "07_test-00000-of-00001-af2d92d1cee28514_000000": ["noisy_low_quality_scan"],
    "08_test-00000-of-00001-af2d92d1cee28514_000001": ["noisy_low_quality_scan"],
    "10_test-00000-of-00001_000003": ["multilingual"],
    "11_test-00000-of-00001_000004": ["multilingual"],
}


def main() -> None:
    args = parse_args()
    benchmark_root = Path(args.benchmark_root).resolve()
    documents = {Path(doc.filename).stem: doc for doc in load_manifest_documents(benchmark_root)}
    missing = [doc_id for doc_id in SELECTED_IDS if doc_id not in documents]
    if missing:
        raise SystemExit(f"Selected document(s) missing from manifest: {', '.join(missing)}")
    selected = [documents[doc_id] for doc_id in SELECTED_IDS]
    if args.update_labels:
        for document in selected:
            upgrade_label_metadata(document)
    labels = [read_json(document.label_path) for document in selected]
    qualities = [
        validate_verified_label_quality(label, label_path=Path(selected[index].label_path.parent.name) / selected[index].label_path.name)
        for index, label in enumerate(labels)
    ]
    selection_payload = build_selection_payload(benchmark_root, selected, qualities)
    write_json(benchmark_root / "selected_verified_10_documents.json", selection_payload)
    validation = build_validation_payload(selection_payload, qualities, labels)
    write_json(benchmark_root / "verified_label_validation.json", validation)
    write_quality_report(benchmark_root / "verified_label_quality_report.md", validation)
    write_review_queue(benchmark_root / "verified_label_review_queue.html", selected, labels, qualities)
    print(f"Selected documents: {benchmark_root / 'selected_verified_10_documents.json'}")
    print(f"Validation: {benchmark_root / 'verified_label_validation.json'}")
    print(f"Quality report: {benchmark_root / 'verified_label_quality_report.md'}")
    print(f"Review helper: {benchmark_root / 'verified_label_review_queue.html'}")
    print(f"Accuracy claims allowed: {validation['accuracy_claims_allowed']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and validate the fixed ten-document verified-label campaign.")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT))
    parser.add_argument("--update-labels", action="store_true", help="Add Phase 2.7 verification metadata to selected label files.")
    return parser.parse_args()


def upgrade_label_metadata(document: Any) -> None:
    label = read_json(document.label_path)
    label.setdefault("verification_status", "draft")
    label.setdefault("verified_by", None)
    label.setdefault("verified_at", None)
    label.setdefault("source_document", document.filename)
    label.setdefault("uncertain_fields", [
        "supplier_name",
        "customer_name",
        "invoice_number",
        "invoice_date",
        "currency",
        "amount_ht",
        "tax_amount",
        "amount_ttc",
        "line_items",
    ])
    if label.get("notes") == "":
        label["notes"] = None
    else:
        label.setdefault("notes", None)
    if label.get("verified_by_human") is True and label.get("verification_status") == "draft":
        label["verification_status"] = "verified"
    write_json(document.label_path, label)


def build_selection_payload(benchmark_root: Path, selected: list[Any], qualities: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = read_json(benchmark_root / "manifest.json")
    by_filename = {item["filename"]: item for item in manifest.get("documents", [])}
    documents = []
    for document, quality in zip(selected, qualities):
        meta = by_filename.get(document.filename, {})
        doc_id = Path(document.filename).stem
        documents.append({
            "document_id": doc_id,
            "filename": document.filename,
            "dataset": document.dataset,
            "document_type_hint": document.document_type_hint,
            "image_path": str(document.image_path.relative_to(benchmark_root)),
            "label_path": str(document.label_path.relative_to(benchmark_root)),
            "source_document": document.filename,
            "file_hash": meta.get("file_hash"),
            "coverage_tags": COVERAGE_PLAN.get(doc_id, []),
            "image_quality": meta.get("image_quality"),
            "table_heavy": meta.get("table_heavy"),
            "multilingual": meta.get("multilingual"),
            "verification_status": quality["verification_status"],
            "eligible_for_accuracy": quality["eligible_for_accuracy"],
        })
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_root": "dataset/manual_ground_truth_benchmark",
        "selection_locked": True,
        "document_count": len(documents),
        "selection_policy": "Exactly 10 fixed commercial documents selected from the existing manual benchmark; labels must be manually verified before accuracy claims.",
        "confidentiality_note": "No new raw private invoices are introduced by this campaign file; it references documents already present in the repository benchmark folder.",
        "documents": documents,
    }


def build_validation_payload(selection: dict[str, Any], qualities: list[dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [quality for quality in qualities if quality["eligible_for_accuracy"]]
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selected_document_count": selection["document_count"],
        "expected_document_count": 10,
        "exactly_10_selected": selection["document_count"] == 10,
        "complete_verified_documents": len(complete),
        "accuracy_claims_allowed": selection["document_count"] == 10 and accuracy_claims_allowed_for_labels(labels),
        "documents": qualities,
    }


def write_quality_report(path: Path, validation: dict[str, Any]) -> None:
    lines = [
        "# Verified Label Quality Report",
        "",
        f"- Selected documents: {validation['selected_document_count']}",
        f"- Exactly 10 selected: {validation['exactly_10_selected']}",
        f"- Complete verified documents: {validation['complete_verified_documents']}",
        f"- Accuracy claims allowed: {validation['accuracy_claims_allowed']}",
        "",
        "Accuracy claims remain blocked until all 10 labels have `verification_status=verified`, required metadata, complete required fields, consistent totals, and meaningful line items.",
        "",
        "| Document | Status | Eligible | Missing Fields | Errors | Warnings | Meaningful Lines | Blank Lines |",
        "|---|---|---:|---|---|---|---:|---:|",
    ]
    for doc in validation["documents"]:
        lines.append(
            "| {filename} | {status} | {eligible} | {missing} | {errors} | {warnings} | {lines} | {blank} |".format(
                filename=doc.get("filename"),
                status=doc.get("verification_status"),
                eligible=doc.get("eligible_for_accuracy"),
                missing=", ".join(doc.get("missing_fields") or []),
                errors="<br>".join(doc.get("errors") or []),
                warnings="<br>".join(doc.get("warnings") or []),
                lines=doc.get("line_items_meaningful_rows"),
                blank=doc.get("line_items_blank_template_rows"),
            )
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_review_queue(path: Path, selected: list[Any], labels: list[dict[str, Any]], qualities: list[dict[str, Any]]) -> None:
    cards = []
    for document, label, quality in zip(selected, labels, qualities):
        display_label = dict(label)
        display_label["source_path"] = display_label.get("source_document") or document.filename
        cards.append(f"""
        <section class="card">
          <h2>{html.escape(document.filename)}</h2>
          <p><strong>Dataset:</strong> {html.escape(document.dataset)} | <strong>Status:</strong> {html.escape(str(quality['verification_status']))}</p>
          <p><strong>Image/PDF:</strong> <a href="{html.escape(str(Path('images') / document.filename))}">{html.escape(str(Path('images') / document.filename))}</a></p>
          <img src="{html.escape(str(Path('images') / document.filename))}" alt="{html.escape(document.filename)}">
          <h3>Validation Errors</h3>
          <pre>{html.escape(json.dumps({"errors": quality["errors"], "warnings": quality["warnings"], "missing_fields": quality["missing_fields"]}, indent=2, ensure_ascii=False))}</pre>
          <h3>Existing Label</h3>
          <textarea>{html.escape(json.dumps(display_label, indent=2, ensure_ascii=False))}</textarea>
          <button onclick="downloadLabel(this, '{html.escape(Path(document.label_path).name)}')">Download edited label JSON</button>
        </section>
        """)
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Verified Ten-Document Label Review Queue</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #f6f8fb; color: #172033; }}
    .card {{ background: white; border: 1px solid #d9e1ee; border-radius: 8px; padding: 16px; margin-bottom: 18px; }}
    img {{ max-width: 780px; width: 100%; display: block; border: 1px solid #d9e1ee; margin: 12px 0; }}
    textarea {{ width: 100%; min-height: 320px; font-family: Consolas, monospace; }}
    pre {{ background: #f0f3f8; padding: 10px; overflow: auto; }}
    button {{ padding: 8px 12px; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>Verified Ten-Document Label Review Queue</h1>
  <p>Review the document image, compare it with deterministic predictions from your benchmark run, edit the JSON, and save it back to the matching label file only after human verification. Do not copy deterministic values without checking the source document.</p>
  {''.join(cards)}
  <script>
    function downloadLabel(button, filename) {{
      const text = button.parentElement.querySelector('textarea').value;
      const blob = new Blob([text], {{type: 'application/json'}});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    }}
  </script>
</body>
</html>
"""
    path.write_text("\n".join(line.rstrip() for line in html_doc.splitlines()) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
