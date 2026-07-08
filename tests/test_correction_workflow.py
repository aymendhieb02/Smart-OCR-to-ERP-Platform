from datetime import date

from app.core.schemas import Candidate, CorrectionItem, CorrectionSubmission, ExtractedInvoiceFields, LineItem
from app.services import correction_store
from app.services.correction_store import boost_candidates_from_memory, submit_corrections


def configure_temp_store(monkeypatch, tmp_path):
    correction_dir = tmp_path / "corrections"
    correction_file = correction_dir / "corrections.jsonl"
    monkeypatch.setattr(correction_store, "CORRECTION_DIR", correction_dir)
    monkeypatch.setattr(correction_store, "CORRECTION_FILE", correction_file)
    return correction_file


def base_fields(**updates):
    fields = ExtractedInvoiceFields(
        supplier_name="acct_1N8CpQGmFzaQxIilDx",
        invoice_number="INV-1",
        invoice_date=date(2026, 5, 6),
        currency="USD",
        amount_ht=100,
        tva_amount=20,
        amount_ttc=120,
        tax_rate=20,
    )
    return fields.model_copy(update=updates)


def test_corrected_supplier_replaces_rejected_supplier_and_is_stored(monkeypatch, tmp_path):
    correction_file = configure_temp_store(monkeypatch, tmp_path)
    payload = CorrectionSubmission(
        document_id="doc-1",
        source_file="invoice.png",
        detected_fields=base_fields(),
        corrected_fields={"supplier_name": "Gardel Metal Inc."},
    )

    response = submit_corrections(payload)

    assert response.corrected_fields.supplier_name == "Gardel Metal Inc."
    assert response.validated_erp_json["supplier"]["name"] == "Gardel Metal Inc."
    assert response.stored_count == 1
    assert "Gardel Metal Inc." in correction_file.read_text(encoding="utf-8")


def test_corrected_totals_recompute_validation(monkeypatch, tmp_path):
    configure_temp_store(monkeypatch, tmp_path)
    payload = CorrectionSubmission(
        document_id="doc-2",
        source_file="invoice.png",
        detected_fields=base_fields(amount_ttc=999),
        corrected_fields={"amount_ttc": 120},
    )

    response = submit_corrections(payload)

    assert response.corrected_fields.amount_ttc == 120
    assert response.validation.errors == []
    assert response.validated_erp_json["amounts"]["ttc"] == 120


def test_corrected_line_item_updates_totals(monkeypatch, tmp_path):
    configure_temp_store(monkeypatch, tmp_path)
    item = LineItem(description="Service A", quantity=2, unit_price=10, line_total_ht=20, tax_amount=4, line_total_ttc=24, total=24)
    payload = CorrectionSubmission(
        document_id="doc-3",
        source_file="invoice.png",
        detected_fields=base_fields(amount_ht=None, tva_amount=None, amount_ttc=None, tax_rate=None),
        corrected_line_items=[item],
    )

    response = submit_corrections(payload)

    assert response.corrected_fields.amount_ht == 20
    assert response.corrected_fields.tva_amount == 4
    assert response.corrected_fields.amount_ttc == 24
    assert response.validated_erp_json["line_items"][0]["description"] == "Service A"


def test_accepted_candidate_becomes_final_erp_value(monkeypatch, tmp_path):
    configure_temp_store(monkeypatch, tmp_path)
    payload = CorrectionSubmission(
        document_id="doc-4",
        source_file="invoice.png",
        detected_fields=base_fields(customer_name=None),
        corrections=[CorrectionItem(field_name="customer_name", original_value="Quantity", corrected_value="ACME Clinic", user_action="accepted", correction_type="customer")],
    )

    response = submit_corrections(payload)

    assert response.corrected_fields.customer_name == "ACME Clinic"
    assert response.validated_erp_json["customer"]["name"] == "ACME Clinic"


def test_correction_memory_boosts_future_candidates(monkeypatch, tmp_path):
    configure_temp_store(monkeypatch, tmp_path)
    submit_corrections(CorrectionSubmission(
        document_id="doc-5",
        source_file="invoice.png",
        detected_fields=base_fields(supplier_name=None),
        corrected_fields={"supplier_name": "Known Supplier LLC"},
    ))
    candidates = {
        "supplier_name": [Candidate(field="supplier_name", value="Known Supplier LLC", score=0.5, source="layout")]
    }

    boost_candidates_from_memory(candidates, "Known Supplier LLC Invoice")

    assert candidates["supplier_name"][0].score > 0.5
    assert "correction memory" in candidates["supplier_name"][0].source
