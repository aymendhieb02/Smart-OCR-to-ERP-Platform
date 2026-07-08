# Invoice OCR ERP

Professional multilingual OCR-to-ERP extraction API for invoices and commercial documents. It is designed for safe automation: high-confidence valid documents can be exported, uncertain documents go to `needs_review`, and invalid data is blocked from ERP insertion.

## Objective

The system processes PDFs, scanned PDFs, PNG, JPG, JPEG, and TIFF files. It supports French, English, and Arabic-oriented workflows without fine-tuning. The goal is not blind 100% automation. The goal is reliable extraction, validation, traceability, and measurable accuracy.

## Why Regex Alone Is Not Enough

Invoices have different layouts, languages, OCR quality, and field names. A single regex can easily confuse a client name with a supplier, a bank RIB with a line item, or an OCR artifact with an invoice number.

This project uses a hybrid approach:

```text
file upload
-> PDF/image loading
-> preprocessing
-> multilingual OCR
-> OCR blocks with coordinates
-> document type detection
-> layout zones
-> candidate extraction
-> candidate scoring
-> best field selection
-> validation status
-> ERP-safe JSON
```

## Supported Document Types

- `invoice`
- `delivery_note`
- `credit_note`
- `receipt`
- `purchase_order`
- `unknown`

Each document type has different validation rules. For example, an invoice requires a total amount, but a delivery note can be valid without totals.

## Architecture

```text
app/
  api/routes.py                  FastAPI endpoints
  core/schemas.py                Pydantic schemas
  services/file_loader.py        PDF/image loading and page splitting
  services/preprocessing.py      OpenCV cleanup and table-region preprocessing
  services/ocr_engine.py         PaddleOCR/Tesseract OCR normalization
  services/document_classifier.py Multilingual document type detection
  services/layout_analyzer.py    OCR block zones and label-value helpers
  services/field_extractor.py    Candidate-based field extraction
  services/line_item_extractor.py Table row extraction and bank-detail rejection
  services/validator.py          valid / needs_review / invalid rules
  services/ai_repair.py          Disabled-by-default AI repair prompt builder
  services/erp_mapper.py         ERP-safe JSON mapping
scripts/evaluate_dataset.py      Dataset evaluation CLI
dataset/
  images/
  labels/
  predictions/
  reports/
```

## Validation Strategy

The API returns:

- `valid`: high-confidence and business-rule compliant
- `needs_review`: usable extraction but uncertain, incomplete, or low confidence
- `invalid`: missing critical fields or serious business-rule failure

Only `valid` documents should be auto-inserted into ERP.

## Run

```powershell
cd D:\Stage_mr_f\invoice-ocr-erp
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Open:

```text
http://127.0.0.1:8000/docs
```

## API

### Process Document

```powershell
curl -X POST "http://127.0.0.1:8000/process-invoice" -F "file=@invoice.png"
```

Response includes:

- `extracted_text`
- `ocr_blocks`
- `document_classification`
- `detected_fields`
- `field_confidences`
- `extraction_debug`
- `validation`
- `erp_json`

### Evaluate Dataset

Use the tiered evaluator. It defaults to fast `smoke` mode, so development never requires processing all 8,000+ documents.

```powershell
python scripts/evaluate_dataset.py --mode smoke
python scripts/evaluate_dataset.py --mode medium
python scripts/evaluate_dataset.py --mode full --resume
python scripts/evaluate_dataset.py --mode smoke --seed 42
python scripts/evaluate_dataset.py --mode smoke --no-ocr-cache false
```

Outputs are written progressively under `outputs/evaluation/runs/{run_id}`. OCR and layout caches are stored by document hash under `outputs/cache/ocr` and `outputs/cache/layout`.

See `README_BENCHMARK.md` for the full evaluation workflow.

## ERP JSON Shape

```json
{
  "document_type": "invoice",
  "validation_status": "valid",
  "supplier": {
    "name": "Vital Distribution",
    "tax_id": "1234567A/M/000"
  },
  "customer": {
    "name": "PHARMA PLUS",
    "tax_id": "1467890B/M/000"
  },
  "document": {
    "number": "FAC-2026-0042",
    "date": "2026-05-06",
    "due_date": "2026-05-21",
    "currency": "TND"
  },
  "amounts": {
    "ht": 87.0,
    "tva": 16.531,
    "ttc": 104.751,
    "tax_rate": 19.0
  },
  "quality": {
    "overall_confidence": 0.88,
    "field_confidences": {},
    "needs_human_review": false
  }
}
```

## Limitations

- Low-resolution bilingual forms may still need manual review.
- Tesseract Arabic support depends on installed language data.
- Line-item extraction is conservative; unclear rows are safer to review than auto-insert.
- The AI repair layer is intentionally disabled by default and does not call external APIs.

## Tests

```powershell
python -m pytest
```

cd D:\Stage_mr_f\invoice-ocr-erp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py


cd D:\Stage_mr_f\invoice-ocr-erp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
