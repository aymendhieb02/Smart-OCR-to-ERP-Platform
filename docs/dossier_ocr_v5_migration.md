# Dossier OCR v5 migration checkpoint

## Scope and status

This checkpoint adds an internal dossier-processing path and an opt-in PP-OCRv5 profile. It does not change API routes, public schemas, the production default, extraction algorithms, or ERP contracts. `optimized_mobile_v4` remains the default and rollback target.

The six-dossier labels are draft development data. They were not loaded by the operational benchmark, have not been marked verified, and were not used to calculate any accuracy percentage. Original rendered page images remain the human-verification source of truth.

## Architecture and data flow

`process_dossier_file()` in `app/services/pipeline_runner.py` implements:

1. render the source PDF once with `load_document()`;
2. OCR every physical page once with one `OCREngine.run()` call;
3. keep `OCRLine.page_number` in the original physical PDF coordinate system;
4. classify each page with deterministic text evidence in `app/services/dossier_segmentation.py`;
5. group classifications into `LogicalDocumentGroup` objects;
6. slice the already-produced OCR evidence and page images per group;
7. reuse `_process_ocr_document()` for extraction, validation, review, and ERP readiness.

No classification OCR pass is followed by a second full-page OCR pass. The existing `process_document_file()` signature and behavior are unchanged. The dossier dataclasses are internal orchestration types and do not alter `app/core/schemas.py`.

`run_fallback_regions()` accepts an optional physical-page-number sequence. A logical document containing original page 3 therefore continues to emit fallback `OCRLine` objects with `page_number=3`, not 1. A length mismatch fails explicitly with `ValueError`.

## Deterministic segmentation

Known families are evidence rules, not extraction engines:

- `ciments_enfidha_invoice_v1`
- `sotacib_kasserine_white_invoice_v1`
- `sotacib_kairouan_grey_invoice_v1`
- `ruspina_reinvoice_v1`
- `customs_douanes_tunisiennes_v1`
- `customs_tradenet_v1`

Unknown evidence produces `document_family=None`; it is never forced into a known family. Customs masthead evidence takes precedence over incidental supplier text on the same customs form. Multiple customs-form layout anchors can classify the document type while leaving the family unknown. The grouper supports future multi-page logical documents through `starts_new_document=False`; it does not encode a permanent one-page assumption.

Latest cache-backed segmentation diagnostics with Latin PP-OCRv5 over all 18 physical pages:

| Dossier | Page 1 | Page 2 | Page 3 |
|---|---|---|---|
| `INV 01.pdf` | Ciments Enfidha invoice | Ruspina reinvoice | TradeNet customs |
| `Inv 02.pdf` | SOTACIB Kasserine white invoice | Ruspina reinvoice | TradeNet customs |
| `INV 03.pdf` | SOTACIB Kasserine white invoice | Ruspina reinvoice | TradeNet customs |
| `Inv 04.pdf` | SOTACIB Kairouan grey invoice | Ruspina reinvoice | SOTACIB Kairouan grey invoice |
| `INV 05.pdf` | SOTACIB Kairouan grey invoice | Ruspina reinvoice | unknown |
| `Inv 06.pdf` | Ciments Enfidha invoice | Ruspina reinvoice | Douanes Tunisiennes customs |

This table reports classifier behavior, not correctness or accuracy. `Inv 04.pdf` page 3 is a known unsafe classification caused by a badly recognized customs masthead plus readable supplier text. `INV 05.pdf` page 3 correctly exercises the conservative unknown fallback. Position-based correction was deliberately rejected.

## PP-OCRv5 profile

The opt-in `optimized_mobile_v5` profile is:

```text
detector: PP-OCRv5_mobile_det
recognizer: latin_PP-OCRv5_mobile_rec
CPU threads: 4
MKLDNN: disabled
GPU: disabled
input_max_side: 1600
preprocessing_profile: current
```

Activate it only for an explicit run with `INVOICE_OCR_PROFILE=optimized_mobile_v5` or the equivalent settings override. The global default is unchanged. Cache fingerprints retain detector and recognizer names and now also include the explicit profile name.

With PaddleOCR 3.7.0 and PaddlePaddle 3.3.1 from the repository `.venv`, real initialization succeeded. The first controlled v5 initialization took 10.049 seconds; the detector downloaded and the Latin recognizer was already cached.

Model artifacts are external user-cache data and are not committed:

| Model | Cache location | Bytes |
|---|---|---:|
| `PP-OCRv5_mobile_det` | `C:\Users\Msi\.paddlex\official_models\PP-OCRv5_mobile_det` | 4,945,147 |
| `latin_PP-OCRv5_mobile_rec` | `C:\Users\Msi\.paddlex\official_models\latin_PP-OCRv5_mobile_rec` | 8,217,689 |
| `arabic_PP-OCRv5_mobile_rec` | `C:\Users\Msi\.paddlex\official_models\arabic_PP-OCRv5_mobile_rec` | 8,172,161 |

## Operational measurements

Command used:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_dossier_ocr.py `
  --samples-root D:\Stage_udgroup\haithem_samples `
  --ocr-profile optimized_mobile_v5 `
  --ocr-mode balanced `
  --passes 2 `
  --refresh-cache
```

The runner is sequential (`document_concurrency=1`, page processing sequential), does not load labels, and reports operational metrics only.

| Pass | 18-page total | Seconds/page | OCR cache behavior |
|---|---:|---:|---|
| cold/refresh | 773.303 s | 42.961 s | fresh PP-OCRv5 inference |
| warm | 85.080 s | 4.727 s | disk-cache-backed OCR |

Cold dossier durations ranged from 125.466 to 132.136 seconds. Warm dossier durations ranged from 12.787 to 15.888 seconds. The highest sampled process working set was 912.12 MiB. System available physical memory changed from 2.666 GiB before the run to 2.089 GiB after it; Windows pagefile usage changed from 29.478 GiB to 30.122 GiB. These host-wide snapshots include unrelated system activity and are not an isolated memory allocation measurement.

The benchmark host exposed 16 logical CPUs. These figures must not be presented as a validated 4-CPU/8-GB capacity result; the configured OCR thread count was four, but the host itself was larger. A target-server soak test remains required.

## Arabic experiment

`arabic_PP-OCRv5_mobile_rec` initialized successfully with the v5 mobile detector and recognized page 3 of `INV 01.pdf`. It produced 251 OCR lines, 50 of which contained at least one Arabic-range character. The run took 72.086 seconds: 4.114 seconds initialization and 67.533 seconds full-page inference. Process working set changed from 139.49 MiB before the run to 623.30 MiB afterward. The Arabic recognizer cache occupies 8,172,161 bytes.

Character recognition and reading order are separate findings:

- Character output is Unicode-preserving and includes Arabic code points, proving the model can initialize and emit Arabic on the source material.
- Many recognized Arabic fragments are visibly noisy or very short, so this experiment does not establish acceptable character quality.
- Engine output interleaves Latin and Arabic blocks in detector order. It does not establish correct human RTL reading order.
- No reversal, reshaping, dual-recognizer merge, or language router was added.

Running both recognizers on every page is not recommended from this checkpoint. The Arabic full-page experiment was expensive and degraded Latin text on the mixed page. A later experiment should crop representative Arabic regions, use human-verified transcriptions, and measure character recognition separately from RTL ordering before considering selective routing.

## Verification and failure behavior

Regression coverage includes deterministic known/unknown classification, customs evidence precedence, missing OCR pages, future multi-page grouping, Unicode-compatible `OCRLine` consumption, one OCR pass per dossier, unchanged single-document entry point, physical fallback page preservation, v4 profile invariance, v5 configuration, cache separation, and graceful model-initialization failure.

If Paddle initialization fails, `OCREngine.run()` logs the failure and follows the existing fallback policy. With Tesseract fallback disabled it returns an empty result rather than crashing. `process_dossier_file()` then fails clearly when no OCR text is available; it does not manufacture extracted data.

## Rollback and follow-up

Rollback is configuration-only: keep or restore `INVOICE_OCR_PROFILE=optimized_mobile_v4`. No public schema or persisted ERP format migration is required.

Before production promotion:

1. independently human-verify the six-dossier labels;
2. benchmark field and segmentation quality only after that verification;
3. review `Inv 04.pdf` and `INV 05.pdf` customs masthead OCR using original page images;
4. design selective Arabic-region routing only if verified measurements justify its latency and memory cost;
5. run a cold/warm soak on the actual 4-CPU, 8-GB, no-GPU target;
6. keep concurrency at one until that soak is complete;
7. do not switch the production default in this checkpoint.
