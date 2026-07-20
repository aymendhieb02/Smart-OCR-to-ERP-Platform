from app.core.schemas import BoundingBox, Candidate, OCRLine
from app.services.field_extractor import extract_with_candidates
from app.services.party_resolver import resolve_parties


def line(text: str, x1: float, y1: float, x2: float, y2: float, idx: int) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.94,
        page_number=1,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        line_index=idx,
    )


def test_french_month_invoice_and_due_dates_are_label_aware() -> None:
    blocks = [
        line("Date facture", 430, 80, 530, 102, 1),
        line("1 janvier 2026", 560, 80, 700, 102, 2),
        line("Date d'echeance", 430, 120, 560, 142, 3),
        line("10 juillet 2026", 590, 120, 740, 142, 4),
    ]

    fields, _candidates, _confidences, _debug = extract_with_candidates("\n".join(block.text for block in blocks), blocks)

    assert fields.invoice_date.isoformat() == "2026-01-01"
    assert fields.due_date.isoformat() == "2026-07-10"


def test_delivery_and_order_dates_do_not_become_invoice_date() -> None:
    blocks = [
        line("Order Date", 420, 60, 520, 82, 1),
        line("01/02/2026", 550, 60, 650, 82, 2),
        line("Delivery Date", 420, 92, 540, 114, 3),
        line("05/02/2026", 550, 92, 650, 114, 4),
        line("Invoice Date", 420, 124, 540, 146, 5),
        line("15/02/2026", 550, 124, 650, 146, 6),
    ]

    fields, _candidates, _confidences, _debug = extract_with_candidates("\n".join(block.text for block in blocks), blocks)

    assert fields.invoice_date.isoformat() == "2026-02-15"


def test_english_numeric_invoice_date_prefers_month_first_when_ambiguous() -> None:
    blocks = [
        line("Invoice Date", 430, 80, 540, 102, 1),
        line("01/05/2017", 560, 80, 660, 102, 2),
    ]

    fields, _candidates, _confidences, _debug = extract_with_candidates("\n".join(block.text for block in blocks), blocks)

    assert fields.invoice_date.isoformat() == "2017-01-05"


def test_purchase_order_and_currency_are_recovered_from_labeled_metadata() -> None:
    text = """
    Invoice No INV-200
    Issued:
    January 10, 2026
    Purchase Order
    PO-77881
    Grand Total 1,250.00 EUR
    """

    fields, _candidates, _confidences, _debug = extract_with_candidates(text)

    assert fields.invoice_date.isoformat() == "2026-01-10"
    assert fields.purchase_order_number == "PO-77881"
    assert fields.currency == "EUR"


def test_party_resolver_rejects_table_headers_and_shipping_labels() -> None:
    decision = resolve_parties({
        "supplier_name": [
            Candidate(field="supplier_name", value="DESCRIPTION QUANTITY UNIT PRICE TOTAL", score=0.95, source="table header"),
            Candidate(field="supplier_name", value="Gardel Metal Inc.", score=0.70, source="header supplier block"),
        ],
        "customer_name": [
            Candidate(field="customer_name", value="SHIP_TO", score=0.95, source="customer label"),
            Candidate(field="customer_name", value="North Star Medical LLC", score=0.70, source="customer label block"),
        ],
    })

    assert decision.supplier is not None
    assert decision.supplier.value == "Gardel Metal Inc."
    assert decision.customer is not None
    assert decision.customer.value == "North Star Medical LLC"


def test_party_resolver_rejects_address_contact_and_footer_candidates() -> None:
    decision = resolve_parties({
        "supplier_name": [
            Candidate(field="supplier_name", value="15 Rue des Entrepreneurs", score=0.95, source="header"),
            Candidate(field="supplier_name", value="contact@example.com", score=0.95, source="header"),
            Candidate(field="supplier_name", value="Vital Distribution", score=0.66, source="header supplier block"),
        ],
        "customer_name": [
            Candidate(field="customer_name", value="Merci pour votre confiance", score=0.95, source="footer"),
            Candidate(field="customer_name", value="Pharma Plus SARL", score=0.66, source="customer label block"),
        ],
    })

    assert decision.supplier is not None
    assert decision.supplier.value == "Vital Distribution"
    assert decision.customer is not None
    assert decision.customer.value == "Pharma Plus SARL"
