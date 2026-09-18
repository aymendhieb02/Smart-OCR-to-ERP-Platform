(function (global) {
  "use strict";

  const DEFAULT_LOCALE = "fr";
  const FALLBACK_LOCALE = "en";
  const dictionaries = {
    en: {
      "app.document_title": "Smart OCR-to-ERP Review Workspace",
      "app.product_name": "Smart OCR-to-ERP Platform",
      "app.heading": "Invoice review workspace",
      "app.subtitle": "Upload an invoice, review the visual evidence, correct uncertain values, and export only validated ERP JSON.",
      "api.label": "API", "api.checking": "Checking...", "api.online": "Online", "api.unknown": "Unknown", "api.offline": "Offline",
      "summary.project_aria": "Project summary", "summary.pipeline_label": "Pipeline coverage", "summary.pipeline_title": "OCR, layout, tables, and validation",
      "summary.pipeline_description": "A single workspace links document preview, evidence overlays, editable fields, product rows, financial checks, and ERP readiness.",
      "summary.safety_label": "Safety rule", "summary.safety_title": "Safe ERP export",
      "summary.safety_description": "Missing, conflicting, or low-confidence values stay blocked until a reviewer confirms or corrects them.",
      "summary.benchmark_label": "Benchmark note", "summary.confidence_title": "Confidence is evidence, not truth",
      "summary.confidence_warning": "Confidence is not accuracy", "summary.confidence_description": "The confidence index describes extraction evidence. Real accuracy still requires verified ground truth labels.",
      "upload.choose": "Choose invoice or document", "upload.formats": "PDF, scanned PDF, JPG, PNG, TIFF, or BMP", "upload.process": "Process document",
      "upload.no_file": "No file selected", "upload.choose_first": "Choose a document first.",
      "camera.open": "Capture photo", "camera.dialog_aria": "Camera capture dialog", "camera.title": "Capture document",
      "camera.description": "Take a photo and process it through the same review workflow.", "camera.close": "Close", "camera.capture": "Take picture",
      "camera.unavailable": "Camera is not available in this browser. Try HTTPS or localhost.", "camera.open_failed": "Camera could not be opened: {message}",
      "camera.loading": "Camera is still loading. Try again in a second.", "camera.capture_failed": "Could not capture the image.",
      "camera.captured_file": "{filename} (camera capture)",
      "demo.aria": "Demo documents", "demo.label": "Demo mode", "demo.title": "Load a prepared document",
      "demo.description": "These samples run through the same extraction pipeline and are useful for demos or quick regression checks.",
      "demo.good_aria": "Load good invoice", "demo.good": "Clean invoice", "demo.review_aria": "Load review invoice", "demo.review": "Needs-review invoice",
      "demo.noisy_aria": "Load noisy document", "demo.noisy": "Noisy document", "demo.loading": "Loading demo: {label}. Running the normal extraction pipeline...",
      "demo.selected": "{label} (demo document)", "demo.failed": "Demo processing failed.",
      "demo.failed_help": "{message} If this is the first run, confirm OCR dependencies are installed.",
      "processing.initial": "Reading document, extracting fields, and validating ERP readiness...",
      "processing.document": "Reading document, detecting layout, extracting fields, and validating ERP readiness...",
      "processing.default": "Running OCR, candidate extraction, and validation...", "processing.failed": "Document processing failed.",
      "common.previous": "Previous", "common.next": "Next", "common.copy": "Copy", "common.copied": "Copied", "common.select": "Select",
      "common.warning": "Warning", "common.error": "Error", "common.reason": "Reason", "common.action": "Action", "common.status": "Status",
      "common.actions": "Actions", "common.restore": "Restore", "common.delete": "Delete", "common.ignore": "Ignore", "common.accept": "Accept",
      "common.reject": "Reject", "common.value": "Value", "common.source": "Source", "common.evidence": "Evidence", "common.yes": "yes", "common.no": "no",
      "summary.validation": "Validation", "summary.document_type": "Document type", "summary.confidence": "Composite Confidence Index", "erp.readiness": "ERP readiness",
      "status.guide_aria": "Status guide", "status.validated": "Validated", "status.needs_review": "Needs review", "status.invalid": "Invalid", "status.rejected": "Rejected",
      "status.corrected": "Corrected", "status.confirmed": "Confirmed", "status.not_extracted": "Not extracted.", "status.not_confirmed": "Not confirmed",
      "status.validated_help": "Required values are present and business checks pass.", "status.needs_review_help": "Some values need confirmation before ERP export.",
      "status.invalid_help": "A blocking issue prevents ERP export.", "status.corrected_help": "A reviewer changed a value; validation refreshes automatically.",
      "status.validated_decorated": "[OK] Validated", "status.needs_review_decorated": "[!] Needs review", "status.invalid_decorated": "[X] Invalid",
      "status.corrected_decorated": "[EDIT] Manually corrected", "status.low_confidence": "Low confidence", "status.missing": "Missing", "status.unmapped": "Unmapped",
      "status.manually_corrected": "Manually corrected", "status.default_help": "Review the extracted evidence before exporting to ERP.",
      "review.visual_title": "Visual evidence review", "review.page_controls_aria": "Preview page controls", "review.zoom_controls_aria": "Preview zoom controls",
      "review.page_empty": "Page - / -", "review.page": "Page {current} / {total}", "review.fit_width": "Fit width", "review.preview_empty": "Upload a document to view the page preview and evidence overlays.",
      "review.structured_title": "Structured data review", "review.structured_help": "Review every extracted value separately from the final ERP export decision.",
      "review.save_recheck": "Save & recheck", "review.process_before_save": "Process a document before saving review changes.",
      "review.saving": "Saving review changes and refreshing ERP validation...", "review.revalidation_failed": "Could not revalidate the corrected document.",
      "review.saved": "Saved {count} review change(s). ERP status: {status}",
      "overlay.ocr_boxes": "OCR boxes", "overlay.layout_blocks": "Layout blocks", "overlay.field_boxes": "Field boxes", "overlay.line_rows": "Line rows",
      "overlay.confidence_labels": "Confidence labels", "overlay.legend": "Overlay legend", "overlay.products": "Products", "overlay.totals": "Totals",
      "overlay.line_row": "Line row", "overlay.field": "Field", "overlay.diagnostics": "Overlay diagnostics",
      "party.supplier": "Supplier", "party.seller": "Seller", "party.customer": "Customer", "party.buyer": "Buyer", "party.consignee": "Consignee",
      "validation.summary": "Validation summary", "validation.waiting": "Waiting for document", "validation.empty_help": "Process a document to see what passed, what failed, and what needs review.",
      "validation.issues": "Validation issues", "validation.no_issues": "No validation issues detected.", "validation.no_summary": "No validation summary returned.",
      "validation.default_action": "Review missing, low-confidence, or inconsistent fields before ERP export.", "validation.explanation": "Validation explanation",
      "validation.errors": "Errors", "validation.warnings": "Warnings", "validation.rechecking": "Rechecking...",
      "validation.queued": "Automatic validation is queued for the latest edit.",
      "region.selected": "Selected region", "region.none": "No region selected", "region.empty_help": "Click any OCR, layout, field, or row box to inspect the evidence.",
      "region.empty_help_short": "Click any OCR, layout, or field box to inspect it.", "region.selected_type": "Selected {type}", "region.default": "Region",
      "region.text_value": "Text/value", "region.confidence": "Confidence", "region.page": "Page", "region.source": "Source", "region.bbox": "Original bbox",
      "region.fields": "Fields", "region.use_text": "Use text", "region.advanced": "Advanced evidence",
      "erp.empty_help": "Process a document to see whether ERP export is allowed.", "erp.export_validated": "Export validated ERP JSON", "erp.export": "Export ERP JSON",
      "erp.ready": "ERP Ready", "erp.field_missing": "{field} is missing", "erp.next_ready": "Next action: export the validated ERP JSON or continue reviewing evidence.",
      "erp.next_fix": "Next action: fix {issue}{more}, then save corrections.", "erp.more_issues": " and {count} more issue(s)",
      "erp.next_review": "Next action: review low-confidence fields, line items, and financial checks before export.",
      "erp.blockers_cleared": "All ERP blockers are cleared.", "erp.export_ready_title": "ERP export is ready", "erp.copied": "Validated ERP JSON copied.",
      "filter.label": "Filter", "filter.all": "All", "filter.erp_only": "ERP only", "filter.validated": "Validated", "filter.needs_review": "Needs review",
      "filter.missing": "Missing", "filter.low_confidence": "Low confidence", "filter.unmapped": "Unmapped", "filter.corrected": "Manually corrected", "filter.no_rows": "No rows match the current filter.",
      "tabs.visual": "Visual Review", "tabs.erp_fields": "ERP Fields", "tabs.all_values": "All Extracted Values", "tabs.line_items": "Line Items",
      "tabs.financial": "Financial Validation", "tabs.suggestions": "Review Suggestions", "tabs.risk": "Duplicate & Risk", "tabs.diagnostics": "Diagnostics",
      "tabs.advanced": "Advanced", "tabs.ocr_blocks": "OCR Blocks", "tabs.layout_blocks": "Layout Blocks", "tabs.unmapped": "Unmapped Text",
      "tabs.validation_report": "Validation Report", "tabs.erp_json": "ERP JSON", "tabs.full_json": "Full JSON", "tabs.ocr_text": "OCR text",
      "tabs.candidate_debug": "Candidate debug", "tabs.api_response": "API response",
      "fields.editable_title": "Editable fields", "fields.supplier_name": "Supplier", "fields.supplier_address": "Supplier address", "fields.supplier_tax_id": "Supplier tax ID",
      "fields.supplier_phone": "Supplier phone", "fields.supplier_email": "Supplier email", "fields.supplier_website": "Supplier website", "fields.supplier_bank_iban": "Supplier IBAN",
      "fields.supplier_bank_rib": "Supplier RIB", "fields.supplier_bank_swift": "Supplier SWIFT", "fields.customer_name": "Customer", "fields.customer_address": "Customer address",
      "fields.customer_tax_id": "Customer tax ID", "fields.customer_phone": "Customer phone", "fields.customer_email": "Customer email", "fields.invoice_number": "Invoice number",
      "fields.invoice_date": "Invoice date", "fields.due_date": "Due date", "fields.currency": "Currency", "fields.amount_ht": "Total HT", "fields.tva_amount": "VAT",
      "fields.amount_ttc": "Total TTC", "fields.tax_rate": "Tax rate", "fields.purchase_order_number": "Purchase order number",
      "candidate.warning": "This value is mathematically inconsistent with related amount fields.", "candidate.use_expected": "Use {value} instead", "candidate.primary": "Candidate:",
      "candidate.alternative": "Alternative:", "candidate.unknown_source": "candidate", "candidate.heading": "Field candidates", "candidate.use": "Use candidate",
      "candidate.rejected": "Rejected candidate for {field}.", "candidate.selected": "Selected candidate for {field}.",
      "confidence.field_title": "Field confidence", "confidence.none": "No field confidence data returned",
      "line_items.editable_title": "Editable line items", "line_items.help": "Review or correct extracted line items. Validation refreshes automatically after edits.",
      "line_items.add": "Add line", "line_items.add_row": "Add row", "line_items.save": "Save table & recheck", "line_items.description": "Description",
      "line_items.quantity": "Quantity", "line_items.unit": "Unit", "line_items.unit_price": "Unit price", "line_items.total_ht": "Total HT",
      "line_items.tax": "Tax %", "line_items.total_ttc": "Total TTC", "line_items.none": "No line items were extracted. Add a row manually if needed.",
      "line_items.lines_total": "Lines total", "line_items.total_title": "Sum of visible line item Total TTC values.",
      "line_items.delete_missing": "No matching line item is available to delete.", "line_items.restore_missing": "No original row is available to restore.",
      "line_items.restored": "Restored line {number} to the original extraction.", "line_items.number": "Line {number}",
      "dynamic.process_first": "Process a document to see dynamic extraction tables.", "dynamic.visual_help": "Use the visual review panel above to inspect OCR boxes, layout blocks, and field boxes on the invoice preview.",
      "dynamic.no_data": "No dynamic table data returned for this view.", "dynamic.summary": "{count} rows - confidence {confidence}", "dynamic.row": "Row",
      "checks.passed": "Passed", "checks.conflict": "Conflict", "checks.warning": "Warning", "checks.no_action": "No action needed.",
      "checks.correct_conflict": "Compare the document totals and correct the conflicting amount before export.", "checks.enter_missing": "Find or enter the missing amount, then save corrections.",
      "checks.expected": "Expected", "checks.extracted": "Extracted", "checks.difference": "Difference", "checks.tolerance": "Tolerance",
      "checks.incomplete": "Financial checks need more complete totals before they can run.",
      "assistant.title": "Review Assistant", "assistant.default_summary": "Review assistant generated guidance.", "assistant.erp_impact": "ERP impact: {value}",
      "assistant.advisory": "Suggestions are advisory only.", "assistant.issue": "Review issue", "assistant.problem": "Problem", "assistant.explanation": "Explanation",
      "assistant.suggested": "Suggested correction", "assistant.no_issues": "Review Assistant found no extra review issues.",
      "suggestion.title": "Suggestion", "suggestion.original": "Original", "suggestion.proposed": "Proposed", "suggestion.reason": "Reason",
      "suggestion.none": "No correction suggestions returned.", "suggestion.rejected": "Suggestion rejected for this review session.", "suggestion.accepted": "Accepted suggestion for {field}.",
      "risk.duplicate": "Duplicate check", "risk.possible": "Possible duplicate: {value}", "risk.indicators": "Automated risk indicators",
      "risk.none": "No duplicate or risk indicators returned.", "risk.disclaimer": "These are automated risk indicators, not a fraud determination.",
      "preview.none": "No preview available.", "preview.open": "Open preview", "preview.alt": "Document preview", "preview.load_failed": "Preview image could not be loaded from the API response.",
      "field.consistency_applied": "Applied consistency suggestion for {field}. Save to recheck.",
      "render.section_error": "{section} render error: {message}"
    }
  };

  let activeLocale = FALLBACK_LOCALE;

  function resolveLocale(locale) {
    return Object.prototype.hasOwnProperty.call(dictionaries, locale) ? locale : FALLBACK_LOCALE;
  }

  function interpolate(value, params) {
    return String(value).replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) =>
      Object.prototype.hasOwnProperty.call(params || {}, name) ? String(params[name]) : match
    );
  }

  function t(key, params) {
    const localized = dictionaries[activeLocale]?.[key];
    const fallback = dictionaries[FALLBACK_LOCALE]?.[key];
    const value = localized ?? fallback;
    if (value === undefined) {
      console.warn(`Missing translation: ${key}`);
      return `[missing:${key}]`;
    }
    return interpolate(value, params);
  }

  function applyTranslations(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach((node) => { node.placeholder = t(node.dataset.i18nPlaceholder); });
    scope.querySelectorAll("[data-i18n-title]").forEach((node) => { node.title = t(node.dataset.i18nTitle); });
    scope.querySelectorAll("[data-i18n-aria-label]").forEach((node) => { node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel)); });
    if (document?.title) document.title = t("app.document_title");
  }

  function setLocale(locale, options) {
    activeLocale = resolveLocale(locale);
    if (global.document?.documentElement) global.document.documentElement.lang = activeLocale;
    if (options?.apply !== false && global.document) applyTranslations(global.document);
    return activeLocale;
  }

  function getLocale() { return activeLocale; }

  global.AppI18n = Object.freeze({ t, applyTranslations, setLocale, getLocale, dictionaries, DEFAULT_LOCALE, FALLBACK_LOCALE });
  if (global.document) {
    const requested = global.document.documentElement?.dataset?.uiLocale || DEFAULT_LOCALE;
    setLocale(requested, { apply: false });
    if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", () => applyTranslations());
    else applyTranslations();
  }
})(typeof window !== "undefined" ? window : globalThis);
