# Table Transformer Integration

## Configuration

- `INVOICE_OCR_ENABLE_TABLE_TRANSFORMER=false`
- `INVOICE_OCR_TABLE_TRANSFORMER_DEVICE=cpu`
- `INVOICE_OCR_TABLE_TRANSFORMER_MODEL_PATH=models/table_transformer`
- `INVOICE_OCR_TABLE_TRANSFORMER_CONFIDENCE=0.75`
- `INVOICE_OCR_TABLE_TRANSFORMER_MAX_TABLES=10`

The loader is lazy and defensive. Missing optional model dependencies or missing
weights produce a structured unavailable result instead of changing production
extraction behavior.

## Dependencies

The optional experiment adds `torch`, `torchvision`, `transformers`, `timm`, `Pillow`, and `psutil`.
They are not imported by production code paths unless the experimental package is
used.

## Limitations

This integration is an experiment. It should not become default until verified
benchmarks show reliable gains over P3 Stable for exact row count and row content.
