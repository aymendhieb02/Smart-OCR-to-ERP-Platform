# Final Extraction Quality Sprint

Date: 2026-07-15

Goal: improve document understanding accuracy without redesigning OCR, UI, or benchmark frameworks.

## Implemented

- Added a VDU-style document tree to the document graph output:
  - document
  - pages
  - semantic blocks
  - lines
  - word-level children
- Enriched graph nodes with:
  - parent block id
  - reading order
  - alignment
  - density
  - semantic hints
- Expanded graph relations with explicit spatial relations:
  - left_of
  - right_of
  - above
  - below
  - same_line
  - same_column
  - same_block
  - contains
- Added semantic block metadata:
  - semantic labels
  - semantic scores
  - block text
  - density
  - alignment
- Added a dedicated rule-based `PartyResolver`.
- Made supplier/customer scoring role-aware.
- Made date ranking label-aware so due-date labels do not win invoice-date selection.
- Made totals ranking prefer graph/totals-block evidence over weak whole-document candidates.
- Changed missing field confidence semantics: if value is `null`, confidence is also `null`.
- Preserved invalid table-like rows in table debug instead of silently discarding them.

## Tests

Commands run:

```powershell
python -m pytest tests\test_final_extraction_quality.py tests\test_phase5_graph_extraction.py tests\test_extractor.py
python -m pytest
python -m compileall -q app scripts tests
```

Results:

- Focused extraction tests: 19 passed.
- Full suite: 108 passed, 1 warning.
- Compileall: passed.

## Benchmarks

OCR environment:

- PaddleOCR: available
- PaddlePaddle: available
- pytesseract: available
- Tesseract executable: available
- OpenCV/Pillow/PyMuPDF: available
- Environment status: READY

Table-heavy smoke with OCR reuse:

```json
{
  "documents_tested": 5,
  "products_table_detected_pct": 100.0,
  "table_anchor_found_pct": 100.0,
  "candidate_rows_found_pct": 100.0,
  "validated_rows_found_pct": 0.0,
  "review_rows_found_pct": 100.0,
  "any_rows_found_pct": 100.0,
  "ttc_found_pct": 0.0,
  "average_processing_time_seconds": 0.027
}
```

Manual benchmark:

- Not completed because all manual labels are still marked unverified.
- The benchmark correctly refused to claim true accuracy.

Multi-dataset smoke:

- `--limit-per-dataset 1` exceeded 4 minutes and was stopped.
- Partial files were written, but no valid global delta is claimed from that interrupted run.

## Timing Notes

The table-heavy OCR-reuse smoke averaged `0.027s/doc` for extraction-only table-heavy checks.

No full OCR timing delta is claimed because the multi-dataset run timed out and the manual benchmark is blocked by unverified labels.

## Remaining Limitations

- The document tree is algorithmic, not a trained LayoutLM/Donut-style model.
- Table-heavy rows are now preserved as review/invalid evidence, but validation still keeps uncertain rows out of ERP export.
- True accuracy deltas require verified manual labels.
- Multi-dataset evaluation still needs a faster cached/resumable run discipline before it can be used interactively.

## Recommendation

Another extraction sprint is useful only after manually verifying the ground-truth labels. Without verified labels, the next best work is not more heuristics; it is measurement.
