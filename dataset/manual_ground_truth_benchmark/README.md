# Manual Ground-Truth Benchmark

This folder contains a small fixed benchmark for true OCR-to-ERP extraction accuracy.

Ground truth is never auto-filled. Each label must be manually checked from the source document.

Phase 2.7 adds a fixed ten-document campaign:

- `selected_verified_10_documents.json`
- `verified_label_validation.json`
- `verified_label_quality_report.md`
- `verified_label_review_queue.html`

Accuracy claims are allowed only when all selected labels have:

```json
"verification_status": "verified",
"verified_by_human": true
```

and complete verification metadata:

- `verified_by`
- `verified_at`
- `source_document`
- `notes`
- `uncertain_fields`

Unverified labels are refused by `scripts/benchmark_manual_ground_truth.py`.

## Workflow

1. Prepare or refresh the ten-document campaign metadata:

```powershell
python scripts/verified_label_campaign.py --benchmark-root dataset/manual_ground_truth_benchmark --update-labels
```

2. Open the review queue:

```powershell
start dataset/manual_ground_truth_benchmark/verified_label_review_queue.html
```

3. For each document, inspect the source image/PDF and fill the matching label JSON.

Do not copy deterministic values into labels without checking the document.

4. Validate label quality:

```powershell
python scripts/verified_label_campaign.py --benchmark-root dataset/manual_ground_truth_benchmark
```

5. Run the current pipeline only after all labels are verified:

```powershell
python scripts/benchmark_manual_ground_truth.py --benchmark-root dataset/manual_ground_truth_benchmark --run-name baseline
```

6. Compare two runs:

```powershell
python scripts/compare_manual_benchmark_runs.py --before baseline --after after_fix
```

## Important

- OCR confidence is not accuracy.
- Missing ground-truth fields are excluded from accuracy denominators.
- Labels should be verified from the document image/PDF, not copied blindly from OCR output.
- Empty strings are not valid verified values. Use explicit `null` when a field is genuinely absent.
- Blank line-item template rows are ignored for counts and block verified status until removed or replaced.
