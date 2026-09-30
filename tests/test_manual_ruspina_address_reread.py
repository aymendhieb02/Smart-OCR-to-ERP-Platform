import json
import re
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.schemas import BoundingBox, OCRLine, RuspinaAddressRereadContext
from app.main import app
from app.services import correction_store
from app.services.manual_field_reread import reread_ruspina_address


def synthetic_context(**overrides):
    payload = {
        "semantic_group": "page2",
        "document_family": "ruspina_reinvoice_v1",
        "field": "address",
        "page": 2,
        "current_value": "OLD ADDRESS TEST",
        "current_confidence": 0.62,
        "current_source": "synthetic full-page OCR",
        "field_bbox": {"x1": 48, "y1": 198, "x2": 360, "y2": 232},
        "page_width": 1000,
        "page_height": 1400,
        "ocr_blocks": [{
            "text": "Address:", "confidence": 0.91, "page_number": 2,
            "bbox": {"x1": 50, "y1": 202, "x2": 140, "y2": 226},
            "page_width": 1000, "page_height": 1400, "coordinate_space": "original_page",
        }],
    }
    payload.update(overrides)
    return payload


class OneRegionalCallOnly:
    def __init__(self, *, fail=False):
        self.regional_calls = []
        self.full_page_calls = 0
        self.fail = fail

    def run(self, *_args, **_kwargs):
        self.full_page_calls += 1
        raise AssertionError("Full-page OCR must never be invoked by address reread")

    def run_targeted_region(self, image, region, *, page_number):
        self.regional_calls.append((image, region, page_number))
        if self.fail:
            raise RuntimeError("synthetic regional OCR failure")
        return [OCRLine(
            text="12 RUE TEST", confidence=0.94, page_number=page_number,
            bbox=BoundingBox(x1=160, y1=203, x2=350, y2=227),
            page_width=1000, page_height=1400, coordinate_space="original_page",
            source="regional_fallback",
        )]


def test_ruspina_address_reread_uses_one_small_regional_crop_and_is_proposal_only():
    context = RuspinaAddressRereadContext.model_validate(synthetic_context())
    image = np.zeros((1400, 1000, 3), dtype=np.uint8)
    engine = OneRegionalCallOnly()

    response = reread_ruspina_address(image, context, engine)

    assert len(engine.regional_calls) == 1
    assert engine.full_page_calls == 0
    assert engine.regional_calls[0][1].name == "ruspina_address"
    assert engine.regional_calls[0][1].image.shape[0] < image.shape[0] * 0.08
    assert response.reread_value == "12 RUE TEST"
    assert response.current_value == "OLD ADDRESS TEST"
    assert response.reread_performed is True
    assert response.regional_ocr_calls == 1
    assert response.full_page_ocr_calls == 0
    assert response.current_value == context.current_value
    assert not hasattr(response, "payment") and not hasattr(response, "line_items")


@pytest.mark.parametrize("overrides", [
    {"semantic_group": "page1"},
    {"semantic_group": "page3", "document_family": "customs_tradenet_v1"},
    {"field": "payment"},
])
def test_reread_service_rejects_out_of_scope_context_before_ocr(overrides):
    context = RuspinaAddressRereadContext.model_validate(synthetic_context(**overrides))
    engine = OneRegionalCallOnly()
    with pytest.raises(ValueError):
        reread_ruspina_address(np.zeros((1400, 1000, 3), dtype=np.uint8), context, engine)
    assert engine.regional_calls == []
    assert engine.full_page_calls == 0


def test_reread_without_address_label_fails_before_ocr():
    payload = synthetic_context(ocr_blocks=[])
    context = RuspinaAddressRereadContext.model_validate(payload)
    engine = OneRegionalCallOnly()
    with pytest.raises(ValueError, match="Address label evidence"):
        reread_ruspina_address(np.zeros((1400, 1000, 3), dtype=np.uint8), context, engine)
    assert engine.regional_calls == []


def test_reread_api_runs_only_targeted_ocr_and_returns_non_destructive_candidate(monkeypatch, tmp_path):
    from app.api import routes

    upload_path = tmp_path / "synthetic-source.pdf"
    upload_path.write_bytes(b"synthetic upload fixture")
    image = np.zeros((1400, 1000, 3), dtype=np.uint8)
    engine = OneRegionalCallOnly()

    async def use_fixture(_upload):
        return upload_path

    monkeypatch.setattr(routes, "save_upload_to_temp", use_fixture)
    monkeypatch.setattr(routes, "load_document_page", lambda _path, page: image)
    monkeypatch.setattr(routes, "ocr_engine", engine)
    response = TestClient(app).post(
        "/review/ruspina-address/reread",
        files={"file": ("synthetic.pdf", b"synthetic upload fixture", "application/pdf")},
        data={"context_json": json.dumps(synthetic_context())},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_value"] == "OLD ADDRESS TEST"
    assert body["reread_value"] == "12 RUE TEST"
    assert body["regional_ocr_calls"] == 1 and body["full_page_ocr_calls"] == 0
    assert len(engine.regional_calls) == 1 and engine.full_page_calls == 0
    assert "payment" not in body and "line_items" not in body
    assert not upload_path.exists()


def test_reread_api_rejects_page1_and_page3_without_calling_ocr(monkeypatch, tmp_path):
    from app.api import routes

    upload_path = tmp_path / "synthetic-source.pdf"
    upload_path.write_bytes(b"synthetic upload fixture")
    engine = OneRegionalCallOnly()

    async def use_fixture(_upload):
        return upload_path

    monkeypatch.setattr(routes, "save_upload_to_temp", use_fixture)
    monkeypatch.setattr(routes, "ocr_engine", engine)
    client = TestClient(app)
    for overrides in ({"semantic_group": "page1"}, {"semantic_group": "page3"}):
        response = client.post(
            "/review/ruspina-address/reread",
            files={"file": ("synthetic.pdf", b"synthetic upload fixture", "application/pdf")},
            data={"context_json": json.dumps(synthetic_context(**overrides))},
        )
        assert response.status_code == 422
    assert engine.regional_calls == [] and engine.full_page_calls == 0


def test_reread_ocr_failure_returns_safe_error_without_touching_corrections(monkeypatch, tmp_path):
    from app.api import routes

    upload_path = tmp_path / "synthetic-source.pdf"
    upload_path.write_bytes(b"synthetic upload fixture")
    async def use_fixture(_upload):
        return upload_path

    monkeypatch.setattr(routes, "save_upload_to_temp", use_fixture)
    monkeypatch.setattr(routes, "load_document_page", lambda _path, _page: np.zeros((1400, 1000, 3), dtype=np.uint8))
    monkeypatch.setattr(routes, "ocr_engine", OneRegionalCallOnly(fail=True))
    response = TestClient(app).post(
        "/review/ruspina-address/reread",
        files={"file": ("synthetic.pdf", b"synthetic upload fixture", "application/pdf")},
        data={"context_json": json.dumps(synthetic_context())},
    )
    assert response.status_code == 502
    assert "current value was not changed" in response.json()["detail"]


def test_accepting_targeted_reread_uses_existing_review_correction_path(monkeypatch, tmp_path):
    directory = tmp_path / "corrections"
    monkeypatch.setattr(correction_store, "CORRECTION_DIR", directory)
    monkeypatch.setattr(correction_store, "CORRECTION_FILE", directory / "corrections.jsonl")
    response = TestClient(app).post("/review/validate-corrections", json={
        "document_id": "synthetic-ruspina-reread",
        "source_file": "synthetic-source.pdf",
        "document_family": "ruspina_reinvoice_v1",
        "detected_fields": {"invoice_number": "INV-TEST-001"},
        "field_corrections": {"address": {
            "value": "12 RUE TEST", "original_value": "OLD ADDRESS TEST",
            "source": "targeted OCR accepted by reviewer", "page": 2,
            "confidence": 0.94, "user_action": "accepted",
        }},
        "original_payload": {"expanded_fields": {"address": {
            "value": "OLD ADDRESS TEST", "display_value": "OLD ADDRESS TEST",
            "machine_value": "OLD ADDRESS TEST", "page": 2, "confidence": 0.62,
            "source": "synthetic full-page OCR",
            "bbox": {"x1": 48, "y1": 198, "x2": 360, "y2": 232},
        }}},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expanded_field_overrides"]["address"]["value"] == "12 RUE TEST"
    assert body["expanded_field_overrides"]["address"]["source"] == "targeted OCR accepted by reviewer"
    saved = correction_store.load_review_field_corrections("synthetic-ruspina-reread", "ruspina_reinvoice_v1")
    assert saved["address"]["corrected_value"] == "12 RUE TEST"
    assert saved["address"]["source"] == "targeted OCR accepted by reviewer"
    assert saved["address"]["user_action"] == "accepted"


def test_frontend_action_is_page2_only_and_keep_current_does_not_edit_field():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    strings = Path("app/static/strings.js").read_text(encoding="utf-8")
    assert 'presentation.key === "ruspina_reinvoice_v1"' in script
    assert 'getSelectedLogicalDocument()?.semantic_group === "page2"' in script
    assert 'fetch("/review/ruspina-address/reread"' in script
    assert 'correctedFields.address.user_action = "accepted"' in script
    keep_handler = re.search(r'keep\.addEventListener\("click", \(\) => panel\.remove\(\)\)', script)
    assert keep_handler
    assert '"review.reread_address": "Relire l’adresse"' in strings
    assert '"review.reread_accept": "Utiliser cette valeur"' in strings
