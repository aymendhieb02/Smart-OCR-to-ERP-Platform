# INV 01 controlled ground-truth workflow

This workflow freezes the unmodified dossier pipeline at commit `7fe5339fbfce841cf13046e81d16e30fe58e8412` before extraction or classification changes. It does not claim accuracy until a human verifies every target against the original page images.

## Artifacts

- `local_data/dossier_ground_truth/inv01/machine_output.json` is a private snapshot of `process_dossier_file()` output. It must never be manually repaired.
- `local_data/dossier_ground_truth/inv01/ground_truth_review_template.json` is a private worksheet. Machine values are contextual only; all verified values start as `null`.
- `ground_truth_verified.json` must be created by a human reviewer. Tooling never creates or marks this file verified automatically.

All generated artifacts contain sensitive client data and must remain local in the ignored `local_data/` directory. Never commit, force-add, or publish them. The legacy `dataset/dossier_ground_truth/` path is also ignored to prevent accidental staging. Only generators, comparator tooling, synthetic tests, and documentation belong in Git. An explicit output directory must also remain private. JSON formatting is not anonymization.

The template preserves OCR evidence as UTF-8 with physical page number, bounding box, confidence, and source. French accents and Arabic text must remain unchanged. Do not transliterate or reverse Arabic text.

## Generate a fresh controlled snapshot

From the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\export_inv01_ground_truth.py `
  --source "C:\private\synthetic-example.pdf" `
  --output-dir local_data\dossier_ground_truth\inv01
```

Regeneration is intentional only when establishing a new named baseline. It updates `generated_at` and may reflect a different commit, OCR profile, model, or pipeline output.

## Human verification

1. Copy `ground_truth_review_template.json` to `ground_truth_verified.json`.
2. Compare every target with the original PDF page image, including targets whose machine value is `null`.
3. Enter each `verified_value`; do not accept a machine value without viewing the source. For a mismatch, optionally set `error_classification` to `OCR_TRANSCRIPTION_ERROR`, `SEMANTIC_EXTRACTION_ERROR`, or another supported taxonomy value only when the cause is established.
4. Verify line items by replacing `verified_rows`; rows may be corrected, removed, or added.
5. Verify each cross-document relation independently.
6. Set individual verification statuses as appropriate.
7. Only after the entire file is reviewed, set `label_status` to `verified`, `human_verified` to `true`, and record `verified_by` and `verified_at`.

The source image is authoritative. Cross-document agreement must not overwrite source evidence.

## Compare

```powershell
.\.venv\Scripts\python.exe scripts\compare_dossier_ground_truth.py `
  --prediction local_data\dossier_ground_truth\inv01\machine_output.json `
  --ground-truth local_data\dossier_ground_truth\inv01\ground_truth_verified.json
```

The comparator refuses draft or non-human-verified labels. Results remain separated into document, identifier, party, financial, table, customs, and relation sections. A mismatch cause is `UNKNOWN` unless it can be established independently; value differences are not automatically labelled OCR errors.
