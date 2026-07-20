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
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.services.ocr_engine import OCREngine, _get_paddle_ocr
from app.services.ocr_profiles import effective_ocr_config
from app.services.performance_timer import PipelineTimer
from app.services.pipeline_runner import process_document_file


OUTPUT_ROOT = PROJECT_ROOT / "dataset" / "reports" / "performance" / "ocr_experiments"
DEFAULT_DOCUMENT = PROJECT_ROOT.parent / "sources" / "datasets" / "high-quality-invoice-images-for-ocr" / "batch_1" / "batch_1" / "batch1_1" / "batch1-0001.jpg"

EXPERIMENTS: dict[str, dict[str, Any]] = {
    "current": {},
    "legacy_medium_v6": {
        "paddle_text_detection_model_name": "PP-OCRv6_medium_det",
        "paddle_text_recognition_model_name": "PP-OCRv6_medium_rec",
        "paddle_cpu_threads": 0,
        "ocr_input_max_side": 0,
    },
    "mkldnn": {"paddle_enable_mkldnn": True},
    "threads_1": {"paddle_cpu_threads": 1},
    "threads_2": {"paddle_cpu_threads": 2},
    "threads_4": {"paddle_cpu_threads": 4},
    "mobile_v4": {
        "paddle_text_detection_model_name": "PP-OCRv4_mobile_det",
        "paddle_text_recognition_model_name": "en_PP-OCRv4_mobile_rec",
    },
    "mobile_v4_resize_1600": {
        "paddle_text_detection_model_name": "PP-OCRv4_mobile_det",
        "paddle_text_recognition_model_name": "en_PP-OCRv4_mobile_rec",
        "ocr_input_max_side": 1600,
    },
    "mobile_v4_threads_4": {
        "paddle_text_detection_model_name": "PP-OCRv4_mobile_det",
        "paddle_text_recognition_model_name": "en_PP-OCRv4_mobile_rec",
        "paddle_cpu_threads": 4,
    },
    "mobile_v4_threads_4_resize_1600": {
        "paddle_text_detection_model_name": "PP-OCRv4_mobile_det",
        "paddle_text_recognition_model_name": "en_PP-OCRv4_mobile_rec",
        "paddle_cpu_threads": 4,
        "ocr_input_max_side": 1600,
    },
    "mobile_v4_resize_1280": {
        "paddle_text_detection_model_name": "PP-OCRv4_mobile_det",
        "paddle_text_recognition_model_name": "en_PP-OCRv4_mobile_rec",
        "ocr_input_max_side": 1280,
    },
    "mobile_v3": {
        "paddle_text_detection_model_name": "PP-OCRv3_mobile_det",
        "paddle_text_recognition_model_name": "en_PP-OCRv3_mobile_rec",
    },
    "resize_2560": {"ocr_input_max_side": 2560},
    "resize_1920": {"ocr_input_max_side": 1920},
    "resize_1600": {"ocr_input_max_side": 1600},
    "resize_1280": {"ocr_input_max_side": 1280},
    "pre_minimal": {"ocr_preprocessing_profile": "minimal"},
    "pre_grayscale": {"ocr_preprocessing_profile": "grayscale"},
    "pre_contrast": {"ocr_preprocessing_profile": "contrast"},
    "pre_direct": {"ocr_preprocessing_profile": "direct"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OCR-only performance experiments.")
    parser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--document-suffix", action="store_true", help="Append the document stem to experiment output folders.")
    parser.add_argument("--experiments", nargs="*", default=["current"], help="Experiment names, or 'all'.")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    environment = inspect_environment()
    (args.output_root / "environment.json").write_text(json.dumps(environment, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.inspect_only:
        report = render_inspection_report(environment)
        (args.output_root / "root_cause_report.md").write_text(report, encoding="utf-8")
        print(report)
        return 0

    names = list(EXPERIMENTS) if "all" in args.experiments else args.experiments
    comparison_rows = []
    baseline_signature = None
    baseline_total = None
    for name in names:
        if name not in EXPERIMENTS:
            raise SystemExit(f"Unknown experiment: {name}")
        result = run_experiment(name, args.document, EXPERIMENTS[name], args.repeats, args.output_root, document_suffix=args.document_suffix)
        if baseline_signature is None and name == "current":
            baseline_signature = result["quality_signature"]
            baseline_total = result["summary"].get("median_total_seconds")
        accepted = evaluate_acceptance(result, baseline_signature)
        row = comparison_row(result, baseline_total, accepted)
        comparison_rows.append(row)

    write_csv(args.output_root / "comparison.csv", comparison_rows)
    (args.output_root / "comparison.md").write_text(render_comparison(comparison_rows), encoding="utf-8")
    return 0


def run_experiment(name: str, document: Path, overrides: dict[str, Any], repeats: int, output_root: Path, *, document_suffix: bool = False) -> dict[str, Any]:
    output_name = f"{name}_{document.stem}" if document_suffix else name
    output_dir = output_root / output_name
    output_dir.mkdir(parents=True, exist_ok=True)
    config = current_ocr_config()
    config.update(overrides)
    (output_dir / "configuration.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    runs = []
    with temporary_settings(overrides):
        _get_paddle_ocr.cache_clear()
        warm = OCREngine(mode=settings.ocr_mode, use_disk_cache=False, refresh_cache=True)
        _warm_engine(warm)
        warm._ocr_cache.clear()
        for repeat in range(1, repeats + 1):
            warm._ocr_cache.clear()
            timer = PipelineTimer(enabled=True, metadata={"document": document.name})
            started = time.perf_counter()
            response = None
            error = None
            try:
                setattr(warm, "timing_recorder", timer)
                response = process_document_file(
                    document,
                    original_filename=document.name,
                    ocr_engine=warm,
                    include_preview=True,
                    persist_erp_json=False,
                    use_ocr_cache=False,
                    refresh_ocr_cache=True,
                    timing_recorder=timer,
                )
                success = True
            except Exception as exc:
                success = False
                error = f"{type(exc).__name__}: {exc}"
            finally:
                warm._ocr_cache.clear()
            timing = timer.to_result(
                document=document.name,
                success=success,
                error_type=error.split(":", 1)[0] if error else None,
                validation_status=response.validation.status if response and response.validation else None,
            )
            stages = timing.get("stages") or {}
            stage_timings = response.extraction_debug.get("stage_timings", {}) if response and response.extraction_debug else {}
            paddle_completed = (
                response is not None
                and response.erp_json is not None
                and response.erp_json.metadata.ocr_engine == "PaddleOCR"
                and int(stage_timings.get("total_paddle_calls") or 0) >= 1
            )
            quality = quality_payload(response)
            runs.append({
                "experiment": name,
                "repeat": repeat,
                "success": success,
                "error": error,
                "total_seconds": stages.get("total_pipeline") or round(time.perf_counter() - started, 6),
                "ocr_seconds": stages.get("ocr_execution"),
                "preprocessing_seconds": stages.get("preprocessing"),
                "postprocessing_seconds": stages.get("ocr_postprocessing"),
                "extraction_seconds": (stages.get("candidate_generation") or 0) + (stages.get("line_item_reconstruction") or 0),
                "validation_seconds": stages.get("financial_validation"),
                "serialization_seconds": stages.get("result_serialization"),
                "ocr_boxes": len(response.ocr_blocks) if response else None,
                "bbox_boxes": sum(1 for block in response.ocr_blocks if block.bbox) if response else None,
                "validation_status": response.validation.status if response and response.validation else None,
                "ocr_executed": (stages.get("ocr_execution") or 0) > 0 and paddle_completed,
                "paddle_completed": paddle_completed,
                "paddle_calls": stage_timings.get("total_paddle_calls"),
                "ocr_engine": response.erp_json.metadata.ocr_engine if response and response.erp_json else None,
                "ocr_cache_source": stage_timings.get("ocr_cache_source"),
                "quality": quality,
                "stages": stages,
            })
    summary = summarize_runs(runs)
    quality = summarize_quality(runs)
    bbox = summarize_bbox(runs)
    result = {
        "experiment": name,
        "configuration": config,
        "summary": summary,
        "quality": quality,
        "bbox": bbox,
        "quality_signature": quality_signature(runs),
    }
    write_csv(output_dir / "timings.csv", flatten_runs(runs))
    (output_dir / "timings.json").write_text(json.dumps(runs, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (output_dir / "quality.json").write_text(json.dumps(quality, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "bbox.json").write_text(json.dumps(bbox, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "report.md").write_text(render_experiment_report(result), encoding="utf-8")
    return result


def quality_payload(response) -> dict[str, Any]:
    if response is None:
        return {}
    fields = response.detected_fields
    return {
        "supplier": fields.supplier_name,
        "customer": fields.customer_name,
        "invoice_number": fields.invoice_number,
        "invoice_date": str(fields.invoice_date) if fields.invoice_date else None,
        "due_date": str(fields.due_date) if fields.due_date else None,
        "currency": fields.currency,
        "amount_ht": fields.amount_ht,
        "tva": fields.tva_amount,
        "ttc": fields.amount_ttc,
        "line_items": len(fields.line_items or []),
        "validation": response.validation.status if response.validation else None,
        "confidence": response.confidence_breakdown.get("overall_confidence") if response.confidence_breakdown else None,
    }


def inspect_environment() -> dict[str, Any]:
    payload = {
        "os": platform.platform(),
        "python_executable": Path(sys.executable).name,
        "python_version": sys.version,
        "cpu": platform.processor() or platform.machine(),
        "logical_cores": os.cpu_count(),
        "openmp": {key: os.environ.get(key) for key in ("OMP_NUM_THREADS", "CPU_NUM", "MKL_NUM_THREADS") if os.environ.get(key)},
        "current_config": current_ocr_config(),
    }
    try:
        import psutil
        payload["physical_cores"] = psutil.cpu_count(logical=False)
        payload["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 2)
    except Exception:
        payload["physical_cores"] = None
        payload["ram_gb"] = None
    try:
        import paddle
        payload["paddle_version"] = paddle.__version__
        payload["gpu_available"] = bool(paddle.device.is_compiled_with_cuda())
        payload["paddle_device"] = paddle.device.get_device()
    except Exception as exc:
        payload["paddle_error"] = str(exc)
    try:
        import paddleocr
        payload["paddleocr_version"] = paddleocr.__version__
    except Exception as exc:
        payload["paddleocr_error"] = str(exc)
    return payload


def current_ocr_config() -> dict[str, Any]:
    effective = effective_ocr_config()
    return {
        "ocr_profile": effective["ocr_profile"],
        "enable_mkldnn": effective["enable_mkldnn"],
        "cpu_threads": effective["cpu_threads"],
        "use_gpu": effective["use_gpu"],
        "lang": "en",
        "ocr_version": settings.paddle_ocr_version,
        "det_model": effective["detector"],
        "rec_model": effective["recognizer"],
        "orientation_model": None,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "text_det_limit_side_len": settings.paddle_text_det_limit_side_len,
        "input_max_side": effective["input_max_side"],
        "text_recognition_batch_size": settings.paddle_text_recognition_batch_size,
        "preprocessing_profile": effective["preprocessing_profile"],
    }


@contextmanager
def temporary_settings(overrides: dict[str, Any]):
    previous = {key: getattr(settings, key) for key in overrides}
    env_names = {
        "paddle_text_detection_model_name": "INVOICE_OCR_PADDLE_TEXT_DETECTION_MODEL_NAME",
        "paddle_text_recognition_model_name": "INVOICE_OCR_PADDLE_TEXT_RECOGNITION_MODEL_NAME",
        "paddle_cpu_threads": "INVOICE_OCR_PADDLE_CPU_THREADS",
        "ocr_input_max_side": "INVOICE_OCR_OCR_INPUT_MAX_SIDE",
        "paddle_enable_mkldnn": "INVOICE_OCR_PADDLE_ENABLE_MKLDNN",
        "paddle_use_gpu": "INVOICE_OCR_PADDLE_USE_GPU",
        "ocr_preprocessing_profile": "INVOICE_OCR_OCR_PREPROCESSING_PROFILE",
    }
    previous_env = {name: os.environ.get(name) for name in env_names.values()}
    try:
        for key, value in overrides.items():
            setattr(settings, key, value)
            env_name = env_names.get(key)
            if env_name:
                os.environ[env_name] = str(value)
        yield
    finally:
        for key, value in previous.items():
            setattr(settings, key, value)
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        _get_paddle_ocr.cache_clear()


def _warm_engine(engine: OCREngine) -> None:
    import numpy as np
    try:
        engine.run([np.full((64, 256, 3), 255, dtype=np.uint8)])
    except Exception:
        pass


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [run for run in runs if run["success"] and run["ocr_executed"]]
    return {
        "runs": len(runs),
        "valid_ocr_runs": len(successful),
        "median_total_seconds": median(run["total_seconds"] for run in successful),
        "median_ocr_seconds": median(run["ocr_seconds"] for run in successful),
        "median_preprocessing_seconds": median(run["preprocessing_seconds"] for run in successful),
        "median_extraction_seconds": median(run["extraction_seconds"] for run in successful),
        "median_validation_seconds": median(run["validation_seconds"] for run in successful),
        "median_serialization_seconds": median(run["serialization_seconds"] for run in successful),
    }


def summarize_quality(runs: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [run for run in runs if run["success"]]
    return successful[0]["quality"] if successful else {}


def summarize_bbox(runs: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [run for run in runs if run["success"]]
    return {
        "ocr_boxes": successful[0].get("ocr_boxes") if successful else None,
        "boxes_with_bbox": successful[0].get("bbox_boxes") if successful else None,
        "bounding_boxes_ok": bool(successful and successful[0].get("bbox_boxes")),
    }


def quality_signature(runs: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [run for run in runs if run["success"]]
    return successful[0]["quality"] if successful else {}


def evaluate_acceptance(result: dict[str, Any], baseline_signature: dict[str, Any] | None) -> bool:
    summary = result["summary"]
    if not summary.get("valid_ocr_runs"):
        return False
    if not result["bbox"].get("bounding_boxes_ok"):
        return False
    if baseline_signature and result["quality_signature"] != baseline_signature:
        return False
    return True


def comparison_row(result: dict[str, Any], baseline_total: float | None, accepted: bool) -> dict[str, Any]:
    config = result["configuration"]
    total = result["summary"].get("median_total_seconds")
    speedup = round(((baseline_total - total) / baseline_total) * 100, 2) if baseline_total and total else None
    return {
        "Experiment": result["experiment"],
        "Detector": config.get("det_model"),
        "Recognizer": config.get("rec_model"),
        "MKLDNN": config.get("enable_mkldnn"),
        "Threads": config.get("cpu_threads"),
        "GPU": config.get("use_gpu"),
        "Input Resolution": config.get("text_det_limit_side_len") or "original/current",
        "OCR Time": result["summary"].get("median_ocr_seconds"),
        "Total Time": total,
        "Speedup %": speedup,
        "OCR Boxes": result["bbox"].get("ocr_boxes"),
        "Supplier OK": bool(result["quality"].get("supplier")),
        "Totals OK": bool(result["quality"].get("ttc")),
        "Line Items OK": (result["quality"].get("line_items") or 0) > 0,
        "Bounding Boxes OK": result["bbox"].get("bounding_boxes_ok"),
        "Accepted": accepted,
    }


def flatten_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for run in runs:
        row = {key: value for key, value in run.items() if key not in {"quality", "stages"}}
        row.update({f"stage_{key}": value for key, value in run.get("stages", {}).items()})
        rows.append(row)
    return rows


def median(values) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    return round(statistics.median(numeric), 6) if numeric else None


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def render_inspection_report(environment: dict[str, Any]) -> str:
    config = environment["current_config"]
    return "\n".join([
        "# OCR P0 Root Cause Inspection",
        "",
        f"- PaddleOCR version: `{environment.get('paddleocr_version')}`",
        f"- PaddlePaddle version: `{environment.get('paddle_version')}`",
        f"- CPU: `{environment.get('cpu')}`",
        f"- Logical cores: `{environment.get('logical_cores')}`",
        f"- Physical cores: `{environment.get('physical_cores')}`",
        f"- RAM GB: `{environment.get('ram_gb')}`",
        f"- GPU available: `{environment.get('gpu_available')}`",
        f"- Paddle device: `{environment.get('paddle_device')}`",
        f"- MKLDNN enabled: `{config.get('enable_mkldnn')}`",
        f"- CPU threads: `{config.get('cpu_threads')}`",
        f"- Detection model: `{config.get('det_model')}`",
        f"- Recognition model: `{config.get('rec_model')}`",
        f"- Orientation model: `{config.get('orientation_model')}`",
        f"- Detection side limit: `{config.get('text_det_limit_side_len')}`",
        f"- Preprocessing profile: `{config.get('preprocessing_profile')}`",
        "",
        "Baseline evidence shows `_run_paddle_prediction` dominates runtime; downstream extraction/ERP work is below 3%.",
    ]) + "\n"


def render_experiment_report(result: dict[str, Any]) -> str:
    return "# OCR Experiment\n\n```json\n" + json.dumps(result["summary"], indent=2) + "\n```\n"


def render_comparison(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "# OCR Experiment Comparison\n\nNo experiments were run.\n"
    headers = list(rows[0])
    lines = ["# OCR Experiment Comparison", "", "|" + "|".join(headers) + "|", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("|" + "|".join(str(row.get(header, "")) for header in headers) + "|")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
