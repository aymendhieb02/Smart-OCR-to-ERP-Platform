from __future__ import annotations

from types import SimpleNamespace

from app.core.schemas import BoundingBox, ExtractedInvoiceFields, FieldExtractionDetail, OCRLine
from app.services.dossier_reconciler import reconcile_dossier


def _doc(role, index, pages, *, fields=None, expanded=None, ocr=None):
    family = {"producer": "synthetic_producer_v1", "ruspina": "ruspina_reinvoice_v1",
              "customs": "customs_tradenet_v1", "unknown": None}[role]
    document_type = "customs_declaration" if role == "customs" else "unknown" if role == "unknown" else "commercial_invoice"
    return SimpleNamespace(
        group=SimpleNamespace(group_id=f"doc_{index}", document_family=family,
                              document_type=document_type, pages=pages),
        response=SimpleNamespace(detected_fields=ExtractedInvoiceFields(**(fields or {})),
                                 expanded_fields={name: FieldExtractionDetail(value=value, confidence=0.9)
                                                  for name, value in (expanded or {}).items()},
                                 field_confidences={}, all_ocr_blocks=ocr or []),
    )


def _customs(*, ptfn=1000.0, currency="EUR", exporter="ALPHA CEMENT LTD warehouse",
             importer="BETA TRADING LLC warehouse", page=3):
    doc = _doc("customs", 3, (page,), expanded={"ptfn_amount": ptfn, "exporter": exporter, "importer": importer})
    doc.response.expanded_fields["ptfn_amount"] = FieldExtractionDetail(
        value=ptfn, confidence=0.95, bbox=BoundingBox(x1=750, y1=380, x2=830, y2=400),
        page=page, page_width=1190, page_height=1684,
    )
    if currency:
        doc.response.all_ocr_blocks.append(OCRLine(
            text=currency, confidence=0.9, page_number=page,
            bbox=BoundingBox(x1=675, y1=380, x2=710, y2=400),
        ))
    return doc


def _set():
    producer = _doc("producer", 1, (1, 2), fields={
        "invoice_number": "INV-TEST-001", "amount_ttc": 1000.0, "currency": "EUR",
        "supplier_name": "ALPHA CEMENT LTD", "customer_name": "BETA TRADING LLC",
    }, expanded={"origin": "TUNÍSIA"})
    ruspina = _doc("ruspina", 2, (4,), expanded={
        "referenced_invoice": "INV TEST 001", "origin": "Tunisia", "total": 1200.0,
    })
    return producer, ruspina, _customs()


def _by_type(documents):
    return {item.type: item for item in reconcile_dossier(documents)}


def test_matches_independent_fields_without_repairing_or_using_ruspina_total():
    producer, ruspina, customs = _set()
    original_reference = ruspina.response.expanded_fields["referenced_invoice"].value
    observed = _by_type((customs, ruspina, producer))  # physical order is irrelevant
    assert {item.status for item in observed.values()} == {"match"}
    assert observed["producer_total_customs_ptfn"].left_value == 1000.0
    assert observed["producer_total_customs_ptfn"].right_value == 1000.0
    assert observed["producer_invoice_reference"].left_document_id == "doc_1"
    assert observed["producer_invoice_reference"].right_document_id == "doc_2"
    assert ruspina.response.expanded_fields["referenced_invoice"].value == original_reference
    assert ruspina.response.expanded_fields["total"].value == 1200.0


def test_identifier_mismatch_and_reconciliation_reruns_after_review_edit():
    producer, ruspina, customs = _set()
    ruspina.response.expanded_fields["referenced_invoice"].value = "INV-TEST-002"
    assert _by_type((producer, ruspina, customs))["producer_invoice_reference"].status == "mismatch"
    ruspina.response.expanded_fields["referenced_invoice"].value = "INV-TEST-001"
    assert _by_type((producer, ruspina, customs))["producer_invoice_reference"].status == "match"


def test_money_uses_decimal_and_requires_ptfn_row_currency_evidence():
    producer, ruspina, customs = _set()
    producer.response.detected_fields.amount_ttc = 0.1 + 0.2
    customs.response.expanded_fields["ptfn_amount"].value = 0.3
    assert _by_type((producer, ruspina, customs))["producer_total_customs_ptfn"].status == "mismatch"
    producer.response.detected_fields.amount_ttc = 1000.0
    customs.response.all_ocr_blocks[0].text = "USD"
    result = _by_type((producer, ruspina, customs))["producer_total_customs_ptfn"]
    assert result.status == "unavailable" and "incompatible" in result.reason
    customs.response.all_ocr_blocks.clear()
    assert _by_type((producer, ruspina, customs))["producer_total_customs_ptfn"].status == "unavailable"


def test_party_comparison_is_conservative_and_origin_is_independent():
    producer, ruspina, customs = _set()
    customs.response.expanded_fields["exporter"].value = "GAMMA EXPORT CORP"
    customs.response.expanded_fields["importer"].value = "BETA TRADING -- OCR fragment"
    observed = _by_type((producer, ruspina, customs))
    assert observed["producer_seller_customs_exporter"].status == "mismatch"
    assert observed["producer_buyer_customs_importer"].status == "unavailable"
    assert observed["producer_ruspina_origin"].status == "match"
    ruspina.response.expanded_fields["origin"].value = "TESTLAND"
    assert _by_type((producer, ruspina, customs))["producer_ruspina_origin"].status == "mismatch"


def test_noisy_party_blocks_do_not_create_false_mismatches():
    producer, ruspina, customs = _set()
    producer.response.detected_fields.supplier_name = "Destinataire: TEST CUSTOMER"
    customs.response.expanded_fields["exporter"].value = "UNRELATED EXPORTER OOO 123 ADDRESS BLOCK"
    assert _by_type((producer, ruspina, customs))["producer_seller_customs_exporter"].status == "unavailable"


def test_missing_ambiguous_and_unknown_documents_are_unavailable_not_guessed():
    producer, ruspina, customs = _set()
    unknown = _doc("unknown", 8, (5,))
    observed = _by_type((unknown, producer, ruspina))
    assert observed["producer_total_customs_ptfn"].status == "unavailable"
    assert observed["producer_seller_customs_exporter"].status == "unavailable"
    assert observed["producer_invoice_reference"].status == "match"
    duplicate_producer = _doc("producer", 4, (6,), fields={"invoice_number": "INV-TEST-999"})
    assert all(item.status == "unavailable" for item in reconcile_dossier((producer, duplicate_producer, ruspina, customs)))


def test_missing_field_never_falls_back_to_another_document():
    producer, ruspina, customs = _set()
    ruspina.response.expanded_fields.pop("referenced_invoice")
    customs.response.expanded_fields["ptfn_amount"].value = 1000.0
    result = _by_type((producer, ruspina, customs))["producer_invoice_reference"]
    assert result.status == "unavailable" and result.right_value is None
