"""Benchmark deterministic table reconstruction against SLANet_plus.

This script is intentionally offline/local and does not participate in the API or
production extraction path. Reports contain structural counts, not OCR text.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import threading
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
from app.services.ocr_profiles import effective_ocr_config
from app.services.table_reconstruction_engine import reconstruct_line_items
from scripts.local_table_structure_adapter import (
    arithmetic_diagnostics,
    canonicalize_deterministic_result,
    canonicalize_slanet_result,
    map_ocr_lines_to_cells,
)


CANONICAL_FILES = ("INV 01.pdf", "Inv 02.pdf", "INV 03.pdf", "Inv 04.pdf", "INV 05.pdf", "Inv 06.pdf")
MODEL_NAME = "SLANet_plus"
RSS_STOP_BYTES = 4 * 1024**3


class ResourceSampler:
    def __init__(self) -> None:
        self.process = psutil.Process()
        self.peak_rss = self.process.memory_info().rss
        self.peak_system_percent = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_args):
        self._stop.set()
        self._thread.join(timeout=1)

    def _sample(self) -> None:
        while not self._stop.wait(0.05):
            self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
            self.peak_system_percent = max(self.peak_system_percent, psutil.cpu_percent(interval=None))


def main() -> None:
    args = parse_args()
    cpu_constraint = constrain_cpu(args.cpu_limit)
    settings.ocr_profile = args.ocr_profile
    files = resolve_files(Path(args.samples_root), args.scope)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notice": "Structural diagnostics only. Draft labels were not used as verified ground truth; no accuracy metrics are reported.",
        "scope": args.scope,
        "concurrency": 1,
        "batch_size": 1,
        "cpu_constraint": cpu_constraint,
        "model": MODEL_NAME,
        "packages": package_versions(),
        "ocr_configuration": effective_ocr_config(),
        "system": system_snapshot(),
        "pages": [],
    }

    cache = Path.home() / ".paddlex" / "official_models" / MODEL_NAME
    process = psutil.Process()
    rss_before = process.memory_info().rss
    available_before = psutil.virtual_memory().available
    init_started = time.perf_counter()
    from paddleocr import TableStructureRecognition

    model = TableStructureRecognition(model_name=MODEL_NAME, device="cpu")
    init_seconds = time.perf_counter() - init_started
    rss_after = process.memory_info().rss
    report["model_load"] = {
        "identifier": MODEL_NAME,
        "cache_location": str(cache),
        "artifact_bytes": directory_size(cache),
        "initialization_seconds": round(init_seconds, 4),
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "rss_delta_bytes": rss_after - rss_before,
        "available_ram_before_bytes": available_before,
        "predictor_class": type(model.paddlex_predictor).__name__,
    }
    if rss_after > RSS_STOP_BYTES:
        model.close()
        raise SystemExit("Safety gate failed: process RSS exceeded 4 GB after model initialization")

    engine = OCREngine(mode=args.ocr_mode, use_disk_cache=True, refresh_cache=False)
    benchmark_peak = rss_after
    try:
        for path in files:
            loaded_started = time.perf_counter()
            document = load_document(path, path.name)
            load_seconds = time.perf_counter() - loaded_started
            ocr_started = time.perf_counter()
            ocr_result = engine.run(document.images, document.embedded_text)
            ocr_seconds = time.perf_counter() - ocr_started
            for page_index, image in enumerate(document.images, start=1):
                page_lines = [line for line in ocr_result.lines if line.page_number == page_index]
                page_result, peak = benchmark_page(
                    model, image, page_lines, filename=path.name, page_number=page_index,
                    file_load_seconds=load_seconds, file_ocr_seconds=ocr_seconds,
                )
                benchmark_peak = max(benchmark_peak, peak)
                report["pages"].append(page_result)
                if peak > RSS_STOP_BYTES:
                    raise RuntimeError(f"Safety gate failed after {path.name} page {page_index}: RSS exceeded 4 GB")
    finally:
        model.close()

    report["summary"] = summarize(report["pages"], benchmark_peak)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote sanitized structural benchmark: {output}")


def benchmark_page(model, image, page_lines, *, filename: str, page_number: int, file_load_seconds: float, file_ocr_seconds: float) -> tuple[dict[str, Any], int]:
    started = time.perf_counter()
    deterministic_started = time.perf_counter()
    deterministic_raw = reconstruct_line_items(page_lines)
    deterministic_seconds = time.perf_counter() - deterministic_started
    deterministic = canonicalize_deterministic_result(deterministic_raw, page_number=page_number)

    process = psutil.Process()
    cpu_before = process.cpu_times()
    with ResourceSampler() as sampler:
        inference_started = time.perf_counter()
        model_results = model.predict(image, batch_size=1)
        inference_seconds = time.perf_counter() - inference_started
    cpu_after = process.cpu_times()
    if not model_results:
        raise RuntimeError(f"SLANet_plus returned no result for {filename} page {page_number}")
    slanet = canonicalize_slanet_result(model_results[0], page_number=page_number)
    mapping = map_ocr_lines_to_cells(slanet, page_lines)
    classification = classify_page(page_lines, page_number)
    slanet_usable_rows = sum(_usable_semantic_row(row.semantic_values) for row in slanet.rows)
    deterministic_arithmetic = arithmetic_diagnostics(deterministic.rows)
    slanet_arithmetic = arithmetic_diagnostics(slanet.rows)
    errors = classify_failures(deterministic, slanet, mapping, len(page_lines), slanet_arithmetic)
    cpu_seconds = (cpu_after.user + cpu_after.system) - (cpu_before.user + cpu_before.system)
    total_seconds = time.perf_counter() - started
    assigned = len(mapping.assigned_ocr_line_ids)
    considered = assigned + len(mapping.unassigned_ocr_line_ids) + len(mapping.ambiguous_ocr_line_ids)
    result = {
        "filename": filename,
        "page_number": page_number,
        "document_type": classification.document_type,
        "document_family": classification.document_family,
        "ocr_line_count": len(page_lines),
        "deterministic": {
            "table_regions": len(getattr(deterministic_raw, "regions", []) or []),
            "strategy": deterministic.metadata["selected_strategy"],
            "header_count": len(deterministic.headers),
            "column_count": deterministic.columns,
            "cell_count": len(deterministic.cells),
            "row_count": len(deterministic.rows),
            "line_item_count": deterministic.metadata["line_item_count"],
            "unresolved_fragment_count": deterministic.metadata["unresolved_fragment_count"],
            "arithmetic": deterministic_arithmetic,
            "seconds": round(deterministic_seconds, 4),
        },
        "slanet_plus": {
            "structure_count": 1 if slanet.cells else 0,
            "row_count": len(slanet.rows),
            "column_count": slanet.columns,
            "cell_count": len(slanet.cells),
            "spanning_cell_count": sum((cell.row_span or 1) > 1 or (cell.column_span or 1) > 1 for cell in slanet.cells),
            "header_count": len(slanet.headers),
            "usable_semantic_row_count": slanet_usable_rows,
            "structure_score": slanet.confidence,
            "empty_cell_count": len(mapping.empty_cell_indexes),
            "assigned_ocr_line_count": assigned,
            "unassigned_ocr_line_count": len(mapping.unassigned_ocr_line_ids),
            "ambiguous_ocr_line_count": len(mapping.ambiguous_ocr_line_ids),
            "assignment_rate": round(assigned / considered, 4) if considered else None,
            "arithmetic": slanet_arithmetic,
            "inference_seconds": round(inference_seconds, 4),
        },
        "differences": {
            "explicit_cells_delta": len(slanet.cells) - len(deterministic.cells),
            "rows_delta": len(slanet.rows) - len(deterministic.rows),
            "deterministic_has_semantic_rows": bool(deterministic.rows),
            "slanet_has_semantic_rows": bool(slanet_usable_rows),
            "model_box_structure_mismatch": bool(slanet.metadata.get("malformed_or_mismatched")),
        },
        "resources": {
            "file_load_seconds": round(file_load_seconds, 4),
            "file_ocr_seconds": round(file_ocr_seconds, 4),
            "total_benchmark_seconds": round(total_seconds, 4),
            "peak_rss_bytes": sampler.peak_rss,
            "available_ram_bytes": psutil.virtual_memory().available,
            "process_cpu_seconds": round(cpu_seconds, 4),
            "process_cpu_percent_of_one_core": round(cpu_seconds / inference_seconds * 100, 2) if inference_seconds else None,
            "peak_system_cpu_percent": sampler.peak_system_percent,
        },
        "failure_taxonomy": errors,
    }
    return result, sampler.peak_rss


def classify_failures(deterministic, slanet, mapping, ocr_count: int, arithmetic: dict[str, int]) -> list[str]:
    errors: list[str] = []
    if not slanet.cells:
        errors.append("TABLE_NOT_DETECTED")
    if slanet.cells and not slanet.headers:
        errors.append("HEADER_ERROR")
    if slanet.metadata.get("malformed_or_mismatched"):
        errors.append("CELL_ASSIGNMENT_ERROR")
    if mapping.ambiguous_ocr_line_ids:
        errors.append("OCR_TO_CELL_MAPPING_ERROR")
    if ocr_count and len(mapping.unassigned_ocr_line_ids) / ocr_count > 0.5:
        errors.append("OCR_TO_CELL_MAPPING_ERROR")
    if slanet.rows and not any(row.semantic_values for row in slanet.rows):
        errors.append("SEMANTIC_MAPPING_ERROR")
    if arithmetic["inconsistent"]:
        errors.append("FINANCIAL_INCONSISTENCY")
    if deterministic.rows and len(slanet.rows) > max(3, len(deterministic.rows) * 3):
        errors.append("ROW_SPLIT_ERROR")
    return sorted(set(errors))


def summarize(pages: list[dict[str, Any]], peak_rss: int) -> dict[str, Any]:
    taxonomy = Counter(code for page in pages for code in page["failure_taxonomy"])
    assigned = sum(page["slanet_plus"]["assigned_ocr_line_count"] for page in pages)
    unassigned = sum(page["slanet_plus"]["unassigned_ocr_line_count"] for page in pages)
    ambiguous = sum(page["slanet_plus"]["ambiguous_ocr_line_count"] for page in pages)
    denominator = assigned + unassigned + ambiguous
    return {
        "page_count": len(pages),
        "peak_rss_bytes": peak_rss,
        "mean_slanet_inference_seconds": round(sum(page["slanet_plus"]["inference_seconds"] for page in pages) / len(pages), 4) if pages else None,
        "total_explicit_slanet_cells": sum(page["slanet_plus"]["cell_count"] for page in pages),
        "total_deterministic_cells": sum(page["deterministic"]["cell_count"] for page in pages),
        "total_deterministic_line_items": sum(page["deterministic"]["line_item_count"] for page in pages),
        "total_slanet_usable_semantic_rows": sum(page["slanet_plus"]["usable_semantic_row_count"] for page in pages),
        "ocr_to_cell_assignment_rate": round(assigned / denominator, 4) if denominator else None,
        "assigned_ocr_lines": assigned,
        "unassigned_ocr_lines": unassigned,
        "ambiguous_ocr_lines": ambiguous,
        "failure_taxonomy_counts": dict(sorted(taxonomy.items())),
    }


def resolve_files(root: Path, scope: str) -> list[Path]:
    names = CANONICAL_FILES[:1] if scope == "three" else CANONICAL_FILES
    files = [root.resolve() / name for name in names]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing canonical dossier PDFs: {missing}")
    return files


def package_versions() -> dict[str, str]:
    from importlib.metadata import version

    return {name: version(name) for name in ("paddleocr", "paddlepaddle", "paddlex")}


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def system_snapshot() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    return {
        "platform": platform.platform(),
        "logical_cpu_count": os.cpu_count(),
        "available_ram_bytes": memory.available,
        "total_ram_bytes": memory.total,
    }


def constrain_cpu(limit: int) -> dict[str, Any]:
    """Constrain this benchmark process to the client target CPU count."""
    os.environ["CPU_NUM"] = str(limit)
    os.environ["OMP_NUM_THREADS"] = str(limit)
    process = psutil.Process()
    available = process.cpu_affinity() if hasattr(process, "cpu_affinity") else []
    selected = available[:limit]
    applied = False
    if selected:
        process.cpu_affinity(selected)
        applied = process.cpu_affinity() == selected
    return {
        "requested_logical_cpus": limit,
        "affinity_supported": bool(available),
        "affinity_applied": applied,
        "selected_logical_cpus": selected,
        "CPU_NUM": os.environ["CPU_NUM"],
        "OMP_NUM_THREADS": os.environ["OMP_NUM_THREADS"],
    }


def _usable_semantic_row(values: dict[str, Any]) -> bool:
    useful = sum(bool(values.get(key)) for key in ("description", "quantity", "unit", "unit_price", "line_total"))
    return useful >= 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-root", default=r"D:\Stage_udgroup\haithem_samples")
    parser.add_argument("--scope", choices=("three", "all"), default="three")
    parser.add_argument("--ocr-profile", choices=("optimized_mobile_v4", "optimized_mobile_v5"), default="optimized_mobile_v4")
    parser.add_argument("--ocr-mode", choices=("fast", "balanced", "accurate"), default="fast")
    parser.add_argument("--cpu-limit", type=int, default=4)
    parser.add_argument("--output", default="outputs/local_table_structure_benchmark.json")
    return parser.parse_args()


if __name__ == "__main__":
    main()
