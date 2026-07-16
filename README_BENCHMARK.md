# Tiered Evaluation Guide

This project should not process all 8,000+ documents during normal development. Use the tiered evaluator in `scripts/evaluate_dataset.py` and keep the default mode as `smoke`.

Dataset root:

```powershell
D:\Stage_udgroup\sources
```

Expected folders:

- `batch_1`
- `batch_2`
- `batch_3`

Supported files: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`.

## Fast Development Loop

Run this after every code change:

```powershell
python scripts/evaluate_dataset.py --mode smoke
```

Smoke mode randomly samples 30 documents balanced across `batch_1`, `batch_2`, and `batch_3`. It runs the full extraction pipeline but finishes fast enough for development.

For reproducible debugging:

```powershell
python scripts/evaluate_dataset.py --mode smoke --seed 42
```

The default mode is `smoke`, so this is equivalent:

```powershell
python scripts/evaluate_dataset.py
```

## Medium Evaluation

Use this when you want a more meaningful report without waiting for the full dataset:

```powershell
python scripts/evaluate_dataset.py --mode medium
```

Medium mode samples 300 documents balanced across the three batches and writes detailed reports.

## Full Evaluation

Use full mode only for overnight or long unattended runs:

```powershell
python scripts/evaluate_dataset.py --mode full --resume
```

Full mode processes every supported document. It writes a checkpoint every 25 documents and can resume without starting from zero.

## Cached Evaluation

The evaluator computes a SHA-256 hash for every file and stores reusable OCR/layout artifacts:

```text
outputs/cache/ocr/{hash}.json
outputs/cache/layout/{hash}.json
```

Cached mode reuses those artifacts when available, so extraction and validation changes can be tested without rerunning OCR for documents already seen:

```powershell
python scripts/evaluate_dataset.py --mode cached --resume
```

Cache is enabled by default. To explicitly keep cache enabled in a scripted command:

```powershell
python scripts/evaluate_dataset.py --mode smoke --no-ocr-cache false
```

To force OCR/layout recomputation:

```powershell
python scripts/evaluate_dataset.py --mode smoke --no-ocr-cache true
```

## Fail-Fast Debugging

Use fail-fast mode when a new change is causing hard crashes:

```powershell
python scripts/evaluate_dataset.py --mode fail-fast --seed 42
```

It uses a smoke-sized sample and stops after 10 critical errors. For an even stricter run that stops after the first critical error:

```powershell
python scripts/evaluate_dataset.py --mode smoke --fail-fast
```

## Outputs

Every run writes to:

```text
outputs/evaluation/runs/{run_id}/
```

Important files:

- `summary.json`
- `results.csv`
- `errors.csv`
- `rejected_candidates.csv`
- `needs_review_samples.json`
- `worst_20_documents.json`
- `report.html`
- `checkpoint.json`
- `predictions/*.json`

The summary is updated progressively after every document, not only at the end.

## Metrics

The evaluator reports:

- documents processed
- average time per document
- estimated time for a full 8,000-document run
- `valid` / `needs_review` / `invalid` distribution
- top missing fields
- top rejected values
- line items validated vs needs review
- totals consistency rate
- OCR cache hit rate
- layout cache hit rate

## Recommended Workflow

1. Run `smoke` after each code change.
2. Run `medium` before showing results or comparing extraction changes.
3. Run `fail-fast` when debugging crashes.
4. Run `cached` when OCR has already been computed and you only changed extraction or validation logic.
5. Run `full --resume` overnight only.

