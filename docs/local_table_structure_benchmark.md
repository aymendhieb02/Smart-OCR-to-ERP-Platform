# SLANet_plus local table-structure benchmark

## Objective and boundary

This experiment compares the existing deterministic table reconstruction with the module-level PaddleOCR `TableStructureRecognition` interface using `SLANet_plus`. It is structural diagnostics, not an accuracy evaluation: the six-dossier labels remain draft and were not scored as verified ground truth.

No production route, extraction service, ERP rule, schema, configuration default, or dependency file was changed. `optimized_mobile_v4` remains the production OCR default and `optimized_mobile_v5` remains opt-in.

## Existing deterministic architecture

`app/services/table_reconstruction_engine.py` consumes positioned `OCRLine` evidence and evaluates `COLUMNAR_TABLE`, `HEADERLESS_COLUMNAR`, `KEY_VALUE_RECORDS`, `REPEATED_VERTICAL_BLOCKS`, `NUMERIC_ANCHORED_ROWS`, and `SINGLE_ITEM_SUMMARY`. Its `TableReconstructionResult` exposes regions, headers, columns, cells, row anchors, fragments, reconstructed rows, excluded rows, unresolved fragments, reconciliation data, diagnostics, and the selected strategy.

`app/services/line_item_extractor.py` uses that engine first, then retains older reconstructed-table, anchored-row, coordinate-row, and text fallbacks. `app/services/row_validation_engine.py` checks required values and quantity × unit price arithmetic. `app/services/table_regions.py` supplies high-value OCR crops. The optional `app/services/table_transformer/` experiment remains disabled and was not invoked or changed.

## Why the module-level interface was used

The benchmark instantiated only:

```python
TableStructureRecognition(model_name="SLANet_plus", device="cpu")
```

`PPStructureV3` and `TableRecognitionPipelineV2` were not used because they would load additional layout, document-preprocessing, OCR, classification, and cell-detection components. The experiment needed to isolate structure while mapping the same existing `OCRLine` evidence into predicted cells.

## Environment and model artifact

- PaddleOCR: `3.7.0`
- PaddlePaddle: `3.3.1`
- PaddleX: `3.7.1`
- Model identifier: `SLANet_plus`
- Predictor: `TableRunnerPredictor`
- Device: CPU
- Batch size: 1
- Concurrency: 1
- Process affinity: four logical CPUs (`0,1,2,3`)
- `CPU_NUM=4`
- `OMP_NUM_THREADS=4`
- Cache: `C:\Users\Msi\.paddlex\official_models\SLANet_plus`
- Cache footprint: `8,006,872` bytes (`7.64 MiB`) across six files
- Main weights: `inference.pdiparams`, `7,666,515` bytes
- Published inference-model size: `6.9 MB`

The model initialized successfully without package upgrades. Initialization took `4.0907 s`. RSS increased from `90,796,032` to `537,595,904` bytes during model load. Peak RSS across all 18 pages was `778,170,368` bytes (`742.1 MiB`), safely below the 4 GiB stop threshold.

## Benchmark method

The benchmark loads each PDF once through the existing loader, runs the configured v4 OCR evidence once, and passes each exact rendered page to both approaches:

1. `reconstruct_line_items(page_ocr_lines)` for the deterministic result.
2. `SLANet_plus` structure inference on the same image.
3. HTML structure tokens and cell polygons are normalized into benchmark-local tables, rows and cells.
4. The same OCR lines are assigned geometrically to predicted cells.
5. A line is left ambiguous when the two best cells are within `0.05` assignment score; it is never silently forced into a cell.
6. Reports store counts and diagnostics only. Images and client OCR text are not committed.

SLANet output preserves row/column indexes, row/column spans, structure score and polygon-derived boxes. Deterministic fields unavailable at cell level, particularly explicit cell-to-row relationships in adaptive strategies, remain unavailable rather than being fabricated.

## Three-page safety gate: `INV 01.pdf`

### Producer invoice, page 1

| Measure | Deterministic | SLANet_plus |
|---|---:|---:|
| Strategy/structure | `COLUMNAR_TABLE` | one structure |
| Rows | 1 | 13 |
| Columns | 5 | 8 |
| Cells | 24 | 41 |
| Line items/usable semantic rows | 1 | 0 |
| Spanning cells | unavailable | 20 |
| Arithmetic-consistent rows | 1 exact | unavailable for 13 rows |
| OCR assignment | inherent source evidence | 52 assigned, 5 unassigned, 12 ambiguous (`75.36%`) |

SLANet_plus over-segmented the full invoice page and did not recover a usable quantity/unit-price/line-total row. The deterministic result retained one arithmetically exact invoice row. Failures: `HEADER_ERROR`, `OCR_TO_CELL_MAPPING_ERROR`, `ROW_SPLIT_ERROR`, `SEMANTIC_MAPPING_ERROR`.

### RUSPINA re-invoice, page 2

| Measure | Deterministic | SLANet_plus |
|---|---:|---:|
| Strategy/structure | `UNRESOLVED` | one structure |
| Rows | 0 | 5 |
| Columns | unavailable | 5 |
| Cells | 0 | 11 |
| Line items/usable semantic rows | 0 | 0 |
| OCR assignment | unavailable | 12 assigned, 19 unassigned, 11 ambiguous (`28.57%`) |

SLANet_plus supplied an explicit local grid where deterministic reconstruction supplied none. That is structurally useful for debugging, but the low unambiguous assignment rate and absence of semantic rows make it unsuitable for ERP extraction without substantial additional localization/mapping work.

### Customs declaration, page 3

| Measure | Deterministic | SLANet_plus |
|---|---:|---:|
| Strategy/structure | `UNRESOLVED` | one structure |
| Rows | 0 | 21 |
| Columns | unavailable | 18 |
| Cells | 0 | 145 |
| Line items/usable semantic rows | 0 | 0 |
| Spanning cells | unavailable | 99 |
| OCR assignment | unavailable | 99 assigned, 50 unassigned, 52 ambiguous (`49.25%`) |

The customs page gained substantial form-like local structure. It should not be judged as one product table, but 52 ambiguous and 50 unmapped lines show that whole-page structure alone does not reliably associate the existing OCR evidence with its cells.

The gate passed operationally: mean SLANet inference was `0.9772 s/page`, peak RSS was `671,387,648` bytes, model output parsed successfully, and no allocation failure or swapping symptom occurred.

## All-18-page results

| File/page | Deterministic rows/items | SLANet rows × cols / cells | Assignment | Inference |
|---|---:|---:|---:|---:|
| INV 01 p1 | 1 / 1 | 13 × 8 / 41 | 75.36% | 0.5621 s |
| INV 01 p2 | 0 / 0 | 5 × 5 / 11 | 28.57% | 0.4438 s |
| INV 01 p3 | 0 / 0 | 21 × 18 / 145 | 49.25% | 0.8893 s |
| Inv 02 p1 | 0 / 0 | 18 × 6 / 40 | 45.83% | 0.7215 s |
| Inv 02 p2 | 1 / 1 | 11 × 5 / 19 | 41.03% | 0.5483 s |
| Inv 02 p3 | 5 / 5 | 20 × 41 / 133 | 46.61% | 0.8737 s |
| INV 03 p1 | 5 / 5 | 15 × 2 / 15 | 88.89% | 0.4291 s |
| INV 03 p2 | 1 / 1 | 6 × 5 / 14 | 65.00% | 0.4391 s |
| INV 03 p3 | 9 / 9 | 21 × 17 / 144 | 63.80% | 1.0250 s |
| Inv 04 p1 | 0 / 0 | 18 × 6 / 40 | 76.60% | 0.5842 s |
| Inv 04 p2 | 0 / 0 | 6 × 5 / 14 | 59.52% | 0.4187 s |
| Inv 04 p3 | 2 / 2 | 21 × 28 / 128 | 54.05% | 1.6350 s |
| INV 05 p1 | 0 / 0 | 20 × 8 / 32 | 66.67% | 0.8134 s |
| INV 05 p2 | 0 / 0 | 6 × 4 / 12 | 66.67% | 0.4782 s |
| INV 05 p3 | 3 / 3 | 23 × 17 / 132 | 62.87% | 1.5076 s |
| Inv 06 p1 | 1 / 1 | 11 × 7 / 38 | 69.57% | 0.5937 s |
| Inv 06 p2 | 0 / 0 | 7 × 5 / 15 | 50.00% | 0.6936 s |
| Inv 06 p3 | 0 / 0 | 20 × 19 / 137 | 62.83% | 1.7894 s |

Aggregate diagnostics:

- Mean SLANet inference: `0.8025 s/page` on the four-CPU-constrained process.
- Explicit SLANet cells: `1,110`; deterministic explicit cells: `49`.
- Deterministic reconstructed line items: `28`.
- SLANet usable semantic rows: `0`.
- OCR lines assigned: `1,114`.
- OCR lines unassigned: `402`.
- OCR lines ambiguous: `392`.
- Overall unambiguous assignment rate: `58.39%`.

The larger cell count is not evidence of correctness. It frequently represents full-page forms split into many rows, columns and spans without reliable invoice semantics.

## Financial consistency

The deterministic engine produced 28 candidate line items and retained arithmetic evidence where quantity, unit price and line total were available. In the initial producer example its candidate was exactly consistent.

SLANet_plus produced no row with at least two mapped invoice semantics among description, quantity, unit, unit price and line total. Consequently, quantity × unit-price and sum-of-line-total diagnostics were unavailable rather than reported as failures or invented values.

## Failure taxonomy

Across 18 pages:

- `HEADER_ERROR`: 16 pages
- `OCR_TO_CELL_MAPPING_ERROR`: 17 pages
- `ROW_SPLIT_ERROR`: 7 pages
- `SEMANTIC_MAPPING_ERROR`: 16 pages
- `CELL_ASSIGNMENT_ERROR`: 2 pages where structure-token and model-box counts differed

No verified `OCR_TEXT_ERROR` count is reported because OCR text was held constant and draft labels cannot establish correctness.

## Where SLANet_plus helped

SLANet_plus exposed explicit structural cells on every page, including pages where deterministic reconstruction was `UNRESOLVED`: all six RUSPINA-style pages in several dossiers, producer pages such as Inv 02/Inv 04/INV 05 page 1, and dense customs pages. This is useful for visualization, region hypotheses and future human-assisted structure inspection.

## Where SLANet_plus hurt

On pages where the deterministic engine already produced usable semantic rows—particularly INV 01 page 1, INV 03 pages 1–3 and Inv 06 page 1—SLANet_plus split the whole page into substantially more rows/cells while producing no ERP-usable semantic row. Whole-page inference also generated many overlapping/spanning cells, causing ambiguity during OCR assignment.

## Limitations

- Labels are draft; no accuracy, precision, recall or F1 is claimed.
- Geometry was evaluated generically on whole rendered pages. No supplier-specific crop or coordinate rule was introduced.
- SLANet_plus recognizes structure, not invoice semantics. A later experiment would need a justified table-localization stage and stronger cell-to-semantic mapping.
- Peak RSS was measured on a 16-GB host with the process pinned to four logical CPUs. The client has 8 GB, so broader service-level memory competition remains unmeasured.
- OCR cache state affects total end-to-end time; reported SLANet inference time excludes OCR and file rendering.
- Vendor performance figures are not used as measured project results.

## Decision

**B. SLANET_PLUS PROVIDES USEFUL STRUCTURE BUT DOES NOT JUSTIFY INTEGRATION.**

The model is small and operationally affordable in isolation, and it reveals cell grids unavailable from deterministic reconstruction. However, those extra cells did not become usable quantity/unit/unit-price/line-total rows, and OCR-to-cell ambiguity remained high. Integrating it into `/process-invoice` now would add memory, latency and mapping complexity without demonstrated ERP benefit.

## Recommended next task

Keep production unchanged. If further research is approved, run a narrowly scoped benchmark that first localizes the table region using existing generic evidence, then applies the same cached SLANet_plus model only to that crop and remaps coordinates to the page. The acceptance gate should require a material increase in unambiguous OCR assignment and usable semantic rows before any production integration is considered.
