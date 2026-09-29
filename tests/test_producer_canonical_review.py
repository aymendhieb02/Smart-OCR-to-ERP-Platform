from types import SimpleNamespace

from app.core.schemas import BoundingBox, ExtractedInvoiceFields, FieldExtractionDetail, LineItem, OCRLine, ReviewCorrectionSubmission
from app.services import correction_store
from app.services.pipeline_runner import _apply_family_review_corrections, _apply_producer_line_item_corrections
from app.services.producer_invoice_review import (
    ENFIDHA_FAMILY,
    PRODUCER_REVIEW_FIELDS,
    SOTACIB_FAMILIES,
    apply_producer_review_fields,
    merge_producer_semantics,
    prepare_producer_fields,
    recover_enfidha_proforma_reference,
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


def test_producer_semantics_override_legacy_values_but_preserve_human_and_template_values():
    expanded = {
        "client": FieldExtractionDetail(value="SUPPLIER_ADDRESS_TEST", source="legacy generic alias"),
        "total": FieldExtractionDetail(value=999.0, source="generic amount candidate"),
        "seller": FieldExtractionDetail(value="HUMAN_SELLER_TEST", source="human correction"),
        "invoice_number": FieldExtractionDetail(value="TEMPLATE-INV-TEST", source="known-template"),
    }
    semantics = {
        "client": FieldExtractionDetail(value="CUSTOMER_TEST", source="generic semantic label"),
        "total": FieldExtractionDetail(value=1000.0, source="generic semantic total label"),
        "seller": FieldExtractionDetail(value="RAW_SELLER_TEST", source="generic semantic label"),
        "invoice_number": FieldExtractionDetail(value="RAW-INV-TEST", source="generic semantic label"),
    }

    merge_producer_semantics(expanded, semantics, ENFIDHA_FAMILY)

    assert expanded["client"].value == "CUSTOMER_TEST"
    assert expanded["total"].value == 1000.0
    assert expanded["seller"].value == "HUMAN_SELLER_TEST"
    assert expanded["invoice_number"].value == "TEMPLATE-INV-TEST"


def test_enfidha_canonical_response_exposes_all_extracted_business_fields():
    from types import SimpleNamespace

    from app.core.schemas import ExtractedInvoiceFields

    lines = [
        SimpleNamespace(text="Client: CUSTOMER_TEST", confidence=0.95, page_number=1),
        SimpleNamespace(text="Client RC: RC-TEST-001", confidence=0.95, page_number=1),
        SimpleNamespace(text="Consignee: RECEIVER_TEST", confidence=0.95, page_number=1),
        SimpleNamespace(text="Consignee address: RECEIVER_TEST STREET", confidence=0.95, page_number=1),
        SimpleNamespace(text="Total including all taxes: 1234.00 EUR", confidence=0.95, page_number=1),
        SimpleNamespace(text="Amount in words: ONE THOUSAND TWO HUNDRED THIRTY FOUR EUROS", confidence=0.95, page_number=1),
        SimpleNamespace(text="HS code: HS-TEST-001", confidence=0.95, page_number=1),
        SimpleNamespace(text="Proforma invoice No: PF-TEST-001", confidence=0.95, page_number=1),
        SimpleNamespace(text="Proforma date: 04/01/2026", confidence=0.95, page_number=1),
        SimpleNamespace(text="Incoterm: EXW", confidence=0.95, page_number=1),
        SimpleNamespace(text="Origin: COUNTRY_TEST", confidence=0.95, page_number=1),
        SimpleNamespace(text="Destination: COUNTRY_TEST", confidence=0.95, page_number=1),
        SimpleNamespace(text="Shipment: PARTIAL_TEST", confidence=0.95, page_number=1),
        SimpleNamespace(text="Payment: TRANSFER_TEST", confidence=0.95, page_number=1),
        SimpleNamespace(text="Bank: BANK_TEST", confidence=0.95, page_number=1),
        SimpleNamespace(text="IBAN: IBAN-TEST-001", confidence=0.95, page_number=1),
        SimpleNamespace(text="SWIFT: SWIFT-TEST", confidence=0.95, page_number=1),
    ]
    response = SimpleNamespace(
        detected_fields=ExtractedInvoiceFields(customer_name="SUPPLIER_ADDRESS_TEST"),
        expanded_fields={"client": FieldExtractionDetail(value="SUPPLIER_ADDRESS_TEST", source="legacy generic alias")},
    )

    apply_producer_review_fields(response, ENFIDHA_FAMILY, lines)

    expected = {
        "client": "CUSTOMER_TEST", "client_rc": "RC-TEST-001", "consignee": "RECEIVER_TEST",
        "consignee_address": "RECEIVER_TEST STREET", "total": 1234.0,
        "total_amount_words": "ONE THOUSAND TWO HUNDRED THIRTY FOUR EUROS", "hs_code": "HS-TEST-001",
        "proforma_invoice_number": "PF-TEST-001", "proforma_invoice_date": "04/01/2026",
        "incoterm": "EXW", "origin": "COUNTRY_TEST", "destination": "COUNTRY_TEST",
        "shipment": "PARTIAL_TEST", "payment": "TRANSFER_TEST", "bank": "BANK_TEST",
        "iban": "IBAN-TEST-001", "swift": "SWIFT-TEST",
    }
    assert {name: response.expanded_fields[name].value for name in expected} == expected


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


def test_total_amount_words_prefers_horizontally_aligned_value_over_far_right_neighbor():
    label = OCRLine(
        text="La somme de:", confidence=0.92, page_number=1, line_index=1,
        bbox=BoundingBox(x1=172, y1=611, x2=559, y2=635),
        source="synthetic fixture",
    )
    expected = OCRLine(
        text="ONE THOUSAND TEST EUROS", confidence=0.94, page_number=1, line_index=2,
        bbox=BoundingBox(x1=167, y1=644, x2=572, y2=667),
        source="synthetic fixture",
    )
    unrelated_far_right = OCRLine(
        text="123.00", confidence=0.98, page_number=1, line_index=3,
        bbox=BoundingBox(x1=1005, y1=636, x2=1093, y2=657),
        source="synthetic regional fallback",
    )

    fields = prepare_producer_fields(
        ExtractedInvoiceFields(), [label, expected, unrelated_far_right], next(iter(SOTACIB_FAMILIES)),
    )

    assert fields["total_amount_words"].value == expected.text
    assert fields["total_amount_words"].line_index == expected.line_index
    assert fields["total_amount_words"].bbox.x1 == expected.bbox.x1


def test_sotacib_french_destination_phrase_uses_positioned_evidence():
    unpositioned = OCRLine(
        text="Marchandise Destinee a l'exportation vers OTHER_TEST", confidence=0.9,
        page_number=1, line_index=1, source="synthetic embedded text",
    )
    positioned = OCRLine(
        text="Marchandlse Destlnee a I'exportation vers COUNTRY_TEST", confidence=0.94,
        page_number=1, line_index=2,
        bbox=BoundingBox(x1=100, y1=300, x2=620, y2=322),
        source="synthetic positioned OCR",
    )

    fields = prepare_producer_fields(
        ExtractedInvoiceFields(), [unpositioned, positioned], next(iter(SOTACIB_FAMILIES)),
    )

    assert fields["destination"].value == "COUNTRY_TEST"
    assert fields["destination"].line_index == positioned.line_index
    assert fields["destination"].bbox.x1 == positioned.bbox.x1


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


def test_generic_bag_quantity_and_weight_keep_numeric_values_and_source_display():
    lines = [
        SimpleNamespace(text="20000 Bags/50 kgs", confidence=0.94, page_number=1),
    ]

    fields = prepare_producer_fields(ExtractedInvoiceFields(), lines, ENFIDHA_FAMILY)

    assert fields["number_of_bags"].value == "20000"
    assert fields["number_of_bags"].display_value == "20000 Bags"
    assert fields["bag_weight"].value == "50"
    assert fields["bag_weight"].display_value == "50 kgs"


def test_enfidha_conform_to_proforma_row_exposes_number_and_canonical_date():
    line = SimpleNamespace(
        text="Conform to the proforma invoice N° PF-TEST-023 DU 04/01/2026",
        confidence=0.93, page_number=1,
    )

    fields = prepare_producer_fields(ExtractedInvoiceFields(), [line], ENFIDHA_FAMILY)

    assert fields["proforma_invoice_number"].value == "PF-TEST-023"
    assert fields["proforma_invoice_number"].evidence_text == line.text
    assert fields["proforma_invoice_date"].value == "2026-01-04"
    assert fields["proforma_invoice_date"].display_value == "04/01/2026"
    assert fields["proforma_invoice_date"].machine_value == "04/01/2026"


def test_enfidha_split_proforma_reference_uses_label_row_and_one_targeted_date_crop():
    import numpy as np

    def line(text, x, y):
        cx, cy = x * 1000, y * 1400
        return OCRLine(
            text=text, confidence=0.91, page_number=1,
            bbox=BoundingBox(x1=cx - 50, y1=cy - 10, x2=cx + 50, y2=cy + 10),
            page_width=1000, page_height=1400, coordinate_space="original_page",
        )

    label = line("Conform to the proforma invoice N°", 0.40, 0.55)
    number = line("02-23", 0.57, 0.55)
    marker = line("DU", 0.65, 0.55)
    date_line = line("04/01/2026", 0.76, 0.55)
    existing = [label, number, marker]

    class Engine:
        calls = []

        def run_targeted_region(self, image, region, *, page_number):
            self.calls.append((region.name, page_number, tuple(region.image.shape[:2])))
            return [date_line]

    engine = Engine()
    fields, targeted, debug = recover_enfidha_proforma_reference(
        existing, ENFIDHA_FAMILY, [np.zeros((1400, 1000, 3), dtype=np.uint8)], engine,
        physical_page_numbers=(1,),
    )

    assert fields["proforma_invoice_number"].value == "02-23"
    assert fields["proforma_invoice_number"].evidence_text == f"{label.text}\n{number.text}"
    assert fields["proforma_invoice_date"].value == "2026-01-04"
    assert fields["proforma_invoice_date"].machine_value == "04/01/2026"
    assert targeted == [date_line]
    assert engine.calls[0][:2] == ("producer_proforma_reference", 1)
    assert debug["reason"] == "reference_recovered"


def test_enfidha_split_proforma_reference_does_not_borrow_the_main_invoice_date():
    def line(text, x, y):
        cx, cy = x * 1000, y * 1400
        return OCRLine(
            text=text, confidence=0.91, page_number=1,
            bbox=BoundingBox(x1=cx - 50, y1=cy - 10, x2=cx + 50, y2=cy + 10),
            page_width=1000, page_height=1400, coordinate_space="original_page",
        )

    lines = [
        line("Date 04/01/2026", 0.80, 0.22),
        line("Conform to the proforma invoice N°", 0.40, 0.55),
        line("02-23", 0.57, 0.55),
        line("DU", 0.65, 0.55),
    ]
    fields, targeted, debug = recover_enfidha_proforma_reference(
        lines, ENFIDHA_FAMILY, [], None,
    )

    assert fields["proforma_invoice_number"].value == "02-23"
    assert "proforma_invoice_date" not in fields
    assert targeted == []
    assert debug["reason"] == "targeted_ocr_unavailable"


def test_payment_transcription_normalization_preserves_raw_ocr_evidence():
    line = SimpleNamespace(
        text="PAYMENT: BANKTYRANSFER IN 30 DAYS", confidence=0.91, page_number=1,
    )

    fields = prepare_producer_fields(ExtractedInvoiceFields(), [line], ENFIDHA_FAMILY)

    assert fields["payment"].value == "BANK TRANSFER IN 30 DAYS"
    assert fields["payment"].display_value == "BANK TRANSFER IN 30 DAYS"
    assert fields["payment"].machine_value == "BANKTYRANSFER IN 30 DAYS"
    assert fields["payment"].evidence_text == "PAYMENT: BANKTYRANSFER IN 30 DAYS"


def test_producer_party_geometry_keeps_seller_client_address_and_consignee_distinct():
    def line(text, x, y, *, width=1000, height=1400):
        return OCRLine(
            text=text, confidence=0.93, page_number=1,
            bbox=BoundingBox(x1=x, y1=y, x2=x + 220, y2=y + 24),
            page_width=width, page_height=height, coordinate_space="original_page",
        )

    lines = [
        line("SUPPLIER_TEST INDUSTRIES", 600, 100),
        line("IMPORTER_CUSTOMER_TEST LLC", 260, 430),
        line("ADDRESS: CUSTOMER_TEST CITY", 260, 470),
        line("RC N°: RC-TEST-023", 260, 510),
        line("CONSIGNEE: RECEIVER_TEST", 360, 560),
        line("ADDRESS: RECEIVER_TEST CITY", 260, 600),
    ]

    fields = prepare_producer_fields(ExtractedInvoiceFields(), lines, ENFIDHA_FAMILY)

    assert fields["seller"].value == "SUPPLIER_TEST INDUSTRIES"
    assert fields["client"].value == "IMPORTER_CUSTOMER_TEST LLC"
    assert fields["client_address"].value == "CUSTOMER_TEST CITY"
    assert fields["consignee"].value == "RECEIVER_TEST"
    assert fields["consignee_address"].value == "RECEIVER_TEST CITY"
    assert fields["client"].value != fields["seller"].value
    assert fields["client_address"].value != fields["consignee_address"].value


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
    assert response.expanded_fields["seller"].source == "human correction"
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
        "seller": "Fournisseur", "invoice_number": "Numéro de facture", "invoice_date": "Date de facture",
        "client": "Client", "client_address": "Adresse du client", "consignee": "Destinataire",
        "currency": "Devise", "total": "Montant total", "total_amount_words": "Montant total en lettres",
        "hs_code": "Code HS", "incoterm": "Incoterm", "origin": "Origine", "destination": "Destination",
        "packaging": "Conditionnement", "client_rc": "RC du client", "client_tax_id": "Matricule fiscal du client",
        "total_ht": "Total HT", "integration_rate": "Taux d'intégration", "payment_method": "Moyen de règlement",
        "payment_terms": "Mode de règlement",
    }.items():
        assert f'"fields.{key}": "{label}"' in strings
    assert "producerVisibleReviewGroups" in app_js
    assert '["line_total", item.line_total ?? item.total' in app_js
    assert "line_total_ttc" in app_js and "line_total_ht" in app_js
    assert '"line_items.total_line": "Total ligne"' in strings


def test_producer_review_contract_has_canonical_general_allowlist_and_optional_detected_extensions():
    from pathlib import Path

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    common = set(PRODUCER_REVIEW_FIELDS["general_supplier_invoice"])
    assert "fields: [...GENERAL_PRODUCER_REVIEW_FIELDS, ...PRODUCER_OPTIONAL_EXTENSION_FIELDS]" in app_js
    assert "const presentExtensions = PRODUCER_OPTIONAL_EXTENSION_FIELDS.filter" in app_js
    assert 'String(value).trim() !== ""' in app_js
    assert "const PRODUCER_COMMON_REVIEW_FIELDS" in app_js
    assert common.issubset(set(PRODUCER_REVIEW_FIELDS["general_supplier_invoice"]))
    assert 'const statusKey = corrected ? "status.manually_corrected"' in app_js
    assert ': !present ? "status.not_extracted"' in app_js
    assert ': explicitReview ? "status.needs_review" : "status.confirmed"' in app_js
    assert "renderProducerFieldStatus(field, input.value)" in app_js
    strings = Path("app/static/strings.js").read_text(encoding="utf-8")
    assert '"producer.section.consignee"' in strings
    assert 'Number(confidence) >= PRODUCER_FIELD_REVIEW_CONFIDENCE_THRESHOLD' in app_js
    assert 'const PRODUCER_FIELD_REVIEW_CONFIDENCE_THRESHOLD = 0.65' in app_js
    assert "status.needs_review" in app_js
    assert 'fieldReport?.accepted === false' in app_js


def test_frontend_producer_allowlists_exclude_legacy_tax_fields_and_sotacib_supplier_tax_id():
    import re
    from pathlib import Path

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    common_block = app_js.split("const GENERAL_PRODUCER_REVIEW_FIELDS = Object.freeze([", 1)[1].split("]);", 1)[0]
    sotacib_block = app_js.split("const SOTACIB_REVIEW_FIELDS = Object.freeze([", 1)[1].split("]);", 1)[0]
    common = set(re.findall(r'"([a-z_]+)"', common_block))
    sotacib = set(re.findall(r'"([a-z_]+)"', sotacib_block))
    sotacib |= set(re.findall(r'"([a-z_]+)"', app_js.split("const PRODUCER_COMMON_REVIEW_FIELDS = Object.freeze([", 1)[1].split("]);", 1)[0]))
    assert common == {
        "seller", "invoice_number", "invoice_date", "client", "client_address", "consignee",
        "currency", "total", "total_amount_words", "hs_code", "incoterm", "origin", "destination", "payment", "packaging",
    }
    assert {
        "seller", "invoice_number", "invoice_date", "client", "client_address", "consignee", "currency", "total",
        "total_amount_words", "hs_code", "incoterm", "origin", "destination", "packaging", "client_tax_id", "total_ht",
        "number_of_bags", "bag_weight", "integration_rate", "bank_account", "payment_method", "payment_terms",
    } == sotacib
    forbidden = {"supplier_tax_id", "amount_ttc", "tva_amount", "tax_rate", "purchase_order_number", "total_ttc", "tax_amount"}
    assert not (common & forbidden)
    assert not (sotacib & forbidden)


def test_producer_normal_ui_hides_candidate_choices_and_diagnostic_dynamic_values():
    from pathlib import Path

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    field_render = app_js.split("function renderFields(fields)", 1)[1].split("function producerVisibleReviewGroups", 1)[0]
    suggestion_render = app_js.split("function renderCorrectionSuggestions(host)", 1)[1].split("function renderDuplicateAndFraud", 1)[0]
    dynamic_render = app_js.split("function renderDynamicReview()", 1)[1].split("function renderFinancialChecks", 1)[0]
    assert "isProducerPresentation(presentation)" in field_render
    assert "renderProducerFieldStatus(field, input.value)" in field_render
    assert "renderFieldCandidateFallback(field, input.value)" in field_render
    assert 'if (isProducerPresentation(resolveDocumentPresentation()))' in suggestion_render
    assert '"erp_fields", "all_extracted_fields", "line_items"' in dynamic_render
    assert "review_candidates: reviewCandidates" in app_js
    assert "rejected_candidates: rejectedCandidates" in app_js


def test_producer_validation_ui_suppresses_legacy_tax_and_rejected_candidate_only_warnings():
    from pathlib import Path

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "visibleProducerValidationMessages(validation.warnings || [])" in app_js
    assert "insufficient totals for complete financial check" in app_js
    assert "some extracted fields were withheld from erp export" in app_js
    assert "suspicious tax rate" in app_js
    assert "visibleProducerValidationMessages(explanation?.warnings" in app_js
    assert "panel?.classList.add(\"hidden\")" in app_js
    assert "irrelevantProducerFields" in app_js


def test_producer_line_table_uses_simple_columns_actions_and_keeps_blank_rows_unvalidated():
    from pathlib import Path

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 't("line_items.description")' in app_js and 't("line_items.quantity")' in app_js
    assert 't("line_items.unit")' in app_js and 't("line_items.unit_price")' in app_js
    assert 't("line_items.total_line")' in app_js and 't("common.actions")' in app_js
    assert 'const status = !hasLineContent ? "needs_review"' in app_js
    assert 'isProducerPresentation(resolveDocumentPresentation()) ? "total"' in app_js
    assert "numberOrNull(producerInvoice ? (item.line_total ?? item.total)" in app_js
    assert 'lastResponse.row_validation?.[index]?.status === "validated"' in app_js
    producer_table = app_js.split("function renderLineItems(items", 1)[1].split("function editableLineItemRow", 1)[0]
    assert "line_items.total_ttc" in producer_table  # Retained only for non-producer tables.
    assert 'producerInvoice ? `<th>${escapeHtml(t("line_items.total_line"))}</th>`' in producer_table
    assert 'producerInvoice ? "" : `' in producer_table


def test_backend_accepts_canonical_producer_line_total_as_existing_total_field():
    from app.services.correction_store import _review_line_items

    rows = _review_line_items([{
        "description": "CEMENT_TEST", "quantity": 2, "unit": "BAG", "unit_price": 50,
        "total": 100, "source": "human verified",
    }], [])
    assert len(rows) == 1
    assert rows[0].total == 100
    assert rows[0].line_total_ht is None and rows[0].line_total_ttc is None
