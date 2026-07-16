# Final Demo Walkthrough

Target duration: 3 to 5 minutes.

## Setup

Run:

```powershell
cd D:\Stage_mr_f\invoice-ocr-erp
.\.venv\Scripts\Activate.ps1
python run.py
```

Open `http://127.0.0.1:8000/`.

## Demo Script

1. Open the application.

Presenter sentence: "This is a human-review workspace for OCR-to-ERP automation. It does not blindly export uncertain invoices."

2. Click **Load good invoice**.

Presenter sentence: "The demo document goes through the same OCR, layout, extraction, validation, and ERP mapping pipeline as an uploaded file."

3. Show the invoice preview.

Presenter sentence: "The left side keeps the original document visible. The overlays show OCR boxes, semantic layout blocks, fields, and line-item rows."

4. Toggle OCR, layout, field, and row overlays.

Presenter sentence: "Every extracted value is traceable back to visual evidence on the document."

5. Click a supplier, customer, total, or line row overlay.

Presenter sentence: "The inspector explains the selected text, confidence, page, source, and why it was selected. Raw debug data is still available under advanced evidence."

6. Open **Product Lines**.

Presenter sentence: "Product rows are editable because invoice automation needs human correction when OCR is uncertain."

7. Edit one line value, then click **Save corrections**.

Presenter sentence: "Corrections are sent back to the backend. Totals, row validation, confidence, and ERP readiness are recomputed."

8. Open **Financial Checks**.

Presenter sentence: "The system separates passed checks from warnings and conflicts, showing expected values, extracted values, differences, tolerance, and the next action."

9. Show **ERP readiness**.

Presenter sentence: "ERP export is only enabled when the document clears required fields and business checks. Otherwise the UI explains exactly why it is blocked."

10. Click **Load noisy document**.

Presenter sentence: "On noisy or receipt-like documents, the system should prefer needs-review or blocked export over false automation."

11. Open **ERP JSON**.

Presenter sentence: "The final payload preserves the ERP schema while keeping quality, validation, and correction evidence attached."

## Best Demo Path

Use this order:

1. `Load good invoice`
2. Preview overlays
3. Product line edit
4. Save corrections
5. Financial checks
6. ERP readiness
7. `Load noisy document`
8. Show export blocked

