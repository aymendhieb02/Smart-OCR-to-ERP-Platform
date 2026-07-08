from app.core.schemas import Candidate, ExtractedInvoiceFields, LineItem
from app.services.extraction_quality import apply_extraction_quality_gate


def test_quality_gate_rejects_bad_field_values_from_failing_batch_example():
    fields = ExtractedInvoiceFields(
        supplier_name="acct_1N8CpQGmFzaQxIilDx",
        customer_name="Quantity",
        invoice_number="ber",
        purchase_order_number="nge",
        amount_ht=100.0,
        tva_amount=12.0,
        amount_ttc=140.0,
        tax_rate=19.0,
        line_items=[
            LineItem(description="cru ing ponge", quantity=2, unit_price=10, line_total_ttc=99, total=99),
            LineItem(description="ranster", quantity=None, unit_price=4, line_total_ttc=8, total=8),
        ],
    )
    candidates = {
        "supplier_name": [Candidate(field="supplier_name", value=fields.supplier_name, score=0.8, source="ocr header")],
        "customer_name": [Candidate(field="customer_name", value=fields.customer_name, score=0.8, source="table header")],
        "invoice_number": [Candidate(field="invoice_number", value=fields.invoice_number, score=0.8, source="weak regex")],
        "purchase_order_number": [Candidate(field="purchase_order_number", value=fields.purchase_order_number, score=0.8, source="weak regex")],
    }
    confidences = {key: 0.8 for key in candidates}

    result = apply_extraction_quality_gate(fields, candidates, confidences)

    assert result.sanitized_fields.supplier_name is None
    assert result.sanitized_fields.customer_name is None
    assert result.sanitized_fields.invoice_number is None
    assert result.sanitized_fields.purchase_order_number is None
    assert result.sanitized_fields.amount_ht is None
    assert result.sanitized_fields.tva_amount is None
    assert result.sanitized_fields.amount_ttc is None
    assert result.sanitized_fields.line_items == []
    assert len(result.line_items_needs_review) == 2
    assert result.validation_report["extraction_status"] == "needs_review"
    assert result.rejected_candidates["supplier_name"]
    assert result.rejected_candidates["customer_name"]
