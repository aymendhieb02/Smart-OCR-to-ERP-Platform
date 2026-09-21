"""Compare deterministic, whole-page SLANet_plus, and generic cropped SLANet_plus."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil

from app.core.config import settings
from app.services.dossier_segmentation import classify_page
from app.services.file_loader import load_document
from app.services.ocr_engine import OCREngine
from app.services.table_reconstruction_engine import reconstruct_line_items
from app.services.table_regions import build_ocr_regions
from scripts.benchmark_local_table_structure import ResourceSampler, constrain_cpu, directory_size, package_versions
from scripts.local_table_structure_adapter import canonicalize_slanet_result, map_ocr_lines_to_cells
from scripts.table_crop_benchmark_adapter import (
    generate_crop_candidates,
    make_crop,
    ocr_lines_in_crop,
    reconstruct_semantic_candidates,
    remap_table_to_page,
)


MODEL_NAME = "SLANet_plus"
PADDINGS = (0.0, 0.02, 0.05)
CANONICAL_FILES = ("INV 01.pdf", "Inv 02.pdf", "INV 03.pdf", "Inv 04.pdf", "INV 05.pdf", "Inv 06.pdf")


def main() -> None:
    args = parse_args()
    cpu_constraint = constrain_cpu(4)
    settings.ocr_profile = "optimized_mobile_v4"
    root = Path(args.samples_root).resolve()
    sources = [root / name for name in (CANONICAL_FILES[:1] if args.scope == "three" else CANONICAL_FILES)]
    missing = [str(source) for source in sources if not source.is_file()]
    if missing:
        raise SystemExit(f"Missing benchmark dossiers: {missing}")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    process = psutil.Process()
    rss_before = process.memory_info().rss
    from paddleocr import TableStructureRecognition
    init_started = time.perf_counter()
    model = TableStructureRecognition(model_name=MODEL_NAME, device="cpu")
    init_seconds = time.perf_counter() - init_started
    rss_after = process.memory_info().rss

    engine = OCREngine(mode="fast", use_disk_cache=True, refresh_cache=False)
    pages = []
    total_ocr_seconds = 0.0
    try:
        for source in sources:
            document = load_document(source, source.name)
            ocr_started = time.perf_counter()
            ocr_result = engine.run(document.images, document.embedded_text)
            total_ocr_seconds += time.perf_counter() - ocr_started
            page_numbers = (1, 2, 3) if args.scope == "three" else (1, 2)
            for page_number in page_numbers:
                image = document.images[page_number - 1]
                lines = [line for line in ocr_result.lines if line.page_number == page_number]
                pages.append(benchmark_page(model, image, lines, page_number, source.name))
    finally:
        model.close()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notice": "Structural diagnostics only; draft labels are not verified ground truth and no accuracy metric is reported.",
        "model": MODEL_NAME,
        "packages": package_versions(),
        "cpu_constraint": cpu_constraint,
        "scope": args.scope,
        "sources": [source.name for source in sources],
        "ocr_profile": "optimized_mobile_v4",
        "ocr_seconds": round(total_ocr_seconds, 4),
        "padding_policies": list(PADDINGS),
        "model_load": {
            "initialization_seconds": round(init_seconds, 4),
            "rss_before_bytes": rss_before,
            "rss_after_bytes": rss_after,
            "cache_location": str(Path.home() / ".paddlex" / "official_models" / MODEL_NAME),
            "artifact_bytes": directory_size(Path.home() / ".paddlex" / "official_models" / MODEL_NAME),
        },
        "pages": pages,
        "summary": summarize(pages),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote sanitized crop benchmark: {output}")


def benchmark_page(model, image, lines, page_number: int, filename: str) -> dict[str, Any]:
    classification = classify_page(lines, page_number)
    deterministic = reconstruct_line_items(lines)
    whole = run_slanet(model, image, lines, page_number)
    generation_started = time.perf_counter()
    raw_candidates = generate_crop_candidates(image, lines, deterministic, build_ocr_regions(image))
    generation_seconds = time.perf_counter() - generation_started
    crop_results = []
    for candidate in raw_candidates:
        for padding in PADDINGS:
            crop, contract = make_crop(image, candidate["bbox"], padding_percent=padding, source=candidate["source"], rationale=candidate["rationale"])
            crop_lines = ocr_lines_in_crop(lines, contract)
            result = run_slanet(model, crop, crop_lines, page_number, contract=contract)
            result["candidate_source"] = candidate["source"]
            result["rationale"] = candidate["rationale"]
            result["padding_percent"] = padding
            result["crop_bbox"] = contract.bbox
            result["page_width"] = contract.page_width
            result["page_height"] = contract.page_height
            result["crop_width"] = contract.crop_width
            result["crop_height"] = contract.crop_height
            result["scale_x"] = contract.scale_x
            result["scale_y"] = contract.scale_y
            result["ocr_lines_inside_crop"] = len(crop_lines)
            result["audit"] = audit_crop(crop_lines, contract, len(lines))
            result["failure_taxonomy"] = crop_failures(result)
            result["selection_score"] = selection_score(result)
            crop_results.append(result)
    selected = max(crop_results, key=lambda item: item["selection_score"]) if crop_results else None
    return {
        "filename": filename,
        "page_number": page_number,
        "document_type": classification.document_type,
        "document_family": classification.document_family,
        "ocr_line_count": len(lines),
        "crop_generation_seconds": round(generation_seconds, 5),
        "deterministic": {
            "strategy": deterministic.selected_strategy,
            "region_count": len(deterministic.regions),
            "regions": [region.bbox for region in deterministic.regions],
            "header_count": len(deterministic.headers),
            "column_count": len(deterministic.columns) if deterministic.columns else None,
            "cell_count": len(deterministic.cells),
            "row_count": len(deterministic.rows),
            "line_item_count": len(deterministic.line_items),
        },
        "whole_page": whole,
        "candidate_count": len(raw_candidates),
        "crop_candidates": crop_results,
        "selected_crop": selected,
    }


def run_slanet(model, image, lines, page_number: int, contract=None) -> dict[str, Any]:
    with ResourceSampler() as resources:
        started = time.perf_counter()
        raw = model.predict(image, batch_size=1)
        inference = time.perf_counter() - started
    if not raw:
        raise RuntimeError(f"No SLANet result on page {page_number}")
    table = canonicalize_slanet_result(raw[0], page_number=page_number)
    if contract is not None:
        remap_table_to_page(table, contract)
    mapping = map_ocr_lines_to_cells(table, lines)
    semantic = reconstruct_semantic_candidates(table)
    assigned = len(mapping.assigned_ocr_line_ids)
    unassigned = len(mapping.unassigned_ocr_line_ids)
    ambiguous = len(mapping.ambiguous_ocr_line_ids)
    total = assigned + unassigned + ambiguous
    field_status = Counter(status["status"] for candidate in semantic for status in candidate.fields.values())
    financial = Counter(candidate.financial_status for candidate in semantic)
    return {
        "row_count": len(table.rows),
        "column_count": table.columns,
        "cell_count": len(table.cells),
        "header_count": len(table.headers),
        "row_span_count": sum((cell.row_span or 1) > 1 for cell in table.cells),
        "column_span_count": sum((cell.column_span or 1) > 1 for cell in table.cells),
        "assigned_ocr_lines": assigned,
        "ambiguous_ocr_lines": ambiguous,
        "unmapped_ocr_lines": unassigned,
        "assignment_rate": round(assigned / total, 4) if total else None,
        "semantic_candidate_rows": len(semantic),
        "coherent_semantic_rows": sum(candidate.coherent for candidate in semantic),
        "complete_semantic_rows": sum(candidate.complete for candidate in semantic),
        "semantic_field_status_counts": dict(field_status),
        "financial_status_counts": dict(financial),
        "structure_score": table.confidence,
        "model_box_structure_mismatch": table.metadata.get("malformed_or_mismatched", False),
        "inference_seconds": round(inference, 4),
        "total_path_seconds": round(inference, 4),
        "peak_rss_bytes": resources.peak_rss,
    }


def audit_crop(lines, contract, page_line_count: int) -> dict[str, Any]:
    text = " ".join(line.text.lower() for line in lines)
    numeric_lines = sum(bool(re.search(r"\d", line.text)) for line in lines)
    header_terms = sum(term in text for term in ("description", "designation", "quantity", "quant", "unit", "price", "prix", "total", "montant"))
    totals = sum(term in text for term in ("total amount", "total ttc", "net a payer", "net à payer"))
    area = (contract.bbox["x2"] - contract.bbox["x1"]) * (contract.bbox["y2"] - contract.bbox["y1"])
    return {
        "area_ratio": round(area / (contract.page_width * contract.page_height), 4),
        "numeric_line_count": numeric_lines,
        "header_term_count": header_terms,
        "totals_term_count": totals,
        "page_ocr_coverage": round(len(lines) / page_line_count, 4) if page_line_count else None,
    }


def crop_failures(result: dict[str, Any]) -> list[str]:
    errors = []
    audit = result["audit"]
    if not result["cell_count"]:
        errors.append("TABLE_NOT_DETECTED")
    if audit["area_ratio"] > 0.5:
        errors.append("CROP_TOO_LARGE")
    if result["ocr_lines_inside_crop"] < 2 or audit["area_ratio"] < 0.005:
        errors.append("CROP_TOO_SMALL")
    if audit["header_term_count"] == 0 and result["coherent_semantic_rows"] == 0:
        errors.append("CROP_MISALIGNED")
    if result["ambiguous_ocr_lines"]:
        errors.append("OCR_TO_CELL_MAPPING_ERROR")
    if not result["coherent_semantic_rows"]:
        errors.append("SEMANTIC_MAPPING_ERROR")
    if result["model_box_structure_mismatch"]:
        errors.append("CELL_ASSIGNMENT_ERROR")
    if result["financial_status_counts"].get("inconsistent"):
        errors.append("FINANCIAL_INCONSISTENCY")
    return sorted(set(errors))


def selection_score(result: dict[str, Any]) -> float:
    total = result["assigned_ocr_lines"] + result["ambiguous_ocr_lines"] + result["unmapped_ocr_lines"]
    ambiguity = result["ambiguous_ocr_lines"] / total if total else 1.0
    unmapped = result["unmapped_ocr_lines"] / total if total else 1.0
    return (
        result["complete_semantic_rows"] * 2000
        + result["coherent_semantic_rows"] * 1000
        + (result["assignment_rate"] or 0) * 100
        - ambiguity * 100
        - unmapped * 50
        - (200 if "CROP_TOO_LARGE" in result["failure_taxonomy"] else 0)
    )


def summarize(pages: list[dict[str, Any]]) -> dict[str, Any]:
    whole_assigned = sum(page["whole_page"]["assigned_ocr_lines"] for page in pages)
    whole_ambiguous = sum(page["whole_page"]["ambiguous_ocr_lines"] for page in pages)
    whole_unmapped = sum(page["whole_page"]["unmapped_ocr_lines"] for page in pages)
    selected = [page["selected_crop"] for page in pages if page["selected_crop"]]
    crop_assigned = sum(item["assigned_ocr_lines"] for item in selected)
    crop_ambiguous = sum(item["ambiguous_ocr_lines"] for item in selected)
    crop_unmapped = sum(item["unmapped_ocr_lines"] for item in selected)
    return {
        "whole_page": aggregate(whole_assigned, whole_ambiguous, whole_unmapped, pages, "whole_page"),
        "selected_crops": aggregate(crop_assigned, crop_ambiguous, crop_unmapped, [{"selected_crop": item} for item in selected], "selected_crop"),
        "invoice_examples_have_coherent_crop_row": any((page["selected_crop"] or {}).get("coherent_semantic_rows", 0) for page in pages if page["page_number"] in (1, 2)),
        "invoice_examples_have_complete_crop_row": any((page["selected_crop"] or {}).get("complete_semantic_rows", 0) for page in pages if page["page_number"] in (1, 2)),
        "failure_taxonomy_counts": dict(Counter(code for item in selected for code in item["failure_taxonomy"])),
    }


def aggregate(assigned: int, ambiguous: int, unmapped: int, pages: list[dict[str, Any]], key: str) -> dict[str, Any]:
    total = assigned + ambiguous + unmapped
    values = [page[key] for page in pages if page.get(key)]
    return {
        "assigned": assigned, "ambiguous": ambiguous, "unmapped": unmapped,
        "assignment_rate": round(assigned / total, 4) if total else None,
        "mean_inference_seconds": round(sum(value["inference_seconds"] for value in values) / len(values), 4) if values else None,
        "peak_rss_bytes": max((value["peak_rss_bytes"] for value in values), default=None),
        "coherent_semantic_rows": sum(value["coherent_semantic_rows"] for value in values),
        "complete_semantic_rows": sum(value["complete_semantic_rows"] for value in values),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-root", default=r"D:\Stage_udgroup\haithem_samples")
    parser.add_argument("--scope", choices=("three", "invoices"), default="three")
    parser.add_argument("--output", default="outputs/table_crop_structure_three_page.json")
    return parser.parse_args()


if __name__ == "__main__":
    main()
