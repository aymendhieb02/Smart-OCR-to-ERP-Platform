# UI localization

## Architecture

The review frontend remains plain HTML, CSS, and JavaScript. `app/static/strings.js` is loaded before `app/static/app.js` and exposes `window.AppI18n`:

- `t(key, params)` returns plain translated text and performs named interpolation.
- `applyTranslations(root)` updates explicitly marked static elements.
- `setLocale(locale)` selects a supported locale, updates `<html lang>`, and reapplies static translations.
- `getLocale()` returns the resolved active locale.

French (`fr`) is the default locale. English (`en`) is the fallback and remains complete. An unknown locale resolves to English. A missing key falls back to English; if it is missing from both dictionaries, the helper logs a warning and returns `[missing:key]` instead of blank or `undefined` text.

The locale is configured in static markup:

```html
<html lang="fr" data-ui-locale="fr">
```

There is no visible language selector, backend configuration endpoint, environment-variable bridge, framework, bundler, or npm dependency.

## Translation keys

Keys describe presentation concepts, for example:

```text
status.needs_review
fields.supplier_name
line_items.unit_price
review.saved
```

Static HTML uses:

```html
<span data-i18n="status.needs_review">Needs review</span>
<input data-i18n-placeholder="search.placeholder">
<button data-i18n-title="line_items.total_title">
<section data-i18n-aria-label="camera.dialog_aria">
```

Dynamic JavaScript uses `t()`:

```javascript
t("review.saved", { count, status })
```

Do not assemble translated sentence fragments. Add the complete sentence to both dictionaries so French word order can differ from English.

Translation values are plain text. When a translated value is placed inside an existing HTML template, it is passed through the existing `escapeHtml()` or `escapeAttribute()` helper. Interpolated filenames, labels, and other dynamic values are never inserted as raw HTML.

## Business state and presentation

Internal values remain unchanged. For example, `needs_review` remains the status code while the UI displays `À vérifier`. Field names such as `supplier_name` remain API/data keys while `fields.supplier_name` displays `Fournisseur`.

Readiness CSS state is derived from stable values (`readiness.ready` and validation codes), never from translated labels such as `ERP Ready` or `Prêt pour l’export ERP`.

## Terminology

| Concept | French presentation |
|---|---|
| Invoice | Facture |
| Supplier | Fournisseur |
| Seller | Vendeur |
| Customer | Client |
| Buyer | Acheteur |
| Consignee | Destinataire |
| Document type | Type de document |
| Review | Vérification |
| Human Review | Vérification humaine |
| Needs Review | À vérifier |
| Validated | Validé |
| Invalid | Non valide |
| Rejected | Rejeté |
| Corrected | Corrigé |
| Confidence | Indice de confiance |
| Low confidence | Confiance faible |
| Evidence | Élément justificatif |
| Extracted value | Valeur extraite |
| Corrected value | Valeur corrigée |
| Line item | Ligne de facture |
| Quantity | Quantité |
| Unit price | Prix unitaire |
| Total excl. tax | Total HT |
| VAT | TVA |
| Total incl. tax | Total TTC |
| Tax rate | Taux de TVA |
| Financial Validation | Contrôle financier |
| Duplicate | Doublon |
| ERP readiness | Statut d’export ERP |
| ERP Ready | Prêt pour l’export ERP |

The safety statement is translated as: “L’indice de confiance ne mesure pas l’exactitude.”

## Content that must not be translated

Never translate or mutate:

- OCR text, including French accents, Arabic Unicode, or mixed-language text;
- supplier, seller, customer, buyer, and consignee names;
- addresses, invoice descriptions, invoice/reference numbers, tax IDs, currencies, amounts, and dates;
- API routes, HTTP methods, MIME types, CSS classes, selectors, event names, local storage keys, filenames, JSON keys, schema fields, enum/status codes, and document-family identifiers;
- raw JSON and diagnostic payloads.

Backend free-form messages are not localized in this layer. Validation errors/warnings, exception details, review-assistant prose, candidate rejection reasons, dynamic backend table labels, and similar natural-language server content can therefore appear in English inside the otherwise French interface. A future backend error/message-code project should normalize these values before presentation.

## Adding a key or locale

1. Add the same key to `en` and `fr` in `app/static/strings.js`.
2. Preserve the current English wording where it represents existing behavior.
3. Use a complete named-interpolation sentence when values are dynamic.
4. Mark static HTML with the appropriate `data-i18n*` attribute or call `t()` from dynamic JavaScript.
5. Run the localization tests and inspect both locales.

A future locale can be added as another dictionary. Arabic source documents are already supported as Unicode document content, but Arabic UI localization is not implemented. Arabic UI requires translations, `lang="ar"`, `dir="rtl"`, layout and component-alignment review, directional-icon review, and typography testing; it is not merely another dictionary entry.

## Verification

Run:

```powershell
node --check app\static\strings.js
node --check app\static\app.js
.\.venv\Scripts\python.exe -m pytest tests\test_ui_localization.py -q
```

`tests/test_ui_localization.py` verifies complete English/French key parity, French default, English and unknown-locale fallback, missing-key behavior, interpolation, static text/placeholder/title/ARIA translation, `<html lang>`, Unicode preservation, OCR/source-data boundaries, and locale-independent business-state logic.

Its pragmatic hardcoded-copy guardrail scans known user-interface sinks (`textContent`, `innerHTML`, and `showError`). It intentionally does not scan every JavaScript literal, avoiding false positives for technical strings such as paths, methods, selectors, classes, and enum codes.

Manual verification should cover the landing page and a processed demo at normal and narrow widths, especially tabs, buttons, filters, status cards, validation text, ERP instructions, tables, and line-item headings. French text currently fits the existing responsive layout without a CSS change.

## Rollback

To return the display to English without removing localization, set:

```html
<html lang="en" data-ui-locale="en">
```

For a complete code rollback, revert the UI-localization commits in reverse order. No API, schema, database, OCR, extraction, validation, or ERP migration is required.
