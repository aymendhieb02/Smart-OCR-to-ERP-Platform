import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.document_classifier import classify_document
from app.services.field_extractor import extract_with_candidates
from app.services.file_loader import load_document
from app.services.ocr_engine import OCREngine
from app.services.validator import validate_invoice
from app.utils.helpers import normalize_text, parse_amount, parse_date


FIELDS = [
    "document_type", "supplier_name", "supplier_tax_id", "invoice_number",
    "invoice_date", "due_date", "amount_ht", "tva_amount", "amount_ttc", "tax_rate",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dataset")
    args = parser.parse_args()
    dataset = ROOT / args.dataset
    images_dir = dataset / "images"
    labels_dir = dataset / "labels"
    predictions_dir = dataset / "predictions"
    reports_dir = dataset / "reports"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    engine = OCREngine()
    totals = {field: {"correct": 0, "total": 0} for field in FIELDS}
    status_counts = {"valid": 0, "invalid": 0, "needs_review": 0}
    confidences = []

    for image_path in sorted(images_dir.glob("*")):
        if image_path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            continue
        label_path = labels_dir / f"{image_path.stem}.json"
        if not label_path.exists():
            continue
        label = json.loads(label_path.read_text(encoding="utf-8"))
        document = load_document(image_path, image_path.name)
        ocr = engine.run(document.images, document.embedded_text)
        classification = classify_document(ocr.raw_text, ocr.lines)
        fields, _candidates, confidences_by_field, _debug = extract_with_candidates(ocr.raw_text, ocr.lines, classification)
        validation = validate_invoice(fields, ocr, classification)
        prediction = {
            "document_type": classification.document_type,
            **fields.model_dump(mode="json"),
            "validation_status": validation.status,
            "field_confidences": confidences_by_field,
        }
        (predictions_dir / f"{image_path.stem}.json").write_text(json.dumps(prediction, indent=2, ensure_ascii=False), encoding="utf-8")
        status_counts[validation.status] += 1
        if ocr.confidence is not None:
            confidences.append(ocr.confidence)
        for field in FIELDS:
            totals[field]["total"] += 1
            if _matches(label.get(field), prediction.get(field), field):
                totals[field]["correct"] += 1

    report = {
        "total_documents": max((next(iter(totals.values()))["total"]), 0),
        **{f"{field}_accuracy": _ratio(value["correct"], value["total"]) for field, value in totals.items()},
        "valid_count": status_counts["valid"],
        "invalid_count": status_counts["invalid"],
        "needs_review_count": status_counts["needs_review"],
        "average_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }
    (reports_dir / "evaluation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


def _matches(expected: Any, actual: Any, field: str) -> bool:
    if expected in (None, "") and actual in (None, ""):
        return True
    if field in {"amount_ht", "tva_amount", "amount_ttc", "tax_rate"}:
        e, a = parse_amount(str(expected)), parse_amount(str(actual))
        return e is not None and a is not None and abs(e - a) <= 0.01
    if field in {"invoice_date", "due_date"}:
        e, a = parse_date(str(expected)), parse_date(str(actual))
        return e is not None and a is not None and e == a
    return normalize_text(str(expected)).lower() == normalize_text(str(actual)).lower()


def _ratio(correct: int, total: int) -> float:
    return round(correct / total, 3) if total else 0.0


if __name__ == "__main__":
    main()
