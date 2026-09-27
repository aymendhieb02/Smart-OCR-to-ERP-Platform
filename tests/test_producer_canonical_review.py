from types import SimpleNamespace

from app.core.schemas import ExtractedInvoiceFields, FieldExtractionDetail, LineItem, ReviewCorrectionSubmission
from app.services import correction_store
from app.services.pipeline_runner import _apply_family_review_corrections, _apply_producer_line_item_corrections
from app.services.producer_invoice_review import (
    ENFIDHA_FAMILY,
    PRODUCER_REVIEW_FIELDS,
    SOTACIB_FAMILIES,
    apply_producer_review_fields,
)


COMMON = {
    "seller", "invoice_number", "invoice_date", "client", "client_address", "consignee",
    "currency", "total", "total_amount_words", "hs_code", "incoterm", "origin", "destination", "packaging",
}
ENFIDHA_ONLY = {
    "client_rc", "consignee_address", "proforma_invoice_number", "proforma_invoice_date", "shipment",
    "payment", "bank", "iban", "swift", "truck_count",
}
SOTACIB_ONLY = {
    "client_tax_id", "total_ht", "integration_rate", "bank_account", "payment_method", "payment_terms",
}


def test_family_review_field_allowlists_are_canonical_and_do_not_leak():
    assert set(PRODUCER_REVIEW_FIELDS[ENFIDHA_FAMILY]) == COMMON | ENFIDHA_ONLY | {"number_of_bags", "bag_weight"}
    for family in SOTACIB_FAMILIES:
        assert set(PRODUCER_REVIEW_FIELDS[family]) == COMMON | SOTACIB_ONLY | {"number_of_bags", "bag_weight"}
    assert not (set(PRODUCER_REVIEW_FIELDS[ENFIDHA_FAMILY]) & SOTACIB_ONLY)
    assert not (set(PRODUCER_REVIEW_FIELDS[next(iter(SOTACIB_FAMILIES))]) & ENFIDHA_ONLY)


def test_canonical_adapter_preserves_machine_value_and_field_evidence():
    original = FieldExtractionDetail(
        value="Supplier OCR", machine_value="Supplier OCR", evidence_text="SELLER: Supplier OCR",
        confidence=0.81, page=1, source="label extraction",
    )
    response = SimpleNamespace(
        detected_fields=ExtractedInvoiceFields(supplier_name="Supplier OCR"),
        expanded_fields={"supplier_name": original},
    )
    apply_producer_review_fields(response, ENFIDHA_FAMILY)
    canonical = response.expanded_fields["seller"]
    assert canonical.value == "Supplier OCR"
    assert canonical.machine_value == "Supplier OCR"
    assert canonical.evidence_text == "SELLER: Supplier OCR"
    assert canonical.confidence == 0.81 and canonical.page == 1
    assert "client_tax_id" not in response.expanded_fields or response.expanded_fields["client_tax_id"].value is None


def test_family_source_labels_map_to_distinct_canonical_amount_and_tariff_concepts():
    response = SimpleNamespace(
        detected_fields=ExtractedInvoiceFields(amount_ht=123.0),
        expanded_fields={
            "amount_ht": FieldExtractionDetail(value=123.0, display_value="123,000", machine_value=123.0),
            "position_tarifaire": FieldExtractionDetail(value="HS-TEST", machine_value="HS-TEST"),
        },
    )
    family = next(iter(SOTACIB_FAMILIES))
    apply_producer_review_fields(response, family)
    # A legacy amount_ht value without total-HT label evidence is not
    # promoted to the canonical producer total_ht concept.
    assert response.expanded_fields["total_ht"].value is None
    assert response.expanded_fields["hs_code"].value == "HS-TEST"
    assert response.expanded_fields["total"].value is None


def test_labeled_ocr_exposes_source_faithful_packaging_and_distinct_sotacib_fields():
    lines = [
        SimpleNamespace(text="PACKING: BAG OF 50 KG", confidence=0.91, page_number=1, bbox={"x1": 1}, page_width=800, page_height=1100, coordinate_space="original_page", line_index=4),
        SimpleNamespace(text="20 000 bags", confidence=0.89, page_number=1, bbox={"x1": 1}, page_width=800, page_height=1100, coordinate_space="original_page", line_index=5),
        SimpleNamespace(text="Position tarifaire: HS-TEST", confidence=0.88, page_number=1, bbox={"x1": 2}, page_width=800, page_height=1100, coordinate_space="original_page", line_index=8),
        SimpleNamespace(text="Total HT: 70,00 EUR", confidence=0.84, page_number=1, bbox={"x1": 3}, page_width=800, page_height=1100, coordinate_space="original_page", line_index=10),
        SimpleNamespace(text="Moyen de règlement: Virement", confidence=0.82, page_number=1, bbox={"x1": 4}, page_width=800, page_height=1100, coordinate_space="original_page", line_index=15),
        SimpleNamespace(text="Mode de règlement: 30 jours", confidence=0.80, page_number=1, bbox={"x1": 5}, page_width=800, page_height=1100, coordinate_space="original_page", line_index=16),
    ]
    response = SimpleNamespace(detected_fields=ExtractedInvoiceFields(), expanded_fields={})
    apply_producer_review_fields(response, next(iter(SOTACIB_FAMILIES)), lines)
    assert response.expanded_fields["packaging"].display_value == "BAG OF 50 KG"
    assert response.expanded_fields["packaging"].bbox.x1 == 1
    assert response.expanded_fields["packaging"].page == 1
    assert response.expanded_fields["bag_weight"].display_value == "50 KG"
    assert response.expanded_fields["number_of_bags"].display_value == "20 000 bags"
    assert response.expanded_fields["hs_code"].value == "HS-TEST"
    assert response.expanded_fields["total_ht"].display_value == "70,00 EUR"
    assert response.expanded_fields["total"].value is None
    assert response.expanded_fields["payment_method"].value == "Virement"
    assert response.expanded_fields["payment_terms"].value == "30 jours"


def test_enfidha_bulk_exposes_truck_count_without_bag_only_values():
    lines = [
        SimpleNamespace(text="PACKING: BULK", confidence=0.9, page_number=1, bbox=None),
        SimpleNamespace(text="5 trucks", confidence=0.9, page_number=1, bbox=None),
    ]
    response = SimpleNamespace(detected_fields=ExtractedInvoiceFields(), expanded_fields={})
    apply_producer_review_fields(response, ENFIDHA_FAMILY, lines)
    assert response.expanded_fields["packaging"].display_value == "BULK"
    assert response.expanded_fields["truck_count"].display_value == "5 trucks"
    assert response.expanded_fields["number_of_bags"].value is None
    assert response.expanded_fields["bag_weight"].value is None


def test_producer_field_correction_uses_existing_store_and_preserves_machine_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(correction_store, "CORRECTION_FILE", tmp_path / "corrections.jsonl")
    payload = ReviewCorrectionSubmission(
        document_id="sha256:synthetic-dossier:doc-1",
        document_family=ENFIDHA_FAMILY,
        detected_fields=ExtractedInvoiceFields(),
        field_corrections={
            "seller": {"original_value": "SUPPLIER_TEST", "corrected_value": "SUPPLIER_TEST_CORRECTED", "page": 1},
            "invoice_number": {"original_value": "INV-TEST-001", "corrected_value": "INV-TEST-002"},
            "client": {"original_value": "CUSTOMER_TEST", "corrected_value": "CUSTOMER_TEST_CORRECTED"},
            "total": {"original_value": "1000.00", "corrected_value": "1001.00"},
            "hs_code": {"original_value": "HS-TEST", "corrected_value": "HS-TEST-2"},
            "incoterm": {"original_value": "EXW", "corrected_value": "FOB"},
            "proforma_invoice_number": {"original_value": "PF-TEST-1", "corrected_value": "PF-TEST-2"},
            "iban": {"original_value": "TN-TEST-1", "corrected_value": "TN-TEST-2"},
        },
        original_payload={"expanded_fields": {
            key: {"value": value, "machine_value": value, "evidence_text": f"{key}: {value}", "page": 1, "confidence": 0.7, "source": "OCR"}
            for key, value in {
                "seller": "SUPPLIER_TEST", "invoice_number": "INV-TEST-001", "client": "CUSTOMER_TEST",
                "total": "1000.00", "hs_code": "HS-TEST", "incoterm": "EXW",
                "proforma_invoice_number": "PF-TEST-1", "iban": "TN-TEST-1",
            }.items()
        }},
    )
    correction_store.validate_review_corrections(payload)
    persisted = correction_store.load_review_field_corrections(payload.document_id, ENFIDHA_FAMILY)
    response = SimpleNamespace(expanded_fields={
        key: FieldExtractionDetail(**detail) for key, detail in payload.original_payload["expanded_fields"].items()
    }, field_boxes=[], dynamic_tables=[])
    _apply_family_review_corrections(response, persisted, ENFIDHA_FAMILY)
    assert response.expanded_fields["seller"].value == "SUPPLIER_TEST_CORRECTED"
    assert response.expanded_fields["seller"].machine_value == "SUPPLIER_TEST"
    assert response.expanded_fields["seller"].evidence_text == "seller: SUPPLIER_TEST"
    assert response.expanded_fields["seller"].page == 1
    assert all(response.expanded_fields[key].value == payload.field_corrections[key]["corrected_value"]
               for key in payload.field_corrections)


def test_sotacib_family_fields_save_through_same_correction_store(tmp_path, monkeypatch):
    monkeypatch.setattr(correction_store, "CORRECTION_FILE", tmp_path / "corrections.jsonl")
    family = next(iter(SOTACIB_FAMILIES))
    payload = ReviewCorrectionSubmission(
        document_id="sha256:synthetic-dossier:sotacib-doc",
        document_family=family,
        detected_fields=ExtractedInvoiceFields(),
        field_corrections={
            "client_tax_id": {"original_value": "TAX-TEST-1", "corrected_value": "TAX-TEST-2"},
            "total_ht": {"original_value": "1000.00", "corrected_value": "1001.00"},
            "payment_method": {"original_value": "TRANSFER", "corrected_value": "CHEQUE"},
        },
        original_payload={"expanded_fields": {
            key: {"value": correction["original_value"], "machine_value": correction["original_value"]}
            for key, correction in {
                "client_tax_id": {"original_value": "TAX-TEST-1"},
                "total_ht": {"original_value": "1000.00"},
                "payment_method": {"original_value": "TRANSFER"},
            }.items()
        }},
    )
    correction_store.validate_review_corrections(payload)
    saved = correction_store.load_review_field_corrections(payload.document_id, family)
    assert {key: value["corrected_value"] for key, value in saved.items()} == {
        "client_tax_id": "TAX-TEST-2", "total_ht": "1001.00", "payment_method": "CHEQUE",
    }


def test_producer_line_item_cell_correction_persists_by_document_row_and_field(tmp_path, monkeypatch):
    monkeypatch.setattr(correction_store, "CORRECTION_FILE", tmp_path / "corrections.jsonl")
    original = {"description": "CEMENT_TEST", "quantity": 2, "unit": "BAG", "unit_price": 50.0,
                "line_total_ttc": 100.0, "bbox": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
                "page": 1, "confidence": 0.72, "source": "OCR"}
    payload = ReviewCorrectionSubmission(
        document_id="sha256:synthetic-dossier:doc-2", document_family=ENFIDHA_FAMILY,
        detected_fields=ExtractedInvoiceFields(),
        line_item_corrections=[{**original, "description": "CEMENT_TEST_CORRECTED", "source": "human verified", "confidence": 1}],
        original_payload={"original_line_items": [original]},
    )
    correction_store.validate_review_corrections(payload)
    corrections = correction_store.load_review_line_item_corrections(payload.document_id, ENFIDHA_FAMILY)
    assert correction_store.load_review_line_item_corrections("sha256:synthetic-dossier:other-doc", ENFIDHA_FAMILY) == {}
    assert corrections[0]["description"]["original_value"] == "CEMENT_TEST"
    assert corrections[0]["description"]["original_bbox"] == original["bbox"]
    assert corrections[0]["description"]["page"] == 1
    assert "unit_price" not in corrections[0]

    row = LineItem(**original)
    response = SimpleNamespace(
        detected_fields=SimpleNamespace(line_items=[row]), all_line_items=[row],
        line_items_validated=[], line_items_needs_review=[],
    )
    _apply_producer_line_item_corrections(response, corrections)
    assert row.description == "CEMENT_TEST_CORRECTED"
    assert row.quantity == 2 and row.unit_price == 50
    assert row.bbox == original["bbox"] and row.page == 1


def test_producer_ui_has_family_scoped_editable_keys_and_french_labels():
    from pathlib import Path

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    strings = Path("app/static/strings.js").read_text(encoding="utf-8")
    for family, fields in PRODUCER_REVIEW_FIELDS.items():
        assert family in app_js
        assert all(f'"{field}"' in app_js for field in fields)
    for key, label in {
        "seller": "Vendeur", "invoice_number": "Numéro de facture", "invoice_date": "Date de facture",
        "client": "Client", "client_address": "Adresse du client", "consignee": "Destinataire",
        "currency": "Devise", "total": "Montant total", "total_amount_words": "Montant total en lettres",
        "hs_code": "Code HS", "incoterm": "Incoterm", "origin": "Origine", "destination": "Destination",
        "packaging": "Conditionnement", "client_rc": "RC du client", "client_tax_id": "Matricule fiscal du client",
        "total_ht": "Total HT", "integration_rate": "Taux d'intégration", "payment_method": "Moyen de règlement",
        "payment_terms": "Mode de règlement",
    }.items():
        assert f'"fields.{key}": "{label}"' in strings
    assert "producerVisibleReviewGroups" in app_js
    assert '["line_total", item.line_total_ttc ?? item.line_total_ht ?? item.total' in app_js
    assert "line_total_ttc" in app_js and "line_total_ht" in app_js
    assert '"line_items.total_line": "Total ligne"' in strings
