# Multi-Dataset Benchmark

This benchmark layer runs the existing OCR-to-ERP pipeline on samples from multiple datasets without changing the core extraction flow.

## Run a small benchmark

```bash
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --limit-per-dataset 5 --seed 42
```

If the environment was broken in a previous run and failed predictions already exist, rerun after fixing OCR with:

```bash
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --limit-per-dataset 5 --seed 42 --force
```

## Check environment

```bash
python scripts/benchmark_multi_datasets.py --check-env
```

The benchmark stops immediately if no OCR engine is available.

## Benchmark sizes

Smoke:

```bash
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --limit-per-dataset 5 --seed 42
```

Medium:

```bash
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --limit-per-dataset 100 --seed 42
```

Real:

```bash
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --limit-per-dataset 500 --seed 42
```

Full:

```bash
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --seed 42
```

## Run one dataset

```bash
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --dataset FATURA2-invoices --limit-per-dataset 100 --seed 42
```

## Run all datasets

```bash
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_udgroup\sources\datasets --limit-per-dataset 50 --seed 42
```

## Regenerate reports only

```bash
python scripts/generate_multi_dataset_report.py --output D:\Stage_udgroup\invoice-ocr-erp\dataset\reports\multi_dataset_benchmark
```

## Outputs

- `dataset/reports/multi_dataset_benchmark/results.csv`
- `dataset/reports/multi_dataset_benchmark/manual_review_sample.csv`
- `dataset/reports/multi_dataset_benchmark/checkpoint.json`
- `dataset/reports/multi_dataset_benchmark/benchmark.log`
- `dataset/reports/multi_dataset_benchmark/predictions/<dataset_name>/`
- `dataset/reports/multi_dataset_benchmark/datasets/<dataset_name>/summary.json`
- `dataset/reports/multi_dataset_benchmark/datasets/<dataset_name>/report.md`
- `dataset/reports/multi_dataset_benchmark/datasets/<dataset_name>/report.html`
- `dataset/reports/multi_dataset_benchmark/global_summary.json`
- `dataset/reports/multi_dataset_benchmark/global_report.md`
- `dataset/reports/multi_dataset_benchmark/global_report.html`

## Completeness vs accuracy

- Completeness means the field was found.
- Accuracy means the predicted value matched ground truth.

If a dataset has no usable labels, the benchmark reports completeness, confidence, validation status, and performance only.

## Label matching

The benchmark tries:

1. same filename stem
2. normalized stem with split prefixes
3. JSON files whose text mentions the image filename
4. nearby label folders such as `labels`, `ground_truth`, `annotations`, `json`, `exported/labels`, and `exported_parquet/labels`

## Interpreting results

- `valid` means safe for automatic ERP export
- `needs_review` means human verification is needed
- `invalid` means blocked

Treat unlabeled datasets as operational smoke tests, not as true accuracy benchmarks.

