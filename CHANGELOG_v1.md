# CHANGELOG v1 - Deterministic Engine

## Release

Recommended tag: `v1.0-deterministic`

## Major Milestones

- Built the FastAPI OCR-to-ERP processing pipeline.
- Added PaddleOCR/Tesseract OCR integration with bbox-aware normalization.
- Added document previews and a human review UI.
- Added candidate-based field extraction with validation guardrails.
- Added document layout, semantic block detection, and document graph extraction.
- Added deterministic table reconstruction and line-item validation.
- Added financial reconciliation for HT, TVA/VAT, TTC, discounts, stamp duty, and payable totals.
- Added ERP readiness decisions to block unsafe export.
- Added multi-dataset benchmark infrastructure with resumable/cached runs.

## OCR Optimization

- Frozen v1 OCR profile: `optimized_mobile_v4`.
- Added OCR cache support and environment checks.
- Preserved bbox/page metadata across the public API and UI review path.

## Benchmark Evolution

- Added smoke, medium, full, cached, fail-fast, and resumable benchmark modes.
- Added per-dataset and global reports.
- Added failure taxonomy, performance timing, and reproducible run metadata.
- Added fair canonical comparisons for parties and line items.

## Table Engine Evolution

- Added deterministic table reconstruction with stable and experimental profiles.
- Default frozen profile: `p3_stable`.
- Experimental profile: `p3_1_adaptive`, disabled by default.
- Added table diagnostics, row count evaluation, line-item comparison, and manual review exports.

## Party Engine Evolution

- Replaced hidden party heuristics with deterministic candidate ranking.
- Added Top-N party candidates with visible score breakdowns.
- Added party diagnostics:
  - `party_candidate_ranking.csv`
  - `party_candidate_debug.json`
  - `party_confidence_report.csv`
- Improved canonical comparison when labels contain company plus address.

## Validation Improvements

- Added required-field checks.
- Added suspicious party, amount, and line-item guardrails.
- Added explicit ERP readiness states.
- Prevented invalid high-confidence extraction from being silently exported.

## Confidence Improvements

- Separated OCR confidence from extraction, validation, business, ERP readiness, and overall confidence.
- Kept confidence conservative for missing required fields and invalid validation states.

## ERP Readiness

- Preserved legacy ERP JSON/export compatibility.
- Added richer debug output while keeping existing API fields stable.
- Only validated and safe results are export-ready.

## Benchmark Fairness Improvements

- Added party canonical normalization.
- Added table ground-truth adapter and schema audit.
- Separated completeness from true accuracy.
- Marked unsupported or explicit-zero ground truth clearly.

## Final Stable Metrics

| Metric | Result |
| --- | ---: |
| Invoice number normalized accuracy | 100% |
| Invoice date normalized accuracy | 100% |
| Amount TTC normalized accuracy | 76.92% |
| Supplier canonical accuracy | 70% |
| Customer canonical accuracy | 100% |
| Canonical line-item presence | 60% |
| Canonical exact row count | 52% |
| Canonical row count within ±1 | 68% |

## Freeze Notes

The deterministic pipeline is frozen at v1.0. Further extraction-quality improvements should begin in the Hybrid LLM phase unless a narrow v1 maintenance defect is opened.
