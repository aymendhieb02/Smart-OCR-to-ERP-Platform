"""Run the current extractor on only physical Page 1 using cached OCR evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import cv2
import fitz
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.schemas import OCRLine, OCRResult
from app.services.correction_identity import correction_document_id
from app.services.correction_store import load_review_field_corrections, load_review_line_item_corrections
from app.services.file_loader import LoadedDocument
from app.services.pipeline_runner import _process_ocr_document
from app.services.producer_invoice_review import PRODUCER_REVIEW_FIELDS
from scripts.compare_page1_ground_truth import compare_page1
from scripts.dossier_ground_truth import current_commit


def _first_page_image(pdf_path: Path) -> np.ndarray:
    with fitz.open(pdf_path) as pdf:
        if not pdf:
            raise ValueError("Source PDF has no pages.")
        page = pdf[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return cv2.cvtColor(image, cv2.COLOR_RGB2BGR) if pix.n == 3 else image


def _cached_page1_lines(cache_path: Path, source: Path) -> tuple[list[OCRLine], dict, str]:
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    metadata = cached.get("metadata", {})
    if metadata.get("source_sha256") != _sha256(source):
        raise ValueError("Cached OCR evidence source hash does not match the source PDF.")
    docs = cached.get("dossier", {}).get("logical_documents", [])
    producers = [doc for doc in docs if doc.get("document_index") == 1 and doc.get("physical_page_numbers") == [1]]
    if len(producers) != 1:
        raise ValueError("Expected a cached first logical document covering only physical Page 1.")
    producer = producers[0]
    family = producer.get("document_family")
    if family not in PRODUCER_REVIEW_FIELDS:
        raise ValueError("Cached Page-1 document family is not a supported producer review form.")
    evidence = [item for item in producer.get("evidence", []) if item.get("physical_page") == 1]
    if not evidence:
        raise ValueError("No cached OCR evidence exists for physical Page 1.")
    lines = [OCRLine(
        text=str(item.get("text") or ""), page_number=1,
        line_index=item.get("line_index"), confidence=item.get("confidence"),
        bbox=item.get("bbox"), source=item.get("source"),
        page_width=item.get("page_width"), page_height=item.get("page_height"),
        coordinate_space=item.get("coordinate_space"),
    ) for item in evidence if item.get("text")]
    return lines, metadata, family


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detail_value(detail, name: str) -> dict:
    if detail is None:
        return {"raw_machine_value": None, "canonical_value": None,
                "effective_machine_value": None, "human_correction": None,
                "normalized_value": None, "display_value": None, "source": None, "evidence_text": None}
    source = str(detail.source or "")
    human = detail.value if "human correction" in source.casefold() else None
    return {
        "raw_machine_value": detail.machine_value if detail.machine_value is not None else detail.value,
        "canonical_value": detail.canonical_value,
        "effective_machine_value": detail.value,
        "human_correction": human,
        "normalized_value": detail.normalized_value,
        "display_value": detail.display_value,
        "source": detail.source,
        "evidence_text": detail.evidence_text,
    }


def build_prediction(source: Path, cached_ocr: Path, *, pipeline_commit: str | None = None) -> dict:
    lines, cache_metadata, family = _cached_page1_lines(cached_ocr, source)
    image = _first_page_image(source)
    ocr_result = OCRResult(
        raw_text="\n".join(line.text for line in lines), lines=lines,
        confidence=None, engine="cached Page-1 OCR evidence", page_count=1,
    )
    loaded = LoadedDocument(source_file=source.name, extension=".pdf", embedded_text="", images=[image])
    timings: dict = {"ocr_source": "cached_page1_evidence", "ocr_calls": 0}
    response = _process_ocr_document(
        loaded, ocr_result, timings=timings, include_preview=False,
        persist_erp_json=False, ocr_engine=None, physical_page_numbers=(1,),
        document_family=family, producer_invoice=True,
    )
    fields = {
        name: _detail_value(response.expanded_fields.get(name), name)
        for name in PRODUCER_REVIEW_FIELDS[family]
    }
    corrected_fields = load_review_field_corrections(
        correction_document_id(source, "logical_document_1"), family,
    )
    for name, record in corrected_fields.items():
        if name in fields:
            fields[name]["human_correction"] = record.get("corrected_value")
    line_corrections = load_review_line_item_corrections(
        correction_document_id(source, "logical_document_1"), family,
    )
    rows = []
    for line in response.detected_fields.line_items:
        row = line.model_dump(mode="json")
        row["line_total"] = next((row.get(key) for key in ("line_total", "total", "line_total_ht", "line_total_ttc") if row.get(key) is not None), None)
        rows.append({key: row.get(key) for key in ("description", "quantity", "unit", "unit_price", "line_total")})
    legacy = {}
    for name in ("tax_rate", "tax_amount", "total_ttc", "purchase_order_number", "supplier_tax_id", "supplier_address"):
        source_key = {"tax_amount": "tva_amount", "total_ttc": "amount_ttc"}.get(name, name)
        value = getattr(response.detected_fields, source_key, None)
        detail = response.expanded_fields.get(source_key)
        legacy[name] = {
            "value": value,
            "evidence_text": getattr(detail, "evidence_text", None),
            # The printed "Total including All taxes" amount is the same source
            # fact as canonical total, not an extra independent amount.
            "source_semantically_relevant": name == "total_ttc" and value is not None,
        }
    return {
        "physical_page": 1,
        "document_role": "producer",
        "document_family": family,
        "source_file": source.name,
        "source_sha256": _sha256(source),
        "pipeline_commit": pipeline_commit,
        "cached_ocr": {
            "source_pipeline_commit": cache_metadata.get("pipeline_commit"),
            "ocr_profile": cache_metadata.get("ocr_profile"),
            "recognizer": cache_metadata.get("recognizer"),
            "evidence_lines": len(lines),
            "ocr_calls": 0,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fields": fields,
        "line_items": rows,
        "line_item_human_corrections": line_corrections,
        "legacy_field_audit": legacy,
        "validation": response.validation.model_dump(mode="json"),
        "ui_contract": {
            "field_allowlist": list(PRODUCER_REVIEW_FIELDS[family]),
            "candidate_selection_ui": False,
            "line_item_columns": ["description", "quantity", "unit", "unit_price", "line_total", "actions"],
            "obsolete_fields_visible": [],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--cached-ocr", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--prediction-output", required=True, type=Path)
    parser.add_argument("--report-output", required=True, type=Path)
    args = parser.parse_args()
    private_root = (ROOT / "local_data").resolve()
    for output in (args.prediction_output, args.report_output):
        if not output.resolve().is_relative_to(private_root):
            parser.error("Prediction and comparison outputs must stay under local_data/.")
    prediction = build_prediction(args.source.resolve(), args.cached_ocr.resolve(), pipeline_commit=current_commit(ROOT))
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    report = compare_page1(prediction, truth)
    args.prediction_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.prediction_output.write_text(json.dumps(prediction, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    args.report_output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"prediction": str(args.prediction_output), "report": str(args.report_output),
                      "scope_pages": report["scope_pages"], "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
