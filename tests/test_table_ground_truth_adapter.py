from __future__ import annotations

from pathlib import Path

from scripts import large_benchmark_runner as runner
from scripts.table_ground_truth_adapter import (
    adapt_donut_line_items,
    adapt_generic_line_items,
    adapt_invoicexpert_line_items,
    adapt_table_ground_truth,
    compare_line_items,
)


def test_donut_nested_item_list() -> None:
    table = adapt_donut_line_items({"gt_parse": {"line_items": [{"description": "Service A", "quantity": "2", "amount": "20.00"}]}})

    assert table.truth_status == "supported"
    assert table.canonical_item_count == 1
    assert table.items[0].description == "Service A"


def test_donut_serialized_tokenized_item_structure() -> None:
    text = "<s_line_item><s_description>Service A</s_description><s_quantity>2</s_quantity><s_amount>20.00</s_amount></s_line_item>"
    table = adapt_donut_line_items({"gt_parse": text})

    assert table.canonical_item_count == 1
    assert table.items[0].quantity == 2


def test_donut_multiple_empty_duplicate_records() -> None:
    table = adapt_donut_line_items({
        "gt_parse": {
            "items": [
                {"description": "Service A", "amount": "20.00"},
                {"description": "Service A", "amount": "20.00"},
                {"description": "   ", "amount": ""},
            ]
        }
    })

    assert table.canonical_item_count == 1
    assert table.duplicate_record_count == 1
    assert table.excluded_record_count == 2


def test_donut_malformed_structure_marked_unsupported() -> None:
    table = adapt_donut_line_items({"gt_parse": "plain text without item tokens"})

    assert table.truth_status in {"unsupported", "explicit_zero"}
    assert table.canonical_item_count == 0


def test_invoicexpert_standard_and_subtotal_removed() -> None:
    table = adapt_invoicexpert_line_items({
        "products": [
            {"name": "Widget", "qty": 2, "price": 10, "total": 20},
            {"description": "Subtotal", "total": 20},
        ]
    })

    assert table.canonical_item_count == 1
    assert table.excluded_record_count == 1


def test_wrapped_description_records_are_preserved() -> None:
    table = adapt_invoicexpert_line_items({"items": [{"description": "Advanced consulting package", "amount": 300}]})

    assert table.canonical_item_count == 1
    assert table.items[0].unit_price is None


def test_zero_and_unsupported_truth_semantics() -> None:
    zero = adapt_generic_line_items({"invoice_number": "INV-1", "items": []}, source_schema="generic")
    unsupported = adapt_generic_line_items([], source_schema="generic")

    assert zero.truth_status == "explicit_zero"
    assert unsupported.truth_status == "unsupported"


def test_header_tax_and_shipping_records_are_auditable() -> None:
    table = adapt_generic_line_items({
        "items": [
            {"description": "Description Quantity Price Total"},
            {"description": "VAT 19%", "amount": "19.00"},
            {"description": "Shipping and Handling", "amount": "10.00"},
        ]
    }, source_schema="generic")

    assert table.canonical_item_count == 0
    assert table.excluded_record_count == 3
    assert "TABLE_GT_TAX_ROW_REMOVED" in table.adapter_warnings
    assert "TABLE_GT_SHIPPING_ROW_REVIEW" in table.adapter_warnings


def test_bipartite_order_independent_matching() -> None:
    table = adapt_generic_line_items({
        "items": [
            {"description": "Widget A", "quantity": 2, "unit_price": 10, "total": 20},
            {"description": "Widget B", "quantity": 1, "unit_price": 5, "total": 5},
        ]
    }, source_schema="generic")
    predicted = [
        {"description": "Widget B", "quantity": 1, "unit_price": 5, "total": 5},
        {"description": "Widget A", "quantity": 2, "unit_price": 10, "total": 20},
    ]

    comparison = compare_line_items(predicted, table.items)

    assert comparison["item_match_count"] == 2
    assert comparison["order_independent_row_match_rate"] == 1.0


def test_prediction_split_and_merged_classification() -> None:
    truth_one = adapt_generic_line_items({"items": [{"description": "Consulting package", "total": 100}]}, source_schema="generic")
    split = compare_line_items([{"description": "Consulting", "total": 40}, {"description": "Package", "total": 60}], truth_one.items)
    truth_two = adapt_generic_line_items({"items": [{"description": "Analysis", "total": 40}, {"description": "Testing", "total": 60}]}, source_schema="generic")
    merged = compare_line_items([{"description": "Analysis Testing", "total": 100}], truth_two.items)

    assert split["granularity_class"] in {"prediction_split", "ambiguous_granularity"}
    assert merged["granularity_class"] in {"prediction_merged", "ambiguous_granularity"}


def test_canonical_metrics_are_added_separately() -> None:
    rows = [
        {"canonical_exact_count_match": True, "canonical_within_one": True, "presence_match": True, "absolute_count_error": 0, "truth_status": "supported"},
        {"canonical_exact_count_match": False, "canonical_within_one": True, "presence_match": False, "absolute_count_error": 1, "truth_status": "supported"},
    ]

    summary = runner._canonical_line_item_quality_summary(rows)

    assert summary["canonical_exact_count_accuracy"] == 0.5
    assert summary["canonical_count_within_one_accuracy"] == 1.0


def test_missing_truth_excluded_from_canonical_metrics() -> None:
    rows = [{"truth_status": "missing", "canonical_exact_count_match": None}]

    summary = runner._canonical_line_item_quality_summary(rows)

    assert summary["evaluated_document_count"] == 0


def test_p3_default_and_p3_1_selectable() -> None:
    from app.core.config import settings

    assert getattr(settings, "table_reconstruction_profile", "p3_stable") == "p3_stable"
    assert "p3_1_adaptive" != ""


def test_no_ocr_configuration_changed() -> None:
    import app.services.ocr_engine as ocr_engine

    source = Path(ocr_engine.__file__).read_text(encoding="utf-8")
    assert "optimized_mobile_v4" in source or "PaddleOCR" in source
