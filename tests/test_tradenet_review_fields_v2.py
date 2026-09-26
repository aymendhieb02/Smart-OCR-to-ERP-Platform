import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.schemas import FieldExtractionDetail
from app.main import app
from app.services import correction_store
from app.services.pipeline_runner import _apply_tradenet_corrections


CUSTOMS_FIELDS = [
    "declaration_number", "declaration_date", "declaration_type", "exporter", "importer",
    "ptfn_amount", "currency_conversion_rate", "customs_total_value_tnd",
]


@pytest.fixture
def correction_file(monkeypatch, tmp_path):
    directory = tmp_path / "corrections"
    monkeypatch.setattr(correction_store, "CORRECTION_DIR", directory)
    monkeypatch.setattr(correction_store, "CORRECTION_FILE", directory / "corrections.jsonl")
    return directory / "corrections.jsonl"


def test_customs_review_configuration_is_exact_and_every_key_is_editable():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    strings = Path("app/static/strings.js").read_text(encoding="utf-8")
    match = re.search(r"const CUSTOMS_TRADENET_REVIEW_FIELDS = Object\.freeze\((\[[\s\S]*?\])\);", script)
    assert match
    configured = re.findall(r'"([a-z_]+)"', match.group(1))
    assert configured == CUSTOMS_FIELDS
    assert 'customs_tradenet_v1: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS' in script
    assert 'customs_douanes_tunisiennes_v1: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS' in script
    assert 'customs_declaration: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS' in script
    assert 'presentation.key === "customs_declaration" ? presentation.fields : null' in script
    assert "(!allowed || allowed.has(key))" in script
    editable = script.split("const EDITABLE_FIELDS = [", 1)[1].split("];", 1)[0]
    assert all(f'"{field}"' in editable for field in CUSTOMS_FIELDS)
    assert "EDITABLE_FIELDS.filter((field) => presentation.fields.includes(field))" in script
    assert 'input.addEventListener("input", () => updateReviewField(field, input.value))' in script
    assert "input.value = detail?.display_value ?? detail?.value ?? fields[field] ?? \"\"" in script
    assert 'fetch("/review/validate-corrections"' in script
    assert 'fetch("/review/reconcile-dossier"' in script

    excluded = (
        "supplier_name", "supplier_address", "supplier_tax_id", "customer_name", "customer_address",
        "customer_tax_id", "amount_ht", "tva_amount", "amount_ttc", "tax_rate", "purchase_order_number",
        "declaration_article_count", "declaration_code",
    )
    for field in excluded:
        assert f'"{field}"' not in match.group(1)
    for label in (
        "Numéro de déclaration", "Date de déclaration", "Type de déclaration", "Exportateur", "Importateur",
        "Cours de conversion de la devise", "Valeur douane totale (TND)",
    ):
        assert label in strings


def test_producer_and_ruspina_review_field_configurations_are_unchanged():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'ruspina_reinvoice_v1: { labelKey: "dossier.document_ruspina", fields: INVOICE_FIELD_GROUPS' in script
    assert 'commercial_invoice: { labelKey: "dossier.document_supplier_invoice", fields: INVOICE_FIELD_GROUPS' in script


@pytest.mark.parametrize("field", CUSTOMS_FIELDS)
@pytest.mark.parametrize("document_family", ["customs_tradenet_v1", "customs_douanes_tunisiennes_v1"])
def test_customs_field_correction_persists_for_its_logical_document_and_preserves_evidence(
    field, document_family, correction_file, tmp_path
):
    original_values = {name: f"OCR {name.upper()} TEST" for name in CUSTOMS_FIELDS}
    corrected_value = {
        "ptfn_amount": "52000.000",
        "currency_conversion_rate": "3.2842000",
        "customs_total_value_tnd": "170778.400",
    }.get(field, f"REVIEWED {field.upper()} TEST")
    original_payload = {"expanded_fields": {
        name: {
            "value": original_values[name],
            "display_value": original_values[name],
            "machine_value": original_values[name],
            "evidence_text": f"SOURCE OCR {name.upper()} TEST",
            "bbox": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
            "page": 3,
            "confidence": 0.88,
            "source": "synthetic OCR",
        }
        for name in CUSTOMS_FIELDS
    }}
    document_id = f"sha256:synthetic-{document_family}-logical-document-3"
    result = TestClient(app).post("/review/validate-corrections", json={
        "document_id": document_id,
        "source_file": "synthetic-dossier.pdf",
        "document_family": document_family,
        "detected_fields": {},
        "field_corrections": {field: {"value": corrected_value, "original_value": original_values[field]}},
        "original_payload": original_payload,
    })

    assert result.status_code == 200
    response = result.json()
    detail = response["expanded_field_overrides"][field]
    assert detail["value"] == detail["display_value"] == corrected_value
    assert detail["machine_value"] == original_values[field]
    assert detail["evidence_text"] == f"SOURCE OCR {field.upper()} TEST"
    assert detail["bbox"] == original_payload["expanded_fields"][field]["bbox"]
    assert detail["page"] == 3
    assert detail["confidence"] == 0.88
    assert detail["source"] == "synthetic OCR"

    saved = correction_store.load_tradenet_field_corrections(document_id)
    assert set(saved) == {field}
    assert saved[field]["corrected_value"] == corrected_value
    assert correction_store.load_tradenet_field_corrections("sha256:another-logical-document") == {}

    reloaded = SimpleNamespace(
        expanded_fields={name: FieldExtractionDetail(**detail) for name, detail in original_payload["expanded_fields"].items()},
        field_boxes=[],
        dynamic_tables=[],
    )
    _apply_tradenet_corrections(reloaded, saved)
    for name in CUSTOMS_FIELDS:
        expected = corrected_value if name == field else original_values[name]
        assert reloaded.expanded_fields[name].value == expected
    assert reloaded.expanded_fields[field].machine_value == original_values[field]
    assert reloaded.expanded_fields[field].evidence_text == f"SOURCE OCR {field.upper()} TEST"
    assert reloaded.expanded_fields[field].confidence == 0.88
    assert reloaded.expanded_fields[field].source == "synthetic OCR"
    assert reloaded.expanded_fields[field].page == 3
