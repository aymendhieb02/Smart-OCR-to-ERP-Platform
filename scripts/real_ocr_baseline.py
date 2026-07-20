from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.services.ocr_engine import OCREngine
from app.services.performance_timer import PipelineTimer
from app.services.pipeline_runner import process_document_file


RUN_ID = "real_ocr_baseline_01"
OUTPUT_DIR = PROJECT_ROOT / "dataset" / "reports" / "performance" / "runs" / RUN_ID
DATASETS_ROOT = PROJECT_ROOT.parent / "sources" / "datasets"
CONTROL_PDF = PROJECT_ROOT / "dataset" / "reports" / "performance" / "timing_sample_invoice.pdf"


@dataclass(frozen=True)
class SelectedDocument:
    document_id: str
    category: str
    difficulty: str
    language: str
    dataset: str
    relative_path: str
    reason: str

    @property
    def path(self) -> Path:
        return (PROJECT_ROOT / self.relative_path).resolve() if not Path(self.relative_path).is_absolute() else Path(self.relative_path)


SELECTED_DOCUMENTS = [
    SelectedDocument(
        "clean_invoice_image",
        "clean invoice image",
        "clean",
        "en/fr unknown",
        "high-quality-invoice-images-for-ocr",
        str(DATASETS_ROOT / "high-quality-invoice-images-for-ocr" / "batch_1" / "batch_1" / "batch1_1" / "batch1-0001.jpg"),
        "Clean scanned invoice from the 8k benchmark-style image dataset.",
    ),
    SelectedDocument(
        "table_heavy_invoice",
        "table-heavy invoice image",
        "table-heavy",
        "fr",
        "manual_ground_truth_benchmark",
        "dataset/manual_ground_truth_benchmark/images/04_test-00000-of-00001_000000.png",
        "Known project benchmark invoice with product rows and totals.",
    ),
    SelectedDocument(
        "noisy_low_resolution_invoice",
        "noisy or low-resolution invoice image",
        "noisy",
        "unknown",
        "manual_ground_truth_benchmark",
        "dataset/manual_ground_truth_benchmark/images/07_test-00000-of-00001-af2d92d1cee28514_000000.png",
        "Noisier benchmark image used to stress OCR and review logic.",
    ),
    SelectedDocument(
        "french_invoice_image",
        "French invoice image",
        "normal",
        "fr",
        "project_demo",
        "dataset/demo/demo_good_invoice.png",
        "French-language invoice image with VAT/totals and ERP fields.",
    ),
    SelectedDocument(
        "photographed_invoice",
        "photographed invoice image",
        "photo",
        "unknown",
        "md_invoices",
        str(DATASETS_ROOT / "md_invoices" / "md_invoices" / "img" / "test" / "2023-05-05at11.42.10.jpg"),
        "Phone/photo-style invoice image from md_invoices.",
    ),
]

UNAVAILABLE_DOCUMENTS = [
    {
        "category": "scanned invoice PDF without embedded text",
        "status": "unavailable",
        "reason": "No PDF files were found under D:\\Stage_udgroup\\sources\\datasets.",
    },
    {
        "category": "multi-page scanned PDF",
        "status": "unavailable",
        "reason": "No multi-page PDF files were found under D:\\Stage_udgroup\\sources\\datasets.",
    },
]


class FunctionCounter:
    def __init__(self) -> None:
        self.counts: dict[str, dict[str, Any]] = {}
        self._patches: list[tuple[Any, str, Any]] = []

    def wrap(self, module: Any, attr: str, label: str | None = None) -> None:
        if not hasattr(module, attr):
            return
        original = getattr(module, attr)
        name = label or attr

        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            success = True
            try:
                return original(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                elapsed = time.perf_counter() - started
                payload = self.counts.setdefault(name, {"count": 0, "seconds": 0.0, "errors": 0})
                payload["count"] += 1
                payload["seconds"] += elapsed
                if not success:
                    payload["errors"] += 1

        setattr(module, attr, wrapped)
        self._patches.append((module, attr, original))

    def restore(self) -> None:
        for module, attr, original in reversed(self._patches):
            setattr(module, attr, original)
        self._patches.clear()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "count": int(payload["count"]),
                "seconds": round(float(payload["seconds"]), 6),
                "errors": int(payload["errors"]),
            }
            for name, payload in sorted(self.counts.items())
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run controlled real OCR performance baseline.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--warm-repeats", type=int, default=5)
    parser.add_argument("--skip-cold-start", action="store_true")
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--child-cold-run", action="store_true")
    parser.add_argument("--document-id")
    parser.add_argument("--mode", choices=["cold_start"], default="cold_start")
    parser.add_argument("--ocr-mode", default=settings.ocr_mode)
    args = parser.parse_args()

    if args.child_cold_run:
        document = _selected_by_id(args.document_id)
        if document is None:
            print(f"Unknown document id: {args.document_id}", file=sys.stderr)
            return 2
        result = _run_once(document, scenario="cold_start_cold_document_cache", ocr_mode=args.ocr_mode, use_cache=False, reuse_engine=False)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result.get("success") else 1

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.finalize_only:
        results = _read_jsonl(output_dir / "raw_runs.jsonl")
        _finalize_outputs(output_dir, results)
        print(f"Finalized existing real OCR baseline outputs in {output_dir}")
        return 0
    _write_json(output_dir / "selected_documents.json", {
        "selected": [_document_metadata(doc) for doc in SELECTED_DOCUMENTS if doc.path.exists()],
        "unavailable": UNAVAILABLE_DOCUMENTS,
    })
    _write_json(output_dir / "environment.json", _environment_payload(args.ocr_mode))

    all_results: list[dict[str, Any]] = []
    cold_results: list[dict[str, Any]] = []
    warm_cold_results: list[dict[str, Any]] = []
    warm_cache_results: list[dict[str, Any]] = []
    invalid_runs: list[dict[str, Any]] = []

    selected = [doc for doc in SELECTED_DOCUMENTS if doc.path.exists()]
    if not selected:
        raise SystemExit("No selected OCR documents exist.")

    raw_path = output_dir / "raw_runs.jsonl"
    raw_path.write_text("", encoding="utf-8")

    if not args.skip_cold_start:
        for doc in selected:
            result = _run_cold_subprocess(doc, args.ocr_mode)
            cold_results.append(result)
            all_results.append(result)
            _append_jsonl(raw_path, result)
            if not result.get("valid_for_ocr_baseline"):
                invalid_runs.append(result)

    warm_engine = OCREngine(mode=args.ocr_mode, use_disk_cache=False, refresh_cache=True)
    _warm_paddle_in_process(warm_engine)
    for doc in selected:
        for repeat in range(1, max(1, args.warm_repeats) + 1):
            result = _run_once(
                doc,
                scenario="warm_engine_cold_document_cache",
                ocr_mode=args.ocr_mode,
                use_cache=False,
                reuse_engine=True,
                engine=warm_engine,
                repeat=repeat,
            )
            warm_cold_results.append(result)
            all_results.append(result)
            _append_jsonl(raw_path, result)
            if not result.get("valid_for_ocr_baseline"):
                invalid_runs.append(result)

        cache_engine = OCREngine(mode=args.ocr_mode, use_disk_cache=True, refresh_cache=True)
        _run_once(doc, scenario="warm_cache_seed", ocr_mode=args.ocr_mode, use_cache=True, reuse_engine=True, engine=cache_engine)
        cache_engine.refresh_cache = False
        result = _run_once(doc, scenario="warm_engine_warm_document_cache", ocr_mode=args.ocr_mode, use_cache=True, reuse_engine=True, engine=cache_engine)
        warm_cache_results.append(result)
        all_results.append(result)
        _append_jsonl(raw_path, result)
        if not result.get("valid_for_ocr_baseline"):
            invalid_runs.append(result)

    embedded_control = []
    if CONTROL_PDF.exists():
        control_doc = SelectedDocument(
            "embedded_text_control",
            "embedded text PDF control",
            "control",
            "en",
            "project_performance_control",
            str(CONTROL_PDF),
            "Selectable-text PDF fast path; excluded from OCR baseline statistics.",
        )
        control = _run_once(control_doc, scenario="embedded_text_fast_path", ocr_mode="fast", use_cache=False, reuse_engine=False)
        control["valid_for_ocr_baseline"] = False
        control["invalid_reason"] = "embedded_text_fast_path"
        embedded_control.append(control)
        all_results.append(control)
        _append_jsonl(raw_path, control)
        invalid_runs.append(control)

    summary = _finalize_outputs(output_dir, all_results)
    print(f"Real OCR baseline written to {output_dir}")
    print(json.dumps(summary.get("headline", {}), indent=2, ensure_ascii=False))
    return 0


def _run_once(
    document: SelectedDocument,
    *,
    scenario: str,
    ocr_mode: str,
    use_cache: bool,
    reuse_engine: bool,
    engine: OCREngine | None = None,
    repeat: int | None = None,
) -> dict[str, Any]:
    counter = FunctionCounter()
    _install_counters(counter)
    timer = PipelineTimer(enabled=True, metadata={"document": document.path.name, "filename": document.path.name})
    started = time.perf_counter()
    response = None
    run_engine = None
    error_type = None
    error_message = None
    try:
        run_engine = engine if reuse_engine and engine is not None else OCREngine(mode=ocr_mode, use_disk_cache=use_cache, refresh_cache=not use_cache, timing_recorder=timer)
        setattr(run_engine, "timing_recorder", timer)
        if not use_cache:
            run_engine._ocr_cache.clear()
        response = process_document_file(
            document.path,
            original_filename=document.path.name,
            ocr_engine=run_engine,
            include_preview=True,
            persist_erp_json=True,
            ocr_mode=ocr_mode,
            use_ocr_cache=use_cache,
            refresh_ocr_cache=not use_cache,
            timing_recorder=timer,
        )
        if not use_cache:
            run_engine._ocr_cache.clear()
        success = True
    except Exception as exc:
        success = False
        error_type = type(exc).__name__
        error_message = str(exc)
    finally:
        counter.restore()
    elapsed = time.perf_counter() - started
    timing = timer.to_result(
        document=document.path.name,
        success=success,
        error_type=error_type,
        validation_status=response.validation.status if response and response.validation else None,
    )
    stages = timing.get("stages") or {}
    metadata = timing.get("metadata") or {}
    engine_timings = response.extraction_debug.get("stage_timings", {}) if response and response.extraction_debug else getattr(run_engine, "last_timings", {}) or {}
    ocr_executed = stages.get("ocr_execution", 0) > 0 and int(engine_timings.get("total_paddle_calls") or 0) >= 1
    embedded_text_present = _embedded_text_present(document.path)
    valid, invalid_reason = _validate_ocr_run(scenario, success, stages, engine_timings, metadata, embedded_text_present)
    return {
        "run_id": RUN_ID,
        "scenario": scenario,
        "repeat": repeat,
        "document_id": document.document_id,
        "document": document.path.name,
        "dataset": document.dataset,
        "category": document.category,
        "difficulty": document.difficulty,
        "language": document.language,
        "file_type": document.path.suffix.lower(),
        "file_size_bytes": document.path.stat().st_size if document.path.exists() else None,
        "page_count": metadata.get("page_count"),
        "dimensions": metadata.get("image_dimensions"),
        "embedded_text_present": embedded_text_present,
        "ocr_executed": ocr_executed,
        "ocr_engine": metadata.get("ocr_engine"),
        "ocr_mode": ocr_mode,
        "paddle_state": "warm" if scenario.startswith("warm") else "cold",
        "cache_enabled": use_cache,
        "cache_directory": settings.ocr_cache_dir.name,
        "cache_hit": metadata.get("cache_hit"),
        "memory_cache_hits": engine_timings.get("memory_cache_hits"),
        "disk_cache_hits": engine_timings.get("disk_cache_hits"),
        "cache_misses": engine_timings.get("cache_misses"),
        "ocr_cache_source": engine_timings.get("ocr_cache_source"),
        "success": success,
        "error_type": error_type,
        "error_message": error_message,
        "validation_status": timing.get("validation_status"),
        "total_pipeline_seconds": stages.get("total_pipeline") or round(elapsed, 6),
        "stages": stages,
        "stage_percentages": timing.get("stage_percentages") or {},
        "records": timing.get("records") or [],
        "call_counts": counter.snapshot(),
        "ocr_block_count": metadata.get("ocr_blocks"),
        "fallback_region_count": engine_timings.get("fallback_region_count"),
        "total_paddle_calls": engine_timings.get("total_paddle_calls"),
        "valid_for_ocr_baseline": valid,
        "invalid_reason": invalid_reason,
    }


def _validate_ocr_run(
    scenario: str,
    success: bool,
    stages: dict[str, float],
    engine_timings: dict[str, Any],
    metadata: dict[str, Any],
    embedded_text_present: bool,
) -> tuple[bool, str | None]:
    if scenario == "embedded_text_fast_path":
        return False, "embedded_text_fast_path"
    if not success:
        return False, "pipeline_failed"
    if embedded_text_present:
        return False, "embedded_text_present"
    if stages.get("ocr_execution", 0) <= 0:
        return False, "missing_ocr_execution"
    if int(engine_timings.get("total_paddle_calls") or 0) < 1:
        return False, "paddle_not_invoked"
    if int(metadata.get("ocr_blocks") or 0) <= 0:
        return False, "no_ocr_blocks"
    if "warm_document_cache" not in scenario and (engine_timings.get("memory_cache_hits") or engine_timings.get("disk_cache_hits")):
        return False, "unexpected_cache_hit"
    if "warm_document_cache" in scenario and not (engine_timings.get("memory_cache_hits") or engine_timings.get("disk_cache_hits")):
        return False, "expected_cache_hit_missing"
    return True, None


def _run_cold_subprocess(document: SelectedDocument, ocr_mode: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child-cold-run",
        "--document-id",
        document.document_id,
        "--ocr-mode",
        ocr_mode,
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600, check=False)
    json_line = None
    for line in reversed((result.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            json_line = line
            break
    if json_line:
        return json.loads(json_line)
    return {
        "scenario": "cold_start_cold_document_cache",
        "document_id": document.document_id,
        "document": document.path.name,
        "dataset": document.dataset,
        "success": False,
        "valid_for_ocr_baseline": False,
        "invalid_reason": "cold_subprocess_failed",
        "error_type": "SubprocessError",
        "error_message": (result.stderr or result.stdout or "").strip()[-1000:],
        "total_pipeline_seconds": None,
        "stages": {},
        "call_counts": {},
    }


def _warm_paddle_in_process(engine: OCREngine) -> None:
    import numpy as np
    engine.run([np.full((64, 256, 3), 255, dtype=np.uint8)])
    engine._ocr_cache.clear()
    engine.refresh_cache = True


def _install_counters(counter: FunctionCounter) -> None:
    import app.services.ocr_engine as ocr_engine
    import app.services.pipeline_runner as pipeline_runner
    import app.services.field_extractor as field_extractor
    import app.services.line_item_extractor as line_item_extractor
    import app.services.document_layout as document_layout
    import app.services.graph_field_extractor as graph_field_extractor
    import app.services.preview_generator as preview_generator
    import app.services.json_writer as json_writer
    import app.services.correction_store as correction_store
    import app.services.layout_analyzer as layout_analyzer

    counter.wrap(ocr_engine, "preprocess_image", "preprocess_image")
    counter.wrap(ocr_engine, "preprocess_table_region", "preprocess_table_region")
    counter.wrap(ocr_engine, "_run_paddle_prediction", "_run_paddle_prediction")
    counter.wrap(ocr_engine.OCREngine, "run_fallback_regions", "run_fallback_regions")
    counter.wrap(layout_analyzer.LayoutAnalyzer, "detect_layout_blocks", "LayoutAnalyzer.detect_layout_blocks")
    counter.wrap(pipeline_runner, "analyze_document_layout", "analyze_document_layout")
    counter.wrap(document_layout, "group_ocr_lines", "group_ocr_lines")
    counter.wrap(document_layout, "reconstruct_tables", "reconstruct_tables")
    counter.wrap(line_item_extractor, "group_ocr_lines", "group_ocr_lines")
    counter.wrap(line_item_extractor, "reconstruct_tables", "reconstruct_tables")
    counter.wrap(graph_field_extractor, "build_document_graph", "DocumentGraph construction")
    counter.wrap(field_extractor, "add_graph_field_candidates", "DocumentGraph construction")
    counter.wrap(field_extractor, "build_graph_debug", "build_graph_debug")
    counter.wrap(field_extractor, "extract_line_items", "extract_line_items")
    counter.wrap(pipeline_runner, "generate_document_preview", "preview writes")
    counter.wrap(preview_generator, "generate_document_preview", "preview writes")
    counter.wrap(pipeline_runner, "write_erp_json", "ERP JSON writes")
    counter.wrap(json_writer, "write_erp_json", "ERP JSON writes")
    counter.wrap(pipeline_runner, "write_invoice_validation_report", "validation report writes")
    counter.wrap(json_writer, "write_invoice_validation_report", "validation report writes")
    counter.wrap(field_extractor, "boost_candidates_from_memory", "correction memory reads")
    counter.wrap(correction_store, "boost_candidates_from_memory", "correction memory reads")


def _document_metadata(doc: SelectedDocument) -> dict[str, Any]:
    path = doc.path
    payload = {
        "document_id": doc.document_id,
        "category": doc.category,
        "difficulty": doc.difficulty,
        "language": doc.language,
        "dataset": doc.dataset,
        "document": path.name,
        "exists": path.exists(),
        "extension": path.suffix.lower(),
        "file_size_bytes": path.stat().st_size if path.exists() else None,
        "embedded_text_present": _embedded_text_present(path),
        "reason": doc.reason,
    }
    dims = _image_dimensions(path)
    if dims:
        payload["dimensions"] = dims
        payload["page_count"] = 1
    return payload


def _image_dimensions(path: Path) -> dict[str, int] | None:
    if path.suffix.lower() == ".pdf" or not path.exists():
        return None
    try:
        import cv2
        image = cv2.imread(str(path))
        if image is None:
            return None
        return {"width": int(image.shape[1]), "height": int(image.shape[0])}
    except Exception:
        return None


def _embedded_text_present(path: Path) -> bool:
    if path.suffix.lower() != ".pdf" or not path.exists():
        return False
    try:
        import fitz
        with fitz.open(path) as doc:
            return bool("\n".join(page.get_text("text") for page in doc).strip())
    except Exception:
        return False


def _selected_by_id(document_id: str | None) -> SelectedDocument | None:
    return next((doc for doc in SELECTED_DOCUMENTS if doc.document_id == document_id), None)


def _environment_payload(ocr_mode: str) -> dict[str, Any]:
    payload = {
        "os": platform.platform(),
        "python_executable": Path(sys.executable).name,
        "python_version": sys.version,
        "cpu_model": platform.processor() or platform.machine(),
        "logical_core_count": os.cpu_count(),
        "ocr_mode": ocr_mode,
        "cache_enabled": settings.enable_ocr_disk_cache,
        "cache_directory": settings.ocr_cache_dir.name,
        "thread_settings": {
            key: os.environ.get(key)
            for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")
            if os.environ.get(key)
        },
        "git_commit": _git(["rev-parse", "--short", "HEAD"]),
        "working_tree_status": _git(["status", "--short"]),
    }
    try:
        import psutil
        payload["physical_core_count"] = psutil.cpu_count(logical=False)
        payload["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 2)
    except Exception:
        payload["physical_core_count"] = None
        payload["ram_gb"] = None
    for module_name, key in (("paddleocr", "paddleocr_version"), ("paddle", "paddlepaddle_version"), ("cv2", "opencv_version")):
        try:
            module = __import__(module_name)
            payload[key] = getattr(module, "__version__", "available")
        except Exception as exc:
            payload[key] = f"unavailable: {type(exc).__name__}"
    try:
        import paddle
        payload["gpu_available"] = bool(paddle.device.is_compiled_with_cuda())
    except Exception:
        payload["gpu_available"] = None
    return payload


def _summary(all_results, cold_results, warm_cold_results, warm_cache_results, embedded_control, invalid_runs) -> dict[str, Any]:
    valid_warm = [item for item in warm_cold_results if item.get("valid_for_ocr_baseline")]
    valid_cold = [item for item in cold_results if item.get("valid_for_ocr_baseline")]
    valid_cache = [item for item in warm_cache_results if item.get("success")]
    normal_single = [item for item in valid_warm if item.get("page_count") == 1 and item.get("difficulty") in {"clean", "normal"}]
    return {
        "headline": {
            "valid_warm_engine_cold_cache_runs": len(valid_warm),
            "normal_single_page_median_seconds": _median([item.get("total_pipeline_seconds") for item in normal_single]),
            "normal_single_page_p90_seconds": _percentile([item.get("total_pipeline_seconds") for item in normal_single], 90),
            "documents_above_30_seconds": [item["document"] for item in valid_warm if (item.get("total_pipeline_seconds") or 0) > 30],
            "target_30_seconds_met": all((item.get("total_pipeline_seconds") or 999999) <= 30 for item in normal_single) if normal_single else None,
        },
        "counts": {
            "all_runs": len(all_results),
            "cold_start_runs": len(cold_results),
            "warm_engine_cold_cache_runs": len(warm_cold_results),
            "warm_cache_runs": len(warm_cache_results),
            "embedded_text_control_runs": len(embedded_control),
            "invalid_runs": len(invalid_runs),
        },
        "cold_start": _stats(cold_results),
        "warm_engine_cold_cache": _stats(valid_warm),
        "warm_cache": _stats(valid_cache),
        "embedded_text_control": _stats(embedded_control),
        "stage_stats_warm_engine_cold_cache": _stage_stats(valid_warm),
        "call_count_stats_warm_engine_cold_cache": _call_stats(valid_warm),
        "invalid_reasons": _counts(item.get("invalid_reason") for item in invalid_runs),
    }


def _finalize_outputs(output_dir: Path, all_results: list[dict[str, Any]]) -> dict[str, Any]:
    cold_results = [item for item in all_results if item.get("scenario") == "cold_start_cold_document_cache"]
    warm_cold_results = [item for item in all_results if item.get("scenario") == "warm_engine_cold_document_cache"]
    warm_cache_results = [item for item in all_results if item.get("scenario") == "warm_engine_warm_document_cache"]
    embedded_control = [item for item in all_results if item.get("scenario") == "embedded_text_fast_path"]
    invalid_runs = [item for item in all_results if not item.get("valid_for_ocr_baseline")]
    _write_csv(output_dir / "per_document_results.csv", all_results)
    _write_csv(output_dir / "cold_start_results.csv", cold_results)
    _write_csv(output_dir / "warm_engine_cold_cache_results.csv", warm_cold_results)
    _write_csv(output_dir / "warm_cache_results.csv", warm_cache_results)
    _write_csv(output_dir / "embedded_text_control.csv", embedded_control)
    _write_csv(output_dir / "invalid_runs.csv", invalid_runs)
    _write_stage_aggregates(output_dir / "stage_aggregates.csv", all_results)
    _write_call_counts(output_dir / "call_counts.csv", all_results)
    _write_json(output_dir / "slowest_runs.json", sorted(all_results, key=lambda item: item.get("total_pipeline_seconds") or 0, reverse=True)[:10])
    summary = _summary(all_results, cold_results, warm_cold_results, warm_cache_results, embedded_control, invalid_runs)
    _write_json(output_dir / "summary.json", summary)
    selected = [doc for doc in SELECTED_DOCUMENTS if doc.path.exists()]
    (output_dir / "report.md").write_text(_render_report(summary, selected), encoding="utf-8")
    return summary


def _stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(item["total_pipeline_seconds"]) for item in items if item.get("total_pipeline_seconds") is not None]
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 6) if values else None,
        "median": _median(values),
        "min": round(min(values), 6) if values else None,
        "max": round(max(values), 6) if values else None,
        "stdev": round(statistics.stdev(values), 6) if len(values) > 1 else None,
        "p50": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "p95": _percentile(values, 95),
    }


def _stage_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    stages: dict[str, list[float]] = {}
    for item in items:
        for name, seconds in (item.get("stages") or {}).items():
            stages.setdefault(name, []).append(float(seconds or 0))
    return {name: _stats([{"total_pipeline_seconds": value} for value in values]) for name, values in sorted(stages.items())}


def _call_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, dict[str, float]] = {}
    for item in items:
        for name, payload in (item.get("call_counts") or {}).items():
            target = totals.setdefault(name, {"count": 0, "seconds": 0.0, "errors": 0})
            target["count"] += int(payload.get("count") or 0)
            target["seconds"] += float(payload.get("seconds") or 0)
            target["errors"] += int(payload.get("errors") or 0)
    return {name: {"count": int(v["count"]), "seconds": round(v["seconds"], 6), "errors": int(v["errors"])} for name, v in sorted(totals.items())}


def _render_report(summary: dict[str, Any], selected: list[SelectedDocument]) -> str:
    headline = summary["headline"]
    lines = [
        "# Real OCR Performance Baseline 01",
        "",
        "This report excludes embedded-text PDF fast-path runs from real OCR statistics.",
        "",
        "## Selected Documents",
        "",
        "| ID | Dataset | Category | Language | Difficulty |",
        "|---|---|---|---|---|",
    ]
    for doc in selected:
        lines.append(f"| `{doc.document_id}` | `{doc.dataset}` | {doc.category} | {doc.language} | {doc.difficulty} |")
    lines.extend([
        "",
        "## Headline",
        "",
        f"- Valid warm-engine/cold-cache OCR runs: {headline.get('valid_warm_engine_cold_cache_runs')}",
        f"- Normal single-page median: {headline.get('normal_single_page_median_seconds')}",
        f"- Normal single-page p90: {headline.get('normal_single_page_p90_seconds')}",
        f"- Documents above 30 seconds: {headline.get('documents_above_30_seconds')}",
        f"- 30-second target met: {headline.get('target_30_seconds_met')}",
        "",
        "## Warm Engine / Cold Document Cache Stats",
        "",
        "```json",
        json.dumps(summary.get("warm_engine_cold_cache"), indent=2),
        "```",
        "",
        "## Invalid Runs",
        "",
        "```json",
        json.dumps(summary.get("invalid_reasons"), indent=2),
        "```",
    ])
    return "\n".join(lines) + "\n"


def _write_stage_aggregates(path: Path, results: list[dict[str, Any]]) -> None:
    rows = []
    for result in results:
        for stage, seconds in (result.get("stages") or {}).items():
            rows.append({
                "scenario": result.get("scenario"),
                "document": result.get("document"),
                "valid_for_ocr_baseline": result.get("valid_for_ocr_baseline"),
                "stage": stage,
                "seconds": seconds,
                "percentage": (result.get("stage_percentages") or {}).get(stage),
            })
    _write_csv(path, rows)


def _write_call_counts(path: Path, results: list[dict[str, Any]]) -> None:
    rows = []
    for result in results:
        for name, payload in (result.get("call_counts") or {}).items():
            rows.append({
                "scenario": result.get("scenario"),
                "document": result.get("document"),
                "valid_for_ocr_baseline": result.get("valid_for_ocr_baseline"),
                "function": name,
                **payload,
            })
    _write_csv(path, rows)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _append_jsonl(path: Path, payload: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _median(values: list[Any]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    return round(statistics.median(numeric), 6) if numeric else None


def _percentile(values: list[Any], percentile: int) -> float | None:
    numeric = sorted(float(value) for value in values if value is not None)
    if not numeric:
        return None
    if len(numeric) == 1:
        return round(numeric[0], 6)
    rank = (len(numeric) - 1) * percentile / 100
    low = int(rank)
    high = min(low + 1, len(numeric) - 1)
    fraction = rank - low
    return round(numeric[low] + (numeric[high] - numeric[low]) * fraction, 6)


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _git(args: list[str]) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=False)
        return (result.stdout or "").strip()[:4000] if result.returncode == 0 else None
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
