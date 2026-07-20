# OCR Performance Optimization P0

## Root Cause

The real OCR baseline shows that OCR inference is the bottleneck:

- Warm engine / cold document cache median: `147.82s`
- Warm engine / cold document cache p90: `148.54s`
- Median OCR inference for the accepted clean invoice runs: about `144.45s`
- OCR percentage of total runtime: about `97.65%`
- Downstream extraction, validation, ERP mapping, and serialization are below `3%`

The baseline is valid because PaddleOCR executed, OCR cache was disabled for the cold-cache runs, and OCR bounding boxes were preserved.

## Legacy Runtime Configuration

Recorded by:

```powershell
.\.venv\Scripts\python.exe scripts\ocr_experiments.py --inspect-only
```

Original baseline environment:

- PaddleOCR: `3.7.0`
- PaddlePaddle: `3.3.1`
- Device: `cpu`
- GPU available: `False`
- Logical cores: `16`
- Physical cores: `12`
- RAM: `15.71 GB`
- MKLDNN: `False`
- CPU threads: unset
- Detector: `PP-OCRv6_medium_det`
- Recognizer: `PP-OCRv6_medium_rec`
- Orientation classifier: disabled
- Document unwarping: disabled
- Textline orientation: disabled
- Preprocessing profile: `current`
- Detection side limit: unset

## P0 Implementation

The accepted OCR optimization is now the default production OCR profile.

New reversible configuration switches were added:

- `INVOICE_OCR_PADDLE_ENABLE_MKLDNN`
- `INVOICE_OCR_PADDLE_CPU_THREADS`
- `INVOICE_OCR_PADDLE_USE_GPU`
- `INVOICE_OCR_PADDLE_OCR_VERSION`
- `INVOICE_OCR_PADDLE_TEXT_DETECTION_MODEL_NAME`
- `INVOICE_OCR_PADDLE_TEXT_RECOGNITION_MODEL_NAME`
- `INVOICE_OCR_PADDLE_TEXT_DET_LIMIT_SIDE_LEN`
- `INVOICE_OCR_PADDLE_TEXT_RECOGNITION_BATCH_SIZE`
- `INVOICE_OCR_OCR_PREPROCESSING_PROFILE`
- `INVOICE_OCR_OCR_INPUT_MAX_SIDE`

The OCR cache fingerprint now includes these settings, so incompatible OCR experiment outputs do not reuse old cache entries.

## Experiment Harness

Created:

```powershell
.\.venv\Scripts\python.exe scripts\ocr_experiments.py --experiments current --repeats 3
```

Output root:

```text
dataset/reports/performance/ocr_experiments/
```

Supported experiment names:

- `current`
- `legacy_medium_v6`
- `mkldnn`
- `threads_1`
- `threads_2`
- `threads_4`
- `mobile_v4`
- `mobile_v4_resize_1600`
- `mobile_v4_resize_1280`
- `mobile_v4_threads_4`
- `mobile_v4_threads_4_resize_1600`
- `mobile_v3`
- `resize_2560`
- `resize_1920`
- `resize_1600`
- `resize_1280`
- `pre_minimal`
- `pre_grayscale`
- `pre_contrast`
- `pre_direct`

Each experiment writes:

- `configuration.json`
- `timings.csv`
- `timings.json`
- `quality.json`
- `bbox.json`
- `summary.json`
- `report.md`

The global output writes:

- `comparison.csv`
- `comparison.md`
- `environment.json`
- `root_cause_report.md`

## Acceptance Rule

The accepted configuration was required to:

- Executes real OCR.
- Uses warm engine / cold document cache timing.
- Improves median runtime.
- Preserves bounding boxes.
- Preserves extracted fields and ERP output.
- Pass the full regression suite.

## Experiment Results

Clean invoice, warm engine, cold document cache:

| Experiment | Median Total | Median OCR | Quality | Decision |
|---|---:|---:|---|---|
| Legacy `PP-OCRv6_medium_*` | `152.87s` | `149.28s` | Preserved | Rejected, too slow |
| Resize 1280 only | `107.28s` | `104.56s` | Lost line items | Rejected |
| Resize 1600 only | `123.18s` | `120.27s` | Preserved | Rejected, too slow |
| Mobile v4 only | `46.08s` | `43.95s` | Preserved | Rejected, above target |
| Mobile v4 + resize 1600 | `39.05s` | `38.14s` | Preserved | Rejected, above target |
| Mobile v4 + resize 1280 | `36.91s` | `36.10s` | Lost line items | Rejected |
| MKLDNN | invalid | invalid | Paddle failed, Tesseract fallback | Rejected |
| Threads 4 only | `93.06s` | `89.73s` | Preserved | Rejected, too slow |
| Mobile v4 + threads 4 + resize 1600 | `26.61s` | `25.78s` | Preserved | Accepted |

Accepted configuration:

- Detector: `PP-OCRv4_mobile_det`
- Recognizer: `en_PP-OCRv4_mobile_rec`
- CPU threads: `4`
- Input max side: `1600`
- MKLDNN: `False`
- GPU: `False`
- Preprocessing profile: `current`

## Final Validation

Representative one-run validation with the accepted configuration:

| Document | Total | OCR | Boxes | Key quality |
|---|---:|---:|---:|---|
| Clean invoice `batch1-0001.jpg` | `24.29s` median over 3 | `23.57s` | `88` | Supplier/customer/invoice/totals preserved; line items found |
| French demo invoice | `19.95s` | `19.01s` | `66` | Valid; invoice/totals/line items found |
| Table-heavy invoice | `13.97s` | `13.04s` | `30` | Needs review; lines found |
| Noisy invoice | `12.36s` | `11.65s` | `42` | Invalid but OCR completed with boxes |
| Photographed invoice | `14.35s` | `13.70s` | `49` | Low confidence, still below target |

The target is achieved for the measured representative set. OCR remains the largest stage, but it is now below the 30-second target on the accepted benchmark.

## Production Default

The accepted configuration is now the default. The previous v6-medium configuration remains available by environment override:

```powershell
$env:INVOICE_OCR_PADDLE_TEXT_DETECTION_MODEL_NAME="PP-OCRv6_medium_det"
$env:INVOICE_OCR_PADDLE_TEXT_RECOGNITION_MODEL_NAME="PP-OCRv6_medium_rec"
$env:INVOICE_OCR_PADDLE_CPU_THREADS="0"
$env:INVOICE_OCR_OCR_INPUT_MAX_SIDE="0"
```

No non-OCR optimization was applied.
