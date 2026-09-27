# Page-1 verified producer baseline workflow

This tooling evaluates one physical invoice page at a time. It is an evaluation
workflow only: it does not change extraction, OCR, UI behavior, or export policy.
It must not be described as global/model/production accuracy.

## Private versus tracked artifacts

Keep source documents, human-verified values, cached OCR evidence, machine
predictions, and comparison reports under the ignored `local_data/` tree. Do not
copy real names, identifiers, amounts, addresses, OCR text, or evidence into
tracked fixtures, tests, or documentation. The canonical full-dossier GT may
contain other page sections; a Page-1 evaluation selects only the one producer
logical-document block whose `physical_page_numbers` is `[1]` and must leave all
other blocks unchanged.

The committed comparator and runner are generic. Synthetic tests use values
such as `INV-TEST-001`, `SUPPLIER_TEST`, and `1000.00`.

## Ground-truth field shape

Each producer Page-1 field record uses:

```json
{
  "source_value": "1 000.00 EUR",
  "verified_value": 1000.0,
  "normalized_value": 1000.0,
  "machine_value": 990.0,
  "canonical_machine_value": 1000.0,
  "verification_status": "verified",
  "source_page": 1,
  "presence_status": "present",
  "likely_root_cause": null
}
```

`source_value` preserves the printed display, `verified_value` is the human
verified semantic value, and `normalized_value` is used for semantic comparison.
Use explicit `verified_value: null` plus `presence_status: "absent"` for concepts
that are truly absent; this permits true absence and false-positive extraction
to be distinguished. Never infer verified values from a machine prediction.

Line-item rows keep stable `row_id`, source/display values, normalized values,
and canonical cells: `description`, `quantity`, `unit`, `unit_price`,
`line_total`. Do not make Page-2 invoice rows or Page-3 customs articles part of
the producer Page-1 metric.

## Prediction layers and comparison

The runner records raw machine output, canonical machine value, and any stored
human correction separately. Extraction comparison scores canonical machine
output before human correction; the correction and post-correction effective
value are still reported for audit. A missing field is not silently filled by a
correction.

The comparator emits field- and cell-level statuses (`correct`, `missing`,
`wrong_value`, `false_positive`, `formatting_only`, `unavailable`) and a root
cause for every mismatch. Uncertain causes are `UNKNOWN`. Groups are identifiers,
parties, financial, product, logistics, payment, and line items. Legacy fields
are separately audited and are not silently added to the canonical success set.

## Local invocation

Use a Page-1-only cached-evidence file with a matching source PDF hash. The
runner renders only physical page 1, does not invoke OCR, skips persisted
corrections for machine extraction, and writes both output files only under
`local_data/`:

```powershell
.\.venv\Scripts\python.exe scripts/run_page1_ground_truth_baseline.py `
  --source <local-source.pdf> `
  --cached-ocr <local-page1-cache.json> `
  --ground-truth <local-verified-dossier-ground-truth.json> `
  --prediction-output local_data/<case>/page1_machine_prediction.json `
  --report-output local_data/<case>/page1_comparison_report.json
```

For an already-created Page-1 prediction, run only the generic comparator:

```powershell
.\.venv\Scripts\python.exe scripts/compare_page1_ground_truth.py `
  --prediction local_data/<case>/page1_machine_prediction.json `
  --ground-truth <local-verified-dossier-ground-truth.json> `
  --output local_data/<case>/page1_comparison_report.json
```

The comparator rejects unverified GT and non-Page-1/non-producer predictions.
Reports are scoped to the named single document. They are descriptive baselines,
not estimates of generalization or accuracy on other invoices.
