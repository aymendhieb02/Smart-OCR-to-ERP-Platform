# Microsoft Table Transformer Experimental Architecture

The deterministic P3 Stable table engine remains the production default. The
Table Transformer integration is an optional structure detector that is disabled
unless `INVOICE_OCR_ENABLE_TABLE_TRANSFORMER=true`.

```mermaid
flowchart TD
  A["Document image"] --> B["Existing PaddleOCR"]
  B --> C["OCR lines with boxes"]
  C --> D["P3 Stable deterministic table engine"]
  C --> E["Optional Table Transformer structure detector"]
  E --> F["OCR-to-cell mapper"]
  F --> G["Line item reconstruction"]
  G --> H["Existing validation and ERP readiness"]
```

The experimental detector never performs OCR. It consumes the existing page
image and maps existing OCR lines into detected or inferred cells.

## Two-stage model flow

The experimental path is now page detector -> padded table crop -> structure recognizer -> full-page coordinate remap -> OCR-to-cell mapper. If either optional model is unavailable, the existing geometry fallback is used and P3 Stable remains the production default.
