from app.core.schemas import ExtractedInvoiceFields
from app.services.financial_reasoner import verify_amount_field_consistency


def test_amount_ttc_outlier_when_ht_vat_and_rate_agree():
    fields = ExtractedInvoiceFields(
        amount_ht=4391.11,
        tva_amount=399.19,
        amount_ttc=4391.10,
        tax_rate=9.09,
    )

    result = verify_amount_field_consistency(fields)

    assert result["amount_ht"]["status"] == "consistent"
    assert result["tva_amount"]["status"] == "consistent"
    assert result["tax_rate"]["status"] == "consistent"
    assert result["amount_ttc"]["status"] == "inconsistent"
    assert result["amount_ttc"]["expected_value"] == 4790.3
    assert result["amount_ttc"]["outlier"] is True


def test_amount_fields_consistent_set_has_no_false_positive():
    fields = ExtractedInvoiceFields(
        amount_ht=100.0,
        tva_amount=19.0,
        amount_ttc=119.0,
        tax_rate=19.0,
    )

    result = verify_amount_field_consistency(fields)

    assert result["amount_ht"]["status"] == "consistent"
    assert result["tva_amount"]["status"] == "consistent"
    assert result["amount_ttc"]["status"] == "consistent"
    assert result["tax_rate"]["status"] == "consistent"


def test_hard_guard_flags_ht_equal_ttc_with_nonzero_vat():
    fields = ExtractedInvoiceFields(
        amount_ht=250.0,
        tva_amount=25.0,
        amount_ttc=250.0,
        tax_rate=10.0,
    )

    result = verify_amount_field_consistency(fields)

    assert result["amount_ttc"]["status"] == "inconsistent"
    assert result["amount_ttc"]["expected_value"] == 275.0
    assert result["amount_ttc"]["guard"] == "ht_equals_ttc_with_nonzero_vat"
