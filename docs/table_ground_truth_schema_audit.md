# Table Ground-Truth Schema Audit

Generated for P3.2 table evaluation hardening.

This audit documents how benchmark labels are interpreted for line-item evaluation. It does not change source annotations, OCR, production extraction, party extraction, date extraction, totals extraction, or ERP mapping.

## Evaluation Principle

The benchmark now reports two line-item views:

- Strict source-label metrics: preserve the previous count interpretation where available.
- Canonical adapter metrics: normalize supported label schemas into auditable product rows.

Unsupported or missing labels are not counted as zero-item documents. Explicit empty item containers are counted as zero-item truth.

## Canonical Model

Each canonical row stores:

- `source_index`
- `description`
- `reference`
- `quantity`
- `unit`
- `unit_price`
- `discount`
- `tax_rate`
- `line_total_ht`
- `line_total_ttc`
- `raw_value`
- `source_schema`
- `source_path`
- `normalization_warnings`
- `exclusion_reason`
- `item_confidence`

Each canonical table stores raw count, canonical count, excluded count, duplicate count, zero-item status, source schema, and adapter warnings.

## Dataset Schemas

### invoices-donut-data-v1

Detected schema: `donut_gt_parse`.

Typical structure:

```json
{
  "gt_parse": {
    "line_items": [
      {"description": "Item name", "quantity": "2", "amount": "20.00"}
    ]
  }
}
```

Also supported:

```text
<s_line_item><s_description>Item name</s_description><s_quantity>2</s_quantity></s_line_item>
```

Audit conclusion from the 50-document run:

- All 8 evaluated Donut documents changed truth count after canonical adaptation.
- The previous strict interpretation treated these as line-item failures, but canonical parsing found explicit zero-item semantics for the evaluated labels.
- Root cause: representation mismatch between raw Donut-style annotation structures and the previous generic `items`-list count logic.

### invoiceXpert

Detected schema: `invoiceXpert`.

Typical structure:

```json
{
  "products": [
    {"name": "Product or service", "qty": 2, "price": 10, "total": 20}
  ]
}
```

Adapter behavior:

- Product dictionaries are normalized.
- Empty records are removed.
- Duplicates are removed.
- Subtotal, VAT, payment, and bank rows are excluded.
- Service rows with description and amount only remain valid but lower confidence.

Audit conclusion:

- The evaluated invoiceXpert labels were supported.
- Remaining mismatch is mostly extraction or granularity, not adapter failure.

### invoices-and-receipts_ocr_v1

Detected schemas: `donut_gt_parse` and `invoices-and-receipts`.

Typical structures include nested parsed labels and item arrays. Some evaluated documents had counts changed by the adapter because empty or non-product records were removed.

Audit conclusion:

- Canonical labels are more reliable than strict raw counts for this dataset.
- P3.1 still underperforms P3 on this dataset, so adaptive extraction should not become default.

### FATURA2-invoices

Detected schema: `FATURA2`.

Typical structure:

```json
{
  "parsed_data": "{\"json\": {\"items\": [...]}}"
}
```

Adapter behavior:

- Parses stringified `parsed_data`.
- Reads nested `json.items`.
- Excludes totals/tax/payment rows if present.

### md_invoices

Detected schema: `md_invoices`.

Adapter behavior:

- Uses the generic supported aliases: `line_items`, `items`, `products`, `rows`, `invoice_items`, `articles`.
- Marks unknown label structure as unsupported instead of zero.

### high-quality-invoice-images-for-ocr and invoices

These may have missing labels in the sampled benchmark. Missing truth is excluded from canonical accuracy denominators.

## Exclusion Rules

Excluded from canonical item count:

- Empty records.
- Duplicate records.
- Header-only rows.
- Subtotal/total rows.
- VAT/tax summary rows.
- Payment and bank-detail rows.
- Shipping rows unless explicitly modeled as a product/service item.

## P3.2 Audit Totals

From `optimized_p3_1_table_50doc_01`:

- Evaluated table-label documents: 25.
- Truth counts changed by adapters: 14.
- Empty records removed: 1.
- Duplicate records removed: 1.
- Explicit zero-item documents: 10.
- Unsupported table labels: 0.

## Key Conclusion

The Donut benchmark failure was primarily a label interpretation problem, not proof that OCR or table extraction failed on product rows. After canonical adaptation, Donut evaluates as zero-item truth for the sampled documents.

The extraction engine still needs deterministic improvement for invoiceXpert and invoices-and-receipts, but the next extraction change should be driven by `line_item_comparison.csv`, not by raw strict count alone.
