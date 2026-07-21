from __future__ import annotations

import socket
from datetime import date
from types import SimpleNamespace

import pytest

from app.core.schemas import ExtractedInvoiceFields, LineItem, ValidationResult
from app.services.llm_prompt_builder import build_llm_payload
from app.services.llm_response_parser import LLMResponseParseError, parse_llm_response
from app.services.llm_resolver import LLMResolverError, resolve_with_llm
from app.services.llm_router import resolve_if_needed


class MockClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_parser_accepts_strict_json_response() -> None:
    result = parse_llm_response('{"supplier":"ACME SARL","customer":"North Clinic","corrected_rows":[],"confidence":0.82,"reasoning":"top candidates"}')

    assert result.supplier == "ACME SARL"
    assert result.customer == "North Clinic"
    assert result.confidence == 0.82


def test_parser_extracts_json_from_wrapped_text() -> None:
    result = parse_llm_response('Here is JSON:\n{"supplier": null, "customer": "Client A", "corrected_rows": [], "confidence": 2, "reasoning": "candidate"}')

    assert result.customer == "Client A"
    assert result.confidence == 1.0


def test_parser_rejects_malformed_json() -> None:
    with pytest.raises(LLMResponseParseError):
        parse_llm_response("not json")


def test_prompt_payload_never_includes_raw_ocr_text() -> None:
    response = sample_response()
    payload = build_llm_payload(response)
    serialized = str(payload)

    assert "secret raw OCR text" not in serialized
    assert payload["supplier_candidates"][0]["value"] == "ACME SARL"
    assert "totals" in payload


def test_router_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_router.settings.enable_llm_resolver", False)

    result = resolve_if_needed(sample_response(validation_status="invalid", confidence=0.1), client=MockClient("{}"))

    assert result.invoked is False
    assert result.skipped_reason == "disabled"


def test_router_skips_high_confidence_valid_document(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_router.settings.enable_llm_resolver", True)
    monkeypatch.setattr("app.services.llm_router.settings.llm_resolver_confidence_threshold", 0.78)

    result = resolve_if_needed(sample_response(validation_status="valid", confidence=0.95), client=MockClient("{}"))

    assert result.invoked is False
    assert result.skipped_reason == "high_confidence_deterministic_result"


def test_router_invokes_for_low_confidence_document(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_router.settings.enable_llm_resolver", True)
    client = MockClient('{"supplier":"ACME SARL","customer":"North Clinic","corrected_rows":[],"confidence":0.73,"reasoning":"candidate scores"}')

    result = resolve_if_needed(sample_response(validation_status="needs_review", confidence=0.4), client=client)

    assert result.invoked is True
    assert result.resolution is not None
    assert result.resolution.supplier == "ACME SARL"
    assert result.metrics.success is True
    assert client.prompts
    assert "secret raw OCR text" not in client.prompts[0]


def test_resolver_wraps_ollama_timeout() -> None:
    with pytest.raises(LLMResolverError):
        resolve_with_llm({}, client=MockClient(socket.timeout("timed out")))


def test_router_records_malformed_json_failure(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_router.settings.enable_llm_resolver", True)

    result = resolve_if_needed(sample_response(validation_status="invalid", confidence=0.2), client=MockClient("bad json"))

    assert result.invoked is True
    assert result.resolution is None
    assert result.metrics.success is False
    assert result.error and "parse failed" in result.error


def sample_response(*, validation_status: str = "needs_review", confidence: float = 0.5):
    fields = ExtractedInvoiceFields(
        supplier_name=None,
        customer_name=None,
        invoice_number="INV-1",
        invoice_date=date(2026, 7, 21),
        amount_ttc=120.0,
        currency="TND",
        line_items=[LineItem(description="Item A", quantity=1, unit_price=100, total=100)],
    )
    return SimpleNamespace(
        extracted_text="secret raw OCR text",
        detected_fields=fields,
        validation=ValidationResult(status=validation_status, is_valid=validation_status == "valid", warnings=["needs review"]),
        confidence_breakdown={"overall_confidence": confidence},
        erp_readiness={"ready": validation_status == "valid", "missing_fields": ["supplier_name"]},
        financial_reasoning={"financial_consistency_score": 0.4},
        line_items_validated=[],
        line_items_needs_review=fields.line_items,
        document_classification=SimpleNamespace(document_type="invoice"),
        extraction_debug={
            "party_resolver": {
                "supplier_candidates": [{"value": "ACME SARL", "score": 0.7, "score_breakdown": {"role_label": 0.2}}],
                "customer_candidates": [{"value": "North Clinic", "score": 0.6}],
            },
            "table_extraction_debug": {"counts": {"needs_review_rows": 1}},
        },
    )
