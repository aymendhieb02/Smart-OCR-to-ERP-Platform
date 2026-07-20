from __future__ import annotations

import re

from app.services.document_graph import DocumentGraph, DocumentNode
from app.utils.helpers import parse_amount, strip_accents

COMPANY_SUFFIXES = (
    "llc", "ltd", "limited", "inc", "inc.", "plc", "sarl", "suarl", "corp", "corporation",
    "company", "co.", "group", "electronics", "services", "distribution", "trading", "industries",
    "interiors", "canada", "tunisie", "pharma", "pharmacy", "medical", "technologies",
    "spa", "sarlau", "clinic", "laboratory", "labs", "logistics", "solutions",
)
TABLE_WORDS = ("description", "designation", "quantity", "qty", "qte", "unit", "unite", "unité", "price", "prix", "total", "amount", "tva", "vat", "net", "gross", "worth")
CUSTOMER_LABELS = ("bill to", "invoice to", "client", "customer", "acheteur", "destinataire", "facture a", "facture à", "facturé à", "livre a", "livré à")
SUPPLIER_LABELS = ("supplier", "seller", "vendor", "bill from", "fournisseur", "vendeur", "from")
PAYMENT_WORDS = ("payment details", "payment", "iban", "rib", "swift", "bank", "banque")
ADDRESS_WORDS = ("street", "st", "road", "rd", "avenue", "ave", "rue", "route", "suite", "unit", "apt", "postal", "zip")


def classify_graph_nodes(graph: DocumentGraph) -> DocumentGraph:
    for node in graph.nodes:
        node.node_type = classify_node(node)
    return graph


def classify_node(node: DocumentNode) -> str:
    text = node.text.strip()
    plain = strip_accents(text).lower().strip(" :#|-")
    if not plain:
        return "random_noise"
    if _is_low_confidence_noise(node):
        return "random_noise"
    if _is_table_header(plain):
        return "table_header"
    if _is_payment_label_line(plain):
        return "payment_label"
    if re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", text):
        return "email"
    if re.search(r"(?:\+?\d{1,3}[\s.-]?)?(?:\d[\s.-]?){6,}", text) and not _has_money_context(plain):
        return "phone"
    if _is_total_label(plain):
        return "total_label"
    if _is_subtotal_label(plain):
        return "subtotal_label"
    if _is_tax_label(plain):
        return "tax_label"
    if _is_due_date_label(plain):
        return "due_date_label"
    if _is_invoice_label(plain):
        return "invoice_label"
    if _is_customer_label_line(plain):
        return "customer_label"
    if _is_supplier_label_line(plain):
        return "supplier_label"
    if _is_date_candidate(text):
        return "date_candidate"
    if _is_amount(text):
        return "amount"
    if _is_postal_code_only(text):
        return "address_candidate"
    if _looks_like_address(text):
        return "address_candidate"
    if _looks_like_product_row(plain):
        return "table_row_text"
    if _is_company_candidate(text):
        return "company_candidate"
    if any(word in plain for word in ("thank you", "merci", "terms", "conditions", "signature")):
        return "footer_note"
    return "random_noise" if len(text) <= 3 else "unknown"


def is_company_candidate_text(text: str) -> bool:
    return _is_company_candidate(text)


def is_forbidden_party_name(text: str) -> bool:
    plain = strip_accents(text).lower().strip(" :#|-")
    if _is_postal_code_only(text):
        return True
    if plain.replace("_", " ") in {"ship to", "bill to", "supplier", "seller", "vendor", "customer", "client", "address", "adresse", "phone", "email", "bank"}:
        return True
    if _is_table_header(plain):
        return True
    if any(re.search(rf"\b{re.escape(word)}\b", plain) for word in PAYMENT_WORDS):
        return True
    if any(re.search(rf"\b{re.escape(word)}\b", plain) for word in TABLE_WORDS):
        return True
    if _is_customer_label_line(plain) or _is_supplier_label_line(plain):
        return True
    if re.search(r"@|\b(?:tel|phone|fax|email|date|invoice|facture|total|tax|vat|tva)\b", plain):
        return True
    if _looks_like_address(text):
        return True
    return False


def _is_company_candidate(text: str) -> bool:
    if is_forbidden_party_name(text):
        return False
    plain = strip_accents(text).lower()
    letters = sum(char.isalpha() for char in text)
    if letters < 4 or len(text.strip()) > 95:
        return False
    if re.match(r"^\d", text.strip()):
        return False
    suffix_hit = any(re.search(rf"\b{re.escape(suffix)}\b", plain) for suffix in COMPANY_SUFFIXES)
    uppercase_words = sum(1 for word in re.findall(r"[A-Z][A-Z&.]{1,}", text))
    alpha_words = [
        word
        for word in re.split(r"\s+", text.strip())
        if re.fullmatch(r"[^\W\d_][^\d_&|]{1,}", word, flags=re.UNICODE)
    ]
    title_or_hyphenated_words = [
        word for word in alpha_words
        if "-" in word or re.match(r"^[A-Z][a-z]{2,}", word)
    ]
    arabic_words = re.findall(r"[\u0600-\u06FF]{2,}", text)
    return (
        suffix_hit
        or uppercase_words >= 2
        or len(arabic_words) >= 2
        or (letters >= 6 and len(title_or_hyphenated_words) >= 1 and len(alpha_words) <= 4 and not _looks_like_product_row(plain))
        or (letters >= 8 and len(alpha_words) >= 2 and not _looks_like_product_row(plain))
    )


def _is_table_header(plain: str) -> bool:
    keyword_hits = sum(1 for word in TABLE_WORDS if re.search(rf"\b{re.escape(word)}\b", plain))
    return keyword_hits >= 3 or bool(re.search(r"\bid\s*\|?\s*description\b", plain))


def _is_label_line(plain: str, labels: tuple[str, ...]) -> bool:
    normalized = plain.strip().rstrip(":")
    if normalized in labels:
        return True
    for label in labels:
        if normalized.startswith(f"{label}:") or normalized.startswith(f"{label} #"):
            return True
        if len(normalized) <= len(label) + 4 and label in normalized.split():
            return True
    return False


def _is_customer_label_line(plain: str) -> bool:
    return _is_label_line(plain, CUSTOMER_LABELS)


def _is_supplier_label_line(plain: str) -> bool:
    return _is_label_line(plain, SUPPLIER_LABELS)


def _is_payment_label_line(plain: str) -> bool:
    return _is_label_line(plain, PAYMENT_WORDS)


def _is_invoice_label(plain: str) -> bool:
    if _is_label_line(plain, ("invoice date", "date of invoice", "bill date", "date facture", "date d'emission", "date d emission")):
        return True
    return bool(re.search(r"\b(?:invoice|facture)\b", plain) and re.search(r"\b(?:no|number|numero|numero|#|n°|ref|reference)\b", plain))


def _is_due_date_label(plain: str) -> bool:
    return _is_label_line(plain, ("due date", "date d'echeance", "echeance", "payment due", "date limite", "echéance"))


def _is_subtotal_label(plain: str) -> bool:
    return bool(re.search(r"\b(?:subtotal|sub total|sous-total|total ht|h\.t|htva)\b", plain))


def _is_tax_label(plain: str) -> bool:
    if re.search(r"\b(?:tax\s*id|taxid|vat\s*(?:number|no\.?|id)|matricule|identifier)\b", plain):
        return False
    return bool(re.search(r"\b(?:sales tax|tax|vat|tva)\b", plain))


def _is_total_label(plain: str) -> bool:
    if plain in {"total", "total:"}:
        return True
    return bool(re.search(r"\b(?:total due|grand total|amount due|balance due|total ttc|invoice total|montant ttc)\b", plain))


def _is_date_candidate(text: str) -> bool:
    return bool(re.search(r"\b(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2})\b", text))


def _is_amount(text: str) -> bool:
    if not re.search(r"\d", text):
        return False
    value = parse_amount(text)
    return value is not None and ("$" in text or "€" in text or re.search(r"\d+[,.]\d{2,3}\b", text))


def _is_postal_code_only(text: str) -> bool:
    clean = text.strip().upper()
    return bool(re.fullmatch(r"[A-Z]\d[A-Z]\s*\d[A-Z]\d|\d{4,6}|[A-Z]{1,3}\s*\d[A-Z0-9]{2,5}", clean))


def _looks_like_address(text: str) -> bool:
    plain = strip_accents(text).lower()
    return bool(re.search(r"\d", text) and (any(word in plain for word in ADDRESS_WORDS) or re.search(r"\b[A-Z]\d[A-Z]\s*\d[A-Z]\d\b", text.upper())))


def _looks_like_product_row(plain: str) -> bool:
    if any(word in plain for word in ("subtotal", "total due", "sales tax", "shipping")):
        return False
    return sum(1 for word in TABLE_WORDS if word in plain) >= 1 and len(re.findall(r"\d", plain)) >= 2


def _has_money_context(plain: str) -> bool:
    return any(word in plain for word in ("total", "tax", "price", "amount", "subtotal"))


def _is_low_confidence_noise(node: DocumentNode) -> bool:
    text = node.text.strip()
    if node.confidence is not None and node.confidence < 0.35:
        if len(text) <= 20 or not _is_company_candidate(text):
            return True
    if re.fullmatch(r"[\W_]+", text):
        return True
    return False
