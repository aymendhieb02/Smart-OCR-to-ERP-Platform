from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from scripts import evaluate_dataset as evaluator


def test_smoke_sampling_is_balanced_and_deterministic() -> None:
    files_by_batch = {
        batch: [Path(f"/{batch}/doc_{idx}.jpg") for idx in range(20)]
        for batch in evaluator.BATCHES
    }

    first = evaluator.select_files(files_by_batch, mode="smoke", seed=42, limit=None)
    second = evaluator.select_files(files_by_batch, mode="smoke", seed=42, limit=None)

    assert first == second
    assert len(first) == 30
    assert {batch: sum(1 for item_batch, _ in first if item_batch == batch) for batch in evaluator.BATCHES} == {
        "batch_1": 10,
        "batch_2": 10,
        "batch_3": 10,
    }


def test_medium_sampling_and_limit() -> None:
    files_by_batch = {
        batch: [Path(f"/{batch}/doc_{idx}.png") for idx in range(150)]
        for batch in evaluator.BATCHES
    }

    selected = evaluator.select_files(files_by_batch, mode="medium", seed=7, limit=12)

    assert len(selected) == 12
    assert all(batch in evaluator.BATCHES for batch, _ in selected)


def test_fail_fast_is_a_real_mode() -> None:
    files_by_batch = {
        batch: [Path(f"/{batch}/doc_{idx}.jpg") for idx in range(20)]
        for batch in evaluator.BATCHES
    }

    selected = evaluator.select_files(files_by_batch, mode="fail-fast", seed=1, limit=None)

    assert len(selected) == 30
    args = argparse.Namespace(mode="fail-fast", fail_fast=False)
    assert evaluator.should_stop_after_error(args, evaluator.CRITICAL_ERROR_LIMIT)
    assert not evaluator.should_stop_after_error(args, evaluator.CRITICAL_ERROR_LIMIT - 1)


def test_no_ocr_cache_boolean_parsing() -> None:
    assert evaluator.parse_bool("false") is False
    assert evaluator.parse_bool("true") is True
    assert evaluator.parse_bool("0") is False
    assert evaluator.parse_bool("yes") is True


def test_file_hash_is_stable(tmp_path: Path) -> None:
    sample = tmp_path / "doc.txt"
    sample.write_text("same document", encoding="utf-8")

    assert evaluator.compute_file_hash(sample) == evaluator.compute_file_hash(sample)


def test_checkpoint_selected_files_roundtrip(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    selected = [("batch_1", tmp_path / "a.jpg"), ("batch_2", tmp_path / "b.jpg")]
    processed = {str((tmp_path / "a.jpg").resolve())}
    args = argparse.Namespace(mode="smoke", seed=42)

    evaluator.save_checkpoint(run_dir, selected, processed, args)
    checkpoint = evaluator.load_checkpoint(run_dir)

    assert checkpoint["processed_paths"] == sorted(processed)
    assert evaluator.checkpoint_selected_files(checkpoint) == [(batch, path.resolve()) for batch, path in selected]


def test_summary_contains_required_phase4_metrics() -> None:
    rows = [
        {
            "validation_status": "valid",
            "processing_time_seconds": "2.0",
            "ocr_cache_hit": "true",
            "layout_cache_hit": "false",
            "line_items_validated": "2",
            "line_items_needs_review": "0",
            "totals_consistent": "true",
            "missing_fields": "",
        },
        {
            "validation_status": "needs_review",
            "processing_time_seconds": "4.0",
            "ocr_cache_hit": "false",
            "layout_cache_hit": "true",
            "line_items_validated": "0",
            "line_items_needs_review": "1",
            "totals_consistent": "false",
            "missing_fields": "invoice_number;amount_ttc",
        },
    ]
    args = argparse.Namespace(run_id="run", mode="smoke", seed=42)

    summary = evaluator.compute_summary(rows, [], [], [("batch_1", Path("a")), ("batch_2", Path("b"))], set(), time.perf_counter(), args)

    assert summary["docs_processed"] == 2
    assert summary["average_time_per_doc"] == 3.0
    assert summary["estimated_time_for_full_8000_hours"] is not None
    assert summary["valid"] == 1
    assert summary["needs_review"] == 1
    assert summary["ocr_cache_hit_rate"] == 0.5
    assert summary["totals_consistency_rate"] == 0.5
    assert summary["line_items_validated"] == 2
    assert summary["line_items_needs_review"] == 1
