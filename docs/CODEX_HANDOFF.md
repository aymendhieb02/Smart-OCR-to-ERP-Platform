# Codex Technical Handoff

Generated: 2026-09-17

Repository: `https://github.com/aymendhieb02/Smart-OCR-to-ERP-Platform.git`

Workspace: `D:\Stage_udgroup\invoice-ocr-erp`

Branch at handoff: `main`

Baseline release: `v1.0-deterministic`

This is a developer-to-developer continuation document. It records the current implementation, decisions, benchmark state, and the work performed in the Codex sessions associated with the former `Stage_mr_f` project and the current `invoice-ocr-erp` workspace.

## 1. Project objective and current state

The product converts invoices and related commercial documents into traceable, validated ERP JSON. It is not merely OCR: it preserves OCR evidence, understands layout, extracts ranked field candidates, reconstructs product tables, checks financial consistency, exposes a human correction workflow, and blocks unsafe export.

The current production path is deterministic and intentionally frozen as the v1.0 baseline:

```text
document -> load/render -> OCR -> normalized OCRLine evidence
         -> layout/semantic regions + document graph
         -> candidate extraction + party resolution
         -> table/line-item reconstruction
         -> extraction quality gate + financial reasoning
         -> validation + confidence + ERP readiness
         -> review/correction when unsafe -> ERP JSON
```

Current defaults are `optimized_mobile_v4`, `balanced`, and `p3_stable`. The root README describes the final deterministic product. The older Hybrid LLM implementation was removed from the shipped baseline in commit `148f16b`; future LLM work must be advisory/evidence-grounded and must not silently bypass deterministic validation.

## 2. Architecture and folder structure

```text
app/
  main.py                         FastAPI app, CORS, static UI, /health
  api/routes.py                   upload, demo, corrections, export, evaluation endpoints
  core/config.py                  pydantic-settings configuration (INVOICE_OCR_ prefix)
  core/schemas.py                 OCR, extraction, validation, review, ERP API models
  services/
    file_loader.py                image/PDF decoding, PDF rendering, upload handling
    preprocessing.py              deterministic image preprocessing
    ocr_profiles.py               effective OCR profiles/configuration hash
    ocr_engine.py                 PaddleOCR/Tesseract orchestration, cache, normalization
    ocr_fallback_planner.py       targeted fallback-region decisions
    bbox_contract.py              public bbox/page/coordinate contract
    layout_analyzer.py            logical semantic blocks
    document_layout.py            line grouping, tables, layout diagnostics
    document_graph.py             spatial/document relationships
    layout_model/                 optional ML layout detector and router
    field_extractor.py            candidate-based field extraction
    graph_field_extractor.py      graph/spatial field recovery
    party_resolver.py             deterministic supplier/customer ranking
    party_name_normalizer.py      canonical party comparison
    line_item_extractor.py        selects/rejects line-item rows
    table_reconstruction_engine.py deterministic table reconstruction
    table_transformer/            optional two-stage Table Transformer path
    extraction_quality.py         sanitization and validated-vs-review row gate
    financial_reasoner.py         totals/VAT/discount/payable reasoning
    row_validation_engine.py      row-level validation and scoring
    validator.py                  high-level validity/review decision
    confidence_engine.py          component and overall confidence
    erp_readiness.py              final Ready/Needs Review/Rejected decision
    erp_mapper.py                 nested ERP JSON and flat export mapping
    correction_store.py           correction persistence and revalidation
    review_assistant.py           deterministic review guidance
    pipeline_runner.py            shared orchestration used by API and benchmarks
  static/index.html               single-page review UI
  static/app.js                   upload, overlays, editing, revalidation, JSON copy
  static/styles.css               responsive UI styling
scripts/                          benchmarks, profiling, audits, label workflows
tests/                            unit/regression/integration tests
dataset/demo/                     three presentation documents
dataset/manual_ground_truth_benchmark/ fixed manual-label campaign
dataset/reports/                  generated benchmark output (do not commit routinely)
docs/                             architecture, limitations, benchmark and release notes
outputs/                          generated ERP JSON/reports/corrections
```

`app/services/pipeline_runner.py::process_document_file()` is the canonical entry point for both the API and benchmark scripts. Do not create a second processing path.

## 3. Complete OCR pipeline and data flow

1. `POST /process-invoice` saves the upload with `file_loader.save_upload_to_temp()`.
2. `pipeline_runner.process_document_file()` calls `file_loader.load_document()`.
3. Images are decoded; PDFs are rendered page-by-page and embedded PDF text is retained when available.
4. `OCREngine.run()` applies the selected OCR mode/profile, preprocessing, memory/disk caching, and OCR inference.
5. Paddle output is normalized to `OCRResult` containing `raw_text`, engine, aggregate confidence, and `OCRLine` records with text, confidence, page, bbox, page dimensions, polygon, and coordinate space when available.
6. Empty OCR text raises `ValueError("No text could be extracted from the invoice")`.
7. `LayoutAnalyzer.detect_layout_blocks()` and `analyze_document_layout()` derive semantic/spatial structure. An optional layout model may replace the deterministic semantic blocks only when enabled and successfully loaded.
8. `classify_document()` classifies invoice/receipt/delivery-note/purchase-like input.
9. `extract_with_candidates()` extracts metadata, parties, financial fields, and line items with evidence and score breakdowns.
10. In balanced mode, `determine_required_fallbacks()` can request targeted region OCR when essential fields are weak/missing. `OCREngine.run_fallback_regions()` runs those regions, merges new lines, then repeats layout/classification/extraction.
11. `apply_extraction_quality_gate()` removes unsafe values and separates line rows into validated and needs-review collections.
12. Expanded fields and boxes are built for overlays/review; bbox integrity is checked through `bbox_contract`.
13. Validation, row validation, financial reasoning, confidence, readiness, duplicate/fraud indicators, suggestions, review explanations, and reports are assembled.
14. `build_erp_json()` and `map_to_flat_erp()` produce compatible nested and flat ERP representations. `build_validated_erp_json()` excludes unsafe fields/rows from the validated payload.
15. Optional preview generation writes page images under the static preview area.
16. The response includes detected fields, candidates/evidence, layout/table diagnostics, confidence, readiness, review guidance, ERP payloads, preview metadata, and timings.

OCR cache defaults to `.cache/ocr`; benchmark scripts can disable or refresh it. Generated predictions and reports live under `dataset/reports/`.

## 4. Important design decisions and reasons

- **Deterministic baseline first.** Financial/ERP data must be auditable by OCR evidence, positions, score breakdowns, and explicit rules. This is cheaper, reproducible, and easier to test than an opaque end-to-end model.
- **Extraction does not imply export.** A detected value may remain in review/debug output while being excluded from validated ERP JSON.
- **Conservative failure policy.** Ambiguity produces `needs_review` or `invalid`, not optimistic export.
- **One shared pipeline.** UI, API, demos, and benchmarks use `process_document_file()` to prevent demo-only behavior.
- **Stable table default.** `p3_stable` remains production because experiments did not prove a safe non-regressive rule change. `p3_1_adaptive` is selectable but not default.
- **Optional ML is feature-gated.** Layout model and Table Transformer cannot change default behavior merely because their dependencies/models exist.
- **Evidence preservation.** OCR boxes, page metadata, original values, candidate sources, and correction metadata remain available after human edits.
- **Accuracy claims require human labels.** OCR confidence and completeness are explicitly not called accuracy on unlabeled datasets.
- **Canonical benchmark comparisons.** Party names and heterogeneous public labels require normalized/canonical comparisons in addition to strict equality.
- **No hidden human approval.** The system should never infer approval from successful extraction or validation; a reviewer action and revalidation are distinct stages.

## 5. OCR engines and models currently used

Production/default OCR:

- PaddleOCR is primary.
- Detection model: `PP-OCRv4_mobile_det`.
- Recognition model: `en_PP-OCRv4_mobile_rec`.
- Profile: `optimized_mobile_v4`.
- Mode: `balanced`.
- CPU threads: `4`.
- GPU: disabled.
- MKL-DNN: disabled.
- Input max side: `1600`.
- Disk cache: enabled.

Fallback OCR:

- Tesseract is enabled by default and used for fallback paths/regions when available.
- The last environment audit found `C:\Program Files\Tesseract-OCR\tesseract.EXE`.

Language configuration is `fr,en`, with `ocr_languages_list = ["fr", "en", "ar"]`. Arabic keyword/text handling exists but is materially less mature than French/English.

Known runtime warning: PaddleOCR reports that `lang` and `ocr_version` are ignored when explicit model names/directories are supplied. This is non-fatal; the explicit model names win. Cached model messages are informational.

## 6. Extraction logic

`field_extractor.extract_with_candidates()` is candidate-based rather than first-regex-wins. Candidate sources include regex/labels, spatial and graph relationships, semantic regions, layout position, normalized values, and business rules. Candidate objects retain field, raw value, normalized value, score, confidence, source, evidence text/bbox/page, and score breakdown where available.

Core fields include supplier/customer, invoice number/date/due date, currency, amount HT/subtotal, TVA/VAT, amount TTC/payable, tax rate, and line items. `party_resolver.py` ranks supplier/customer candidates separately; `party_name_normalizer.py` supports fair canonical comparisons. Correction memory in `correction_store.py` can add `+0.16` to known matching party candidates or introduce an exact-text candidate at `0.78`, but only when surrounding context permits it; table/footer/payment contexts are explicitly rejected.

`extraction_quality.apply_extraction_quality_gate()` sanitizes implausible candidates and classifies rows. `field_enricher.py` prepares expanded field/evidence structures and overlay boxes. `dynamic_tables.py` preserves non-canonical table columns for review rather than discarding invoice-specific information.

## 7. Layout-analysis logic

Deterministic layout is the default:

- `LayoutAnalyzer` groups OCR lines into semantic blocks such as supplier, customer, metadata, products, totals, taxes, payment, notes, and footer.
- `document_layout.analyze_document_layout()` groups lines/blocks, identifies table structure, and emits diagnostics.
- `document_graph.py` and `graph_field_extractor.py` use spatial relationships between labels and values.
- Fuzzy layout matching uses `layout_fuzzy_threshold=85`; a retry may use `layout_retry_fuzzy_threshold=74` when the unmapped OCR ratio reaches `0.60`.
- Row grouping uses a minimum overlap ratio of `0.35`.

The optional layout model is routed through `layout_model/layout_model_router.py`. It is disabled by default. If enabled but unavailable or unhelpful, deterministic blocks remain the fallback. Default model confidence is `0.5`, maximum regions `40`, device `cpu`, model path `models/layout_model`.

## 8. Table and line-item reconstruction

The production table profile is `p3_stable`. `document_layout.py`, `table_reconstruction_engine.py`, and `line_item_extractor.py` use OCR geometry, header anchors, horizontal alignment, row grouping, column inference, semantic filtering, and financial plausibility to reconstruct rows. Non-product/header/footer/summary rows are rejected. The extraction quality gate keeps validated rows separate from `needs_review` rows; both remain visible in diagnostics/review.

A specific text-fallback bug was fixed in commit `81725e5`: layout text fallback previously failed to reconstruct rows reliably when OCR geometry/table signals were incomplete. The fix improved header recognition, row grouping, and fallback reconstruction and added `tests/test_document_layout_text_fallback.py`.

`p3_1_adaptive` exists for experiments but is disabled by default because benchmark diagnostics did not justify replacing the stable profile without increasing false positives.

Optional Table Transformer uses a two-stage design:

1. `microsoft/table-transformer-detection` detects table boxes on the full page.
2. `microsoft/table-transformer-structure-recognition` runs on padded crops and detects rows, columns, and headers.
3. OCR lines are mapped to inferred cells and reconstructed into `LineItem` objects.

It is disabled by default (`enable_table_transformer=false`), capped at 10 tables, uses confidence `0.75`, CPU device, and cached/debug outputs. Geometry fallback exists when the model is unavailable. It must remain experimental until verified benchmarks show consistent gains.

## 9. Financial validation rules

The main rules are distributed across `financial_reasoner.py`, `validator.py`, `row_validation_engine.py`, `extraction_quality.py`, and `erp_readiness.py`:

- Required ERP fields must be present (supplier, invoice metadata, relevant totals/rows according to the document state).
- HT/subtotal plus VAT/TVA, discounts, shipping/stamp/payable adjustments must reconcile with TTC/payable within configured/relative tolerance.
- Each row is checked for meaningful description/reference and numeric plausibility.
- `quantity * unit_price`, less discount where applicable, must reconcile with line HT/total.
- Sum of line items must reconcile with invoice subtotal/total when sufficient values exist.
- Tax rates must be within a plausible range and VAT implied by rate/base must be consistent.
- Invalid/rejected candidates, incomplete table rows, visible product-table text with no parsed rows, and suspicious financial values block export or require review.
- Low OCR confidence creates warnings; `validator.py` uses the configured low-confidence threshold `0.60`, and documents with warnings/unknown type or confidence below `0.75` are conservatively placed in review when not already invalid.
- Human-corrected financial values are revalidated. `_validate_corrected_fields()` accepts a TTC reconciliation tolerance of `max(0.05, abs(TTC) * 0.002)` before removing an amount-mismatch error.

Do not collapse `invalid`, `needs_review`, and `valid` into one Boolean when extending the API; callers rely on the distinction.

## 10. ERP readiness and export logic

`assess_erp_readiness()` returns readiness details including status (`Ready`, `Needs Review`, or `Rejected`), score, missing fields, blocking errors, and `ready`. Pipeline validation is forced to invalid when readiness is Rejected and to needs-review when readiness requires review.

The response provides:

- full extraction/debug structures;
- `erp_json` for traceable data and quality metadata;
- `validated_erp_json`, which contains only data that survived safety gates;
- flat ERP mapping for compatibility;
- `erp_export_allowed`, driven by readiness rather than mere OCR/extraction success.

The public `POST /export-erp-json` maps a supplied `ERPInvoiceJSON` to the flat format; it is not a live ERP connector. There is no SAP/Odoo/etc. integration, authentication, tenant authorization, or production export queue.

## 11. Human review and correction workflow

The single-page UI shows rendered pages, OCR/layout/field overlays, candidate evidence, editable fields, dynamic tables, line-item totals, validation reasons, and JSON output.

Review flow:

1. Process a document normally.
2. Reviewer edits canonical fields and/or dynamic line rows, accepts/rejects suggestions, deletes/restores rows, or marks ignored rows.
3. UI sends the original payload plus edits to `POST /review/validate-corrections`.
4. `validate_review_corrections()` reconstructs fields/rows without rerunning OCR, retains original evidence, applies `confidence=1.0` and source `human verified` only to explicitly reviewed rows, recomputes missing financial aggregates, reruns row/financial/ERP validation, and returns corrected/validated ERP payloads.
5. Correction records are appended as JSONL under the configured output correction area and can feed conservative candidate-memory boosts.

The older `POST /corrections` endpoint also accepts correction submissions. Human editing must invalidate prior readiness until revalidation completes. Do not treat a correction as approval without rerunning readiness.

## 12. API endpoints

- `GET /` - serves `app/static/index.html` review UI.
- `GET /health` - `{status: "ok", service: settings.app_name}`.
- `POST /process-invoice` - multipart upload; full OCR-to-ERP pipeline, preview, persisted ERP JSON.
- `GET /demo-documents` - lists `good`, `review`, and `noisy` demo fixtures and existence state.
- `POST /demo-documents/{demo_id}/process` - runs a demo through the real pipeline without persisting ERP JSON.
- `POST /corrections` - legacy/general correction submission.
- `POST /review/validate-corrections` - current review revalidation endpoint.
- `POST /export-erp-json` - nested-to-flat ERP mapping.
- `POST /evaluate-dataset` - launches `scripts/evaluate_dataset.py` with a 1,800-second timeout; intended for controlled local use.
- Swagger/OpenAPI: `GET /docs`.

`app/main.py` currently allows all CORS origins/methods/headers with credentials. This is acceptable for the local demo but must be tightened before deployment.

## 13. Important configuration and environment variables

All settings use the `INVOICE_OCR_` prefix and load `.env` through `pydantic-settings`.

| Setting / environment suffix | Default | Meaning |
|---|---:|---|
| `APP_NAME` | `Invoice OCR ERP` | FastAPI title/service name |
| `OUTPUT_DIR` | `outputs` | generated JSON/reports/corrections |
| `MAX_UPLOAD_SIZE_MB` | `25` | upload cap |
| `LOW_CONFIDENCE_THRESHOLD` | `0.60` | low OCR confidence warning gate |
| `OCR_LANGUAGES` | `fr,en` | OCR language intent |
| `ENABLE_TESSERACT_FALLBACK` | `true` | fallback engine |
| `OCR_MODE` | `balanced` | fast/balanced/accurate behavior |
| `OCR_PROFILE` | `optimized_mobile_v4` | frozen v1 OCR profile |
| `ENABLE_OCR_DISK_CACHE` | `true` | persistent OCR cache |
| `OCR_CACHE_DIR` | `.cache/ocr` | OCR cache location |
| `PADDLE_ENABLE_MKLDNN` | `false` | CPU optimization flag |
| `PADDLE_CPU_THREADS` | `4` | Paddle threads |
| `PADDLE_USE_GPU` | `false` | Paddle device |
| `PADDLE_OCR_VERSION` | `None` | generic version selector; ignored with explicit model names |
| `PADDLE_TEXT_DETECTION_MODEL_NAME` | `PP-OCRv4_mobile_det` | detector |
| `PADDLE_TEXT_RECOGNITION_MODEL_NAME` | `en_PP-OCRv4_mobile_rec` | recognizer |
| `OCR_INPUT_MAX_SIDE` | `1600` | input resize limit |
| `TABLE_RECONSTRUCTION_PROFILE` | `p3_stable` | deterministic table engine |
| `ENABLE_TABLE_TRANSFORMER` | `false` | optional TATR path |
| `TABLE_TRANSFORMER_CONFIDENCE` | `0.75` | TATR structure threshold |
| `TABLE_TRANSFORMER_MAX_TABLES` | `10` | per-document cap |
| `ENABLE_LAYOUT_MODEL` | `false` | optional learned layout path |
| `LAYOUT_MODEL_CONFIDENCE` | `0.5` | learned layout threshold |
| `LAYOUT_MODEL_MAX_REGIONS` | `40` | detection cap |
| `LAYOUT_FUZZY_THRESHOLD` | `85` | normal deterministic fuzzy match |
| `LAYOUT_RETRY_FUZZY_THRESHOLD` | `74` | retry threshold |
| `UNMAPPED_RATIO_RETRY_THRESHOLD` | `0.60` | triggers layout retry |
| `ROW_GROUPING_MIN_OVERLAP_RATIO` | `0.35` | table row grouping |

Use `.env.example` as the deployment template. Install `requirements-ml.txt` only for optional Table Transformer/layout experiments.

## 14. Dataset and benchmark setup

Benchmark layers:

- `scripts/evaluate_dataset.py`: tiered local evaluator (`smoke`, `medium`, `full --resume`). Smoke samples 30 documents balanced over `batch_1`, `batch_2`, `batch_3`; medium samples 300.
- `scripts/benchmark_multi_datasets.py`: scans multiple dataset directories, runs the shared pipeline, writes CSV/checkpoint/predictions/per-dataset/global reports.
- `scripts/large_benchmark_runner.py`: isolated resumable runs with run ID, timeout, retries, checkpointing, profile selection, and report-only mode.
- `scripts/manual_label_helper.py` + `scripts/benchmark_manual_ground_truth.py`: fixed human-verified ground-truth workflow; unverified labels are refused.
- `scripts/verified_label_campaign.py`: exactly-ten-document verification campaign and review dashboard.
- `scripts/benchmark_table_heavy.py`, `benchmark_table_transformer.py`, and `benchmark_layout_model.py`: focused experiments.

Primary frozen run:

```text
run_id: v1_deterministic_50doc_01
documents: 50
seed: 42
ocr_profile: optimized_mobile_v4
ocr_mode: balanced
table_profile: p3_stable
workers: 1
timeout: 120 seconds/document
config_hash: 9699262229f6b19dc0ab0f5ad813adc79aa9a777c693ccb183ff46e6a4640f32
datasets: FATURA2-invoices, high-quality-invoice-images-for-ocr,
          invoiceXpert, invoices-and-receipts_ocr_v1,
          invoices-donut-data-v1, md_invoices
```

Common commands:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_multi_datasets.py --check-env
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py --mode smoke --seed 42
.\.venv\Scripts\python.exe scripts\benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --limit-per-dataset 5 --seed 42 --force
.\.venv\Scripts\python.exe scripts\benchmark_manual_ground_truth.py --benchmark-root dataset\manual_ground_truth_benchmark --run-name baseline
```

Generated reports/caches/model weights are local artifacts and should not be committed unless intentionally promoted as release evidence.

## 15. Current benchmark results

Frozen 50-document deterministic v1 metrics (`release_metrics.md`, `benchmark_snapshot.json`):

| Metric | Result |
|---|---:|
| Invoice number normalized accuracy | 100% |
| Invoice date normalized accuracy | 100% |
| Amount TTC normalized accuracy | 76.92% |
| Supplier canonical accuracy | 70% |
| Customer canonical accuracy | 100% |
| Canonical line-item presence | 60% |
| Canonical exact row count | 52% |
| Canonical row count within +/-1 | 68% |
| Canonical line-item MAE | 1.76 |

Execution: 50 completed, 0 failed, 0 timeout; validation: 1 valid, 17 needs review, 32 invalid; ERP ready: 1, blocked: 49. These were cached attempts (median 1.494 s, p90 2.916 s), not fresh OCR timing.

Latest conversation-specific run on all six PDFs in `D:\Stage_udgroup\haithem_samples`:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_multi_datasets.py `
  --datasets-root D:\Stage_udgroup `
  --dataset haithem_samples `
  --limit-per-dataset 6 --seed 42 --force --ocr-mode balanced
```

Results: 6/6 processing success, 0 runtime errors, average processing time 52.671 s, average OCR confidence 0.706, 5 invalid, 1 needs-review, 0 ERP-ready. Completeness: supplier 100%, customer 100%, invoice number 83.33%, invoice date 66.67%, TTC 100%, any line items 100%, validated line items present 50%, review rows present 83.33%. Slow documents were `Inv 04.pdf` (138.777 s) and `INV 05.pdf` (116.029 s).

The `haithem_samples` dataset has no verified labels, so every accuracy field is `null`. Several extracted customers and financial amounts are visibly implausible (for example TTC often became `5.0` or `50.0`), proving that completeness/confidence must not be presented as accuracy. Report: `dataset/reports/multi_dataset_benchmark/datasets/haithem_samples/report.html`; predictions: `dataset/reports/multi_dataset_benchmark/predictions/haithem_samples/`.

## 16. Files created or heavily modified during the relevant Codex work

Recent major repository work, reflected in Git history:

- `app/services/pipeline_runner.py`: deterministic production orchestration, fallback recovery, quality/readiness wiring.
- `app/services/ocr_engine.py`, `ocr_profiles.py`, `preprocessing.py`: OCR profile, cache, model and timing behavior.
- `app/services/document_layout.py`, `layout_analyzer.py`, `dynamic_tables.py`: layout/table fallback and review diagnostics.
- `app/services/field_extractor.py`, `party_resolver.py`, `party_name_normalizer.py`: candidate ranking, party disambiguation, tax guardrails.
- `app/services/line_item_extractor.py`, `table_reconstruction_engine.py`: stable table path and precision improvements.
- `app/services/financial_reasoner.py`: deterministic business/financial checks.
- `app/services/correction_store.py`: human review revalidation and evidence/correction memory.
- `app/static/index.html`, `app.js`, `styles.css`: final review UI and correction workflow.
- `app/services/layout_model/*`, `app/services/table_transformer/*`: optional experiments.
- `scripts/benchmark_multi_datasets.py`, `large_benchmark_runner.py`, manual-label and report scripts: reproducible evaluation infrastructure.
- Numerous regression tests, especially `test_document_layout_text_fallback.py`, `test_field_extractor_tax_rate_guard.py`, correction/review, bbox, table, layout, benchmark, and performance tests.
- `README.md`, `.env.example`, `Dockerfile`, `requirements*.txt`, `docs/v1_deterministic_architecture.md`, `release_metrics.md`, `benchmark_snapshot.json`.

In this immediate conversation, no application source was changed before this handoff. The six-file benchmark generated/updated local files under `dataset/reports/multi_dataset_benchmark/`. This handoff itself adds `docs/CODEX_HANDOFF.md`. An unrelated untracked file, `docs/vllm_environment_audit.md`, already existed and must not be included in this handoff commit.

## 17. Important bugs discovered

- OCR can confidently select wrong financial tokens from dense commercial PDFs; the latest samples produced implausible TTC/VAT values and wrong customer strings despite 0.70 average OCR confidence.
- Two of six `haithem_samples` invoices missed dates; one missed invoice number.
- Some PDFs take much longer (116-139 s) than typical 14-17 s, suggesting page/layout-dependent fallback or repeated OCR work.
- PaddleOCR emits a configuration warning because explicit model names are combined with `lang`/`ocr_version` intent.
- `OMP_NUM_THREADS=4` may warn that speed is not optimized and can be problematic with OpenBLAS builds; current runs completed successfully.
- CORS is fully open and API endpoints are unauthenticated.
- `POST /evaluate-dataset` launches a long local subprocess from the web API; this is unsuitable for an exposed production service.
- Benchmark environment output said `Virtualenv: not detected` even when launched with `.venv\Scripts\python.exe`; this is a detection/reporting issue, not proof that the wrong interpreter ran.

## 18. Bugs already fixed and how

- **Text fallback table rows:** commit `81725e5` fixed `document_layout.py` fallback row reconstruction and added dedicated regression coverage.
- **Table Transformer architecture:** commits around `3f1cb56`/`0a5f8e0` changed it to proper two-stage full-page detection plus crop structure recognition and verified model loading/fallback behavior.
- **Layout review reliability:** commit `a4f8523` added optional layout routing, fuzzy retry thresholds, format support, row precision guards, and diagnostics without enabling ML by default.
- **Review edits and tax extraction:** commit `0a6fb78` tightened tax-rate extraction and improved correction revalidation/UI wording so edited data is rechecked.
- **Deterministic default:** commit `ccbea83` removed the LLM-first presentation/path and made the shared deterministic pipeline the official workflow.
- **Client-delivery cleanup:** commit `148f16b` removed obsolete Hybrid LLM service/benchmark modules, split dependency sets, added deployment files, and restored deterministic financial reasoning.
- **README state:** commit `371b6f4` refreshed the final product README; local `main` and `origin/main` were synchronized before this handoff.

## 19. Known limitations

- Complex tables, fragmented OCR, multi-line descriptions, missing headers, stamps/signatures, handwriting, overlap, and low-resolution scans still require review.
- Arabic support is partial.
- OCR confidence is not calibrated true accuracy.
- Public dataset labels are heterogeneous and sometimes weak/zero-item; adapter diagnostics must accompany metrics.
- ERP readiness is intentionally conservative; most benchmark documents are blocked.
- The correction loop is rule-based memory, not model training.
- The UI is a review console, not a full ERP.
- No auth, reviewer roles, tenant isolation enforcement, durable production DB, queue, audit permissions, or real ERP connector exists.
- Optional models require local weights/dependencies and are not part of the guaranteed core install.
- Fresh OCR performance is hardware/model/cache dependent.

## 20. Experimental features/models and enabled state

| Feature | State | Notes |
|---|---|---|
| Deterministic OCR/layout/extraction | Enabled | production baseline |
| `optimized_mobile_v4` | Enabled | default OCR profile |
| `p3_stable` tables | Enabled | production default |
| `p3_1_adaptive` tables | Disabled/default-off | selectable experiment only |
| Targeted balanced-mode fallback OCR | Enabled | only when planner requests regions |
| Tesseract fallback | Enabled when installed | secondary path |
| Layout model | Disabled | `ENABLE_LAYOUT_MODEL=false`; CPU, threshold 0.5 |
| Table Transformer | Disabled | `ENABLE_TABLE_TRANSFORMER=false`; CPU, threshold 0.75 |
| Hybrid/LLM correction services | Removed from shipped tree | prior implementation rejected for final client baseline |
| Correction memory | Enabled locally | conservative candidate boost; not ML |

## 21. Things tried and rejected

- **LLM-first/hybrid code in the final product:** removed because it increased dependency/configuration burden and weakened the clear deterministic client-delivery story. Any future LLM phase should consume deterministic Top-N evidence and remain advisory behind validation.
- **Replacing `p3_stable` with adaptive table heuristics:** rejected because diagnostics did not show safe, consistent row-level gains without regression/false positives.
- **Enabling Table Transformer by default:** rejected; optional dependencies/model loading and benchmark evidence were not strong enough for the production baseline.
- **Enabling a learned layout model by default:** rejected for the same reproducibility/dependency reasons; deterministic layout remains the fallback and default.
- **Treating OCR confidence as accuracy:** explicitly rejected; unlabeled runs report completeness/confidence only.
- **Running all 8,000+ dataset documents during routine development:** rejected in favor of smoke/medium/resumable tiers.
- **Automatically exporting extracted data:** rejected; readiness and human correction/revalidation must gate export.
- **Adding a final aggressive table heuristic just to hit a metric target:** rejected; stable behavior was preferred over benchmark gaming.

## 22. Current TODOs

Priority order based on the latest evidence:

1. Build manually verified labels for the six `haithem_samples` PDFs (parties, invoice number/date, currency, HT, VAT, TTC, and every line row) before claiming or optimizing accuracy.
2. Diagnose why TTC/VAT tokens collapse to small values (`5.0`, `50.0`) on these documents; inspect candidate score breakdowns, totals-region layout, and financial consistency rejection paths.
3. Profile `Inv 04.pdf` and `INV 05.pdf` to identify repeated/fallback OCR and page-level hotspots.
4. Improve customer/supplier disambiguation for export invoices where address/destination/table header text is being selected as a party.
5. Add these six files or sanitized equivalents as a fixed regression slice only after confidentiality and repository-size decisions.
6. Remove the PaddleOCR argument warning by avoiding generic `lang`/`ocr_version` arguments when explicit model names are passed; verify no language regression.
7. Fix virtualenv detection in benchmark environment reporting.
8. Tighten CORS, add auth/roles/audit storage, and move long benchmark execution out of HTTP before any deployment.
9. Add a real ERP connector sandbox only after readiness/authorization semantics are specified.
10. Keep optional ML disabled until a verified before/after benchmark demonstrates non-regressive gains.

## 23. Exact task at the end of the conversation

Immediately before this handoff request, the task was: run the current project against **all files** in `D:\Stage_udgroup\haithem_samples`. All six PDFs were processed with the official multi-dataset benchmark using balanced PaddleOCR. The run completed successfully at the process level but yielded five invalid documents and one needs-review document. The HTML report was opened in Codex.

The final task in this turn is to create this file and commit **only** `docs/CODEX_HANDOFF.md`. Do not accidentally commit `docs/vllm_environment_audit.md` or generated benchmark artifacts.

## 24. Recommended next steps

1. Start with a human ground-truth JSON schema for the six Haithem invoices and verify every label directly against the PDFs.
2. Capture a baseline run ID for this fixed slice and preserve predictions plus field/row evidence.
3. Investigate totals candidates first; wrong financial values are the principal safety failure and already block ERP export as designed.
4. Inspect fallback/timing metadata for the two slow PDFs and eliminate redundant page/region work without changing extraction semantics.
5. Add focused regression tests for each confirmed root cause before changing heuristics.
6. Run `python -m compileall app scripts tests`, focused tests, full `pytest -q`, and the fixed six-document benchmark after each material extraction change.
7. Compare before/after using verified accuracy, completeness, invalid/review rate, row count metrics, and fresh/cached timing separately.
8. Preserve `p3_stable` and default feature flags unless the evidence supports an explicit new v1.x phase.

## 25. Assumptions for the next coding agent

- Work from the repository root and use the existing `.venv` interpreter where available.
- `main` is the active branch. At the start of this handoff task, `HEAD` and `origin/main` were both `371b6f4`.
- The worktree contained an unrelated untracked `docs/vllm_environment_audit.md`; preserve it and do not stage it without explicit instruction.
- Generated benchmark files may be present under ignored/local report directories; do not bulk-stage the repository.
- The deterministic v1 freeze is an architectural constraint, not just documentation. Make narrow, evidence-backed fixes with regression tests.
- Never infer accuracy from OCR confidence or from 6/6 process success.
- Never make ERP export readiness true merely because extraction returned values.
- Preserve API compatibility (`detected_fields`, `erp_json`, flat export) while adding richer diagnostics.
- Preserve bbox/page/source evidence through corrections and revalidation.
- Do not enable Table Transformer, layout ML, or a future LLM path by default without verified non-regression results.
- Treat the private/local sample path as external data; do not commit its PDFs unless the owner explicitly authorizes that action.
- Use `scripts/benchmark_multi_datasets.py --check-env` before expensive benchmark runs.
- Expected last documented full suite state is `260 passed, 1 Starlette/FastAPI httpx deprecation warning`; rerun tests rather than assuming it remains current.

## Verification commands

```powershell
git status --short
.\.venv\Scripts\python.exe -m compileall app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\benchmark_multi_datasets.py --check-env
```

Relevant documentation: `README.md`, `docs/architecture_overview.md`, `docs/v1_deterministic_architecture.md`, `docs/limitations.md`, `docs/benchmark_summary.md`, `release_metrics.md`, and `benchmark_snapshot.json`.
