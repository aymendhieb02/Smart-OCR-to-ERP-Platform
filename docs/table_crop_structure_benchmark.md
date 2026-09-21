# Generic table-crop SLANet_plus benchmark

## Objective and boundary

This benchmark tests whether generic table cropping reduces the structural and semantic noise observed when `SLANet_plus` processes whole pages. It compares the existing deterministic reconstruction, whole-page `SLANet_plus`, and cropped `SLANet_plus` while reusing the same `optimized_mobile_v4` OCR evidence.

This is benchmark-only. No production service, route, schema, configuration, dependency, OCR default, validation rule, or ERP behavior changed. Draft dossier labels were not treated as verified ground truth, so the report makes structural and diagnostic—not accuracy—claims.

## First audit: existing region behavior

All three `INV 01.pdf` pages render at `1190 × 1684` pixels.

### Producer invoice, page 1

- The deterministic engine detects one `header_aliases` region: `(53.71, 673.60)–(1088.90, 1234.58)`.
- It detects the header at `(73.72, 673.60)–(1086.80, 703.07)` and reconstructs the product row.
- The deterministic region contains the table but extends through legal/footer material below the product row.
- The existing `line_items_table_area` is `(35,606)–(1154,1044)`. It includes the table and additional non-table text.

### RUSPINA re-invoice, page 2

- Deterministic strategy: `UNRESOLVED`; no region or header.
- Legacy layout reconstruction detects no table.
- The actual OCR table evidence occupies approximately y=432–493.
- The existing fixed `line_items_table_area`, y=606–1044, misses the product table entirely.
- A generic dense-row component identifies `(100,432)–(1073,493)` from horizontally distributed header/numeric OCR evidence.

### Customs declaration, page 3

- Deterministic strategy: `UNRESOLVED`; no deterministic region/header.
- The older layout heuristic proposes an oversized region from approximately y=0–1335, mixing many unrelated form sections.
- The fixed `line_items_table_area` captures only a middle form section.
- Generic dense-row detection produces several independent form-region candidates; none should be interpreted as a complete invoice line-item table.

## Crop generation

Candidate sources are evaluated in this order:

1. Existing deterministic `TableRegion` results.
2. Detected table headers expanded through geometrically adjacent OCR rows.
3. Existing `line_items_table_area`, retained as an audited baseline.
4. Generic dense OCR-row components when earlier sources are absent or insufficient.

Dense-row detection uses only OCR geometry, numeric evidence, header-like evidence, horizontal spread and vertical continuity. It has no filename, supplier, dossier, or page-number rules.

Every crop records original dimensions, page-space bbox, crop dimensions, padding, and scale. SLANet cell polygons are converted to axis-aligned crop boxes and then translated back to physical page coordinates before OCR mapping.

Padding policies tested: `0%`, `2%`, `5%`. Zero padding was sufficient for both successful `INV 01` invoice crops. Two percent helped one later producer case. Five percent did not create additional coherent rows in the representative pages. Recommended future default: `0%`, with `2%` as a generic edge-clipping fallback.

## Three-page gate

| Page | Selected generic crop | Whole inference | Crop inference | Whole → crop assignment | Ambiguous | Unmapped | Semantic result |
|---|---|---:|---:|---:|---:|---:|---|
| Producer p1 | header expansion `(54,673)–(1089,941)`, 0% | 0.5223 s | 0.4431 s | 75.36% → 100% | 12 → 0 | 5 → 0 | 1 complete row; financially consistent |
| RUSPINA p2 | dense OCR `(100,432)–(1073,493)`, 0% | 0.4334 s | 0.4003 s | 28.57% → 100% | 11 → 0 | 19 → 0 | 1 coherent row; unit unavailable; financially consistent |
| Customs p3 | dense OCR `(38,1365)–(1190,1611)`, 5% | 1.5663 s | 0.4874 s | 49.25% → 80% | 52 → 2 | 50 → 2 | no invoice semantic row; crop marked misaligned |

Crop generation took `0.05425`, `0.02498`, and `0.07287` seconds respectively. Peak RSS across representative crop runs was approximately `629 MiB`; no memory gate was approached.

The invoice gate was positive because both invoice examples materially reduced ambiguity and produced coherent semantic grouping. Expansion therefore proceeded only to the 12 producer/RUSPINA pages, excluding customs pages.

## Twelve-page invoice expansion

Aggregate comparison:

| Diagnostic | Whole-page SLANet | Selected generic crops |
|---|---:|---:|
| OCR lines assigned | 353 | 128 within crops |
| Ambiguous mappings | 114 | 1 |
| Unmapped OCR lines | 98 | 2 |
| Assignment rate | 62.48% | 97.71% |
| Mean inference | 0.5350 s | 0.4545 s |
| Peak RSS | 731.8 MiB | 735.2 MiB |
| Coherent semantic rows | 0 | 4 |
| Complete semantic rows | 0 | 2 |

Selected crop findings:

- `INV 01` producer: complete, financially consistent row.
- `INV 01` RUSPINA: coherent and financially consistent; unit unavailable.
- `INV 05` producer: coherent but financially inconsistent, so it remains review-only.
- `Inv 06` producer: complete and financially consistent; one OCR mapping remained ambiguous.
- The other eight invoice/re-invoice pages gained cleaner cell assignment but did not produce a coherent semantic row.

The selected crops used header expansion where reliable headers existed and dense OCR regions otherwise. One later candidate reused the existing fixed table area, but it produced no semantic row. There was no supplier-specific tuning.

## Semantic and financial diagnostics

Fields are reported individually as `available`, `unavailable`, or `ambiguous`. A coherent row requires description, quantity, unit price and line total; a complete row additionally requires unit.

Of four coherent rows:

- Three passed quantity × unit-price versus line-total tolerance.
- One was inconsistent and is explicitly flagged `FINANCIAL_INCONSISTENCY`.
- Missing units remain unavailable rather than inferred.

These signals require human verification because the labels remain draft.

## Failure taxonomy on selected 12-page crops

- `SEMANTIC_MAPPING_ERROR`: 8
- `CROP_MISALIGNED`: 1
- `FINANCIAL_INCONSISTENCY`: 1
- `OCR_TO_CELL_MAPPING_ERROR`: 1

The customs representative crop also had `CROP_MISALIGNED`, `OCR_TO_CELL_MAPPING_ERROR`, and `SEMANTIC_MAPPING_ERROR`. Its improved mapping rate does not make it an invoice table.

## Resource measurements

- Model: `SLANet_plus`
- Interface: `TableStructureRecognition`
- CPU affinity: four logical CPUs
- Batch/concurrency: 1/1
- Artifact: `8,006,872` cached bytes
- Cache: `C:\Users\Msi\.paddlex\official_models\SLANet_plus`
- Warm cached initialization during the crop run: `1.3364 s`
- Twelve-page mean crop inference: `0.4545 s`
- Twelve-page peak crop-path RSS: approximately `735.2 MiB`

Crop inference was modestly faster than whole-page inference. Memory was essentially unchanged because model weights dominate RSS.

## Limitations

- No verified ground truth exists; coherent rows are structural candidates, not correctness claims.
- Only one small model and v4 OCR evidence were evaluated.
- Candidate selection maximizes coherent grouping and conservative mapping diagnostics; production selection behavior is not designed here.
- Eight of twelve invoice pages still produced no coherent semantic row.
- The dense heuristic can select unrelated form regions, as demonstrated on the customs page.
- Service-level RAM contention on the target 8-GB server remains untested.
- No client images, OCR text reports, or model binaries are committed.

## Decision

**C. CROPPED SLANET PRODUCES USEFUL SEMANTIC ROW STRUCTURE AND JUSTIFIES A LATER OPTIONAL-INTEGRATION EXPERIMENT.**

Cropping materially reduced ambiguity and produced semantic rows that whole-page SLANet did not. This does not authorize production integration: coverage is incomplete, one row was financially inconsistent, and crop selection still needs a conservative runtime policy.

## Recommended next task

Design a separate optional-fallback experiment—not a default replacement—that invokes cropped SLANet only when deterministic reconstruction is unresolved or incomplete. It should require a generic high-confidence crop, conservative OCR mapping, row financial validation, explicit resource limits, and human-review routing. No production integration should proceed until verified ground truth is available and fallback behavior is tested independently.
