import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.schemas import FieldExtractionDetail
from app.main import app
from app.services import correction_store
from app.services.pipeline_runner import _apply_family_review_corrections


RUSPINA_FIELDS = [
    "invoice_number", "invoice_date", "referenced_invoice", "client", "address", "currency", "total",
    "total_amount_words", "gross_weight", "net_weight", "number_of_bags", "delivery", "origin",
    "payment", "iban", "bank", "swift",
]


@pytest.fixture
def correction_file(monkeypatch, tmp_path):
    directory = tmp_path / "corrections"
    monkeypatch.setattr(correction_store, "CORRECTION_DIR", directory)
    monkeypatch.setattr(correction_store, "CORRECTION_FILE", directory / "corrections.jsonl")
    return directory / "corrections.jsonl"


def test_ruspina_review_configuration_is_exact_editable_and_keeps_table_separate():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    strings = Path("app/static/strings.js").read_text(encoding="utf-8")
    match = re.search(r"const RUSPINA_REVIEW_FIELDS = Object\.freeze\((\[[\s\S]*?\])\);", script)
    assert match
    configured = re.findall(r'"([a-z_]+)"', match.group(1))
    assert configured == RUSPINA_FIELDS
    assert 'ruspina_reinvoice_v1: { labelKey: "dossier.document_ruspina", fields: RUSPINA_REVIEW_FIELDS' in script
    assert 'showLineItems: true, allowCorrections: true' in script
    editable = script.split("const EDITABLE_FIELDS = [", 1)[1].split("];", 1)[0]
    assert all(f'"{field}"' in editable for field in RUSPINA_FIELDS)
    assert "EDITABLE_FIELDS.filter((field) => presentation.fields.includes(field))" in script
    assert "renderLineItems(lastResponse.detected_fields?.line_items || [], lastResponse.row_validation || [])" in script
    assert "detail.machine_value = detail.value" in script
    assert "if (!detail.source) detail.source = \"manual correction\"" in script
    assert "if (detail.confidence === null || detail.confidence === undefined) detail.confidence = 1" in script
    assert 'resolveDocumentPresentation().key === "ruspina_reinvoice_v1"' in script
    assert "Object.entries(data.expanded_field_overrides || {})" in script

    unrelated = {
        "supplier_name", "supplier_address", "supplier_tax_id", "supplier_phone", "supplier_email",
        "supplier_website", "supplier_bank_iban", "supplier_bank_rib", "supplier_bank_swift",
        "customer_name", "customer_address", "customer_tax_id", "customer_phone", "customer_email",
        "buyer", "customer", "seller", "client_tax_id", "due_date", "amount_ht", "total_ht", "tva_amount",
        "tax_amount", "amount_ttc", "total_ttc", "tax_rate", "purchase_order_number",
        "declaration_article_count", "declaration_code",
    }
    assert not unrelated.intersection(configured)
    labels = {
        "invoice_number": ("Numéro de facture", "Invoice number"),
        "invoice_date": ("Date de facture", "Invoice date"),
        "referenced_invoice": ("Facture de référence", "Referenced invoice"),
        "client": ("Client", "Client"), "address": ("Adresse", "Address"),
        "currency": ("Devise", "Currency"), "total": ("Montant total", "Total amount"),
        "total_amount_words": ("Montant total en lettres", "Total amount in words"),
        "gross_weight": ("Poids brut", "Gross weight"), "net_weight": ("Poids net", "Net weight"),
        "number_of_bags": ("Nombre de sacs", "Number of bags"), "delivery": ("Livraison", "Delivery"),
        "origin": ("Origine", "Origin"), "payment": ("Paiement", "Payment"),
        "iban": ("IBAN", "IBAN"), "bank": ("Banque", "Bank"), "swift": ("SWIFT", "SWIFT"),
    }
    for field, (french, english) in labels.items():
        assert f'"fields.{field}": "{french}"' in strings
        assert f'"fields.{field}": "{english}"' in strings


def test_producer_and_tradenet_review_configurations_remain_unchanged():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'commercial_invoice: { labelKey: "dossier.document_supplier_invoice", fields: INVOICE_FIELD_GROUPS' in script
    assert 'customs_tradenet_v1: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS' in script
    assert 'customs_douanes_tunisiennes_v1: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS' in script


@pytest.mark.parametrize("field", RUSPINA_FIELDS)
def test_ruspina_correction_saves_reloads_and_preserves_machine_evidence(field, correction_file, tmp_path):
    original = f"OCR_{field.upper()}_TEST"
    corrected = f"REVIEWED_{field.upper()}_TEST"
    if field in {"total", "gross_weight", "net_weight"}:
        original, corrected = "1000.000", "1250.500"
    elif field == "number_of_bags":
        original, corrected = "1000", "1250"
    elif field == "iban":
        original, corrected = "TN00TEST00000000000000000000", "TN00TEST00000000000000000001"
    original_payload = {
        "detected_fields": {"invoice_number": "INV-TEST-001"},
        "expanded_fields": {
            field: {
                "value": original, "display_value": original, "machine_value": original,
                "evidence_text": f"SOURCE OCR {field.upper()} TEST",
                "bbox": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
                "page": 2, "confidence": 0.87, "source": "synthetic RUSPINA OCR",
            }
        },
    }
    document_id = f"sha256:synthetic-ruspina-logical-document-{field}"
    response = TestClient(app).post("/review/validate-corrections", json={
        "document_id": document_id,
        "source_file": "synthetic-dossier.pdf",
        "document_family": "ruspina_reinvoice_v1",
        "detected_fields": {"invoice_number": "INV-TEST-001"},
        "field_corrections": {field: {"value": corrected, "original_value": original}},
        "original_payload": original_payload,
    })

    assert response.status_code == 200
    detail = response.json()["expanded_field_overrides"][field]
    assert detail["value"] == detail["display_value"] == corrected
    assert detail["machine_value"] == original
    if field in {"total", "gross_weight", "net_weight"}:
        assert detail["normalized_value"] == 1250.5
    elif field == "number_of_bags":
        assert detail["normalized_value"] == 1250
    else:
        assert detail["normalized_value"] == corrected
    assert detail["evidence_text"] == f"SOURCE OCR {field.upper()} TEST"
    assert detail["bbox"] == original_payload["expanded_fields"][field]["bbox"]
    assert detail["page"] == 2
    assert detail["confidence"] == 0.87
    assert detail["source"] == "synthetic RUSPINA OCR"

    saved = correction_store.load_review_field_corrections(document_id, "ruspina_reinvoice_v1")
    assert set(saved) == {field}
    assert saved[field]["corrected_value"] == corrected
    assert correction_store.load_review_field_corrections(document_id, "customs_tradenet_v1") == {}
    assert correction_store.load_review_field_corrections("sha256:another-ruspina-document", "ruspina_reinvoice_v1") == {}

    reloaded = SimpleNamespace(
        expanded_fields={field: FieldExtractionDetail(**original_payload["expanded_fields"][field])},
        field_boxes=[], dynamic_tables=[],
    )
    _apply_family_review_corrections(reloaded, saved, "ruspina_reinvoice_v1")
    detail = reloaded.expanded_fields[field]
    assert detail.value == detail.display_value == corrected
    assert detail.machine_value == original
    if field in {"total", "gross_weight", "net_weight"}:
        assert detail.normalized_value == 1250.5
    elif field == "number_of_bags":
        assert detail.normalized_value == 1250
    else:
        assert detail.normalized_value == corrected
    assert detail.evidence_text == f"SOURCE OCR {field.upper()} TEST"
    assert detail.page == 2 and detail.confidence == 0.87
    assert detail.source == "synthetic RUSPINA OCR"
