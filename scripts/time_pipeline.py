from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.performance_reporter import DEFAULT_PERFORMANCE_ROOT, write_performance_reports
from app.services.performance_timer import PipelineTimer
from app.services.pipeline_runner import process_document_file


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".pdf"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OCR-to-ERP with performance timing enabled.")
    parser.add_argument("--file", action="append", dest="files", help="Document path to process. Can be used multiple times.")
    parser.add_argument("--glob", dest="glob_pattern", help="Glob pattern for multiple documents, for example dataset/demo/*.png.")
    parser.add_argument("--output", type=Path, default=DEFAULT_PERFORMANCE_ROOT, help="Report output directory.")
    parser.add_argument("--ocr-mode", default=None, help="OCR mode passed to the normal pipeline.")
    parser.add_argument("--no-preview", action="store_true", help="Skip preview generation during timing.")
    parser.add_argument("--persist-erp-json", action="store_true", help="Keep normal ERP JSON disk output enabled.")
    parser.add_argument("--no-ocr-cache", action="store_true", help="Disable OCR disk cache for this timing run.")
    parser.add_argument("--refresh-ocr-cache", action="store_true", help="Refresh OCR cache for this timing run.")
    args = parser.parse_args()

    paths = list(_resolve_inputs(args.files or [], args.glob_pattern))
    if not paths:
        print("No input documents found. Use --file or --glob.", file=sys.stderr)
        return 2

    results = []
    for path in paths:
        timer = PipelineTimer(enabled=True, metadata={"document": path.name, "filename": path.name})
        try:
            response = process_document_file(
                path,
                original_filename=path.name,
                include_preview=not args.no_preview,
                persist_erp_json=args.persist_erp_json,
                ocr_mode=args.ocr_mode,
                use_ocr_cache=not args.no_ocr_cache,
                refresh_ocr_cache=args.refresh_ocr_cache,
                timing_recorder=timer,
            )
            results.append(timer.to_result(
                document=path.name,
                success=True,
                validation_status=response.validation.status if response.validation else None,
            ))
            print(f"OK {path.name}: {results[-1]['total_seconds']}s status={results[-1].get('validation_status')}")
        except Exception as exc:
            timer.set_metadata(error_type=type(exc).__name__)
            results.append(timer.to_result(
                document=path.name,
                success=False,
                error_type=type(exc).__name__,
            ))
            print(f"ERROR {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)

    written = write_performance_reports(results, args.output)
    print("Timing reports written:")
    for label, path in written.items():
        print(f"- {label}: {path}")
    return 0 if all(result.get("success") for result in results) else 1


def _resolve_inputs(files: list[str], glob_pattern: str | None) -> Iterable[Path]:
    seen: set[Path] = set()
    for file_name in files:
        path = Path(file_name).expanduser().resolve()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and path not in seen:
            seen.add(path)
            yield path
    if glob_pattern:
        search_pattern = glob_pattern if Path(glob_pattern).is_absolute() else str(PROJECT_ROOT / glob_pattern)
        for match in sorted(glob.glob(search_pattern)):
            path = Path(match)
            resolved = path.expanduser().resolve()
            if resolved.is_file() and resolved.suffix.lower() in SUPPORTED_EXTENSIONS and resolved not in seen:
                seen.add(resolved)
                yield resolved


if __name__ == "__main__":
    raise SystemExit(main())
