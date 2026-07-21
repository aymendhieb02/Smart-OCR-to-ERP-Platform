from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.manual_benchmark_utils import BenchmarkDocument
from scripts import benchmark_hybrid_llm as bench


def test_parse_args_defaults_to_advisory(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["benchmark_hybrid_llm.py"])

    args = bench.parse_args()

    assert args.mode == "advisory"
    assert args.prompt_version == "hybrid_prompt_v1"
    assert args.max_documents == 10
    assert args.timeout >= 60


def test_prompt_versions_cli_parses_multiple_versions(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", [
        "benchmark_hybrid_llm.py",
        "--prompt-versions",
        "hybrid_prompt_v1,hybrid_prompt_v3,hybrid_prompt_v4",
    ])

    args = bench.parse_args()

    assert bench.parse_prompt_versions(args) == ("hybrid_prompt_v1", "hybrid_prompt_v3", "hybrid_prompt_v4")


def test_prompt_versions_cli_supports_single_version(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["benchmark_hybrid_llm.py", "--prompt-versions", "hybrid_prompt_v3"])

    args = bench.parse_args()

    assert bench.parse_prompt_versions(args) == ("hybrid_prompt_v3",)


def test_environment_check_reports_missing_ollama(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bench.shutil, "which", lambda name: None)
    monkeypatch.setattr("scripts.benchmark_hybrid_llm.settings.llm_resolver_cache_dir", tmp_path)

    def fail_http(*args, **kwargs):
        raise OSError("service down")

    monkeypatch.setattr(bench, "http_json", fail_http)

    status = bench.check_environment(model="qwen2.5-coder:7b", endpoint="http://localhost:11434/api/generate", timeout=0.01)

    assert status["ready"] is False
    assert status["ollama_reachable"] is False
    assert status["cache_directory_writable"] is True


def test_environment_check_success(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bench.shutil, "which", lambda name: "C:/ollama/ollama.exe")
    monkeypatch.setattr("scripts.benchmark_hybrid_llm.settings.llm_resolver_cache_dir", tmp_path)

    def fake_http(url, payload, *, timeout, method):
        if method == "GET":
            return {"models": [{"name": "qwen2.5-coder:7b", "size": 1}]}
        return {"response": '{"ok": true}'}

    monkeypatch.setattr(bench, "http_json", fake_http)

    status = bench.check_environment(model="qwen2.5-coder:7b", endpoint="http://localhost:11434/api/generate", timeout=1)

    assert status["ready"] is True
    assert status["model_installed"] is True
    assert status["test_response_valid_json"] is True


def test_artifact_path_persists_prompt_version(tmp_path: Path) -> None:
    path = bench.artifact_path(tmp_path, "hybrid_prompt_v2", "invoice 01.png")

    assert path.parent.name == "hybrid_prompt_v2"
    assert path.name == "invoice 01.json"


def test_report_only_outputs_metrics(tmp_path: Path) -> None:
    artifact = {
        "status": "success",
        "document_id": "doc1",
        "dataset": "test",
        "prompt_version": "hybrid_prompt_v1",
        "proposals": [{"field": "supplier"}],
        "accepted_proposals": [],
        "rejected_proposals": [{"proposal": {"field": "supplier"}, "reason": "missing evidence"}],
        "parsed_response": {"document_decision": "propose_corrections"},
        "hybrid_debug": {"invoked": True, "metrics": {"duration_seconds": 1.2}},
        "label": {},
        "deterministic_result": {"fields": {}, "line_items_count": 0},
        "final_selected_result": {"fields": {}, "line_items_count": 0},
    }
    path = bench.artifact_path(tmp_path, "hybrid_prompt_v1", "doc1.png")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")

    bench.generate_reports(tmp_path, ("hybrid_prompt_v1",))

    assert (tmp_path / "hybrid_metrics.json").exists()
    assert (tmp_path / "prompt_version_metrics.json").exists()
    assert (tmp_path / "hybrid_benchmark_report.md").exists()


def test_incremental_jsonl_persistence(tmp_path: Path) -> None:
    path = tmp_path / "calibration_invocations.jsonl"

    bench.append_jsonl(path, {"document_id": "doc1"})
    bench.append_jsonl(path, {"document_id": "doc2"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["document_id"] == "doc2"


def test_resume_key_includes_prompt_model_and_document(tmp_path: Path) -> None:
    document = make_document(tmp_path, "invoice.png")

    key = bench.processed_key(document, "hybrid_prompt_v3", "qwen2.5-coder:7b")

    assert key == "hybrid_prompt_v3:qwen2.5-coder:7b:invoice.png"


def test_fixed_document_selection_is_persisted(monkeypatch, tmp_path: Path) -> None:
    document = make_document(tmp_path, "invoice.png")
    args = type("Args", (), {"benchmark_root": str(tmp_path), "dataset": None, "max_documents": 1, "resume": False})()
    monkeypatch.setattr(bench, "load_manifest_documents", lambda root: [document])

    selected = bench.load_or_select_documents(args, tmp_path / "run")

    assert selected == [document]
    assert (tmp_path / "run" / "selected_documents.json").exists()


def test_report_generation_handles_partial_prompt_runs(tmp_path: Path) -> None:
    artifact = {
        "status": "success",
        "document_id": "doc1",
        "dataset": "test",
        "prompt_version": "hybrid_prompt_v3",
        "parser_status": "failed",
        "proposals": [],
        "accepted_proposals": [],
        "rejected_proposals": [],
        "parsed_response": None,
        "hybrid_debug": {"invoked": True, "error": "Ollama request failed: timed out", "metrics": {"duration_seconds": 60.0}},
        "prompt_stats": {"prompt_characters": 1200, "output_tokens_estimated": 0},
        "label": {},
        "deterministic_result": {"fields": {}, "line_items_count": 0},
        "final_selected_result": {"fields": {}, "line_items_count": 0},
    }
    path = bench.artifact_path(tmp_path, "hybrid_prompt_v3", "doc1.png")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")

    bench.generate_reports(tmp_path, ("hybrid_prompt_v3",))
    metrics = json.loads((tmp_path / "prompt_version_metrics.json").read_text(encoding="utf-8"))

    assert metrics[0]["prompt_version"] == "hybrid_prompt_v3"
    assert metrics[0]["timeout_rate"] == 1.0


def test_ground_truth_completeness_detects_missing_fields() -> None:
    label = {"supplier_name": "ACME", "verified_by_human": False}

    missing = bench.missing_ground_truth_fields(label)

    assert "customer_name" in missing
    assert "line_items" in missing
    assert "verified_by_human" in missing


def test_correction_classification_correct_improvement() -> None:
    result = bench.classify_correction(None, "ACME SARL", "ACME SARL", True)

    assert result == "Correct improvement"


def test_correction_classification_wrong_accepted() -> None:
    result = bench.classify_correction(None, "Wrong SARL", "ACME SARL", True)

    assert result == "Wrong correction"


def test_field_accuracy_rows_mark_improvement() -> None:
    artifact = {
        "status": "success",
        "document_id": "doc1",
        "prompt_version": "hybrid_prompt_v3",
        "label": {"supplier_name": "ACME SARL", "verified_by_human": True},
        "deterministic_result": {"fields": {"supplier_name": None}, "line_items_count": 0},
        "final_selected_result": {"fields": {"supplier_name": "ACME SARL"}, "line_items_count": 0},
    }

    rows = bench.build_hybrid_field_accuracy_rows([artifact])
    supplier = next(row for row in rows if row["field"] == "supplier_name")

    assert supplier["improved"] is True


def test_blank_truth_line_item_template_is_not_applicable() -> None:
    artifact = {
        "status": "success",
        "document_id": "doc1",
        "prompt_version": "hybrid_prompt_v3",
        "label": {"line_items": [{"description": None, "quantity": None}], "verified_by_human": True},
        "deterministic_result": {"fields": {}, "line_items_count": 0},
        "final_selected_result": {"fields": {}, "line_items_count": 0},
    }

    rows = bench.build_hybrid_field_accuracy_rows([artifact])
    line_count = next(row for row in rows if row["field"] == "line_item_count")

    assert line_count["ground_truth"] is None
    assert line_count["deterministic_correct"] is None
    assert line_count["hybrid_correct"] is None


def test_accuracy_metrics_gate_requires_verified_complete_labels() -> None:
    artifact = {
        "status": "success",
        "document_id": "doc1",
        "prompt_version": "hybrid_prompt_v3",
        "label": {"supplier_name": "ACME SARL", "verified_by_human": True},
        "deterministic_result": {"fields": {"supplier_name": "ACME SARL"}, "line_items_count": 0},
        "final_selected_result": {"fields": {"supplier_name": "ACME SARL"}, "line_items_count": 0},
    }

    rows = bench.build_hybrid_field_accuracy_rows([artifact])
    metrics = bench.calculate_hybrid_accuracy_metrics(rows, [], [artifact])

    assert metrics["accuracy_claim_allowed"] is False


def test_accuracy_report_blocks_when_labels_incomplete(tmp_path: Path) -> None:
    quality = {
        "all_documents_verified_complete": False,
        "documents": [],
    }
    metrics = {
        "overall_field_accuracy_before": None,
        "overall_field_accuracy_after": None,
        "accepted_correction_precision": None,
        "false_acceptance_rate": None,
        "hallucination_rate": None,
        "field_metrics": {},
    }

    bench.write_hybrid_accuracy_report(tmp_path, metrics, quality)

    text = (tmp_path / "hybrid_accuracy_report.md").read_text(encoding="utf-8")
    assert "Not proven" in text
    assert "A) Keep advisory mode" in text


def test_error_taxonomy_detects_missing_supplier() -> None:
    artifact = {
        "status": "success",
        "document_id": "doc1",
        "dataset": "test",
        "prompt_version": "hybrid_prompt_v3",
        "trigger_reasons": ["missing_supplier_name"],
        "label": {},
        "deterministic_result": {
            "fields": {"supplier_name": None},
            "validation_status": "needs_review",
            "erp_readiness": {"missing_fields": ["supplier_name"]},
            "line_items_count": 1,
        },
        "hybrid_debug": {"invoked": True},
    }

    rows = bench.build_hybrid_error_taxonomy_rows([artifact], [])

    assert any(row["category"] == "PARTY" and row["error_type"] == "supplier missing" for row in rows)


def test_trigger_analysis_recommends_party_advisory() -> None:
    artifacts = [{
        "status": "success",
        "document_id": "doc1",
        "prompt_version": "hybrid_prompt_v3",
        "trigger_reasons": ["missing_supplier_name"],
    }]
    corrections = [{
        "document_id": "doc1",
        "prompt_version": "hybrid_prompt_v3",
        "field": "supplier",
        "accepted_by_gate": True,
        "classification": "Unsupported correction",
    }]

    rows = bench.build_hybrid_trigger_analysis_rows(artifacts, corrections)

    assert rows[0]["category"] == "PARTY"
    assert rows[0]["recommendation"] == "keep enabled as advisory"


def test_roi_recommendation_is_narrow_when_ground_truth_missing() -> None:
    quality = {"all_documents_verified_complete": False}

    recommendation = bench.deployment_recommendation([], quality)

    assert recommendation["choice"].startswith("A)")


def test_phase_25_reports_are_generated(tmp_path: Path) -> None:
    artifact = {
        "status": "success",
        "document_id": "doc1",
        "dataset": "test",
        "filename": "doc1.png",
        "prompt_version": "hybrid_prompt_v3",
        "trigger_reasons": ["missing_supplier_name"],
        "proposals": [{"field": "supplier"}],
        "accepted_proposals": [{"proposal": {"field": "supplier", "proposed_value": "ACME SARL"}}],
        "rejected_proposals": [],
        "parsed_response": {"document_decision": "propose_corrections"},
        "parser_status": "parsed",
        "prompt_stats": {"prompt_characters": 1200, "output_tokens_estimated": 80},
        "label": {},
        "deterministic_result": {
            "fields": {"supplier_name": None},
            "line_items_count": 1,
            "validation_status": "needs_review",
            "erp_readiness": {"missing_fields": ["supplier_name"], "ready": False},
        },
        "final_selected_result": {
            "fields": {"supplier_name": None},
            "line_items_count": 1,
            "validation_status": "needs_review",
            "erp_readiness": {"missing_fields": ["supplier_name"], "ready": False},
        },
        "hybrid_debug": {"invoked": True, "metrics": {"duration_seconds": 2.5}},
    }
    path = bench.artifact_path(tmp_path, "hybrid_prompt_v3", "doc1.png")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")

    bench.generate_reports(tmp_path, ("hybrid_prompt_v3",))

    assert (tmp_path / "hybrid_error_taxonomy.csv").exists()
    assert (tmp_path / "hybrid_trigger_analysis.csv").exists()
    assert (tmp_path / "hybrid_latency_breakdown.csv").exists()
    assert (tmp_path / "hybrid_roi_report.md").exists()
    assert (tmp_path / "hybrid_deployment_recommendation.md").exists()


def test_configure_hybrid_keeps_auto_apply_disabled(tmp_path: Path) -> None:
    args = type("Args", (), {
        "mode": "validated-apply",
        "model": "qwen2.5-coder:7b",
        "endpoint": "http://localhost:11434/api/generate",
        "timeout": 3.0,
    })()
    snapshot = bench.snapshot_settings()
    try:
        bench.configure_hybrid(args, "hybrid_prompt_v2", tmp_path)

        assert bench.settings.enable_llm_resolver is True
        assert bench.settings.llm_resolver_mode == "validated_apply"
        assert bench.settings.llm_resolver_auto_apply_safe_corrections is False
        assert bench.settings.llm_resolver_prompt_version == "hybrid_prompt_v2"
    finally:
        bench.restore_settings(snapshot)


def make_document(tmp_path: Path, filename: str) -> BenchmarkDocument:
    image = tmp_path / filename
    image.write_bytes(b"fake")
    label = tmp_path / f"{Path(filename).stem}.json"
    label.write_text("{}", encoding="utf-8")
    return BenchmarkDocument(
        filename=filename,
        image_path=image,
        label_path=label,
        source_path=image,
        dataset="test",
    )
