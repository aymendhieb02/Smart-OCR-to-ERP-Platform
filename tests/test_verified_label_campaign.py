from __future__ import annotations

import json
from pathlib import Path

from scripts import verified_label_campaign as campaign
from scripts.manual_benchmark_utils import (
    accuracy_claims_allowed_for_labels,
    meaningful_line_items,
    validate_verified_label_quality,
)


def valid_label() -> dict:
    return {
        "filename": "invoice.png",
        "source_path": None,
        "document_type": "invoice",
        "supplier_name": "ACME SARL",
        "customer_name": "Client SARL",
        "invoice_number": "INV-001",
        "invoice_date": "2026-01-01",
        "due_date": None,
        "currency": "TND",
        "amount_ht": 100.0,
        "tax_amount": 19.0,
        "amount_ttc": 119.0,
        "tax_rate": 19.0,
        "line_items": [{
            "reference": None,
            "description": "Service",
            "quantity": 1,
            "unit": None,
            "unit_price": 100,
            "tax_rate": 19,
            "line_total_ht": 100,
            "line_total_ttc": 119,
        }],
        "notes": "verified from source image",
        "verified_by_human": True,
        "verification_status": "verified",
        "verified_by": "reviewer",
        "verified_at": "2026-07-21T00:00:00Z",
        "source_document": "invoice.png",
        "uncertain_fields": [],
    }


def test_empty_values_are_rejected() -> None:
    label = valid_label()
    label["supplier_name"] = ""

    quality = validate_verified_label_quality(label)

    assert any("empty string" in error for error in quality["errors"])
    assert quality["eligible_for_accuracy"] is False


def test_null_allowed_for_genuinely_absent_optional_due_date() -> None:
    label = valid_label()
    label["due_date"] = None

    quality = validate_verified_label_quality(label)

    assert "due_date" not in quality["missing_fields"]
    assert quality["eligible_for_accuracy"] is True


def test_blank_line_item_templates_are_ignored_for_meaningful_rows() -> None:
    rows = [{"description": None, "quantity": None}, valid_label()["line_items"][0]]

    assert len(meaningful_line_items(rows)) == 1


def test_verified_status_requires_metadata() -> None:
    label = valid_label()
    label["verified_by"] = None

    quality = validate_verified_label_quality(label)

    assert "verified label missing metadata: verified_by" in quality["errors"]
    assert quality["eligible_for_accuracy"] is False


def test_inconsistent_totals_are_detected_without_explanation() -> None:
    label = valid_label()
    label["amount_ttc"] = 150
    label["notes"] = ""

    quality = validate_verified_label_quality(label)

    assert any("amount_ht + tax_amount" in error for error in quality["errors"])


def test_incomplete_line_items_are_reported() -> None:
    label = valid_label()
    label["line_items"][0]["quantity"] = None

    quality = validate_verified_label_quality(label)

    assert any("missing quantity" in error for error in quality["errors"])


def test_only_fully_verified_documents_enable_accuracy_claims() -> None:
    draft = valid_label()
    draft["verification_status"] = "draft"

    assert accuracy_claims_allowed_for_labels([valid_label()]) is True
    assert accuracy_claims_allowed_for_labels([valid_label(), draft]) is False


def test_campaign_requires_exactly_ten_documents(tmp_path: Path) -> None:
    selection = {"document_count": 9}
    qualities = []
    labels = []

    payload = campaign.build_validation_payload(selection, qualities, labels)

    assert payload["exactly_10_selected"] is False
    assert payload["accuracy_claims_allowed"] is False


def test_review_queue_is_generated(tmp_path: Path) -> None:
    image = tmp_path / "invoice.png"
    image.write_bytes(b"fake")
    label_path = tmp_path / "invoice.json"
    label_path.write_text(json.dumps(valid_label()), encoding="utf-8")
    document = type("Doc", (), {
        "filename": "invoice.png",
        "dataset": "test",
        "image_path": image,
        "label_path": label_path,
    })()
    quality = validate_verified_label_quality(valid_label())

    campaign.write_review_queue(tmp_path / "queue.html", [document], [valid_label()], [quality])

    assert "Download edited label JSON" in (tmp_path / "queue.html").read_text(encoding="utf-8")
