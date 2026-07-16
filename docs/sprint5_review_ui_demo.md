# Sprint 5 Review UI Demo

This demo shows the human-review workflow added in Sprint 5. It is designed for jury presentation and for practical testing after OCR extraction.

## Scenario 1: Table-heavy invoice

1. Start the API with `python run.py`.
2. Open `http://127.0.0.1:8000/`.
3. Upload a table-heavy invoice.
4. Confirm that the real invoice preview is visible in the left panel.
5. Enable `OCR boxes`, `Layout blocks`, `Field boxes`, and `Line rows`.
6. Open the `Product Lines` tab.
7. Confirm validated rows and `needs_review` rows are visually different.
8. Click a line row overlay on the preview and verify the selected-region panel shows its text, bbox, page, confidence, and source.

Expected result: table rows are inspectable and editable without being silently exported to ERP when they need review.

## Scenario 2: Missing total TTC

1. Upload an invoice where `amount_ttc` is missing or withheld.
2. Check the `ERP readiness` panel.
3. Confirm export is disabled and the missing total is listed as a blocker.
4. Edit `amount_ttc` in the field review section or select a candidate from the preview.
5. Click `Save corrections`.
6. Open `Financial Checks` and confirm the subtotal/tax/total check is rerun.
7. Check whether readiness changes from `Needs Review` to `ERP Ready`.

Expected result: the original extracted value remains preserved, the corrected value is marked as human-reviewed, and ERP export is enabled only after blockers are cleared.

## Scenario 3: Supplier/customer uncertainty

1. Upload an invoice where supplier and customer candidates conflict.
2. Open `Suggestions`.
3. Review field candidates with score, source, evidence text, page, and bbox.
4. Select the correct candidate or manually edit the field.
5. Click `Save corrections`.
6. Confirm the corrected supplier/customer is reflected in `ERP JSON`.

Expected result: candidate selection becomes a human correction and triggers business revalidation.

## Scenario 4: Duplicate and risk indicators

1. Open the `Duplicate / Risk` tab after processing.
2. Review duplicate evidence if present.
3. Review automated risk indicators.
4. Confirm the disclaimer is visible:

`These are automated risk indicators, not a fraud determination.`

Expected result: the UI presents warnings as indicators only, never as confirmed fraud.

## Manual Verification Checklist

- The real invoice preview is visible.
- Multi-page navigation works when the document has more than one page.
- Zoom, fit width, and reset zoom work.
- Overlays stay aligned after zooming.
- Clicking an OCR/layout/field/row box updates selected-region details.
- Product rows are editable.
- Field values are editable and resettable through correction flow.
- Correction suggestions are not applied automatically.
- `Save corrections` calls `POST /review/validate-corrections`.
- Financial checks are readable without opening raw JSON.
- ERP export is disabled for `Needs Review` and `Rejected`.
- ERP export is enabled only for `ERP Ready`.

## Current Limitations

- Corrections are stored through the project correction store, but this sprint does not add user accounts or long-lived review sessions.
- The UI uses the existing static frontend, so tests are lightweight static and API checks rather than a browser automation suite.
- Overlay quality depends on bbox quality returned by OCR and extraction services.
