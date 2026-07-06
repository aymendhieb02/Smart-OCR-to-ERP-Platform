import json
import re
from pathlib import Path

from app.core.config import settings
from app.core.schemas import ERPInvoiceJSON


def write_erp_json(payload: ERPInvoiceJSON) -> Path:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    invoice_ref = payload.invoice.number or Path(payload.metadata.source_file).stem
    safe_ref = re.sub(r"[^A-Za-z0-9_.-]+", "_", invoice_ref).strip("_") or "invoice"
    output_path = settings.output_dir / f"{safe_ref}.json"
    output_path.write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path
