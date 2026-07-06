from app.core.schemas import DocumentClassification, OCRLine
from app.utils.helpers import strip_accents


KEYWORDS = {
    "invoice": [
        "facture", "numero facture", "n facture", "total ttc", "tva",
        "invoice", "invoice number", "vat", "total amount", "amount due",
        "فاتورة", "رقم الفاتورة", "ضريبة", "المبلغ الجملي", "الإجمالي",
    ],
    "delivery_note": [
        "bon de livraison", "livraison", "quantite livree", "quantité livrée",
        "delivery note", "delivered quantity", "shipped",
        "وصل تسليم", "إذن تسليم", "تسليم",
    ],
    "credit_note": [
        "avoir", "facture d'avoir", "note de credit", "note de crédit",
        "credit note", "refund", "credit memo",
        "إشعار دائن", "إرجاع", "استرجاع",
    ],
    "receipt": [
        "recu", "reçu", "ticket", "paiement recu", "paiement reçu",
        "receipt", "payment received",
        "وصل", "إيصال", "وصل دفع",
    ],
    "purchase_order": [
        "bon de commande", "commande", "purchase order", "po number",
        "طلب شراء", "أمر شراء",
    ],
}


def classify_document(text: str, blocks: list[OCRLine] | None = None) -> DocumentClassification:
    normalized = strip_accents(text).lower()
    scores: dict[str, list[str]] = {}
    for document_type, keywords in KEYWORDS.items():
        matches = []
        for keyword in keywords:
            keyword_norm = strip_accents(keyword).lower()
            if keyword_norm in normalized:
                matches.append(keyword)
        if matches:
            scores[document_type] = matches

    if not scores:
        return DocumentClassification(document_type="unknown", confidence=0.0, matched_keywords=[])

    ranked = sorted(scores.items(), key=lambda item: (len(item[1]), _priority(item[0])), reverse=True)
    document_type, matched = ranked[0]
    confidence = min(0.98, 0.45 + len(matched) * 0.14)
    return DocumentClassification(document_type=document_type, confidence=round(confidence, 3), matched_keywords=matched)


def _priority(document_type: str) -> int:
    order = {"invoice": 5, "delivery_note": 4, "credit_note": 3, "receipt": 2, "purchase_order": 1}
    return order.get(document_type, 0)
