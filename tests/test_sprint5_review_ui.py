from datetime import date

from fastapi.testclient import TestClient

from app.core.schemas import ExtractedInvoiceFields
from app.main import app
from app.services import correction_store


def configure_temp_store(monkeypatch, tmp_path):
    correction_dir = tmp_path / "corrections"
    correction_file = correction_dir / "corrections.jsonl"
    monkeypatch.setattr(correction_store, "CORRECTION_DIR", correction_dir)
    monkeypatch.setattr(correction_store, "CORRECTION_FILE", correction_file)
    return correction_file


def test_review_correction_endpoint_preserves_original_and_revalidates(monkeypatch, tmp_path):
    correction_file = configure_temp_store(monkeypatch, tmp_path)
    client = TestClient(app)
    fields = ExtractedInvoiceFields(
        supplier_name="Vital Distribution",
        customer_name="Pharma Plus",
        invoice_number="FAC-1",
        invoice_date=date(2026, 5, 6),
        currency="TND",
        amount_ht=100,
        tva_amount=19,
        amount_ttc=None,
        tax_rate=19,
    )

    response = client.post("/review/validate-corrections", json={
        "document_id": "doc-sprint5",
        "source_file": "invoice.png",
        "detected_fields": fields.model_dump(mode="json"),
        "field_corrections": {
            "amount_ttc": {
                "value": 119,
                "original_value": None,
                "source": "human",
                "bbox": {"x1": 10, "y1": 20, "x2": 80, "y2": 40},
                "page": 1,
                "confidence": 0.71,
            }
        },
        "line_item_corrections": [
            {
                "description": "Service A",
                "quantity": 2,
                "unit_price": 50,
                "line_total_ht": 100,
                "tax_amount": 19,
                "line_total_ttc": 119,
                "tax_rate": 19,
                "source": "human correction",
            }
        ],
        "original_payload": {
            "expanded_fields": {
                "amount_ttc": {
                    "value": None,
                    "bbox": {"x1": 10, "y1": 20, "x2": 80, "y2": 40},
                    "page": 1,
                    "confidence": 0.71,
                    "source": "totals block",
                }
            },
            "confidence_breakdown": {
                "ocr_confidence": 0.9,
                "layout_confidence": 0.9,
                "table_confidence": 0.9,
                "field_confidence": 0.9,
            },
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["corrected_fields"]["amount_ttc"] == 119
    assert payload["original_evidence"]["amount_ttc"]["source"] == "totals block"
    assert payload["erp_readiness"]["erp_ready_status"] == "ERP Ready"
    assert payload["erp_export_allowed"] is True
    assert payload["invoice_validation_report"]["summary"]["erp_ready"] is True
    assert "amount_ttc" in correction_file.read_text(encoding="utf-8")


def test_review_endpoint_keeps_export_disabled_with_blockers(monkeypatch, tmp_path):
    configure_temp_store(monkeypatch, tmp_path)
    client = TestClient(app)
    fields = ExtractedInvoiceFields(
        supplier_name="Vital Distribution",
        invoice_number="FAC-2",
        invoice_date=date(2026, 5, 6),
        currency="TND",
        amount_ht=100,
        tva_amount=19,
        amount_ttc=None,
        tax_rate=19,
    )

    response = client.post("/review/validate-corrections", json={
        "document_id": "doc-needs-review",
        "detected_fields": fields.model_dump(mode="json"),
        "field_corrections": {},
        "line_item_corrections": [],
        "original_payload": {},
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["erp_readiness"]["erp_ready_status"] == "Needs Review"
    assert "customer_name" in payload["erp_readiness"]["missing_fields"]
    assert payload["erp_export_allowed"] is False


def test_static_review_ui_contains_sprint5_workspace_controls():
    index = (app.root_path or "")
    html = open("app/static/index.html", encoding="utf-8").read()
    script = open("app/static/app.js", encoding="utf-8").read()

    assert index == ""
    assert "review workspace" in html.lower()
    assert "toggleRows" in html
    assert "financial_checks" in html
    assert "correction_suggestions" in html
    assert "duplicate_fraud" in html
    assert "erpReadinessPanel" in html
    assert "/review/validate-corrections" in script
    assert "setPreviewPage" in script
    assert "getLineItemOverlayRows" in script
    assert "renderFinancialChecks" in script
    assert "renderDuplicateAndFraud" in script
