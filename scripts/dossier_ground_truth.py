from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from app.services.ocr_profiles import effective_ocr_config
from app.services.pipeline_runner import DossierProcessResult


ROLE_BY_INDEX = {1: "producer", 2: "ruspina", 3: "customs"}

TARGET_FIELDS = {
    "producer": (
        "document_type", "document_family", "invoice_number", "invoice_date",
        "seller", "supplier", "buyer", "customer", "consignee", "currency",
        "amount_ht", "tax", "amount_ttc", "total", "hs_code", "incoterm",
        "origin", "destination", "payment",
    ),
    "ruspina": (
        "document_type", "document_family", "invoice_number", "invoice_date",
        "referenced_invoice", "seller", "buyer", "customer", "currency", "total",
        "description", "quantity", "unit", "unit_price", "line_total",
        "gross_weight", "net_weight", "number_of_bags", "delivery", "incoterm",
        "origin", "payment",
    ),
    "customs": (
        "document_type", "document_family", "declaration_number",
        "declaration_reference", "referenced_invoice", "invoice_value", "exporter",
        "importer", "declarant", "hs_code", "gross_weight", "net_weight", "origin",
        "destination",
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_commit(repo_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()


def serialize_machine_output(
    result: DossierProcessResult,
    *,
    source_path: Path,
    pipeline_commit: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    config = effective_ocr_config()
    documents = []
    for index, item in enumerate(result.logical_documents, start=1):
        response = item.response
        fields = response.detected_fields.model_dump(mode="json")
        documents.append({
            "logical_document_id": item.group.group_id,
            "document_index": index,
            "physical_page_numbers": list(item.group.pages),
            "document_type": item.group.document_type,
            "document_family": item.group.document_family,
            "detected_fields": fields,
            "expanded_fields": {
                key: value.model_dump(mode="json")
                for key, value in sorted(response.expanded_fields.items())
            },
            "parties": {
                "supplier": fields.get("supplier_name"),
                "customer": fields.get("customer_name"),
                "consignee": None,
                "exporter": None,
                "importer": None,
                "declarant": None,
            },
            "identifiers": {
                "invoice_number": fields.get("invoice_number"),
                "invoice_date": fields.get("invoice_date"),
                "referenced_invoice": None,
                "declaration_reference": None,
            },
            "financial": {
                "currency": fields.get("currency"),
                "amount_ht": fields.get("amount_ht"),
                "tax": fields.get("tva_amount"),
                "amount_ttc": fields.get("amount_ttc"),
                "invoice_value": None,
            },
            "line_items": fields.get("line_items", []),
            "validation": response.validation.model_dump(mode="json"),
            "confidence": {
                "overall": response.validation.confidence,
                "fields": response.field_confidences,
                "breakdown": response.confidence_breakdown,
            },
            "erp_readiness": response.erp_readiness,
            "evidence": [_serialize_evidence(line) for line in response.all_ocr_blocks],
        })
    return {
        "metadata": {
            "source_file": source_path.name,
            "source_sha256": sha256_file(source_path),
            "pipeline_commit": pipeline_commit,
            "ocr_profile": config["ocr_profile"],
            "detector": config["detector"],
            "recognizer": config["recognizer"],
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            "label_status": "machine_prediction",
            "ocr_engine": result.ocr_engine,
        },
        "dossier": {
            "page_count": result.page_count,
            "logical_document_count": len(documents),
            "logical_documents": documents,
        },
    }


def build_review_template(machine: dict[str, Any]) -> dict[str, Any]:
    review_documents = []
    for index, document in enumerate(machine["dossier"]["logical_documents"], start=1):
        role = ROLE_BY_INDEX.get(index, f"document_{index}")
        values = _machine_target_values(document)
        fields = {
            name: _review_field(values.get(name), document["physical_page_numbers"])
            for name in TARGET_FIELDS.get(role, ("document_type", "document_family"))
        }
        review_documents.append({
            "logical_document_id": document["logical_document_id"],
            "document_role": role,
            "physical_page_numbers": document["physical_page_numbers"],
            "fields": fields,
            "line_items": {
                "machine_rows": document.get("line_items", []),
                "verified_rows": None,
                "verification_status": "unverified",
                "notes": None,
                "instructions": "Verify, correct, remove false rows, and add missed rows.",
            },
            "source_evidence": document.get("evidence", []),
        })
    by_role = {item["document_role"]: item for item in review_documents}
    relations = [
        _relation("producer.invoice_number", "ruspina.referenced_invoice", by_role),
        _relation("producer.invoice_number", "customs.referenced_invoice", by_role),
        _relation("producer.total", "customs.invoice_value", by_role),
    ]
    return {
        "metadata": {
            "source_file": machine["metadata"]["source_file"],
            "source_sha256": machine["metadata"]["source_sha256"],
            "pipeline_commit": machine["metadata"]["pipeline_commit"],
            "template_generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "label_status": "draft",
        "human_verified": False,
        "verified_by": None,
        "verified_at": None,
        "logical_documents": review_documents,
        "relations": relations,
    }


def _serialize_evidence(line: Any) -> dict[str, Any]:
    return {
        "text": line.text,
        "physical_page": line.page_number,
        "line_index": line.line_index,
        "bbox": line.bbox.model_dump(mode="json") if line.bbox else None,
        "confidence": line.confidence,
        "source": line.source,
        "page_width": line.page_width,
        "page_height": line.page_height,
        "coordinate_space": line.coordinate_space,
    }


def _machine_target_values(document: dict[str, Any]) -> dict[str, Any]:
    fields = document["detected_fields"]
    return {
        "document_type": document["document_type"],
        "document_family": document["document_family"],
        "invoice_number": fields.get("invoice_number"),
        "invoice_date": fields.get("invoice_date"),
        "referenced_invoice": None,
        "declaration_number": None,
        "declaration_reference": None,
        "seller": fields.get("supplier_name"),
        "supplier": fields.get("supplier_name"),
        "buyer": fields.get("customer_name"),
        "customer": fields.get("customer_name"),
        "consignee": None,
        "exporter": None,
        "importer": None,
        "declarant": None,
        "currency": fields.get("currency"),
        "amount_ht": fields.get("amount_ht"),
        "tax": fields.get("tva_amount"),
        "amount_ttc": fields.get("amount_ttc"),
        "total": fields.get("amount_ttc"),
        "invoice_value": None,
        "hs_code": None,
        "incoterm": None,
        "origin": None,
        "destination": None,
        "payment": None,
        "description": None,
        "quantity": None,
        "unit": None,
        "unit_price": None,
        "line_total": None,
        "gross_weight": None,
        "net_weight": None,
        "number_of_bags": None,
        "delivery": None,
    }


def _review_field(machine_value: Any, pages: list[int]) -> dict[str, Any]:
    return {
        "machine_value": machine_value,
        "verified_value": None,
        "verification_status": "unverified",
        "source_page": pages[0] if len(pages) == 1 else pages,
        "error_classification": None,
        "notes": None,
    }


def _relation(left: str, right: str, documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    left_role, left_field = left.split(".", 1)
    right_role, right_field = right.split(".", 1)
    left_value = documents[left_role]["fields"][left_field]["machine_value"]
    right_value = documents[right_role]["fields"][right_field]["machine_value"]
    status = "unavailable" if left_value is None or right_value is None else (
        "match" if left_value == right_value else "mismatch"
    )
    return {
        "left_field": left,
        "right_field": right,
        "machine_status": status,
        "verified_status": None,
        "verification_status": "unverified",
        "notes": None,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
