# Manual Ground-Truth Benchmark

This folder contains a small fixed benchmark for true OCR-to-ERP extraction accuracy.

Ground truth is never auto-filled. Each label must be manually checked and must contain:

```json
"verified_by_human": true
```

Unverified labels are refused by `scripts/benchmark_manual_ground_truth.py`.

## Workflow

1. Prepare candidates and blank labels:

```powershell
python scripts/manual_label_helper.py --prepare --benchmark-root dataset/manual_ground_truth_benchmark
```

2. Manually verify labels:

```powershell
python scripts/manual_label_helper.py --benchmark-root dataset/manual_ground_truth_benchmark
```

3. Run the current pipeline:

```powershell
python scripts/benchmark_manual_ground_truth.py --benchmark-root dataset/manual_ground_truth_benchmark --run-name baseline
```

4. Compare two runs:

```powershell
python scripts/compare_manual_benchmark_runs.py --before baseline --after after_fix
```

## Important

- OCR confidence is not accuracy.
- Missing ground-truth fields are excluded from accuracy denominators.
- Labels should be verified from the document image/PDF, not copied blindly from OCR output.
