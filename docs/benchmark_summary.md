# Benchmark Summary

This project contains two benchmark directions:

1. Tiered dataset evaluation for fast development checks.
2. Manual ground-truth benchmark scaffolding for true accuracy measurement.

## Tiered Evaluation

The tiered evaluator avoids running thousands of documents after every code change.

Recommended commands:

```powershell
python scripts/evaluate_dataset.py --mode smoke
python scripts/evaluate_dataset.py --mode medium
python scripts/evaluate_dataset.py --mode full --resume
```

Smoke mode is for quick regression checks. Full mode is intended for overnight runs only.

## Multi-Dataset Benchmark

The multi-dataset benchmark scans external datasets and writes per-dataset and global reports.

```powershell
python scripts/benchmark_multi_datasets.py --check-env
python scripts/benchmark_multi_datasets.py --datasets-root D:\Stage_mr_f\sources\datasets --limit-per-dataset 5 --seed 42 --force
```

The environment check is important. If no OCR engine is available, the benchmark must stop instead of producing a fake all-failed report.

## Manual Ground Truth

True accuracy requires labels verified by a human. The manual benchmark intentionally refuses to claim accuracy for unverified labels.

Important distinction:

- Extraction completeness: the field was found.
- OCR confidence: the OCR/extraction engine is confident.
- True accuracy: the extracted field matches manually verified ground truth.

OCR confidence is not true accuracy.

## What to Show in Presentation

Show benchmark methodology, not exaggerated numbers:

- fast smoke checks for development
- cached OCR for repeatability
- blocked export for uncertain results
- manual ground truth required for final accuracy claims

