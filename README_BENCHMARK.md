# Large-Scale Benchmark Guide

This benchmark system evaluates the existing OCR-to-ERP pipeline on the document batches in:

```powershell
D:\Stage_mr_f\sources
```

Expected folders:

- `batch_1`
- `batch_2`
- `batch_3`

Supported files:

- `.pdf`
- `.png`
- `.jpg`
- `.jpeg`
- `.tif`
- `.tiff`
- `.bmp`

## Run A Small Test

Use this before launching the full 8,000-document benchmark:

```powershell
cd D:\Stage_mr_f\invoice-ocr-erp
.venv\Scripts\python.exe scripts\benchmark_8000.py --source D:\Stage_mr_f\sources --limit 10
.venv\Scripts\python.exe scripts\generate_benchmark_report.py
```

## Run The Full Benchmark

```powershell
cd D:\Stage_mr_f\invoice-ocr-erp
.venv\Scripts\python.exe scripts\benchmark_8000.py --source D:\Stage_mr_f\sources
.venv\Scripts\python.exe scripts\generate_benchmark_report.py
```

The full run may take hours depending on OCR speed and document size.

## Run One Batch

```powershell
.venv\Scripts\python.exe scripts\benchmark_8000.py --source D:\Stage_mr_f\sources --batch batch_1
```

## Resume Mode

Resume is automatic.

If a prediction JSON already exists, the benchmark skips that file unless `--force` is used.

```powershell
.venv\Scripts\python.exe scripts\benchmark_8000.py --source D:\Stage_mr_f\sources
```

## Force Rerun

```powershell
.venv\Scripts\python.exe scripts\benchmark_8000.py --source D:\Stage_mr_f\sources --force
```

## Outputs

Prediction JSON files:

```powershell
dataset\predictions\benchmark_8000\
```

Reports:

```powershell
dataset\reports\benchmark_8000\
```

Important output files:

- `results.csv`
- `error_analysis.csv`
- `manual_review_sample.csv`
- `metrics.json`
- `benchmark.log`
- `report.md`
- `report.html`
- `charts\*.png`

## Report Meaning

The report measures:

- processing speed
- OCR confidence
- overall confidence
- validation status distribution
- extraction completeness
- missing field rates
- line item extraction coverage
- error categories
- ERP export safety

ERP statuses:

- `valid`: can be exported automatically
- `needs_review`: requires human verification
- `invalid`: blocked from ERP export

Blocking uncertain data is safer than exporting wrong data.

## Important Accuracy Warning

True field accuracy requires manually verified ground-truth labels.

Without labels in:

```powershell
dataset\labels\benchmark_8000\
```

the benchmark does not claim true accuracy. It reports extraction completeness, confidence, validation status, and performance.

## Manual Accuracy Sampling

The benchmark creates:

```powershell
dataset\reports\benchmark_8000\manual_review_sample.csv
```

It selects around:

- 40 valid documents
- 40 needs-review documents
- 20 invalid documents

Use this file to manually verify supplier name, invoice number, invoice date, and total TTC without labeling all 8,000 files.
