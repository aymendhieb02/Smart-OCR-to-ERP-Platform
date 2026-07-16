from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.core.schemas import BoundingBox, OCRLine
from app.services.document_layout import group_ocr_lines, reconstruct_tables
from app.services.field_extractor import extract_with_candidates
from scripts import evaluate_dataset


def line(text: str, x1: float, y1: float, x2: float, y2: float, confidence: float = 0.92, idx: int = 0) -> OCRLine:
    return OCRLine(text=text, confidence=confidence, page_number=1, bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2), line_index=idx)


def extract(blocks: list[OCRLine]):
    text = "\n".join(block.text for block in blocks)
    fields, candidates, confidences, debug = extract_with_candidates(text, blocks)
    return fields, candidates, confidences, debug


def test_low_confidence_noise_is_not_supplier() -> None:
    fields, candidates, _confidences, debug = extract([
        line("NV Ole", 40, 20, 110, 38, confidence=0.21),
        line("Invoice # INV-1001", 420, 20, 580, 38),
        line("Total Due", 420, 700, 520, 720),
        line("1291.78", 560, 700, 640, 720),
    ])

    assert fields.supplier_name is None
    assert "document_graph" in debug
    assert all(candidate.value != "NV Ole" for candidate in candidates.get("supplier_name", []))


def test_real_company_selected_as_supplier() -> None:
    fields, _candidates, _confidences, _debug = extract([
        line("AUTOLIV ELECTRONICS CANADA INC.", 30, 25, 310, 45),
        line("123 Industrial Road", 30, 52, 200, 70),
        line("phone 555 123 4567", 30, 76, 190, 92),
        line("Invoice # INV-2002", 430, 25, 590, 45),
    ])

    assert fields.supplier_name == "AUTOLIV ELECTRONICS CANADA INC."


def test_postal_code_rejected_as_supplier_name() -> None:
    fields, candidates, _confidences, _debug = extract([
        line("LIW 3V4", 35, 20, 95, 38),
        line("Invoice # INV-3003", 420, 22, 590, 40),
    ])

    assert fields.supplier_name is None
    assert all(candidate.value != "LIW 3V4" for candidate in candidates.get("supplier_name", []))


def test_supplier_company_with_suffix_wins() -> None:
    fields, _candidates, _confidences, _debug = extract([
        line("DURHAM ELEVATOR INTERIORS INC.", 30, 20, 300, 40),
        line("55 Summit Road", 30, 48, 180, 66),
        line("Bill To", 420, 160, 490, 180),
        line("Customer Group LLC", 420, 188, 590, 208),
    ])

    assert fields.supplier_name == "DURHAM ELEVATOR INTERIORS INC."
    assert fields.customer_name == "Customer Group LLC"


def test_table_header_and_payment_details_never_become_party_names() -> None:
    fields, candidates, _confidences, _debug = extract([
        line("ID | DESCRIPTION QUANTITY PRICE TOTAL", 35, 260, 650, 285),
        line("Payment Details", 420, 130, 560, 150),
        line("Bill To", 420, 165, 490, 185),
        line("123 Any Street", 420, 190, 550, 210),
    ])

    assert fields.supplier_name is None
    assert fields.customer_name is None
    party_values = [candidate.value for field in ("supplier_name", "customer_name") for candidate in candidates.get(field, [])]
    assert "ID | DESCRIPTION QUANTITY PRICE TOTAL" not in party_values
    assert "Payment Details" not in party_values


def test_total_due_next_line_extracts_amount_ttc() -> None:
    fields, candidates, _confidences, _debug = extract([
        line("Total Due", 410, 700, 510, 720),
        line("1291.78", 540, 700, 620, 720),
    ])

    assert fields.amount_ttc == 1291.78
    assert any(candidate.source.startswith("document graph totals") for candidate in candidates.get("amount_ttc", []))


def test_consistent_totals_group_candidates_visible() -> None:
    fields, candidates, _confidences, _debug = extract([
        line("Subtotal 1150.00", 410, 620, 560, 640),
        line("Sales Tax 8% 91.78", 410, 650, 590, 670),
        line("S&H 50.00", 410, 680, 520, 700),
        line("Total Due 1291.78", 410, 710, 590, 730),
    ])

    assert fields.amount_ht == 1150.0
    assert fields.tva_amount == 91.78
    assert fields.amount_ttc == 1291.78
    assert any(candidate.source == "document graph consistent totals group" for candidate in candidates.get("amount_ttc", []))


def test_combined_table_header_creates_table_and_line_item() -> None:
    blocks = [
        line("ID | DESCRIPTION QUANTITY PRICE TOTAL", 20, 250, 720, 275, idx=1),
        line("01 Widget Premium Tape 2 12.00 24.00", 20, 292, 720, 316, idx=2),
        line("02 Safety Gloves 4 11.00 44.00", 20, 330, 720, 354, idx=3),
        line("Total Due 68.00", 520, 430, 700, 454, idx=4),
    ]

    tables = reconstruct_tables(blocks, group_ocr_lines(blocks))
    fields, _candidates, _confidences, _debug = extract(blocks)

    assert tables
    assert len(fields.line_items) >= 1
    assert any(item.description and "Widget" in item.description for item in fields.line_items)


def test_evaluation_json_serializes_date_datetime_and_decimal(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    evaluate_dataset.write_json(path, {"date": date(2026, 5, 6), "datetime": datetime(2026, 5, 6, 1, 2, 3), "amount": Decimal("12.30")})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"date": "2026-05-06", "datetime": "2026-05-06T01:02:03", "amount": 12.3}


def test_compute_summary_with_date_row_serializable(tmp_path: Path) -> None:
    args = argparse.Namespace(run_id="run", mode="smoke", seed=42)
    rows = [{"validation_status": "valid", "processing_time_seconds": "1", "invoice_date": date(2026, 5, 6)}]
    run_dir = tmp_path / "run"

    evaluate_dataset.write_progress(run_dir, rows, [], [], [("batch_1", Path("a.jpg"))], {str(Path("a.jpg"))}, time.perf_counter(), args)

    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["docs_processed"] == 1
