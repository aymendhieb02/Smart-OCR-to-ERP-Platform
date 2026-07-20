import json

import pytest

from app.services.performance_reporter import build_timing_summary, write_performance_reports
from app.services.performance_timer import PipelineTimer, disabled_timer


def test_timer_records_successful_stage() -> None:
    timer = PipelineTimer(enabled=True)

    with timer.stage("file_loading", input_type=".png"):
        pass

    result = timer.to_result(document="sample.png")
    assert result["stages"]["file_loading"] >= 0
    assert result["records"][0]["success"] is True
    assert result["records"][0]["metadata"]["input_type"] == ".png"


def test_timer_records_failed_stage() -> None:
    timer = PipelineTimer(enabled=True)

    with pytest.raises(RuntimeError):
        with timer.stage("ocr_execution"):
            raise RuntimeError("boom")

    result = timer.to_result(document="sample.png", success=False, error_type="RuntimeError")
    assert result["success"] is False
    assert result["records"][0]["success"] is False
    assert result["records"][0]["error_type"] == "RuntimeError"


def test_nested_stages_preserve_depth() -> None:
    timer = PipelineTimer(enabled=True)

    with timer.stage("total_pipeline"):
        with timer.stage("ocr_execution"):
            pass

    records = {record["name"]: record for record in timer.to_result()["records"]}
    assert records["total_pipeline"]["depth"] == 0
    assert records["ocr_execution"]["depth"] == 1


def test_repeated_stages_aggregate_correctly() -> None:
    timer = PipelineTimer(enabled=True)
    timer.add_measurement("ocr_cache_lookup", 0.1)
    timer.add_measurement("ocr_cache_lookup", 0.2)

    assert timer.aggregate()["ocr_cache_lookup"] == 0.3


def test_percentage_calculation_uses_total_pipeline() -> None:
    timer = PipelineTimer(enabled=True)
    timer.add_measurement("total_pipeline", 10)
    timer.add_measurement("ocr_execution", 2.5)

    assert timer.percentages()["ocr_execution"] == 25.0


def test_disabled_timer_has_minimal_behavior() -> None:
    timer = disabled_timer()

    with timer.stage("ocr_execution"):
        pass
    timer.add_measurement("ocr_execution", 2)
    timer.set_metadata(document="sample.png")

    result = timer.to_result(document="sample.png")
    assert result["stages"] == {}
    assert result["records"] == []
    assert result["metadata"] == {}


def test_timer_result_is_json_serializable() -> None:
    timer = PipelineTimer(enabled=True, metadata={"document": "C:/secret/path/invoice.pdf"})
    timer.add_measurement("total_pipeline", 1.5)

    payload = timer.to_result()

    encoded = json.dumps(payload, ensure_ascii=False)
    assert "invoice.pdf" in encoded
    assert "C:/secret/path" not in encoded


def test_reporter_writes_all_files(tmp_path) -> None:
    timer = PipelineTimer(enabled=True, metadata={"document": "invoice.pdf", "ocr_blocks": 3})
    timer.add_measurement("total_pipeline", 1)
    timer.add_measurement("ocr_execution", 0.5)
    result = timer.to_result(document="invoice.pdf", validation_status="needs_review")

    written = write_performance_reports([result], tmp_path)
    summary = build_timing_summary([result])

    assert set(written) == {"jsonl", "csv", "summary", "report"}
    assert all(path.exists() for path in written.values())
    assert summary["documents"] == 1
    assert summary["stages"]["ocr_execution"]["average_percent"] == 50.0
