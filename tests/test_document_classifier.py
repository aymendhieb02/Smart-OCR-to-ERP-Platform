from app.services.document_classifier import classify_document


def test_multilingual_invoice_detection():
    result = classify_document("Facture N° FAC-1 Total TTC TVA")
    assert result.document_type == "invoice"
    assert result.confidence > 0.5


def test_delivery_note_detection():
    result = classify_document("BON DE LIVRAISON Quantité livrée N° BL")
    assert result.document_type == "delivery_note"


def test_arabic_receipt_detection():
    result = classify_document("وصل دفع المبلغ الإجمالي")
    assert result.document_type in {"receipt", "invoice"}
