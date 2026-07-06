from typing import Any

from app.core.schemas import OCRLine


def repair_with_ai(extracted_text: str, ocr_blocks: list[OCRLine], current_json: dict[str, Any]) -> dict[str, Any]:
    build_ai_repair_prompt(extracted_text, ocr_blocks, current_json)
    return current_json


def build_ai_repair_prompt(extracted_text: str, ocr_blocks: list[OCRLine], current_json: dict[str, Any]) -> str:
    missing_fields = _find_missing_fields(current_json)
    return f"""You are an invoice/document extraction repair assistant.
Return strict JSON only. Do not add markdown.

Target schema:
{{
  "document_type": "invoice|delivery_note|credit_note|receipt|purchase_order|unknown",
  "supplier": {{"name": null, "tax_id": null}},
  "customer": {{"name": null, "tax_id": null}},
  "document": {{"number": null, "date": null, "due_date": null, "currency": null}},
  "amounts": {{"ht": null, "tva": null, "ttc": null, "tax_rate": null}},
  "line_items": []
}}

Missing fields:
{missing_fields}

Current extracted JSON:
{current_json}

OCR text:
{extracted_text}

OCR blocks:
{[block.model_dump(mode="json") for block in ocr_blocks[:120]]}
"""


def _find_missing_fields(payload: dict[str, Any]) -> list[str]:
    missing = []
    for path in (
        ("supplier", "name"),
        ("document", "number"),
        ("document", "date"),
        ("amounts", "ttc"),
    ):
        node: Any = payload
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if node in (None, ""):
            missing.append(".".join(path))
    return missing
