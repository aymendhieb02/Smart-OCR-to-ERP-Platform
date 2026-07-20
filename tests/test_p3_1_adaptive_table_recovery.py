from __future__ import annotations

import inspect

from app.core.schemas import BoundingBox, OCRLine
from app.services import table_reconstruction_engine
from app.services.table_reconstruction_engine import reconstruct_line_items
from scripts.large_benchmark_runner import _table_quality_row


def block(text: str, x1: float, y1: float, x2: float, y2: float, index: int) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=0.95,
        page_number=1,
        line_index=index,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
    )


def test_key_value_item_records_are_reconstructed() -> None:
    blocks = [
        block("Description: Consulting Service", 60, 100, 280, 118, 1),
        block("Quantity: 2", 60, 124, 160, 142, 2),
        block("Unit Price: 150.00", 60, 148, 210, 166, 3),
        block("Amount: 300.00", 60, 172, 190, 190, 4),
        block("Description: Support Package", 60, 220, 280, 238, 5),
        block("Quantity: 1", 60, 244, 160, 262, 6),
        block("Unit Price: 80.00", 60, 268, 210, 286, 7),
        block("Amount: 80.00", 60, 292, 190, 310, 8),
    ]

    result = reconstruct_line_items(blocks)

    assert result.selected_strategy == "KEY_VALUE_RECORDS"
    assert [item.description for item in result.line_items] == ["Consulting Service", "Support Package"]
    assert [item.total for item in result.line_items] == [300, 80]


def test_repeated_vertical_product_blocks_are_supported() -> None:
    blocks = [
        block("Consulting Service", 80, 100, 250, 118, 1),
        block("2", 300, 124, 320, 142, 2),
        block("150.00", 390, 124, 450, 142, 3),
        block("300.00", 520, 124, 585, 142, 4),
        block("Support Package", 80, 190, 240, 208, 5),
        block("1", 300, 214, 320, 232, 6),
        block("80.00", 390, 214, 450, 232, 7),
        block("80.00", 520, 214, 585, 232, 8),
    ]

    result = reconstruct_line_items(blocks)

    assert result.selected_strategy in {"REPEATED_VERTICAL_BLOCKS", "NUMERIC_ANCHORED_ROWS", "HEADERLESS_COLUMNAR"}
    assert len(result.line_items) == 2


def test_headerless_aligned_table_is_reviewable() -> None:
    blocks = [
        block("Service A", 60, 100, 160, 118, 1),
        block("2", 300, 100, 320, 118, 2),
        block("10.00", 390, 100, 450, 118, 3),
        block("20.00", 520, 100, 585, 118, 4),
        block("Service B", 60, 130, 160, 148, 5),
        block("3", 300, 130, 320, 148, 6),
        block("20.00", 390, 130, 450, 148, 7),
        block("60.00", 520, 130, 585, 148, 8),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 2
    assert all("review" in (item.source or "") or item.confidence <= 0.62 for item in result.line_items)


def test_single_generic_header_token_does_not_confirm_table() -> None:
    blocks = [
        block("Total", 500, 100, 560, 118, 1),
        block("500.00", 590, 100, 650, 118, 2),
    ]

    result = reconstruct_line_items(blocks)

    assert not result.diagnostics["header_confirmed"]
    assert not result.line_items


def test_header_candidate_without_body_is_not_confirmed_as_rows() -> None:
    blocks = [
        block("Description", 60, 100, 170, 118, 1),
        block("Total", 500, 100, 560, 118, 2),
    ]

    result = reconstruct_line_items(blocks)

    assert result.diagnostics["header_candidate_found"]
    assert not result.diagnostics["rows_reconstructed"]


def test_overmerged_two_rows_are_split() -> None:
    blocks = [
        block("1 Service A 2 10.00 20.00 2 Service B 3 20.00 60.00", 60, 100, 650, 118, 1),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 2
    assert result.line_items[0].description == "Service A"
    assert result.line_items[1].description == "Service B"


def test_undermerged_description_joins_numeric_anchor() -> None:
    blocks = [
        block("Advanced Consulting", 60, 100, 240, 118, 1),
        block("2", 300, 124, 320, 142, 2),
        block("150.00", 390, 124, 450, 142, 3),
        block("300.00", 520, 124, 585, 142, 4),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 1
    assert result.line_items[0].description == "Advanced Consulting"


def test_document_total_is_not_used_as_item_total() -> None:
    blocks = [
        block("Subtotal", 430, 100, 500, 118, 1),
        block("300.00", 520, 100, 585, 118, 2),
        block("Total Due", 430, 130, 520, 148, 3),
        block("357.00", 520, 130, 585, 148, 4),
    ]

    result = reconstruct_line_items(blocks)

    assert not result.line_items


def test_tax_summary_is_not_treated_as_products() -> None:
    blocks = [
        block("VAT 19%", 430, 100, 500, 118, 1),
        block("57.00", 520, 100, 585, 118, 2),
        block("Tax Summary", 430, 130, 540, 148, 3),
    ]

    result = reconstruct_line_items(blocks)

    assert not result.line_items


def test_needs_review_row_survives_optional_missing_values() -> None:
    blocks = [
        block("Description: Consulting Service", 60, 100, 280, 118, 1),
        block("Amount: 300.00", 60, 124, 190, 142, 2),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 1
    assert result.rows[0].validation_status == "needs_review"


def test_invalid_contradictory_row_is_rejected_from_validated_status() -> None:
    blocks = [
        block("Description: Consulting Service", 60, 100, 280, 118, 1),
        block("Quantity: 2", 60, 124, 160, 142, 2),
        block("Unit Price: 150.00", 60, 148, 210, 166, 3),
        block("Amount: 20.00", 60, 172, 190, 190, 4),
    ]

    result = reconstruct_line_items(blocks)

    assert result.rows[0].validation_status == "invalid"
    assert "ROW_TOTAL_MISMATCH" in result.rows[0].warning_codes


def test_strategy_selection_prefers_arithmetic_consistent_output() -> None:
    blocks = [
        block("Description: Service A", 60, 100, 260, 118, 1),
        block("Quantity: 2", 60, 124, 160, 142, 2),
        block("Unit Price: 10.00", 60, 148, 210, 166, 3),
        block("Amount: 20.00", 60, 172, 190, 190, 4),
        block("Service A", 60, 250, 160, 268, 5),
        block("2", 300, 250, 320, 268, 6),
        block("10.00", 390, 250, 450, 268, 7),
        block("999.00", 520, 250, 585, 268, 8),
    ]

    result = reconstruct_line_items(blocks)

    assert result.selected_strategy == "KEY_VALUE_RECORDS"
    assert result.line_items[0].total == 20


def test_strategy_selection_does_not_prefer_maximum_row_count_blindly() -> None:
    blocks = [
        block("Description: Service A", 60, 100, 260, 118, 1),
        block("Quantity: 2", 60, 124, 160, 142, 2),
        block("Unit Price: 10.00", 60, 148, 210, 166, 3),
        block("Amount: 20.00", 60, 172, 190, 190, 4),
        block("Subtotal", 60, 210, 130, 228, 5),
        block("20.00", 520, 210, 585, 228, 6),
        block("VAT", 60, 235, 100, 253, 7),
        block("3.80", 520, 235, 585, 253, 8),
    ]

    result = reconstruct_line_items(blocks)

    assert len(result.line_items) == 1
    assert result.line_items[0].description == "Service A"


def test_metric_distinguishes_header_candidate_from_confirmed_header() -> None:
    row = {
        "document_id": "doc",
        "dataset_name": "dataset",
        "line_items_count_pred": 0,
        "line_items_count_true": 1,
        "validated_line_items_count_pred": 0,
        "review_line_items_count_pred": 0,
        "failure_codes": [],
        "table_diagnostics": {
            "header_candidate_found": True,
            "header_confirmed": False,
            "table_region_detected": False,
        },
    }

    quality = _table_quality_row(row)

    assert quality["header_candidate_found"] is True
    assert quality["header_confirmed"] is False


def test_no_dataset_name_branching_in_extraction_logic() -> None:
    source = inspect.getsource(table_reconstruction_engine).lower()

    assert "invoices-donut" not in source
    assert "invoicexpert" not in source
    assert "dataset_name" not in source

