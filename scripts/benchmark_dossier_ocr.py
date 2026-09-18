from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.services.ocr_engine import OCREngine
from app.services.ocr_profiles import PROFILES, effective_ocr_config
from app.services.pipeline_runner import process_dossier_file


CANONICAL_FILENAMES = (
    "INV 01.pdf",
    "Inv 02.pdf",
    "INV 03.pdf",
    "Inv 04.pdf",
    "INV 05.pdf",
    "Inv 06.pdf",
)


def main() -> None:
    args = _parse_args()
    settings.ocr_profile = args.ocr_profile
    samples_root = Path(args.samples_root).resolve()
    files = [samples_root / name for name in CANONICAL_FILENAMES]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing canonical dossier PDFs: {missing}")

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notice": "Operational benchmark only. Draft labels were not used and no accuracy metric was calculated.",
        "samples_root": str(samples_root),
        "canonical_files": list(CANONICAL_FILENAMES),
        "expected_physical_pages": len(files) * 3,
        "ocr_configuration": effective_ocr_config(),
        "execution": {
            "document_concurrency": 1,
            "page_processing": "sequential",
            "passes": args.passes,
            "disk_cache_enabled": not args.disable_cache,
            "refresh_cache": args.refresh_cache,
        },
        "system_before": _system_snapshot(),
        "passes": [],
    }

    for pass_index in range(args.passes):
        pass_name = "cold" if pass_index == 0 else f"warm_{pass_index}"
        engine = OCREngine(
            mode=args.ocr_mode,
            use_disk_cache=not args.disable_cache,
            refresh_cache=args.refresh_cache and pass_index == 0,
        )
        pass_started = time.perf_counter()
        documents = []
        for path in files:
            documents.append(_run_one(path, engine, pass_name))
        elapsed = time.perf_counter() - pass_started
        page_count = sum(item.get("page_count", 0) for item in documents)
        report["passes"].append({
            "name": pass_name,
            "elapsed_seconds": round(elapsed, 4),
            "seconds_per_page": round(elapsed / page_count, 4) if page_count else None,
            "page_count": page_count,
            "documents": documents,
        })

    report["system_after"] = _system_snapshot()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote operational dossier benchmark: {output}")


def _run_one(path: Path, engine: OCREngine, pass_name: str) -> dict[str, Any]:
    peak_rss = _process_rss_bytes()
    stop = threading.Event()

    def sample_memory() -> None:
        nonlocal peak_rss
        while not stop.wait(0.05):
            current = _process_rss_bytes()
            if current is None:
                return
            peak_rss = max(peak_rss or 0, current)

    sampler = threading.Thread(target=sample_memory, name="dossier-rss-sampler", daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        result = process_dossier_file(
            path,
            original_filename=path.name,
            ocr_engine=engine,
            persist_erp_json=False,
            ocr_mode=engine.mode,
            use_ocr_cache=engine.use_disk_cache,
            refresh_ocr_cache=engine.refresh_cache,
        )
        elapsed = time.perf_counter() - started
        logical_documents = []
        for item in result.logical_documents:
            response = item.response
            present_fields = sum(
                value not in (None, "", [], {})
                for value in response.detected_fields.model_dump().values()
            )
            logical_documents.append({
                "group_id": item.group.group_id,
                "physical_pages": list(item.group.pages),
                "document_type": item.group.document_type,
                "document_family": item.group.document_family,
                "validation_status": response.validation.status,
                "erp_export_allowed": response.erp_readiness.get("export_allowed"),
                "present_detected_fields": present_fields,
                "ocr_line_count": len(response.ocr_blocks),
            })
        return {
            "filename": path.name,
            "pass": pass_name,
            "status": "processed",
            "elapsed_seconds": round(elapsed, 4),
            "seconds_per_page": round(elapsed / result.page_count, 4) if result.page_count else None,
            "page_count": result.page_count,
            "ocr_engine": result.ocr_engine,
            "ocr_confidence": _average_confidence(result),
            "peak_process_rss_bytes": peak_rss,
            "peak_process_rss_mib": round(peak_rss / (1024 * 1024), 2) if peak_rss is not None else None,
            "timings": result.timings,
            "page_classifications": [
                {
                    "page_number": item.page_number,
                    "document_type": item.document_type,
                    "document_family": item.document_family,
                    "match_score": item.match_score,
                    "matched_anchors": list(item.matched_anchors),
                }
                for item in result.page_classifications
            ],
            "logical_documents": logical_documents,
        }
    except Exception as exc:
        return {
            "filename": path.name,
            "pass": pass_name,
            "status": "error",
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "peak_process_rss_bytes": peak_rss,
            "peak_process_rss_mib": round(peak_rss / (1024 * 1024), 2) if peak_rss is not None else None,
        }
    finally:
        stop.set()
        sampler.join(timeout=1.0)


def _average_confidence(result) -> float | None:
    values = [
        block.confidence
        for document in result.logical_documents
        for block in document.response.ocr_blocks
        if block.confidence is not None
    ]
    return round(sum(values) / len(values), 4) if values else None


def _system_snapshot() -> dict[str, Any]:
    memory = _windows_memory_status()
    return {
        "logical_cpu_count": os.cpu_count(),
        "available_memory_bytes": memory.get("available_physical"),
        "available_memory_gib": _gib(memory.get("available_physical")),
        "pagefile_used_bytes": memory.get("pagefile_used"),
        "pagefile_used_gib": _gib(memory.get("pagefile_used")),
    }


def _process_rss_bytes() -> int | None:
    if os.name != "nt":
        return None
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_current_process = ctypes.windll.kernel32.GetCurrentProcess
    get_current_process.restype = ctypes.c_void_p
    get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    get_process_memory_info.restype = ctypes.c_int
    handle = get_current_process()
    if not get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
        return None
    return int(counters.WorkingSetSize)


def _windows_memory_status() -> dict[str, int | None]:
    if os.name != "nt":
        return {"available_physical": None, "pagefile_used": None}
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"available_physical": None, "pagefile_used": None}
    return {
        "available_physical": int(status.ullAvailPhys),
        "pagefile_used": int(status.ullTotalPageFile - status.ullAvailPageFile),
    }


def _gib(value: int | None) -> float | None:
    return round(value / (1024**3), 3) if value is not None else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run operational (non-accuracy) OCR and segmentation measurements on the six canonical dossiers."
    )
    parser.add_argument("--samples-root", default=r"D:\Stage_udgroup\haithem_samples")
    parser.add_argument("--ocr-profile", choices=sorted(PROFILES), default="optimized_mobile_v5")
    parser.add_argument("--ocr-mode", choices=("fast", "balanced", "accurate"), default="balanced")
    parser.add_argument("--passes", type=int, choices=(1, 2), default=1)
    parser.add_argument("--disable-cache", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument(
        "--output",
        default=str(ROOT / "dataset" / "reports" / "dossier_ocr_v5_operational.json"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
