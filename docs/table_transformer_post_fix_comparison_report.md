# Table Transformer Post-Fix Comparison Report

## 1. Summary Of Fix

The optional Microsoft Table Transformer path was repaired from a one-stage full-page structure-recognition call into the correct two-stage flow: `microsoft/table-transformer-detection` runs on the full page, detected table regions are padded and cropped, `microsoft/table-transformer-structure-recognition` runs only inside each crop, and crop-relative structure boxes are remapped back into full-page coordinates before OCR-to-cell mapping. P3 Stable remains the production default and `INVOICE_OCR_ENABLE_TABLE_TRANSFORMER` remains off by default.

## 2. Test Results

| Check | Result |
|---|---|
| `python -m compileall app scripts tests` | PASS |
| `python -m pytest tests/test_table_transformer_experimental.py -v` | PASS, 10 passed |
| `python -m pytest -q` | PASS, 326 passed, 1 existing Starlette deprecation warning |

No failures were observed, so no regression patching was needed.

## 3. Post-Fix Benchmark Metrics

Source: `dataset/reports/table_transformer/post_fix_two_stage_v1/table_transformer_metrics.json`

| Metric | Value |
|---|---:|
| Documents | 10 |
| Success count | 10 |
| Error count | 0 |
| Table detection rate | 0.6 |
| Average latency seconds | 2.2182 |
| Cache hit ratio | 0.0 |

## 4. Table Detection Sanity Check

**Result: YES, the flat `max_tables` bug is resolved.**

Pre-fix, the 10-document `compare_normal_vs_transformer` run reported `Tables = 10` for every document, exactly matching `INVOICE_OCR_TABLE_TRANSFORMER_MAX_TABLES`. Post-fix, the table counts are:

`[2, 2, 2, 0, 0, 0, 2, 2, 2, 0]`

That means the detector is no longer returning a flat hard-cap value. Counts are now small and realistic for this benchmark slice: either 0 or 2 table regions per document. Four FATURA2 documents had 0 detected tables, which should be reviewed separately, but the cap-misapplication symptom is gone.

## 5. Row-Count Table: P3 Stable vs Table Transformer

| Filename | P3 Stable rows | Table Transformer rows (post-fix) | P3 status | TT status | Agreement |
|---|---:|---:|---|---|---|
| 01_batch1-0001.jpg | 7 | 0 | success | success | N |
| 02_batch1-0002.jpg | 5 | 5 | success | success | Y |
| 03_batch1-0003.jpg | 3 | 2 | success | success | N |
| 04_test-00000-of-00001_000000.png | 0 | 0 | success | success | Y |
| 05_test-00000-of-00001_000001.png | 0 | 0 | success | success | Y |
| 06_test-00000-of-00001_000002.png | 0 | 0 | success | success | Y |
| 07_test-00000-of-00001-af2d92d1cee28514_000000.png | 1 | 1 | success | success | Y |
| 08_test-00000-of-00001-af2d92d1cee28514_000001.png | 6 | 5 | success | success | N |
| 09_test-00000-of-00001-af2d92d1cee28514_000002.png | 1 | 1 | success | success | Y |
| 10_test-00000-of-00001_000003.png | 0 | 0 | success | success | Y |

Across 10 documents, P3 and Table Transformer agreed on row count for 7 documents: **70.0% agreement**.

## 6. Ground-Truth-Scored Accuracy

No verified ground truth is available for this document set. The manual benchmark metadata reports `verified_documents = 0` and `accuracy_claims_allowed = false`; labels exist, but they are still `draft` and contain blank templates. Therefore this report measures row-count agreement and benchmark behavior only, not extraction accuracy.

Because there are no verified labels, the question "which engine is closer to ground truth" cannot be answered honestly for this 10-document slice.

## 7. Latency And Pre-Fix Comparison

| Metric | Pre-fix | Post-fix |
|---|---:|---:|
| Table detection rate | 1.0 | 0.6 |
| Average TT latency seconds | 2.944 | 2.2182 |
| Table counts | `[10, 10, 10, 10, 10, 10, 10, 10, 10, 10]` | `[2, 2, 2, 0, 0, 0, 2, 2, 2, 0]` |

The post-fix two-stage path did not cost more on this cached benchmark run. Average Table Transformer latency decreased from 2.944s to 2.2182s, likely because the structure model is no longer run noisily over the full page and no longer produces max-capped full-page structure candidates. The first document still pays model/session warm-up cost.

P3 Stable average processing time on the same 10 documents was 2.062s. Table Transformer post-fix average latency was 2.2182s.

## 8. Recommendation

Recommendation: **remain optional until verified row-level gains exceed P3 Stable**.

The bar has been **partially met**: the detection bug is fixed and the flat `max_tables` symptom is gone. The bar has **not** been fully met because there is no verified ground-truth accuracy proof, and Table Transformer only matched P3 row count on 70.0% of this slice. Do not switch production default based on this report.
