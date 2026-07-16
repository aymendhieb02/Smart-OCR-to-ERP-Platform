# Limitations

This system is demo-ready and portfolio-ready, but it is not a finished commercial invoice AI product.

## Known Technical Limits

- OCR quality depends on image resolution, rotation, compression, and installed OCR dependencies.
- Arabic and multilingual documents require the correct OCR language data and may need extra verification.
- Table extraction is conservative. Ambiguous rows should become `needs_review`, not validated ERP rows.
- Complex scanned documents with stamps, signatures, handwriting, or overlapping text may produce incomplete boxes.
- The current learning loop is rule-based correction memory, not ML training.
- Demo documents are useful for presentation, but they do not prove dataset-wide accuracy.

## Benchmark Limits

- OCR confidence is not the same as accuracy.
- Accuracy can only be claimed against manually verified ground truth.
- Public invoice datasets often have inconsistent label schemas.
- Some datasets include receipts, forms, or non-invoice documents, so document-type classification matters.

## Product Limits

- The UI is a review console, not a full ERP.
- User authentication, multi-user roles, audit permissions, and production storage are not implemented.
- Export is represented as JSON copy/export readiness, not a live ERP connector.

## Future Improvements

- Add authenticated reviewer accounts and correction audit trails.
- Add a real ERP connector sandbox.
- Add manually verified benchmark labels and publish field-level accuracy.
- Train or fine-tune a layout-aware model once enough corrected examples exist.

