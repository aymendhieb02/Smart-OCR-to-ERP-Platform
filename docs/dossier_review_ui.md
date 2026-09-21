# Dossier-aware review workflow

The review workspace processes uploaded client files through `POST /process-dossier`. The endpoint wraps the existing `process_dossier_file()` pipeline: OCR runs once, deterministic segmentation assigns physical pages to logical documents, and the existing `ProcessInvoiceResponse` remains isolated inside each logical-document wrapper. `POST /process-invoice` remains unchanged for integrations, demos, and single-document callers.

## Response and identity

The dossier response contains an upload-instance `dossier_id`, the full physical-page preview, page classifications, a validation summary, and `logical_documents`. Each logical document has an identity of the form `<dossier_id>:<group_id>`, stable document type/family codes, physical page membership, and its own extraction response. The source filename is metadata, never the dossier-mode correction identity.

The full preview is generated once before logical splitting. Preview page numbers, OCR blocks, layout boxes, field boxes, and line rows use original physical page numbers and original-page coordinates.

## Frontend state and navigation

The frontend keeps separate `selectedLogicalDocumentIndex` and `selectedPageWithinLogicalDocument` values. Selecting a document resets its internal page selection; previous/next page controls never select another logical document. `lastResponse` always refers to the selected logical document, allowing established field, validation, and evidence renderers to remain document-scoped.

Document tabs are keyboard accessible and use stable type/family codes for behavior. French is the default locale and English remains the fallback. At narrow widths the tab list scrolls horizontally.

## Presentation and safety

`DOCUMENT_PRESENTATION` centralizes field groups and capabilities. Producer and RUSPINA invoices keep separate fields, line items, validation, corrections, and ERP payloads. Customs and unknown documents allow preview, OCR, and evidence review but disable invoice correction, invoice revalidation, line editing, and invoice ERP export.

Correction submissions for invoice-like dossier documents use `logical_document_id` as `document_id`, while retaining the source filename, physical page, bounding box, and original evidence. The existing correction store already supports these attributes.

The dossier summary is presentation-only and counts the existing validation states. The relationships section compares only structured runtime fields. It does not parse OCR text, consume draft ground truth, or approve ERP export. When the required structured relationship fields are absent, it displays an unavailable state.

## Known limitations

- The current structured extraction schema does not provide most customs fields or `referenced_invoice`.
- Customs correction/revalidation needs a future document-type-aware backend workflow.
- ERP export remains per selected invoice-like logical document; there is no combined dossier ERP schema.
- Dossier IDs identify a processing run and are not persistent business dossier identifiers.
