from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.core.schemas import BoundingBox, ExtractedInvoiceFields, LayoutBlock, LineItem, OCRLine, ValidationResult
from app.services.llm_correction_applier import build_hybrid_candidate
from app.services.llm_correction_gate import review_llm_corrections
from app.services.llm_evidence_builder import build_evidence_package
from app.services.llm_response_parser import parse_llm_response
from app.services.llm_router import resolve_if_needed


class MockClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        assert "full secret dump" not in prompt
        return self.response


def test_strict_proposal_parser_requires_evidence_refs() -> None:
    parsed = parse_llm_response(
        '{"document_decision":"propose_corrections","proposals":[{"field":"supplier","operation":"fill_missing","old_value":null,"proposed_value":"ABC SARL","confidence":0.92,"reason":"VAT evidence","evidence_refs":["line:page1_line_0"]}],"unresolved_fields":[],"overall_confidence":0.91}'
    )

    assert parsed.document_decision == "propose_corrections"
    assert parsed.supplier == "ABC SARL"
    assert parsed.proposals[0].evidence_refs == ["line:page1_line_0"]


def test_gate_accepts_evidence_grounded_missing_supplier(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_correction_gate.settings.llm_resolver_acceptance_threshold", 0.85)
    response = sample_response()
    evidence = build_evidence_package(response, ["missing_supplier_name"])
    ref = next(item.ref for item in evidence.evidence_items if "ABC SARL" in item.text)
    decision = parse_llm_response(
        f'{{"document_decision":"propose_corrections","proposals":[{{"field":"supplier","operation":"fill_missing","old_value":null,"proposed_value":"ABC SARL","confidence":0.94,"reason":"Header and VAT evidence support it","evidence_refs":["{ref}"]}}],"unresolved_fields":[],"overall_confidence":0.94}}'
    )

    accepted, rejected = review_llm_corrections(response, decision, evidence)

    assert len(accepted) == 1
    assert not rejected


def test_gate_rejects_hallucinated_supplier_without_evidence(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_correction_gate.settings.llm_resolver_acceptance_threshold", 0.85)
    response = sample_response()
    evidence = build_evidence_package(response, ["missing_supplier_name"])
    ref = evidence.evidence_items[0].ref
    decision = parse_llm_response(
        f'{{"document_decision":"propose_corrections","proposals":[{{"field":"supplier","operation":"fill_missing","old_value":null,"proposed_value":"Imaginary Company","confidence":0.99,"reason":"looks likely","evidence_refs":["{ref}"]}}],"unresolved_fields":[],"overall_confidence":0.99}}'
    )

    accepted, rejected = review_llm_corrections(response, decision, evidence)

    assert not accepted
    assert rejected[0].checks["proposed_value_present"] is False


def test_high_confidence_field_is_protected(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_correction_gate.settings.llm_resolver_acceptance_threshold", 0.85)
    response = sample_response(supplier_name="ABC SARL")
    response.field_confidences = {"supplier_name": 0.96}
    evidence = build_evidence_package(response, ["low_overall_confidence"])
    ref = next(item.ref for item in evidence.evidence_items if "ABC SARL" in item.text)
    decision = parse_llm_response(
        f'{{"document_decision":"propose_corrections","proposals":[{{"field":"supplier","operation":"replace","old_value":"ABC SARL","proposed_value":"ABC SARL","confidence":0.90,"reason":"same visible supplier","evidence_refs":["{ref}"]}}],"unresolved_fields":[],"overall_confidence":0.90}}'
    )

    accepted, rejected = review_llm_corrections(response, decision, evidence)

    assert not accepted
    assert rejected[0].checks["high_confidence_protected"] is False


def test_hybrid_candidate_improves_missing_supplier_without_mutating_original(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm_correction_gate.settings.llm_resolver_acceptance_threshold", 0.85)
    response = sample_response()
    evidence = build_evidence_package(response, ["missing_supplier_name"])
    ref = next(item.ref for item in evidence.evidence_items if "ABC SARL" in item.text)
    decision = parse_llm_response(
        f'{{"document_decision":"propose_corrections","proposals":[{{"field":"supplier","operation":"fill_missing","old_value":null,"proposed_value":"ABC SARL","confidence":0.94,"reason":"Header evidence","evidence_refs":["{ref}"]}}],"unresolved_fields":[],"overall_confidence":0.94}}'
    )
    accepted, _ = review_llm_corrections(response, decision, evidence)

    candidate = build_hybrid_candidate(response, accepted)

    assert response.detected_fields.supplier_name is None
    assert candidate["fields"].supplier_name == "ABC SARL"
    assert candidate["improves_safely"] is True


def test_router_advisory_mode_records_accepted_but_keeps_deterministic(monkeypatch, tmp_path) -> None:
    response = sample_response()
    evidence = build_evidence_package(response, ["missing_supplier_name"])
    ref = next(item.ref for item in evidence.evidence_items if "ABC SARL" in item.text)
    raw = f'{{"document_decision":"propose_corrections","proposals":[{{"field":"supplier","operation":"fill_missing","old_value":null,"proposed_value":"ABC SARL","confidence":0.94,"reason":"Header evidence","evidence_refs":["{ref}"]}}],"unresolved_fields":[],"overall_confidence":0.94}}'
    monkeypatch.setattr("app.services.llm_router.settings.enable_llm_resolver", True)
    monkeypatch.setattr("app.services.llm_router.settings.llm_resolver_mode", "advisory")
    monkeypatch.setattr("app.services.llm_router.settings.llm_resolver_cache_dir", tmp_path)

    route = resolve_if_needed(response, client=MockClient(raw))

    assert route.invoked is True
    assert route.accepted_corrections
    assert route.final_response is None
    assert route.final_source == "deterministic"
    assert route.fallback_reason == "safe_corrections_available_but_auto_apply_disabled"


def test_router_cache_reuses_same_fingerprint(monkeypatch, tmp_path) -> None:
    response = sample_response()
    evidence = build_evidence_package(response, ["missing_supplier_name"])
    ref = next(item.ref for item in evidence.evidence_items if "ABC SARL" in item.text)
    raw = f'{{"document_decision":"propose_corrections","proposals":[{{"field":"supplier","operation":"fill_missing","old_value":null,"proposed_value":"ABC SARL","confidence":0.94,"reason":"Header evidence","evidence_refs":["{ref}"]}}],"unresolved_fields":[],"overall_confidence":0.94}}'
    client = MockClient(raw)
    monkeypatch.setattr("app.services.llm_router.settings.enable_llm_resolver", True)
    monkeypatch.setattr("app.services.llm_router.settings.llm_resolver_mode", "advisory")
    monkeypatch.setattr("app.services.llm_router.settings.llm_resolver_cache_dir", tmp_path)

    first = resolve_if_needed(response, client=client)
    second = resolve_if_needed(response, client=client)

    assert first.cache_source == "fresh"
    assert second.cache_source == "disk"
    assert client.calls == 1


def sample_response(*, supplier_name: str | None = None):
    fields = ExtractedInvoiceFields(
        supplier_name=supplier_name,
        customer_name="Client SARL",
        invoice_number="INV-1",
        invoice_date=date(2026, 7, 21),
        amount_ht=100,
        tva_amount=19,
        amount_ttc=119,
        tax_rate=19,
        currency="TND",
        line_items=[LineItem(description="Service A", quantity=1, unit_price=100, total=100, confidence=0.9)],
    )
    line = OCRLine(
        text="ABC SARL MF 1234567A/M/000 Email contact@abc.tn",
        confidence=0.97,
        page_number=1,
        line_index=0,
        bbox=BoundingBox(x1=10, y1=10, x2=300, y2=30),
    )
    return SimpleNamespace(
        extracted_text="full secret dump",
        detected_fields=fields,
        validation=ValidationResult(status="needs_review", is_valid=False, warnings=["Supplier identity could not be detected"]),
        confidence_breakdown={"overall_confidence": 0.55},
        field_confidences={},
        erp_readiness={"ready": False, "erp_ready_status": "Needs Review", "missing_fields": ["supplier_name"]},
        financial_reasoning={"financial_consistency_score": 0.95, "financially_consistent": True, "financial_errors": [], "financial_warnings": []},
        line_items_validated=fields.line_items,
        line_items_needs_review=[],
        all_line_items=fields.line_items,
        document_classification=SimpleNamespace(document_type="invoice"),
        layout_blocks=[
            LayoutBlock(
                block_type="supplier",
                text="ABC SARL MF 1234567A/M/000 Email contact@abc.tn",
                confidence=0.94,
                bbox=BoundingBox(x1=10, y1=10, x2=300, y2=80),
                page=1,
            )
        ],
        ocr_blocks=[line],
        all_ocr_blocks=[line],
        extraction_debug={"party_resolver": {"supplier_candidates": [{"value": "ABC SARL", "score": 0.92}]}},
    )
