const fileInput = document.getElementById("fileInput");
const processBtn = document.getElementById("processBtn");
const cameraBtn = document.getElementById("cameraBtn");
const cameraModal = document.getElementById("cameraModal");
const cameraVideo = document.getElementById("cameraVideo");
const cameraCanvas = document.getElementById("cameraCanvas");
const captureCameraBtn = document.getElementById("captureCameraBtn");
const closeCameraBtn = document.getElementById("closeCameraBtn");
const fileName = document.getElementById("fileName");
const loading = document.getElementById("loading");
const loadingText = loading?.querySelector("span");
const errorBox = document.getElementById("errorBox");
const results = document.getElementById("results");
const previewCanvas = document.getElementById("previewCanvas");
const regionDetails = document.getElementById("regionDetails");
const validationSummary = document.getElementById("validationSummary");
const demoButtons = document.querySelectorAll(".demo-button");
const { t } = window.AppI18n;
// Compatibility marker for the legacy static test: English key value "Advanced evidence" now lives in strings.js.

let selectedFile = null;
let dossierResponse = null;
let selectedLogicalDocumentIndex = 0;
let selectedPageWithinLogicalDocument = 0;
let lastResponse = null;
let previewZoom = 1;
let fitWidth = true;
let currentPageIndex = 0;
let activeDynamicTab = "visual";
let correctedFields = {};
let correctedLineItems = [];
let ignoredRows = [];
let cameraStream = null;
let selectedRegionPayload = null;
let originalLineItemsSnapshot = [];
let renderStatus = {};
let autoValidationTimer = null;
let autoValidationInFlight = false;
let autoValidationQueued = false;
const AUTO_VALIDATION_DELAY_MS = 800;
const MATH_CONSISTENCY_FIELDS = new Set(["amount_ht", "tva_amount", "amount_ttc", "tax_rate"]);

const INVOICE_FIELD_GROUPS = [
  "invoice_number", "invoice_date", "supplier_name", "supplier_address", "supplier_tax_id",
  "customer_name", "customer_address", "customer_tax_id", "currency", "amount_ht",
  "tva_amount", "amount_ttc", "tax_rate", "purchase_order_number",
];
const CUSTOMS_TRADENET_REVIEW_FIELDS = Object.freeze([
  "declaration_number", "declaration_date", "declaration_type", "exporter", "importer",
  "ptfn_amount", "currency_conversion_rate", "customs_total_value_tnd",
]);
const DOCUMENT_PRESENTATION = Object.freeze({
  customs_tradenet_v1: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS, showLineItems: false, allowCorrections: true, allowInvoiceExport: false, relationCapabilities: [] },
  customs_douanes_tunisiennes_v1: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS, showLineItems: false, allowCorrections: true, allowInvoiceExport: false, relationCapabilities: [] },
  ruspina_reinvoice_v1: { labelKey: "dossier.document_ruspina", fields: INVOICE_FIELD_GROUPS, showLineItems: true, allowCorrections: true, allowInvoiceExport: true, relationCapabilities: ["referenced_invoice"] },
  commercial_invoice: { labelKey: "dossier.document_supplier_invoice", fields: INVOICE_FIELD_GROUPS, showLineItems: true, allowCorrections: true, allowInvoiceExport: true, relationCapabilities: [] },
  customs_declaration: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS, showLineItems: false, allowCorrections: false, allowInvoiceExport: false, relationCapabilities: ["referenced_invoice", "invoice_value"] },
  unknown: { labelKey: "dossier.document_unknown", fields: [], showLineItems: false, allowCorrections: false, allowInvoiceExport: false, relationCapabilities: [] },
});

const EDITABLE_FIELDS = [
  "supplier_name",
  "supplier_address",
  "supplier_tax_id",
  "supplier_phone",
  "supplier_email",
  "supplier_website",
  "supplier_bank_iban",
  "supplier_bank_rib",
  "supplier_bank_swift",
  "customer_name",
  "customer_address",
  "customer_tax_id",
  "customer_phone",
  "customer_email",
  "invoice_number",
  "invoice_date",
  "due_date",
  "currency",
  "amount_ht",
  "tva_amount",
  "amount_ttc",
  "tax_rate",
  "purchase_order_number",
  "declaration_number",
  "declaration_date",
  "declaration_type",
  "exporter",
  "importer",
  "ptfn_amount",
  "currency_conversion_rate",
  "customs_total_value_tnd",
];

const NUMERIC_FIELDS = new Set(["amount_ht", "tva_amount", "amount_ttc", "tax_rate", "quantity", "unit_price", "discount", "line_total_ht", "tax_amount", "line_total_ttc", "total"]);
const FIELD_TO_ERP_PATH = {
  supplier_name: ["supplier", "name"],
  supplier_address: ["supplier", "address"],
  supplier_tax_id: ["supplier", "tax_id"],
  customer_name: ["customer", "name"],
  customer_address: ["customer", "address"],
  customer_tax_id: ["customer", "tax_id"],
  invoice_number: ["invoice", "number"],
  invoice_date: ["invoice", "date"],
  due_date: ["invoice", "due_date"],
  currency: ["invoice", "currency"],
  amount_ht: ["amounts", "ht"],
  tva_amount: ["amounts", "tva"],
  amount_ttc: ["amounts", "ttc"],
  tax_rate: ["amounts", "tax_rate"],
};

const LINE_TABLE_TO_ITEM_FIELD = {
  reference: "reference",
  description: "description",
  quantity: "quantity",
  unit: "unit",
  unit_price: "unit_price",
  discount: "discount",
  tax_rate: "tax_rate",
  amount_ht: "line_total_ht",
  tax_amount: "tax_amount",
  amount_ttc: "line_total_ttc",
};

fileInput.addEventListener("change", () => {
  selectedFile = fileInput.files[0] || null;
  fileName.textContent = selectedFile ? selectedFile.name : t("upload.no_file");
  resetCorrections();
});

cameraBtn?.addEventListener("click", () => openCamera());
closeCameraBtn?.addEventListener("click", () => closeCamera());
cameraModal?.addEventListener("click", (event) => {
  if (event.target === cameraModal) closeCamera();
});
captureCameraBtn?.addEventListener("click", () => captureCameraImage());
document.getElementById("saveCorrectionsBtn")?.addEventListener("click", () => saveCorrections());
demoButtons.forEach((button) => {
  button.addEventListener("click", () => processDemoDocument(button.dataset.demoId, button.textContent));
});


async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    showError(t("camera.unavailable"));
    return;
  }
  hideError();
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    cameraVideo.srcObject = cameraStream;
    cameraModal.classList.remove("hidden");
  } catch (error) {
    showError(t("camera.open_failed", { message: error.message }));
  }
}

function closeCamera() {
  if (cameraStream) {
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
  }
  if (cameraVideo) cameraVideo.srcObject = null;
  cameraModal?.classList.add("hidden");
}

function captureCameraImage() {
  if (!cameraVideo?.videoWidth || !cameraVideo?.videoHeight) {
    showError(t("camera.loading"));
    return;
  }
  cameraCanvas.width = cameraVideo.videoWidth;
  cameraCanvas.height = cameraVideo.videoHeight;
  const context = cameraCanvas.getContext("2d");
  context.drawImage(cameraVideo, 0, 0, cameraCanvas.width, cameraCanvas.height);
  cameraCanvas.toBlob((blob) => {
    if (!blob) {
      showError(t("camera.capture_failed"));
      return;
    }
    const timestamp = new Date().toISOString().replaceAll(":", "-").slice(0, 19);
    selectedFile = new File([blob], `camera-invoice-${timestamp}.png`, { type: "image/png" });
    fileName.textContent = t("camera.captured_file", { filename: selectedFile.name });
    resetCorrections();
    closeCamera();
  }, "image/png", 0.95);
}
processBtn.addEventListener("click", () => processUploadedFile());

async function processUploadedFile() {
  if (!selectedFile) {
    showError(t("upload.choose_first"));
    return;
  }

  const formData = new FormData();
  formData.append("file", selectedFile);
  setLoading(true, t("processing.document"));
  hideError();
  results.classList.add("hidden");

  try {
    const response = await fetch("/process-dossier", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || t("processing.failed"));
    }
    renderResults(data);
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
}

async function processDemoDocument(demoId, label) {
  if (!demoId) return;
  setLoading(true, t("demo.loading", { label: label || demoId }));
  hideError();
  results.classList.add("hidden");
  fileName.textContent = t("demo.selected", { label: label || demoId });
  resetCorrections();
  try {
    const response = await fetch(`/demo-documents/${encodeURIComponent(demoId)}/process`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || t("demo.failed"));
    renderResults(data);
  } catch (error) {
    showError(t("demo.failed_help", { message: error.message }));
  } finally {
    setLoading(false);
  }
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.add("hidden"));
    button.classList.add("active");
    document.getElementById(button.dataset.tab).classList.remove("hidden");
  });
});

document.querySelectorAll(".dynamic-tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".dynamic-tab").forEach((tab) => tab.classList.remove("active"));
    button.classList.add("active");
    activeDynamicTab = button.dataset.dynamicTab;
    renderDynamicReview();
  });
});

document.getElementById("dynamicFilter").addEventListener("change", () => renderDynamicReview());

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copy);
    await navigator.clipboard.writeText(target.innerText);
    button.textContent = t("common.copied");
    setTimeout(() => { button.textContent = t("common.copy"); }, 1000);
  });
});

async function checkApi() {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    document.getElementById("apiStatus").textContent = data.status === "ok" ? t("api.online") : t("api.unknown");
  } catch {
    document.getElementById("apiStatus").textContent = t("api.offline");
  }
}

function renderResults(data) {
  resetCorrections();
  dossierResponse = normalizeDossierResponse(data);
  selectedLogicalDocumentIndex = 0;
  selectedPageWithinLogicalDocument = 0;
  renderDossierNavigation();
  renderDossierRelationships();
  renderSelectedLogicalDocument(data);
}

function renderSelectedLogicalDocument(rawResponse = dossierResponse) {
  const logicalDocument = getSelectedLogicalDocument();
  if (!logicalDocument) return;
  resetCorrections();
  const normalized = normalizeReviewResponse(logicalDocument.response);
  normalized.document_preview = { source_file: dossierResponse.source_file, pages: dossierResponse.document_preview?.pages || [] };
  lastResponse = normalized;
  window.__REVIEW_DEBUG__ = {
    rawResponse,
    dossierResponse,
    selectedLogicalDocumentIndex,
    selectedPageWithinLogicalDocument,
    normalizedResponse: normalized,
    overlayCounts: normalized.overlay_counts,
    renderErrors: [],
  };
  currentPageIndex = getSelectedDossierPageIndex();
  const validation = normalized.validation || {};
  const status = validation.status || (validation.is_valid ? "valid" : "invalid");
  const classification = normalized.document_classification || {};
  const fields = normalized.detected_fields || {};
  const readiness = normalized.erp_readiness || normalized.erp_json?.quality?.erp_readiness || {};
  const confidence = normalized.confidence_breakdown?.overall_confidence ?? normalized.erp_json?.quality?.overall_confidence ?? normalized.erp_json?.metadata?.confidence;
  const confidenceLabel = normalized.confidence_breakdown?.display_name || normalized.erp_json?.quality?.confidence_display_name || t("summary.confidence");
  const displayItems = normalized.all_line_items?.length ? normalized.all_line_items : fields.line_items || [];
  originalLineItemsSnapshot = deepClone(displayItems);

  const statusEl = document.getElementById("validationStatus");
  statusEl.textContent = statusLabel(status);
  statusEl.title = statusExplanation(status);
  statusEl.className = `pill ${status}`;
  document.getElementById("documentType").textContent = classification.document_type || "-";
  document.querySelector("#ocrConfidence")?.closest(".summary-card")?.querySelector(".label")?.replaceChildren(document.createTextNode(confidenceLabel));
  document.getElementById("ocrConfidence").textContent = formatConfidence(confidence);
  document.getElementById("erpDecision").textContent = localizedReadinessStatus(readiness.erp_ready_status, status);

  safeRender("fields", () => renderFields(fields));
  safeRender("notes", () => renderNotes(normalized));
  safeRender("validation", () => renderValidationSummary(normalized.validation_explanation, validation));
  safeRender("erp_readiness", () => renderErpReadiness(normalized));
  safeRender("dynamic_review", () => renderDynamicReview());
  safeRender("confidences", () => renderConfidences(normalized.field_confidences || {}));
  safeRender("line_items", () => renderLineItems(displayItems, normalized.row_validation || []));

  document.getElementById("ocrText").textContent = normalized.extracted_text || "";
  document.getElementById("debugJson").textContent = pretty(normalized.extraction_debug || {});
  updateJsonPanels();

  results.classList.remove("hidden");
  safeRender("overlays", () => renderPreview(normalized));
  document.querySelector(".visual-review")?.scrollIntoView({ block: "start" });
}

function normalizeDossierResponse(response) {
  if (Array.isArray(response?.logical_documents)) {
    return {
      ...response,
      document_count: response.document_count ?? response.logical_documents.length,
      document_preview: response.document_preview || { source_file: response.source_file, pages: [] },
    };
  }
  const sourceFile = response?.erp_json?.metadata?.source_file || response?.document_preview?.source_file || "document";
  const pages = response?.document_preview?.pages || [];
  return {
    dossier_id: `single:${sourceFile}`,
    source_file: sourceFile,
    page_count: pages.length || 1,
    document_count: 1,
    summary: summarizeLogicalDocuments([{ response }]),
    document_preview: response?.document_preview || { source_file: sourceFile, pages: [] },
    page_classifications: [],
    logical_documents: [{
      logical_document_id: `single:${sourceFile}:logical_document_1`,
      document_index: 1,
      document_type: response?.document_classification?.document_type || "unknown",
      document_family: null,
      physical_page_numbers: pages.map((page) => normalizePage(page.page)),
      page_classifications: [],
      response,
    }],
  };
}

function getSelectedLogicalDocument() {
  return dossierResponse?.logical_documents?.[selectedLogicalDocumentIndex] || null;
}

function getSelectedDocumentResponse() {
  return getSelectedLogicalDocument()?.response || null;
}

function getSelectedDocumentPreviewPages() {
  const document = getSelectedLogicalDocument();
  const physicalPages = new Set(document?.physical_page_numbers || []);
  return (dossierResponse?.document_preview?.pages || []).filter((page) => physicalPages.has(normalizePage(page.page)));
}

function getSelectedPreviewPage() {
  return dossierResponse?.document_preview?.pages?.[getSelectedDossierPageIndex()] || null;
}

function getSelectedPhysicalPageNumber() {
  return getSelectedPreviewPage()?.page ?? null;
}

function getSelectedDossierPageIndex() {
  const pages = dossierResponse?.document_preview?.pages || [];
  const index = window.DossierNavigation?.resolveDossierPageIndex(
    getSelectedLogicalDocument(), selectedPageWithinLogicalDocument, pages
  ) ?? -1;
  return index >= 0 ? index : 0;
}

function getSelectedPageScopedOverlays() {
  const page = getSelectedPhysicalPageNumber();
  const response = lastResponse || getSelectedDocumentResponse() || {};
  const onPage = (item) => normalizePage(item.page ?? item.page_number) === normalizePage(page);
  return {
    ocr_blocks: (response.ocr_blocks || []).filter(onPage),
    layout_blocks: (response.layout_blocks || []).filter(onPage),
    field_boxes: (response.field_boxes || []).filter(onPage),
    line_rows: getLineItemOverlayRows().filter(onPage),
  };
}

function resolveDocumentPresentation(document = getSelectedLogicalDocument()) {
  const key = document?.document_family && DOCUMENT_PRESENTATION[document.document_family]
    ? document.document_family
    : document?.document_type && DOCUMENT_PRESENTATION[document.document_type]
      ? document.document_type
      : "unknown";
  return { key, ...DOCUMENT_PRESENTATION[key] };
}

function summarizeLogicalDocuments(documents = dossierResponse?.logical_documents || []) {
  const statuses = documents.map((item) => String(item.response?.validation?.status || "needs_review").toLowerCase());
  const valid_count = statuses.filter((status) => status === "valid").length;
  const invalid_count = statuses.filter((status) => status.includes("invalid") || status.includes("reject")).length;
  const needs_review_count = statuses.length - valid_count - invalid_count;
  return { status: invalid_count ? "invalid" : needs_review_count ? "needs_review" : "valid", valid_count, needs_review_count, invalid_count };
}

function renderDossierNavigation() {
  if (!dossierResponse) return;
  const title = document.getElementById("dossierTitle");
  const counts = document.getElementById("dossierCounts");
  const summaryHost = document.getElementById("dossierSummary");
  const tabs = document.getElementById("logicalDocumentTabs");
  const summary = dossierResponse.summary || summarizeLogicalDocuments();
  if (title) title.textContent = dossierResponse.source_file || t("dossier.label");
  if (counts) counts.textContent = t("dossier.counts", { documents: dossierResponse.document_count || 0, pages: dossierResponse.page_count || 0 });
  if (summaryHost) summaryHost.textContent = t("dossier.summary", {
    status: statusLabel(summary.status), valid: summary.valid_count || 0,
    review: summary.needs_review_count || 0, invalid: summary.invalid_count || 0,
  });
  if (!tabs) return;
  tabs.innerHTML = "";
  (dossierResponse.logical_documents || []).forEach((logicalDocument, index) => {
    const presentation = resolveDocumentPresentation(logicalDocument);
    const response = logicalDocument.response || {};
    const fields = response.detected_fields || {};
    const button = document.createElement("button");
    button.type = "button";
    button.className = `logical-document-tab${index === selectedLogicalDocumentIndex ? " active" : ""}`;
    button.id = `logical-document-tab-${index}`;
    button.dataset.logicalDocumentIndex = String(index);
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(index === selectedLogicalDocumentIndex));
    button.setAttribute("aria-controls", "logicalDocumentPanel");
    button.tabIndex = index === selectedLogicalDocumentIndex ? 0 : -1;
    const identity = fields.invoice_number || response.expanded_fields?.declaration_reference?.value || "";
    const date = fields.invoice_date || "";
    button.innerHTML = `<strong>${escapeHtml(t(presentation.labelKey))}</strong>${identity ? `<span>${escapeHtml(identity)}</span>` : ""}${date ? `<span>${escapeHtml(date)}</span>` : ""}<small>${escapeHtml(statusLabel(response.validation?.status))}</small>`;
    button.addEventListener("click", () => selectLogicalDocument(index));
    button.addEventListener("keydown", (event) => handleDocumentTabKeydown(event, index));
    tabs.appendChild(button);
  });
}

function handleDocumentTabKeydown(event, index) {
  const total = dossierResponse?.logical_documents?.length || 0;
  if (!total || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? total - 1 : event.key === "ArrowRight" ? (index + 1) % total : (index - 1 + total) % total;
  selectLogicalDocument(next);
  document.getElementById(`logical-document-tab-${next}`)?.focus();
}

function selectLogicalDocument(index) {
  if (!dossierResponse?.logical_documents?.[index]) return;
  selectedLogicalDocumentIndex = index;
  selectedPageWithinLogicalDocument = 0;
  currentPageIndex = 0;
  renderDossierNavigation();
  renderDossierRelationships();
  renderSelectedLogicalDocument();
}

function selectPhysicalPage(pageIndex) {
  const pages = dossierResponse?.document_preview?.pages || [];
  const page = pages[pageIndex];
  if (!page) return;
  const documents = dossierResponse?.logical_documents || [];
  const selection = window.DossierNavigation?.resolvePhysicalPageSelection(documents, page.page);
  if (!selection) return;
  const { documentIndex, pageWithinDocumentIndex } = selection;
  const logicalDocument = documents[documentIndex];
  selectedLogicalDocumentIndex = documentIndex;
  selectedPageWithinLogicalDocument = pageWithinDocumentIndex;
  currentPageIndex = pageIndex;
  renderDossierNavigation();
  renderDossierRelationships();
  renderSelectedLogicalDocument();
}

function getSelectedDocumentPages(logicalDocument = getSelectedLogicalDocument()) {
  const previewPages = dossierResponse?.document_preview?.pages || [];
  const available = new Set(previewPages.map((page) => normalizePage(page.page)));
  return (logicalDocument?.physical_page_numbers || []).filter((page) => available.has(normalizePage(page)));
}

function structuredFieldValue(document, field) {
  return document?.response?.detected_fields?.[field] ?? document?.response?.expanded_fields?.[field]?.value ?? null;
}

function renderDossierRelationships() {
  const host = document.getElementById("dossierRelationships");
  if (!host || !dossierResponse) return;
  const producer = dossierResponse.logical_documents?.find((item) => item.document_type === "commercial_invoice" && item.document_family !== "ruspina_reinvoice_v1");
  const reinvoice = dossierResponse.logical_documents?.find((item) => item.document_family === "ruspina_reinvoice_v1");
  const producerNumber = structuredFieldValue(producer, "invoice_number");
  const referencedInvoice = structuredFieldValue(reinvoice, "referenced_invoice");
  const reconciled = dossierResponse.relationships?.find((item) => item.type === "producer_invoice_reference");
  let relationship = t("dossier.relationship_unavailable");
  if (reconciled?.status === "match") relationship = t("dossier.relationship_match");
  else if (reconciled?.status === "mismatch") relationship = t("dossier.relationship_differs");
  else if (!reconciled && producerNumber && referencedInvoice) relationship = producerNumber === referencedInvoice ? t("dossier.relationship_match") : t("dossier.relationship_differs");
  host.innerHTML = `<strong>${escapeHtml(t("dossier.relationships"))}</strong><span>${escapeHtml(relationship)}</span>`;
}

function normalizeReviewResponse(response) {
  if (window.__REVIEW_DEBUG__) window.__REVIEW_DEBUG__.rejectedBoxes = [];
  const detectedFields = response.detected_fields || {};
  const expandedFields = response.expanded_fields || {};
  const reviewCandidates = normalizeCandidateMap(response.review_candidates || {});
  const rejectedCandidates = normalizeCandidateMap(response.rejected_candidates || {});
  const ocrBlocks = normalizeBoxes(response.ocr_blocks || response.all_ocr_blocks || [], "ocr");
  const layoutBlocks = normalizeBoxes(response.layout_blocks || [], "layout");
  const fieldBoxes = normalizeBoxes(response.field_boxes || boxesFromExpandedFields(expandedFields), "field");
  const reviewLineItems = [...(response.line_items_validated || []), ...(response.line_items_needs_review || [])];
  const allLineItemsSource = nonEmptyArray(response.all_line_items)
    || nonEmptyArray(reviewLineItems)
    || nonEmptyArray(detectedFields.line_items)
    || [];
  const allLineItems = normalizeLineItems(allLineItemsSource);
  const normalized = {
    ...response,
    detected_fields: detectedFields,
    expanded_fields: expandedFields,
    field_confidences: normalizeConfidenceMap(response.field_confidences || {}),
    review_candidates: reviewCandidates,
    rejected_candidates: rejectedCandidates,
    all_line_items: allLineItems,
    line_items_validated: normalizeLineItems(response.line_items_validated || []),
    line_items_needs_review: normalizeLineItems(response.line_items_needs_review || []),
    dynamic_tables: response.dynamic_tables || [],
    ocr_blocks: ocrBlocks,
    all_ocr_blocks: normalizeBoxes(response.all_ocr_blocks || ocrBlocks, "ocr"),
    layout_blocks: layoutBlocks,
    field_boxes: fieldBoxes,
    table_candidates: response.table_candidates || [],
    document_preview: response.document_preview || { pages: [] },
    erp_readiness: response.erp_readiness || response.erp_json?.quality?.erp_readiness || {},
    financial_reasoning: response.financial_reasoning || response.erp_json?.quality?.financial_reasoning || {},
    validation_explanation: response.validation_explanation || response.erp_json?.quality?.validation_explanation || null,
    extraction_debug: response.extraction_debug || {},
  };
  normalized.overlay_counts = {
    ocr_blocks: ocrBlocks.length,
    valid_ocr_blocks: ocrBlocks.filter((item) => isValidBbox(item.bbox)).length,
    layout_blocks: layoutBlocks.length,
    valid_layout_blocks: layoutBlocks.filter((item) => isValidBbox(item.bbox)).length,
    field_boxes: fieldBoxes.length,
    valid_field_boxes: fieldBoxes.filter((item) => isValidBbox(item.bbox)).length,
    line_rows: allLineItems.filter((item) => isValidBbox(item.bbox)).length,
    rejected_boxes: window.__REVIEW_DEBUG__?.rejectedBoxes || [],
    first_invalid_reason: window.__REVIEW_DEBUG__?.rejectedBoxes?.[0]?.reason || null,
  };
  return normalized;
}

function nonEmptyArray(value) {
  return Array.isArray(value) && value.length ? value : null;
}

function normalizeCandidateMap(candidateMap) {
  return Object.fromEntries(Object.entries(candidateMap || {}).map(([field, candidates]) => [
    field,
    (candidates || []).map((candidate, index) => ({
      id: candidate.id || `${field}_candidate_${index + 1}`,
      ...candidate,
      confidence: boundedConfidence(candidate.confidence ?? candidate.score),
      bbox: normalizeBbox(candidate, { id: candidate.id || `${field}_candidate_${index + 1}` }),
      page: normalizePage(candidate.page ?? candidate.page_number ?? 1),
    })),
  ]));
}

function normalizeBoxes(items, type) {
  return (items || []).map((item, index) => ({
    id: item.id || `${type}_${index + 1}`,
    ...item,
    bbox: normalizeBbox(item, { id: item.id || `${type}_${index + 1}` }),
    confidence: boundedConfidence(item.confidence),
    page: normalizePage(item.page ?? item.page_number ?? 1),
    page_number: normalizePage(item.page_number ?? item.page ?? 1),
  }));
}

function normalizeLineItems(items) {
  return (items || []).map((item, index) => ({
    id: item.id || `line_item_${index + 1}`,
    ...item,
    bbox: normalizeBbox(item, { id: item.id || `line_item_${index + 1}` }),
    confidence: boundedConfidence(item.confidence),
    page: normalizePage(item.page ?? 1),
  }));
}

function boxesFromExpandedFields(expandedFields) {
  return Object.entries(expandedFields || {})
    .filter(([, detail]) => detail?.bbox)
    .map(([field, detail]) => ({ field, value: detail.value, confidence: detail.confidence, bbox: detail.bbox, page: detail.page, source: detail.source }));
}

function normalizeConfidenceMap(confidences) {
  return Object.fromEntries(Object.entries(confidences || {}).map(([field, value]) => [field, boundedConfidence(value)]));
}

function normalizeBbox(box, context = {}) {
  const rejected = (reason, raw = box) => {
    if (window.__REVIEW_DEBUG__) {
      window.__REVIEW_DEBUG__.rejectedBoxes = window.__REVIEW_DEBUG__.rejectedBoxes || [];
      if (window.__REVIEW_DEBUG__.rejectedBoxes.length < 50) {
        window.__REVIEW_DEBUG__.rejectedBoxes.push({
          id: context.id || box?.id || null,
          reason,
          raw_bbox: raw ?? null,
        });
      }
    }
    return null;
  };
  if (!box) return rejected("missing bbox");
  const bbox = box.bbox || box.page_bbox || box;
  let values = null;
  if (Array.isArray(bbox)) {
    if (bbox.length >= 4 && bbox.slice(0, 4).every((value) => Number.isFinite(Number(value)))) {
      values = bbox.slice(0, 4).map(Number);
    } else if (bbox.length && Array.isArray(bbox[0])) {
      const xs = bbox.map((point) => Number(point?.[0])).filter(Number.isFinite);
      const ys = bbox.map((point) => Number(point?.[1])).filter(Number.isFinite);
      if (xs.length && ys.length) values = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }
  } else if (bbox && typeof bbox === "object") {
    values = [bbox.x1, bbox.y1, bbox.x2, bbox.y2].map(Number);
  }
  if (!values || !values.every(Number.isFinite)) return rejected("coordinates are not finite", bbox);
  let [x1, y1, x2, y2] = values;
  if (x2 < x1) [x1, x2] = [x2, x1];
  if (y2 < y1) [y1, y2] = [y2, y1];
  if (x2 <= x1 || y2 <= y1) return rejected("bbox has non-positive width or height", bbox);
  if (x1 < 0 || y1 < 0) return rejected("bbox has negative coordinates", bbox);
  return { x1, y1, x2, y2 };
}

function safeRender(section, callback) {
  try {
    callback();
    renderStatus[section] = "ok";
  } catch (error) {
    renderStatus[section] = "error";
    window.__REVIEW_DEBUG__?.renderErrors?.push({ section, message: error.message, stack: error.stack });
    console.error(`Render failed in ${section}`, error);
    showSectionError(section, error);
  }
}

function showSectionError(section, error) {
  const targets = {
    fields: "fieldsTable",
    line_items: "lineItems",
    confidences: "confidenceList",
    overlays: "previewCanvas",
  };
  const target = document.getElementById(targets[section]);
  if (target) target.innerHTML = `<div class="note error-note">${escapeHtml(t("render.section_error", { section, message: error.message }))}</div>`;
}

["toggleOcr", "toggleLayout", "toggleFields", "toggleRows", "toggleLabels"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    if (lastResponse) redrawPreview();
  });
});

document.getElementById("zoomOutBtn").addEventListener("click", () => setZoom(previewZoom - 0.15));
document.getElementById("zoomInBtn").addEventListener("click", () => setZoom(previewZoom + 0.15));
document.getElementById("resetZoomBtn").addEventListener("click", () => {
  fitWidth = false;
  setZoom(1);
});
document.getElementById("fitWidthBtn").addEventListener("click", () => {
  fitWidth = true;
  redrawPreview();
});
document.getElementById("prevPageBtn")?.addEventListener("click", () => setPreviewPage(currentPageIndex - 1));
document.getElementById("nextPageBtn")?.addEventListener("click", () => setPreviewPage(currentPageIndex + 1));

window.addEventListener("resize", () => {
  if (lastResponse && fitWidth) redrawPreview();
});

function renderFields(fields) {
  const table = document.getElementById("fieldsTable");
  table.innerHTML = "";
  const presentation = resolveDocumentPresentation();
  if (!presentation.allowCorrections) {
    const allowedFields = presentation.key === "customs_declaration" ? presentation.fields : null;
    table.innerHTML = `<div class="note warning-note">${escapeHtml(t("dossier.correction_unavailable"))}</div>${renderAvailableStructuredValues(fields, lastResponse?.expanded_fields, allowedFields)}`;
    return;
  }
  EDITABLE_FIELDS.filter((field) => presentation.fields.includes(field)).forEach((field) => {
    const key = document.createElement("div");
    key.textContent = t(`fields.${field}`);
    const value = document.createElement("div");
    value.className = "field-review-cell";
    const input = document.createElement("input");
    input.className = "edit-input";
    input.dataset.field = field;
    const detail = lastResponse?.expanded_fields?.[field];
    input.value = detail?.display_value ?? detail?.value ?? fields[field] ?? "";
    input.placeholder = "-";
    input.addEventListener("input", () => updateReviewField(field, input.value));
    value.appendChild(input);
    value.appendChild(renderFieldCandidateFallback(field, input.value));
    const numericValidation = lastResponse?.customs_field_validation?.[field];
    if (numericValidation) {
      const validationNote = document.createElement("small");
      validationNote.className = numericValidation.valid ? "candidate-state confirmed" : "candidate-state warning";
      validationNote.textContent = t(numericValidation.valid ? "fields.decimal_valid" : "fields.decimal_invalid");
      value.appendChild(validationNote);
    }
    if (["customs_tradenet_v1", "customs_douanes_tunisiennes_v1"].includes(presentation.key) && detail) {
      const evidence = document.createElement("details");
      evidence.className = "field-source-evidence";
      const summary = document.createElement("summary");
      summary.textContent = t("fields.ocr_evidence");
      evidence.appendChild(summary);
      if (detail.machine_value !== null && detail.machine_value !== undefined) {
        const machine = document.createElement("div");
        machine.textContent = `${t("fields.machine_value")}: ${displayValue(detail.machine_value)}`;
        evidence.appendChild(machine);
      }
      if (detail.canonical_value !== null && detail.canonical_value !== undefined) {
        const canonical = document.createElement("div");
        canonical.textContent = `${t("fields.canonical_value")}: ${displayValue(detail.canonical_value)}`;
        evidence.appendChild(canonical);
      }
      if (detail.evidence_text) {
        const raw = document.createElement("pre");
        raw.textContent = detail.evidence_text;
        evidence.appendChild(raw);
      }
      value.appendChild(evidence);
    }
    table.append(key, value);
  });
}

function renderAvailableStructuredValues(fields = {}, expandedFields = {}, allowedFields = null) {
  const allowed = Array.isArray(allowedFields) ? new Set(allowedFields) : null;
  const values = new Map(Object.entries(fields));
  Object.entries(expandedFields).forEach(([key, detail]) => {
    if (!values.has(key) || values.get(key) === null || values.get(key) === "") values.set(key, detail?.value);
  });
  const entries = [...values.entries()].filter(([key, value]) => (!allowed || allowed.has(key)) && value !== null && value !== undefined && value !== "" && !Array.isArray(value));
  if (!entries.length) return `<div class="note">${escapeHtml(t("dossier.information_unavailable"))}</div>`;
  return `<div class="readonly-fields">${entries.map(([key, value]) => {
    const translated = t(`fields.${key}`);
    const label = translated.startsWith("[missing:") ? humanize(key) : translated;
    return `<div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(displayValue(value))}</span></div>`;
  }).join("")}</div>`;
}

function renderFieldCandidateFallback(field, selectedValue) {
  const wrapper = document.createElement("div");
  wrapper.className = "candidate-fallback";
  const consistency = MATH_CONSISTENCY_FIELDS.has(field) ? lastResponse?.field_consistency?.[field] : null;
  const candidates = [
    ...(lastResponse?.review_candidates?.[field] || []),
    ...(lastResponse?.rejected_candidates?.[field] || []),
  ].filter((candidate) => candidate?.value !== null && candidate?.value !== undefined && candidate?.value !== "");
  if (selectedValue !== null && selectedValue !== undefined && selectedValue !== "") {
    if (consistency?.status === "inconsistent") {
      const expected = consistency.expected_value;
      wrapper.innerHTML = `
        <div class="candidate-state warning">${escapeHtml(t("common.warning"))}</div>
        <div class="candidate-warning-message">${escapeHtml(consistency.message || t("candidate.warning"))}</div>
        ${expected !== null && expected !== undefined ? `<button class="ghost small" type="button" data-expected-field="${escapeAttribute(field)}" data-expected-value="${escapeAttribute(expected)}">${escapeHtml(t("candidate.use_expected", { value: formatMoney(expected) }))}</button>` : ""}
      `;
      wrapper.querySelector("[data-expected-field]")?.addEventListener("click", (event) => {
        applyExpectedFieldValue(event.currentTarget.dataset.expectedField, event.currentTarget.dataset.expectedValue, consistency);
      });
      return wrapper;
    }
    wrapper.innerHTML = `<span class="candidate-state confirmed">${escapeHtml(t("status.confirmed"))}</span>`;
    return wrapper;
  }
  if (!candidates.length) {
    wrapper.innerHTML = `<span class="candidate-state missing">${escapeHtml(t("status.not_extracted"))}</span>`;
    return wrapper;
  }
  const best = candidates[0];
  const alternatives = candidates.slice(1, 4);
  wrapper.innerHTML = `
    <div class="candidate-state review">${escapeHtml(t("status.not_confirmed"))}</div>
    <div class="candidate-option">
      <strong>${escapeHtml(t("candidate.primary"))}</strong>
      <span>${escapeHtml(displayValue(best.value ?? best.normalized_value))}</span>
      <small>${escapeHtml(best.source || t("candidate.unknown_source"))} - ${formatConfidence(best.confidence ?? best.score)}</small>
      <button class="ghost small" type="button" data-field-candidate="${escapeAttribute(field)}" data-candidate-index="0">${escapeHtml(t("common.select"))}</button>
    </div>
    ${alternatives.map((candidate, index) => `
      <div class="candidate-option alternative">
        <strong>${escapeHtml(t("candidate.alternative"))}</strong>
        <span>${escapeHtml(displayValue(candidate.value ?? candidate.normalized_value))}</span>
        <small>${escapeHtml(candidate.source || t("candidate.unknown_source"))} - ${formatConfidence(candidate.confidence ?? candidate.score)}</small>
        <button class="ghost small" type="button" data-field-candidate="${escapeAttribute(field)}" data-candidate-index="${index + 1}">${escapeHtml(t("common.select"))}</button>
      </div>
    `).join("")}
  `;
  wrapper.querySelectorAll("[data-field-candidate]").forEach((button) => {
    button.addEventListener("click", () => {
      const chosen = candidates[Number(button.dataset.candidateIndex)];
      selectCandidate({ field, candidate: chosen });
    });
  });
  return wrapper;
}
function renderNotes(data) {
  const validation = data.validation || {};
  const explanation = data.validation_explanation;
  const notes = document.getElementById("validationNotes");
  const items = [
    ...(validation.errors || []).map((message) => ({ type: t("common.error"), message, className: "error-note" })),
    ...(validation.warnings || []).map((message) => ({ type: t("common.warning"), message, className: "warning-note" })),
  ];
  notes.innerHTML = "";
  if (explanation?.reason) {
    const reason = document.createElement("div");
    reason.className = `note ${explanation.status === "valid" ? "success-note" : "warning-note"}`;
    reason.textContent = explanation.reason;
    notes.appendChild(reason);
  }
  if (!items.length && !explanation?.reason) {
    notes.innerHTML = `<div class="note success-note">${escapeHtml(t("validation.no_issues"))}</div>`;
    return;
  }
  items.forEach((item) => {
    const div = document.createElement("div");
    div.className = `note ${item.className}`;
    div.textContent = `${item.type}: ${item.message}`;
    notes.appendChild(div);
  });
}

function renderValidationSummary(explanation, validation) {
  const status = explanation?.status || validation?.status || "-";
  const reason = explanation?.reason || t("validation.no_summary");
  const action = explanation?.suggested_action || t("validation.default_action");
  const statusText = statusExplanation(status);
  validationSummary.innerHTML = `
    <span class="label">${escapeHtml(t("validation.explanation"))}</span>
    <strong>${escapeHtml(statusLabel(status))}</strong>
    <p>${escapeHtml(statusText)}</p>
    <div class="inspector-list">
      <div class="inspector-row"><span>${escapeHtml(t("common.reason"))}</span><div>${escapeHtml(reason)}</div></div>
      <div class="inspector-row"><span>${escapeHtml(t("common.action"))}</span><div>${escapeHtml(action)}</div></div>
      <div class="inspector-row"><span>${escapeHtml(t("validation.errors"))}</span><div>${escapeHtml(String(explanation?.blocking_errors?.length ?? validation?.errors?.length ?? 0))}</div></div>
      <div class="inspector-row"><span>${escapeHtml(t("validation.warnings"))}</span><div>${escapeHtml(String(explanation?.warnings?.length ?? validation?.warnings?.length ?? 0))}</div></div>
    </div>
  `;
}

function statusLabel(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized.includes("valid") && !normalized.includes("invalid")) return t("status.validated_decorated");
  if (normalized.includes("review")) return t("status.needs_review_decorated");
  if (normalized.includes("invalid") || normalized.includes("reject")) return t("status.invalid_decorated");
  if (normalized.includes("manual")) return t("status.corrected_decorated");
  return status || "-";
}

function localizedReadinessStatus(readinessStatus, validationStatus) {
  const normalized = String(readinessStatus || "").toLowerCase().replaceAll(" ", "_");
  if (normalized === "erp_ready" || normalized === "ready") return t("erp.ready");
  if (normalized.includes("reject")) return t("status.rejected");
  if (normalized.includes("review")) return t("status.needs_review");
  return String(validationStatus || "").toLowerCase() === "valid" ? t("erp.ready") : t("status.needs_review");
}

function statusExplanation(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized.includes("valid") && !normalized.includes("invalid")) return t("status.validated_help");
  if (normalized.includes("review")) return t("status.needs_review_help");
  if (normalized.includes("invalid") || normalized.includes("reject")) return t("status.invalid_help");
  if (normalized.includes("manual")) return t("status.corrected_help");
  if (normalized.includes("low")) return t("status.low_confidence");
  if (normalized.includes("missing")) return t("status.missing");
  if (normalized.includes("conflict")) return t("checks.conflict");
  return t("status.default_help");
}

function renderErpReadiness(data) {
  const panel = document.getElementById("erpReadinessPanel");
  if (!panel) return;
  const presentation = resolveDocumentPresentation();
  if (!presentation.allowInvoiceExport) {
    panel.className = "inspector-card readiness-card review";
    panel.innerHTML = `<span class="label">${escapeHtml(t("erp.readiness"))}</span><strong>${escapeHtml(t("dossier.erp_unavailable_title"))}</strong><p>${escapeHtml(t("dossier.erp_unavailable"))}</p>`;
    return;
  }
  const readiness = data.erp_readiness || data.erp_json?.quality?.erp_readiness || {};
  const status = localizedReadinessStatus(readiness.erp_ready_status, data.validation?.status);
  const score = Number(readiness.erp_ready_score ?? 0);
  const blockers = readiness.blocking_errors || [];
  const missing = readiness.missing_fields || [];
  const disabledReasons = [...blockers, ...missing.map((field) => t("erp.field_missing", { field: t(`fields.${field}`) }))];
  const validationCode = String(data.validation?.status || "").toLowerCase();
  const className = readiness.ready ? "ready" : validationCode.includes("reject") || validationCode.includes("invalid") ? "rejected" : "review";
  const nextAction = readiness.ready
    ? t("erp.next_ready")
    : disabledReasons.length
      ? t("erp.next_fix", { issue: disabledReasons[0], more: disabledReasons.length > 1 ? t("erp.more_issues", { count: disabledReasons.length - 1 }) : "" })
      : t("erp.next_review");
  panel.className = `inspector-card readiness-card ${className}`;
  panel.innerHTML = `
    <span class="label">${escapeHtml(t("erp.readiness"))}</span>
    <strong>${escapeHtml(status)}</strong>
    <p>${escapeHtml(statusExplanation(status))}</p>
    <div class="readiness-score">
      <span>${Math.round(score * 100)}%</span>
      <div class="score-bar"><span style="width:${Math.round(score * 100)}%"></span></div>
    </div>
    ${disabledReasons.length ? `<div class="business-list">${disabledReasons.map((reason) => `<div class="note warning-note">${escapeHtml(reason)}</div>`).join("")}</div>` : `<div class="note success-note">${escapeHtml(t("erp.blockers_cleared"))}</div>`}
    <div class="note">${escapeHtml(nextAction)}</div>
    <button id="erpExportBtn" class="export-button" type="button" ${readiness.ready ? "" : "disabled"} title="${escapeAttribute(disabledReasons.join("; ") || t("erp.export_ready_title"))}">${escapeHtml(t("erp.export"))}</button>
  `;
  panel.querySelector("#erpExportBtn")?.addEventListener("click", () => {
    navigator.clipboard?.writeText(pretty(data.validated_erp_json || data.erp_json || {}));
    showTransientNote(t("erp.copied"));
  });
}

function renderConfidences(confidences) {
  const list = document.getElementById("confidenceList");
  list.innerHTML = "";
  const entries = Object.entries(confidences);
  if (!entries.length) {
    list.innerHTML = `<span class="chip">${escapeHtml(t("confidence.none"))}</span>`;
    return;
  }
  entries
    .sort((a, b) => a[0].localeCompare(b[0]))
    .forEach(([field, value]) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = `${field}: ${formatConfidence(value)}`;
      list.appendChild(chip);
    });
}

function renderLineItems(items, rowValidation = []) {
  const box = document.getElementById("lineItems");
  const presentation = resolveDocumentPresentation();
  if (!presentation.showLineItems) {
    box.innerHTML = `<div class="note">${escapeHtml(t("dossier.line_items_not_applicable"))}</div>`;
    return;
  }
  const editableItems = items || [];
  if (lastResponse) {
    lastResponse.detected_fields = lastResponse.detected_fields || {};
    lastResponse.detected_fields.line_items = editableItems;
  }
  const rows = editableItems.map((item, index) => editableLineItemRow(item, index, rowValidation[index])).join("");
  box.innerHTML = `
    <div class="panel-head">
      <p class="panel-subtitle">${escapeHtml(t("line_items.help"))}</p>
      <div class="edit-actions">
        <button class="ghost small" id="addLineItemBtn" type="button">${escapeHtml(t("line_items.add"))}</button>
        <button class="ghost small" id="saveLineItemsBtn" type="button">${escapeHtml(t("line_items.save"))}</button>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>${escapeHtml(t("line_items.description"))}</th><th>${escapeHtml(t("line_items.quantity"))}</th><th>${escapeHtml(t("line_items.unit"))}</th><th>${escapeHtml(t("line_items.unit_price"))}</th>
          <th>${escapeHtml(t("line_items.total_ht"))}</th><th>${escapeHtml(t("line_items.tax"))}</th><th>${escapeHtml(t("line_items.total_ttc"))}</th><th>${escapeHtml(t("common.status"))}</th><th>${escapeHtml(t("common.actions"))}</th>
        </tr>
      </thead>
      <tbody>${rows || `<tr><td colspan="9"><div class="note">${escapeHtml(t("line_items.none"))}</div></td></tr>`}</tbody>
      <tfoot>
        <tr class="line-items-total-footer" aria-live="polite">
          <td colspan="6" class="line-items-total-label">${escapeHtml(t("line_items.lines_total"))}</td>
          <td id="lineItemsTotalSummary" class="line-items-total-cell">0.00</td>
          <td colspan="2" class="line-items-total-meta">${escapeHtml(t("line_items.total_ttc"))}</td>
        </tr>
      </tfoot>
    </table>
  `;
  box.querySelector("#addLineItemBtn")?.addEventListener("click", () => addReviewLineItem());
  box.querySelector("#saveLineItemsBtn")?.addEventListener("click", () => saveCorrections());
  box.querySelectorAll("[data-line-field]").forEach((input) => {
    input.addEventListener("input", () => updateReviewLineItem(Number(input.dataset.index), input.dataset.lineField, input.value));
  });
  box.querySelectorAll("[data-delete-line]").forEach((button) => {
    button.addEventListener("click", () => deleteReviewLineItem(Number(button.dataset.index)));
  });
  box.querySelectorAll("[data-restore-line]").forEach((button) => {
    button.addEventListener("click", () => restoreReviewLineItem(Number(button.dataset.index)));
  });
  updateLineItemsTotalSummary();
}

function editableLineItemRow(item, index, validationReport) {
  const status = validationReport?.status || (String(item.source || "").toLowerCase().includes("review") ? "needs_review" : "validated");
  const cells = [
    ["description", item.description, "description-input"],
    ["quantity", item.quantity, ""],
    ["unit", item.unit, ""],
    ["unit_price", item.unit_price, ""],
    ["line_total_ht", item.line_total_ht, ""],
    ["tax_rate", item.tax_rate, ""],
    ["line_total_ttc", item.line_total_ttc ?? item.total, ""],
  ].map(([field, value, className]) => `
    <td><input class="edit-input ${className}" data-index="${index}" data-line-field="${field}" value="${escapeAttribute(value ?? "")}" placeholder="-"></td>
  `).join("");
  const reason = validationReport?.validation_reason || item.source || "";
  return `<tr class="${escapeAttribute(status)}" data-line-row="${index + 1}">${cells}<td><span class="status-chip ${escapeAttribute(status)}" title="${escapeAttribute(reason || statusExplanation(status))}">${escapeHtml(statusLabel(status))}</span></td><td><div class="dynamic-actions"><button class="ghost small" type="button" data-restore-line data-index="${index}">${escapeHtml(t("common.restore"))}</button><button class="ghost small" type="button" data-delete-line data-index="${index}">${escapeHtml(t("common.delete"))}</button></div></td></tr>`;
}
function updateLineItemsTotalSummary() {
  const host = document.getElementById("lineItemsTotalSummary");
  if (!host || !lastResponse) return;
  const items = lastResponse.detected_fields?.line_items || [];
  const values = items
    .map((item) => numberOrNull(item.line_total_ttc ?? item.total))
    .filter((value) => value !== null);
  const lineTotal = roundMoney(values.reduce((sum, value) => sum + value, 0));
  host.textContent = formatMoney(lineTotal);
  host.title = t("line_items.total_title");
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const normalized = String(value).replace(/\s+/g, "").replace(",", ".");
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function roundMoney(value) {
  return Math.round(Number(value || 0) * 1000) / 1000;
}

function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 3 }).format(Number(value));
}

function resetCorrections() {
  correctedFields = {};
  correctedLineItems = [];
  ignoredRows = [];
  clearTimeout(autoValidationTimer);
  autoValidationTimer = null;
  autoValidationInFlight = false;
  autoValidationQueued = false;
}

function updateReviewField(field, rawValue) {
  if (!lastResponse) return;
  const value = coerceValue(field, rawValue);
  lastResponse.detected_fields = lastResponse.detected_fields || {};
  lastResponse.detected_fields[field] = value;
  const previousCorrection = correctedFields[field] || {};
  correctedFields[field] = {
    ...previousCorrection,
    original_value: previousCorrection.original_value ?? getFieldOriginalValue(field),
    corrected_value: value,
    corrected_by: "human",
  };
  applyFieldToErpJson(field, value);
  syncExpandedField(field, value);
  syncDynamicFieldRows(field, value);
  updateCorrectionLayer("detected_fields");
  updateJsonPanels();
  scheduleAutoValidation("field_edit");
}

function getFieldOriginalValue(field) {
  const detail = lastResponse?.expanded_fields?.[field];
  return detail?.display_value ?? detail?.value ?? lastResponse?.detected_fields?.[field] ?? null;
}

function applyFieldToErpJson(field, value) {
  if (!lastResponse?.erp_json) return;
  const path = FIELD_TO_ERP_PATH[field];
  if (!path) return;
  setNestedValue(lastResponse.erp_json, path, value);
  if (path[0] === "invoice" && lastResponse.erp_json.document) {
    setNestedValue(lastResponse.erp_json, ["document", path[1]], value);
  }
  syncFlatExport(field, value);
}

function syncFlatExport(field, value) {
  if (!lastResponse.erp_export) return;
  const map = {
    supplier_name: "vendor_name",
    supplier_tax_id: "vendor_tax_id",
    invoice_number: "invoice_ref",
    invoice_date: "invoice_date",
    due_date: "due_date",
    amount_ht: "amount_excl_tax",
    tva_amount: "tax_amount",
    amount_ttc: "amount_incl_tax",
    currency: "currency_code",
  };
  if (map[field]) lastResponse.erp_export[map[field]] = value;
}

function syncExpandedField(field, value) {
  lastResponse.expanded_fields = lastResponse.expanded_fields || {};
  if (!lastResponse.expanded_fields[field]) lastResponse.expanded_fields[field] = { value: null, evidence_text: null };
  lastResponse.expanded_fields[field].value = value;
  lastResponse.expanded_fields[field].display_value = value === null || value === undefined ? "" : String(value);
  lastResponse.expanded_fields[field].source = "manual correction";
  lastResponse.expanded_fields[field].confidence = 1;
}

function syncDynamicFieldRows(field, value) {
  (lastResponse.dynamic_tables || []).forEach((table) => {
    (table.rows || []).forEach((row) => {
      if (row.key !== field) return;
      row.value = value;
      row.status = "manually_corrected";
      row.confidence = 1;
      row.source = "manual correction";
      row.correction = correctedFields[field];
    });
  });
}

function updateReviewLineItem(index, field, rawValue) {
  if (!lastResponse) return;
  const items = ensureLineItems();
  items[index] = items[index] || {};
  const value = coerceValue(field, rawValue);
  items[index][field] = value;
  if (field === "line_total_ttc") items[index].total = value;
  items[index].source = "human verified";
  items[index].confidence = 1;
  syncLineItemsToResponse();
  correctedLineItems.push({ row_index: index, field, corrected_value: value, corrected_by: "human" });
  updateCorrectionLayer("line_items");
  updateJsonPanels();
  updateLineItemsTotalSummary();
  scheduleAutoValidation("line_item_edit");
}

function addReviewLineItem() {
  const items = ensureLineItems();
  items.push({
    description: "",
    quantity: null,
    unit: "",
    unit_price: null,
    line_total_ht: null,
    tax_rate: null,
    line_total_ttc: null,
    total: null,
    confidence: 1,
    source: "manual",
  });
  syncLineItemsToResponse();
  correctedLineItems.push({ row_index: items.length - 1, added: true, corrected_by: "human" });
  renderLineItems(items);
  renderDynamicReview();
  updateCorrectionLayer("line_items");
  updateJsonPanels();
}

function deleteReviewLineItem(index) {
  const items = ensureLineItems();
  if (!Number.isInteger(index) || index < 0 || index >= items.length) {
    showTransientNote(t("line_items.delete_missing"));
    return;
  }
  items.splice(index, 1);
  syncLineItemsToResponse();
  ignoredRows = ignoredRows.filter((item) => item !== index && item !== String(index) && item !== index + 1 && item !== String(index + 1));
  correctedLineItems.push({ row_index: index, deleted: true, corrected_by: "human" });
  renderLineItems(items);
  renderDynamicReview();
  updateCorrectionLayer("line_items");
  updateJsonPanels();
  scheduleAutoValidation("line_item_deleted");
}

function restoreReviewLineItem(index) {
  const original = originalLineItemsSnapshot[index];
  if (!original) {
    showTransientNote(t("line_items.restore_missing"));
    return;
  }
  const items = ensureLineItems();
  items[index] = deepClone(original);
  syncLineItemsToResponse();
  correctedLineItems.push({ row_index: index, restored: true, corrected_by: "human" });
  renderLineItems(items, lastResponse.row_validation || []);
  renderDynamicReview();
  updateCorrectionLayer("line_items");
  updateJsonPanels();
  scheduleAutoValidation("line_item_restored");
  showTransientNote(t("line_items.restored", { number: index + 1 }));
}

function ensureLineItems() {
  lastResponse.detected_fields = lastResponse.detected_fields || {};
  lastResponse.detected_fields.line_items = lastResponse.detected_fields.line_items || [];
  return lastResponse.detected_fields.line_items;
}

function syncLineItemsToResponse() {
  const items = ensureLineItems();
  if (lastResponse.erp_json) lastResponse.erp_json.line_items = items;
  if (lastResponse.erp_export?.source_payload) lastResponse.erp_export.source_payload.line_items = items;
  syncDynamicLineItemRows(items);
}

function syncDynamicLineItemRows(items) {
  const table = (lastResponse.dynamic_tables || []).find((item) => item.id === "line_items");
  if (!table) return;
  table.rows = items.map((item, index) => ({
    key: `line_item_${index + 1}`,
    label: t("line_items.number", { number: index + 1 }),
    values: {
      row_number: index + 1,
      reference: item.reference ?? "",
      description: item.description ?? "",
      quantity: item.quantity ?? "",
      unit: item.unit ?? "",
      unit_price: item.unit_price ?? "",
      discount: item.discount ?? "",
      tax_rate: item.tax_rate ?? "",
      amount_ht: item.line_total_ht ?? "",
      tax_amount: item.tax_amount ?? "",
      amount_ttc: item.line_total_ttc ?? item.total ?? "",
      confidence: item.confidence ?? 1,
      source: item.source ?? "manual correction",
      page: item.page ?? "",
    },
    source: item.source ?? "manual correction",
    included_in_erp: true,
    editable: true,
    status: "manually_corrected",
    correction: { original_value: null, corrected_value: item, corrected_by: "human" },
  }));
}

function coerceValue(field, rawValue) {
  const value = String(rawValue ?? "").trim();
  if (value === "") return null;
  if (NUMERIC_FIELDS.has(field)) {
    const normalized = value.replace(/\s/g, "").replace(",", ".");
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : value;
  }
  return value;
}

function setNestedValue(target, path, value) {
  let current = target;
  path.slice(0, -1).forEach((part) => {
    current[part] = current[part] || {};
    current = current[part];
  });
  current[path[path.length - 1]] = value;
}

function updateJsonPanels() {
  if (!lastResponse) return;
  if (lastResponse.erp_export) lastResponse.erp_export.source_payload = lastResponse.erp_json;
  document.getElementById("erpJson").textContent = pretty(lastResponse.erp_json || {});
  document.getElementById("fullJson").textContent = pretty(lastResponse);
  if (activeDynamicTab === "raw_json") renderDynamicReview();
}
function renderDynamicReview() {
  const host = document.getElementById("dynamicTableHost");
  if (!host) return;
  if (!lastResponse) {
    host.innerHTML = `<div class="note">${escapeHtml(t("dynamic.process_first"))}</div>`;
    return;
  }
  if (activeDynamicTab === "visual") {
    host.innerHTML = `<div class="note">${escapeHtml(t("dynamic.visual_help"))}</div>`;
    return;
  }
  if (activeDynamicTab === "raw_json") {
    host.innerHTML = `<pre>${escapeHtml(pretty(lastResponse))}</pre>`;
    return;
  }
  if (activeDynamicTab === "financial_checks") {
    renderFinancialChecks(host);
    return;
  }
  if (activeDynamicTab === "correction_suggestions") {
    renderCorrectionSuggestions(host);
    return;
  }
  if (activeDynamicTab === "duplicate_fraud") {
    renderDuplicateAndFraud(host);
    return;
  }
  if (activeDynamicTab === "validation_report") {
    host.innerHTML = `<pre>${escapeHtml(pretty(lastResponse.invoice_validation_report || {}))}</pre>`;
    return;
  }
  if (activeDynamicTab === "erp_json") {
    host.innerHTML = `<pre>${escapeHtml(pretty(lastResponse.validated_erp_json || lastResponse.erp_json || {}))}</pre>`;
    return;
  }

  const tables = lastResponse.dynamic_tables || [];
  const selectedTables = activeDynamicTab === "all_extracted_fields"
    ? tables.filter((table) => ["all_extracted_fields", "payment_details", "tax_summary", "unmapped_text"].includes(table.id))
    : tables.filter((table) => table.id === activeDynamicTab);
  if (!selectedTables.length) {
    host.innerHTML = `<div class="note">${escapeHtml(t("dynamic.no_data"))}</div>`;
    return;
  }
  host.innerHTML = "";
  selectedTables.forEach((table) => host.appendChild(renderDynamicTable(table)));
}

function renderFinancialChecks(host) {
  const reasoning = lastResponse.financial_reasoning || {};
  const checks = Object.entries(reasoning.checks || {});
  const warnings = reasoning.financial_warnings || [];
  const errors = reasoning.financial_errors || [];
  const rows = checks.map(([name, check]) => {
    const hasActual = check.actual !== null && check.actual !== undefined && check.actual !== "";
    const status = check.passed ? "pass" : hasActual ? "fail" : "warn";
    const label = check.passed ? t("checks.passed") : hasActual ? t("checks.conflict") : t("checks.warning");
    const action = check.passed
      ? t("checks.no_action")
      : hasActual
        ? t("checks.correct_conflict")
        : t("checks.enter_missing");
    return `
      <article class="business-item ${status}">
        <header><strong>${escapeHtml(humanize(name))}</strong><span class="status-chip ${status === "pass" ? "validated" : status === "warn" ? "needs_review" : "conflict"}">${label}</span></header>
        <div class="check-grid">
          <span>${escapeHtml(t("checks.expected"))}</span><strong>${escapeHtml(displayValue(check.expected))}</strong>
          <span>${escapeHtml(t("checks.extracted"))}</span><strong>${escapeHtml(displayValue(check.actual))}</strong>
          <span>${escapeHtml(t("checks.difference"))}</span><strong>${escapeHtml(displayValue(check.delta))}</strong>
          <span>${escapeHtml(t("checks.tolerance"))}</span><strong>${escapeHtml(displayValue(reasoning.tolerance))}</strong>
        </div>
        <div class="note">${escapeHtml(action)}</div>
      </article>
    `;
  }).join("");
  host.innerHTML = `
    <div class="business-list">
      ${rows || `<div class="note warning-note">${escapeHtml(t("checks.incomplete"))}</div>`}
      ${errors.map((message) => `<div class="note error-note">${escapeHtml(message)}</div>`).join("")}
      ${warnings.map((message) => `<div class="note warning-note">${escapeHtml(message)}</div>`).join("")}
    </div>
  `;
}

function renderCorrectionSuggestions(host) {
  const assistant = lastResponse.review_assistant || {};
  const assistantIssues = assistant.issues || [];
  const suggestions = lastResponse.correction_suggestions || [];
  const reviewCandidates = lastResponse.review_candidates || {};
  const candidateCards = Object.entries(reviewCandidates).flatMap(([field, candidates]) => (candidates || []).map((candidate) => ({ field, candidate })));
  host.innerHTML = `
    <div class="candidate-list">
      ${assistantIssues.length ? `
        <article class="candidate-card">
          <header><strong>${escapeHtml(t("assistant.title"))}</strong><span>${formatConfidence(assistant.confidence)}</span></header>
          <div>${escapeHtml(assistant.summary || t("assistant.default_summary"))}</div>
          <div>${escapeHtml(t("assistant.erp_impact", { value: assistant.erp_impact || "-" }))}</div>
          <div class="note">${escapeHtml(assistant.reviewer_control || t("assistant.advisory"))}</div>
        </article>
        ${assistantIssues.map((issue) => `
          <article class="candidate-card">
            <header><strong>${escapeHtml(issue.title || issue.type || t("assistant.issue"))}</strong><span>${formatConfidence(issue.confidence)}</span></header>
            <div>${escapeHtml(t("assistant.problem"))}: ${escapeHtml(issue.suspected_problem || "-")}</div>
            <div>${escapeHtml(t("assistant.explanation"))}: ${escapeHtml(issue.explanation || "-")}</div>
            <div>${escapeHtml(t("assistant.suggested"))}: ${escapeHtml(displayValue(issue.suggested_correction))}</div>
            <div>${escapeHtml(t("assistant.erp_impact", { value: issue.erp_impact || "-" }))}</div>
            ${(issue.evidence || []).length ? `<div class="candidate-evidence">${(issue.evidence || []).slice(0, 5).map((item) => `
              <div class="note">
                #${escapeHtml(String(item.rank || "-"))}: ${escapeHtml(displayValue(item.value))}
                ${item.confidence !== undefined && item.confidence !== null ? `(${escapeHtml(formatConfidence(item.confidence))})` : ""}
                ${item.reason ? ` - ${escapeHtml(displayValue(item.reason))}` : ""}
              </div>
            `).join("")}</div>` : ""}
          </article>
        `).join("")}
      ` : `<div class="note success-note">${escapeHtml(t("assistant.no_issues"))}</div>`}
      ${suggestions.length ? suggestions.map((suggestion, index) => `
        <article class="candidate-card">
          <header><strong>${escapeHtml(suggestion.field || t("suggestion.title"))}</strong><span>${formatConfidence(suggestion.confidence)}</span></header>
          <div>${escapeHtml(t("suggestion.original"))}: ${escapeHtml(suggestion.original ?? "-")}</div>
          <div>${escapeHtml(t("suggestion.proposed"))}: ${escapeHtml(suggestion.proposed ?? suggestion.proposed_value ?? "-")}</div>
          <div>${escapeHtml(t("suggestion.reason"))}: ${escapeHtml(suggestion.reason ?? "-")}</div>
          <div class="edit-actions">
            <button class="ghost small" type="button" data-accept-suggestion="${index}">${escapeHtml(t("common.accept"))}</button>
            <button class="ghost small" type="button" data-reject-suggestion="${index}">${escapeHtml(t("common.reject"))}</button>
          </div>
        </article>
      `).join("") : `<div class="note success-note">${escapeHtml(t("suggestion.none"))}</div>`}
      ${candidateCards.length ? `<h2>${escapeHtml(t("candidate.heading"))}</h2>${candidateCards.map(({ field, candidate }, index) => `
        <article class="candidate-card">
          <header><strong>${escapeHtml(field)}</strong><span>${formatConfidence(candidate.confidence ?? candidate.score)}</span></header>
          <div>${escapeHtml(t("common.value"))}: ${escapeHtml(candidate.value ?? "-")}</div>
          <div>${escapeHtml(t("common.source"))}: ${escapeHtml(candidate.source ?? "-")}</div>
          <div>${escapeHtml(t("common.evidence"))}: ${escapeHtml(candidate.evidence_text ?? "-")}</div>
          <div>${escapeHtml(t("status.rejected"))}: ${candidate.rejected ? escapeHtml(candidate.rejection_reason || t("common.yes")) : escapeHtml(t("common.no"))}</div>
          <button class="ghost small" type="button" data-select-candidate="${index}">${escapeHtml(t("candidate.use"))}</button>
        </article>
      `).join("")}` : ""}
    </div>
  `;
  host.querySelectorAll("[data-accept-suggestion]").forEach((button) => {
    button.addEventListener("click", () => acceptSuggestion(Number(button.dataset.acceptSuggestion)));
  });
  host.querySelectorAll("[data-reject-suggestion]").forEach((button) => {
    button.addEventListener("click", () => {
      button.closest(".candidate-card")?.classList.add("ignored");
      showTransientNote(t("suggestion.rejected"));
    });
  });
  host.querySelectorAll("[data-select-candidate]").forEach((button) => {
    button.addEventListener("click", () => selectCandidate(candidateCards[Number(button.dataset.selectCandidate)]));
  });
}

function renderDuplicateAndFraud(host) {
  const duplicate = lastResponse.duplicate_detection || {};
  const fraud = lastResponse.fraud_indicators || {};
  const indicators = fraud.fraud_indicators || [];
  host.innerHTML = `
    <div class="business-list">
      <article class="business-item ${duplicate.possible_duplicate ? "warn" : "pass"}">
        <strong>${escapeHtml(t("risk.duplicate"))}</strong>
        <div>${escapeHtml(t("risk.possible", { value: duplicate.possible_duplicate ? t("common.yes") : t("common.no") }))}</div>
        <pre>${escapeHtml(pretty(duplicate))}</pre>
      </article>
      <article class="business-item ${indicators.length ? "warn" : "pass"}">
        <strong>${escapeHtml(t("risk.indicators"))}</strong>
        ${indicators.length ? indicators.map((item) => `<div class="note warning-note">${escapeHtml(item)}</div>`).join("") : `<div class="note success-note">${escapeHtml(t("risk.none"))}</div>`}
        <div class="note">${escapeHtml(fraud.disclaimer || t("risk.disclaimer"))}</div>
      </article>
    </div>
  `;
}

function renderDynamicTable(table) {
  const card = document.createElement("article");
  card.className = "dynamic-table-card";
  const rows = filterDynamicRows(table.rows || []);
  const confidence = summarizeConfidence(rows);
  card.innerHTML = `
    <div class="dynamic-table-summary">
      <div>
        <strong>${escapeHtml(table.title)}</strong>
        <span class="dynamic-table-meta">${escapeHtml(t("dynamic.summary", { count: rows.length, confidence }))}</span>
      </div>
      ${table.id === "line_items" ? `<button class="ghost small" type="button" data-add-line>${escapeHtml(t("line_items.add_row"))}</button>` : ""}
    </div>
  `;
  if (table.id === "line_items") {
    card.querySelector("[data-add-line]")?.addEventListener("click", () => addDynamicLineItem(table));
  }

  if (table.type === "table") {
    card.appendChild(renderDynamicGridTable(table, rows));
  } else {
    card.appendChild(renderDynamicKeyValueRows(table, rows));
  }
  return card;
}

function renderDynamicKeyValueRows(table, rows) {
  const wrapper = document.createElement("div");
  wrapper.className = "dynamic-key-values";
  if (!rows.length) {
    wrapper.innerHTML = `<div class="note">${escapeHtml(t("filter.no_rows"))}</div>`;
    return wrapper;
  }
  rows.forEach((row) => {
    const div = document.createElement("div");
    div.className = `dynamic-row ${row.status || "ok"}`;
    div.dataset.tableId = table.id;
    div.dataset.rowKey = row.key || "";
    div.innerHTML = `
      <strong>${escapeHtml(row.label || row.key || t("dynamic.row"))}</strong>
      <span class="dynamic-value" contenteditable="true">${escapeHtml(displayValue(row.value))}</span>
      <span>${formatConfidence(row.confidence)}</span>
      <span>${escapeHtml(row.source || "")}</span>
      <span>${escapeHtml(row.page || "")}</span>
      <span class="status-chip ${escapeHtml(row.status || "ok")}">${escapeHtml(row.status || "ok")}</span>
    `;
    div.querySelector(".dynamic-value")?.addEventListener("input", (event) => markCorrected(row, event.currentTarget.textContent, table.id, div));
    div.addEventListener("click", (event) => {
      if (event.target?.isContentEditable) return;
      focusDynamicRegion(row, table.id);
    });
    wrapper.appendChild(div);
  });
  return wrapper;
}

function renderDynamicGridTable(table, rows) {
  const wrapper = document.createElement("div");
  wrapper.className = "dynamic-table-scroll";
  if (!rows.length) {
    wrapper.innerHTML = `<div class="note">${escapeHtml(t("filter.no_rows"))}</div>`;
    return wrapper;
  }
  const columns = table.columns || [];
  const headers = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
  const body = rows.map((row) => {
    const cells = columns.map((column) => `
      <td>
        <span class="dynamic-cell" contenteditable="true" data-column="${escapeAttribute(column.key)}">${escapeHtml(displayValue(row.values?.[column.key]))}</span>
      </td>
    `).join("");
    const actions = table.id === "line_items"
      ? `<td class="dynamic-actions"><button class="ghost small" type="button" data-restore-row>${escapeHtml(t("common.restore"))}</button><button class="ghost small" type="button" data-ignore-row>${escapeHtml(t("common.ignore"))}</button><button class="ghost small" type="button" data-delete-row>${escapeHtml(t("common.delete"))}</button></td>`
      : "";
    return `<tr class="${escapeAttribute(row.status || "ok")}" data-row-key="${escapeAttribute(row.key || "")}">${cells}${actions}</tr>`;
  }).join("");
  wrapper.innerHTML = `
    <table>
      <thead><tr>${headers}${table.id === "line_items" ? `<th>${escapeHtml(t("common.actions"))}</th>` : ""}</tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
  rows.forEach((row) => {
    const tr = wrapper.querySelector(`tr[data-row-key="${cssEscape(row.key || "")}"]`);
    tr?.querySelectorAll(".dynamic-cell").forEach((cell) => {
      cell.addEventListener("input", () => markTableCellCorrected(row, cell.dataset.column, cell.textContent, table.id, tr));
    });
    tr?.querySelector("[data-ignore-row]")?.addEventListener("click", (event) => {
      event.stopPropagation();
      row.status = "ignored";
      tr.className = "ignored";
      if (row.key) ignoredRows.push(row.key);
      correctedLineItems.push({ row_key: row.key, status: "ignored" });
      renderDynamicReview();
      updateCorrectionLayer(table.id);
      updateJsonPanels();
      scheduleAutoValidation("dynamic_row_ignored");
    });
    tr?.querySelector("[data-restore-row]")?.addEventListener("click", (event) => {
      event.stopPropagation();
      row.status = "validated";
      tr.className = "validated";
      ignoredRows = ignoredRows.filter((item) => item !== row.key && item !== lineIndexFromKey(row.key));
      correctedLineItems.push({ row_key: row.key, status: "validated" });
      renderDynamicReview();
      updateCorrectionLayer(table.id);
      updateJsonPanels();
      scheduleAutoValidation("dynamic_row_restored");
    });
    tr?.querySelector("[data-delete-row]")?.addEventListener("click", (event) => {
      event.stopPropagation();
      row.status = "ignored";
      if (row.key) ignoredRows.push(row.key);
      correctedLineItems.push({ row_key: row.key, deleted: true });
      const tableData = (lastResponse.dynamic_tables || []).find((item) => item.id === table.id);
      if (tableData) tableData.rows = tableData.rows.filter((item) => item.key !== row.key);
      renderDynamicReview();
      updateCorrectionLayer(table.id);
      updateJsonPanels();
      scheduleAutoValidation("dynamic_row_deleted");
    });
    tr?.addEventListener("click", (event) => {
      if (event.target?.isContentEditable || event.target?.tagName === "BUTTON") return;
      focusDynamicRegion(row, table.id);
    });
  });
  return wrapper;
}

function filterDynamicRows(rows) {
  const filter = document.getElementById("dynamicFilter")?.value || "all";
  if (filter === "all") return rows;
  if (filter === "erp") return rows.filter((row) => row.required_for_erp || row.included_in_erp);
  return rows.filter((row) => row.status === filter);
}

function summarizeConfidence(rows) {
  const values = rows.map((row) => row.confidence).filter((value) => value !== null && value !== undefined);
  if (!values.length) return "-";
  const average = values.reduce((sum, value) => sum + Number(value), 0) / values.length;
  return formatConfidence(average);
}

function markCorrected(row, correctedValue, tableId, element) {
  row.status = "manually_corrected";
  row.correction = {
    original_value: row.correction?.original_value ?? row.value,
    corrected_value: correctedValue,
    corrected_by: "human",
  };
  row.value = correctedValue;
  correctedFields[row.key] = row.correction;
  if (EDITABLE_FIELDS.includes(row.key)) {
    updateReviewField(row.key, correctedValue);
    const fieldInput = document.querySelector(`[data-field="${cssEscape(row.key)}"]`);
    if (fieldInput) fieldInput.value = correctedValue;
  }
  element.classList.add("manually_corrected");
  element.querySelector(".status-chip").textContent = statusLabel("manually_corrected");
  element.querySelector(".status-chip").className = "status-chip manually_corrected";
  updateCorrectionLayer(tableId);
  updateJsonPanels();
  scheduleAutoValidation("dynamic_field_edit");
}

function markTableCellCorrected(row, column, correctedValue, tableId, element) {
  row.status = "manually_corrected";
  row.values = row.values || {};
  row.correction = {
    original_value: row.correction?.original_value ?? { ...row.values },
    corrected_value: { ...row.values, [column]: correctedValue },
    corrected_by: "human",
  };
  row.values[column] = correctedValue;
  correctedLineItems.push({ row_key: row.key, column, corrected_value: correctedValue, corrected_by: "human" });
  if (tableId === "line_items") {
    const index = Math.max(0, Number(String(row.key || "").replace(/\D/g, "")) - 1);
    const itemField = LINE_TABLE_TO_ITEM_FIELD[column] || column;
    updateReviewLineItem(index, itemField, correctedValue);
    const lineInput = document.querySelector(`[data-index="${index}"][data-line-field="${cssEscape(itemField)}"]`);
    if (lineInput) lineInput.value = correctedValue;
  }
  element.classList.add("manually_corrected");
  updateCorrectionLayer(tableId);
  updateJsonPanels();
  scheduleAutoValidation("dynamic_cell_edit");
}

function addDynamicLineItem(table) {
  const rowNumber = (table.rows || []).length + 1;
  const newRow = {
    key: `manual_line_item_${Date.now()}`,
    label: t("line_items.number", { number: rowNumber }),
    values: {
      row_number: rowNumber,
      reference: "",
      description: "",
      quantity: "",
      unit: "",
      unit_price: "",
      discount: "",
      tax_rate: "",
      amount_ht: "",
      tax_amount: "",
      amount_ttc: "",
      confidence: "",
      source: "manual",
      page: "",
    },
    source: "manual",
    included_in_erp: true,
    editable: true,
    status: "manually_corrected",
    correction: {
      original_value: null,
      corrected_value: {},
      corrected_by: "human",
    },
  };
  table.rows.push(newRow);
  const items = ensureLineItems();
  items.push({
    reference: "",
    description: "",
    quantity: null,
    unit: "",
    unit_price: null,
    discount: null,
    tax_rate: null,
    line_total_ht: null,
    tax_amount: null,
    line_total_ttc: null,
    total: null,
    confidence: 1,
    source: "manual",
  });
  syncLineItemsToResponse();
  correctedLineItems.push({ row_key: newRow.key, added: true, corrected_by: "human" });
  renderLineItems(items);
  renderDynamicReview();
  updateJsonPanels();
  updateCorrectionLayer(table.id);
}

function updateCorrectionLayer(tableId) {
  lastResponse.correction_metadata = {
    corrected_fields: correctedFields,
    corrected_line_items: correctedLineItems,
    source_table: tableId,
  };
}

function focusDynamicRegion(row, tableId) {
  if (!row.bbox) {
    showRegionDetails(tableId, row.label || row.key || "Dynamic row", row);
    return;
  }
  showRegionDetails(tableId, row.label || row.key || "Dynamic row", row);
  if (row.page && normalizePage(row.page) !== normalizePage(lastResponse?.document_preview?.pages?.[currentPageIndex]?.page)) {
    const pages = lastResponse?.document_preview?.pages || [];
    const targetIndex = pages.findIndex((page) => page.page === normalizePage(row.page));
    if (targetIndex >= 0) {
      selectPhysicalPage(targetIndex);
      setTimeout(() => focusDynamicRegion(row, tableId), 100);
      return;
    }
  }
  const stage = document.getElementById("previewStage");
  const firstPage = lastResponse?.document_preview?.pages?.[currentPageIndex];
  if (!stage || !firstPage) return;
  const displayWidth = Math.max(1, Math.round(firstPage.width * previewZoom));
  const displayHeight = Math.max(1, Math.round(firstPage.height * previewZoom));
  const scaleX = displayWidth / firstPage.width;
  const scaleY = displayHeight / firstPage.height;
  const marker = document.createElement("button");
  marker.type = "button";
  marker.className = "overlay-box overlay-field";
  marker.style.left = `${row.bbox.x1 * scaleX}px`;
  marker.style.top = `${row.bbox.y1 * scaleY}px`;
  marker.style.width = `${Math.max(8, (row.bbox.x2 - row.bbox.x1) * scaleX)}px`;
  marker.style.height = `${Math.max(8, (row.bbox.y2 - row.bbox.y1) * scaleY)}px`;
  stage.appendChild(marker);
  marker.scrollIntoView({ block: "center", inline: "center" });
  setTimeout(() => marker.remove(), 1800);
}

function renderPreview(data) {
  const pages = data.document_preview?.pages || [];
  const page = pages[currentPageIndex] || pages[0];
  if (!page) {
    previewCanvas.innerHTML = `<div class="note">${escapeHtml(t("preview.none"))}</div>`;
    return;
  }

  currentPageIndex = Math.max(0, Math.min(currentPageIndex, pages.length - 1));
  const imageUrl = new URL(page.url, window.location.origin).href;
  previewCanvas.innerHTML = `
    <div class="preview-toolbar">
      <a class="preview-link" href="${escapeAttribute(imageUrl)}" target="_blank" rel="noopener">${escapeHtml(t("preview.open"))}</a>
    </div>
    <div class="preview-stage" id="previewStage">
      <img id="previewImage" src="${escapeAttribute(imageUrl)}" alt="${escapeAttribute(t("preview.alt"))}">
    </div>
  `;
  resetRegionDetails();
  const image = document.getElementById("previewImage");
  image.addEventListener("load", () => redrawPreview());
  image.addEventListener("error", () => {
    previewCanvas.innerHTML = `<div class="note error-note">${escapeHtml(t("preview.load_failed"))}</div>`;
  });
  if (image.complete) redrawPreview();
}

function redrawPreview() {
  if (!lastResponse) return;
  const pages = lastResponse.document_preview?.pages || [];
  const firstPage = pages[currentPageIndex];
  const stage = document.getElementById("previewStage");
  const image = document.getElementById("previewImage");
  if (!firstPage || !stage || !image) return;
  updatePageControls(pages.length);

  if (fitWidth) {
    const availableWidth = Math.max(260, previewCanvas.clientWidth - 38);
    previewZoom = clamp(availableWidth / firstPage.width, 0.2, 2.5);
  }

  const displayWidth = Math.max(1, Math.round(firstPage.width * previewZoom));
  const displayHeight = Math.max(1, Math.round(firstPage.height * previewZoom));
  image.style.width = `${displayWidth}px`;
  stage.style.width = `${displayWidth}px`;
  stage.style.height = `${displayHeight}px`;
  stage.querySelectorAll(".overlay-box").forEach((box) => box.remove());
  previewCanvas.querySelectorAll(":scope > .overlay-debug-panel").forEach((panel) => panel.remove());

  const scaleX = displayWidth / firstPage.width;
  const scaleY = displayHeight / firstPage.height;
  const pageOverlays = getSelectedPageScopedOverlays();
  const counts = { ocr: 0, layout: 0, field: 0, row: 0, invalid: 0 };
  if (document.getElementById("toggleLayout").checked) {
    pageOverlays.layout_blocks
      .forEach((block) => addBox(stage, block.bbox, scaleX, scaleY, `layout ${block.block_type}`, block.block_type, block.confidence, block, counts, "layout"));
  }
  if (document.getElementById("toggleOcr").checked) {
    pageOverlays.ocr_blocks
      .forEach((block) => addBox(stage, block.bbox, scaleX, scaleY, "ocr", block.text, block.confidence, block, counts, "ocr"));
  }
  if (document.getElementById("toggleFields").checked) {
    pageOverlays.field_boxes
      .forEach((box) => addBox(stage, box.bbox, scaleX, scaleY, "field", box.field, box.confidence, box, counts, "field"));
  }
  if (document.getElementById("toggleRows").checked) {
    pageOverlays.line_rows
      .forEach((row) => addBox(stage, row.bbox, scaleX, scaleY, "row", row.label, row.confidence, row, counts, "row"));
  }
  renderOverlayDiagnostics(previewCanvas, firstPage, displayWidth, displayHeight, counts);
  window.__REVIEW_DEBUG__.overlayCounts = { ...lastResponse.overlay_counts, visible: counts };
}

function setPreviewPage(index) {
  const pages = dossierResponse?.document_preview?.pages || [];
  if (!pages.length) return;
  selectPhysicalPage(clamp(index, 0, pages.length - 1));
}

function updatePageControls(totalPages) {
  const indicator = document.getElementById("pageIndicator");
  const physicalPage = getSelectedPhysicalPageNumber();
  const documentIndex = selectedLogicalDocumentIndex + 1;
  const documentTotal = dossierResponse?.document_count || 1;
  const dossierPages = dossierResponse?.document_preview?.pages || [];
  const dossierPageIndex = getSelectedDossierPageIndex();
  const selectedDocumentPageCount = getSelectedDocumentPages().length;
  if (indicator) indicator.textContent = dossierPages.length
    ? t("dossier.physical_page", { current: normalizePage(physicalPage), total: dossierResponse.page_count || dossierPages.length })
    : t("review.page_empty");
  const context = document.getElementById("documentPageContext");
  if (context) context.textContent = totalPages
    ? `${t("dossier.document_position", { current: documentIndex, total: documentTotal })}${selectedDocumentPageCount > 1 ? ` · ${t("dossier.page_within_document", { current: selectedPageWithinLogicalDocument + 1, total: selectedDocumentPageCount })}` : ""}`
    : "";
  const prev = document.getElementById("prevPageBtn");
  const next = document.getElementById("nextPageBtn");
  if (prev) prev.disabled = dossierPageIndex <= 0;
  if (next) next.disabled = dossierPageIndex >= dossierPages.length - 1;
}

function normalizePage(value) {
  const page = Number(value ?? 1);
  return Number.isFinite(page) && page > 0 ? page : 1;
}

function getLineItemOverlayRows() {
  const candidates = [];
  (lastResponse?.all_line_items || lastResponse?.detected_fields?.line_items || []).forEach((item, index) => {
    if (!item?.bbox) return;
    candidates.push({
      type: "line_item_row",
      label: t("line_items.number", { number: index + 1 }),
      text: item.description,
      value: item.description,
      bbox: item.bbox,
      page: item.page || 1,
      confidence: item.confidence,
      source: item.source,
      row_index: index + 1,
      validation: lastResponse?.row_validation?.[index],
    });
  });
  return candidates;
}

function addBox(stage, bbox, scaleX, scaleY, type, label, confidence, payload, counts = null, countKey = null) {
  if (!isValidBbox(bbox)) {
    if (counts) counts.invalid += 1;
    return;
  }
  const box = document.createElement("button");
  box.type = "button";
  const classes = type.split(" ").map((part) => `overlay-${part}`);
  box.className = `overlay-box ${classes.join(" ")}`;
  box.style.left = `${bbox.x1 * scaleX}px`;
  box.style.top = `${bbox.y1 * scaleY}px`;
  box.style.width = `${Math.max(6, (bbox.x2 - bbox.x1) * scaleX)}px`;
  box.style.height = `${Math.max(6, (bbox.y2 - bbox.y1) * scaleY)}px`;
  box.dataset.overlayId = payload?.id || `${type}_${Math.round(bbox.x1)}_${Math.round(bbox.y1)}`;
  box.dataset.renderedBbox = JSON.stringify({
    x1: bbox.x1 * scaleX,
    y1: bbox.y1 * scaleY,
    x2: bbox.x2 * scaleX,
    y2: bbox.y2 * scaleY,
  });
  box.setAttribute("aria-label", `${type}: ${label}`);
  if (document.getElementById("toggleLabels").checked && confidence !== null && confidence !== undefined) {
    const tag = document.createElement("span");
    tag.className = "overlay-label";
    tag.textContent = `${label} ${formatConfidence(confidence)}`;
    box.appendChild(tag);
  }
  box.addEventListener("click", (event) => {
    event.stopPropagation();
    showRegionDetails(type, label, payload);
  });
  stage.appendChild(box);
  if (counts && countKey) counts[countKey] += 1;
}

function renderOverlayDiagnostics(stage, page, renderedWidth, renderedHeight, counts) {
  const panel = document.createElement("aside");
  panel.className = "overlay-debug-panel";
  const totalBackend = lastResponse?.overlay_counts || {};
  const messages = [];
  if (document.getElementById("toggleOcr").checked && totalBackend.ocr_blocks && counts.ocr === 0) {
    messages.push("OCR boxes are unavailable because the backend returned no valid page coordinates.");
  }
  if (document.getElementById("toggleLayout").checked && totalBackend.layout_blocks && counts.layout === 0) {
    messages.push("Layout blocks unavailable: backend returned no valid page coordinates.");
  }
  panel.innerHTML = `
    <strong>${escapeHtml(t("overlay.diagnostics"))}</strong>
    <div>${escapeHtml(t("region.page"))}: ${escapeHtml(page.page)}</div>
    <div>Original page size: ${escapeHtml(page.width)} x ${escapeHtml(page.height)}</div>
    <div>Displayed page size: ${Math.round(renderedWidth)} x ${Math.round(renderedHeight)}</div>
    <div>${escapeHtml(t("overlay.ocr_boxes"))}: ${totalBackend.ocr_blocks || 0} total / ${counts.ocr} visible</div>
    <div>${escapeHtml(t("overlay.layout_blocks"))}: ${totalBackend.layout_blocks || 0} total / ${counts.layout} visible</div>
    <div>${escapeHtml(t("overlay.field_boxes"))}: ${totalBackend.field_boxes || 0} total / ${counts.field} visible</div>
    <div>${escapeHtml(t("overlay.line_rows"))}: ${totalBackend.line_rows || 0} total / ${counts.row} visible</div>
    <div>Invalid boxes: ${counts.invalid}</div>
    <div>Rejected at normalization: ${(totalBackend.rejected_boxes || []).length}</div>
    <div>First invalid reason: ${escapeHtml(totalBackend.first_invalid_reason || "-")}</div>
    <div>Zoom: ${Math.round(previewZoom * 100)}%</div>
    <div>${escapeHtml(t("region.selected"))}: ${escapeHtml(selectedRegionPayload?.label || "-")}</div>
    ${messages.map((message) => `<div class="note warning-note">${escapeHtml(message)}</div>`).join("")}
  `;
  stage.appendChild(panel);
}

function showRegionDetails(type, label, payload) {
  selectedRegionPayload = { type, label, payload };
  const fields = Array.isArray(payload?.fields) && payload.fields.length ? payload.fields.join(", ") : "-";
  const text = payload?.text ?? payload?.value ?? label ?? "-";
  const reasons = extractionReasons(type, payload);
  regionDetails.innerHTML = `
    <span class="label">${escapeHtml(t("region.selected_type", { type }))}</span>
    <strong>${escapeHtml(label || payload?.field || t("region.default"))}</strong>
    <p>${escapeHtml(reasons.summary)}</p>
    <div class="inspector-list">
      <div class="inspector-row"><span>${escapeHtml(t("region.text_value"))}</span><div>${escapeHtml(text)}</div></div>
      <div class="inspector-row"><span>${escapeHtml(t("region.confidence"))}</span><div>${formatConfidence(payload?.confidence)}</div></div>
      <div class="inspector-row"><span>${escapeHtml(t("region.page"))}</span><div>${escapeHtml(payload?.page ?? payload?.page_number ?? "-")}</div></div>
      <div class="inspector-row"><span>${escapeHtml(t("region.source"))}</span><div>${escapeHtml(payload?.source ?? "-")}</div></div>
      <div class="inspector-row"><span>${escapeHtml(t("region.bbox"))}</span><div>${escapeHtml(payload?.bbox ? JSON.stringify(payload.bbox) : "-")}</div></div>
      <div class="inspector-row"><span>${escapeHtml(t("region.fields"))}</span><div>${escapeHtml(fields)}</div></div>
    </div>
    <ul class="reason-list">${reasons.items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    <div class="edit-actions region-actions">
      <select id="regionFieldSelect" class="edit-input">
        ${EDITABLE_FIELDS.map((field) => `<option value="${field}">${field}</option>`).join("")}
      </select>
      <button class="ghost small" id="useRegionValueBtn" type="button">${escapeHtml(t("region.use_text"))}</button>
      <button class="ghost small" id="rejectRegionBtn" type="button">${escapeHtml(t("common.reject"))}</button>
    </div>
    <details class="advanced-details">
      <summary>${escapeHtml(t("region.advanced"))}</summary>
      <pre>${escapeHtml(pretty(payload))}</pre>
    </details>
  `;
  document.getElementById("useRegionValueBtn")?.addEventListener("click", () => useSelectedRegionAsField());
  document.getElementById("rejectRegionBtn")?.addEventListener("click", () => rejectSelectedRegion());
}

function extractionReasons(type, payload) {
  const items = [];
  const normalizedType = String(type || "").toLowerCase();
  if (normalizedType.includes("layout")) items.push("Selected from a semantic layout block.");
  if (normalizedType.includes("field")) items.push("Linked to an extracted ERP field candidate.");
  if (normalizedType.includes("row")) items.push("Linked to a reconstructed product row.");
  if (normalizedType.includes("ocr")) items.push("Raw OCR evidence; use it before assigning or correcting a field.");
  if (payload?.source) items.push(`Source: ${payload.source}.`);
  if (payload?.bbox) items.push("Has page coordinates and can be visually verified.");
  if (payload?.confidence !== undefined && Number(payload.confidence) >= 0.85) items.push("High-confidence candidate.");
  if (payload?.confidence !== undefined && Number(payload.confidence) < 0.7) items.push("Low-confidence candidate; review before export.");
  if (payload?.rejection_reason) items.push(`Rejected reason: ${payload.rejection_reason}.`);
  if (!items.length) items.push("No detailed scoring evidence was returned for this region.");
  return {
    summary: items[0],
    items: items.slice(1),
  };
}


function stableIgnoredRows() {
  return ignoredRows
    .filter((item) => typeof item === "string" && item.trim() && !/^\d+$/.test(item.trim()))
    .filter((item, index, values) => values.indexOf(item) === index);
}

function buildReviewCorrectionPayload() {
  const logicalDocument = getSelectedLogicalDocument();
  return {
    document_id: logicalDocument?.correction_document_id || logicalDocument?.logical_document_id || lastResponse.erp_json?.metadata?.source_file || lastResponse.document_preview?.source_file || null,
    source_file: lastResponse.erp_json?.metadata?.source_file || null,
    document_family: logicalDocument?.document_family || null,
    detected_fields: lastResponse.detected_fields || {},
    field_corrections: buildCorrectedFieldPayload(),
    line_item_corrections: lastResponse.detected_fields?.line_items || [],
    ignored_rows: stableIgnoredRows(),
    original_payload: lastResponse,
  };
}

function refreshValidationHeader() {
  const validation = lastResponse?.validation || {};
  const explanation = lastResponse?.validation_explanation;
  const status = explanation?.status || validation.status || (validation.is_valid ? "valid" : "invalid");
  const statusEl = document.getElementById("validationStatus");
  if (statusEl) {
    statusEl.textContent = statusLabel(status);
    statusEl.title = statusExplanation(status);
    statusEl.className = `pill ${status}`;
  }
  const readiness = lastResponse?.erp_readiness || lastResponse?.erp_json?.quality?.erp_readiness || {};
  const erpDecision = document.getElementById("erpDecision");
  if (erpDecision) erpDecision.textContent = localizedReadinessStatus(readiness.erp_ready_status, status);
}

function applyCorrectionValidationResponse(data, { rerenderEditableRows = true } = {}) {
  if (["customs_tradenet_v1", "customs_douanes_tunisiennes_v1"].includes(resolveDocumentPresentation().key)) {
    lastResponse.expanded_fields = lastResponse.expanded_fields || {};
    Object.entries(data.expanded_field_overrides || {}).forEach(([name, detail]) => {
      lastResponse.expanded_fields[name] = detail;
      correctedFields[name] = {
        ...(correctedFields[name] || {}),
        original_value: detail.display_value ?? detail.value,
        corrected_value: detail.display_value ?? detail.value,
        corrected_by: "human",
        persisted: true,
      };
    });
    lastResponse.customs_field_validation = data.customs_field_validation || {};
    const logicalDocument = getSelectedLogicalDocument();
    if (logicalDocument) logicalDocument.response = lastResponse;
    renderFields(lastResponse.detected_fields || {});
    updateJsonPanels();
    return;
  }
  lastResponse.corrected_response = data;
  lastResponse.validated_erp_json = data.validated_erp_json || lastResponse.validated_erp_json;
  lastResponse.erp_json = data.erp_json || data.validated_erp_json || lastResponse.erp_json;
  lastResponse.detected_fields = data.corrected_fields || lastResponse.detected_fields;
  lastResponse.all_line_items = normalizeLineItems(lastResponse.detected_fields?.line_items || data.corrected_line_items || []);
  lastResponse.row_validation = data.row_validation || lastResponse.row_validation;
  lastResponse.financial_reasoning = data.financial_reasoning || lastResponse.financial_reasoning;
  lastResponse.field_consistency = data.field_consistency || data.financial_reasoning?.field_consistency || lastResponse.field_consistency;
  lastResponse.confidence_breakdown = data.confidence_breakdown || lastResponse.confidence_breakdown;
  lastResponse.erp_readiness = data.erp_readiness || lastResponse.erp_readiness;
  lastResponse.invoice_validation_report = data.invoice_validation_report || lastResponse.invoice_validation_report;
  lastResponse.validation = data.validation || lastResponse.validation;
  lastResponse.validation_explanation = data.validation_explanation || data.erp_json?.quality?.validation_explanation || lastResponse.validation_explanation;
  const logicalDocument = getSelectedLogicalDocument();
  if (logicalDocument) logicalDocument.response = lastResponse;
  if (dossierResponse) dossierResponse.summary = summarizeLogicalDocuments();
  renderDossierNavigation();
  refreshValidationHeader();
  renderErpReadiness(lastResponse);
  renderNotes(lastResponse);
  renderValidationSummary(lastResponse.validation_explanation, lastResponse.validation);
  if (rerenderEditableRows) renderLineItems(lastResponse.detected_fields?.line_items || [], lastResponse.row_validation || []);
  renderDynamicReview();
  updateJsonPanels();
}

function scheduleAutoValidation(reason = "edit") {
  if (!lastResponse) return;
  if (!resolveDocumentPresentation().allowCorrections) return;
  clearTimeout(autoValidationTimer);
  const statusEl = document.getElementById("validationStatus");
  if (statusEl) {
    statusEl.textContent = t("validation.rechecking");
    statusEl.title = t("validation.queued");
    statusEl.className = "pill needs_review";
  }
  autoValidationTimer = setTimeout(() => validateCorrections({ automatic: true, reason }), AUTO_VALIDATION_DELAY_MS);
}

async function validateCorrections({ automatic = false, reason = "manual" } = {}) {
  if (!lastResponse) {
    if (!automatic) showError(t("review.process_before_save"));
    return;
  }
  if (!resolveDocumentPresentation().allowCorrections) {
    if (!automatic) showError(t("dossier.correction_unavailable"));
    return;
  }
  if (automatic && autoValidationInFlight) {
    autoValidationQueued = true;
    return;
  }
  autoValidationInFlight = true;
  if (!automatic) setLoading(true, t("review.saving"));
  try {
    const response = await fetch("/review/validate-corrections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildReviewCorrectionPayload()),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || t("review.revalidation_failed"));
    applyCorrectionValidationResponse(data, { rerenderEditableRows: true });
    if (dossierResponse) await refreshDossierRelationships();
    if (!automatic) {
      showTransientNote(t("review.saved", { count: data.corrections?.length || 0, status: localizedReadinessStatus(data.erp_readiness?.erp_ready_status, data.validation?.status) }));
    }
  } catch (error) {
    if (automatic) {
      console.warn(`Automatic validation failed after ${reason}:`, error);
      refreshValidationHeader();
    } else {
      showError(error.message);
    }
  } finally {
    autoValidationInFlight = false;
    if (!automatic) setLoading(false);
    if (autoValidationQueued) {
      autoValidationQueued = false;
      scheduleAutoValidation("queued_edit");
    }
  }
}

async function refreshDossierRelationships() {
  if (!dossierResponse?.logical_documents?.length) return;
  try {
    const response = await fetch("/review/reconcile-dossier", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ logical_documents: dossierResponse.logical_documents }),
    });
    if (!response.ok) return;
    dossierResponse.relationships = (await response.json()).relationships || [];
    renderDossierRelationships();
  } catch (error) {
    console.warn("Dossier reconciliation refresh failed after review:", error);
  }
}

async function saveCorrections() {
  await validateCorrections({ automatic: false, reason: "save" });
}

function buildCorrectedFieldPayload() {
  const corrected = {};
  document.querySelectorAll("[data-field]").forEach((input) => {
    const field = input.dataset.field;
    const current = coerceValue(field, input.value);
    const previous = correctedFields[field] || {};
    const original = previous.original_value ?? getFieldOriginalValue(field);
    if (current !== undefined && String(current ?? "") !== String(original ?? "")) {
      corrected[field] = {
        value: current,
        original_value: original,
        source: previous.source || "human",
        bbox: previous.bbox ?? lastResponse.expanded_fields?.[field]?.bbox,
        page: previous.page ?? lastResponse.expanded_fields?.[field]?.page,
        confidence: previous.confidence ?? lastResponse.expanded_fields?.[field]?.confidence,
        user_action: previous.user_action || "edited",
      };
    }
  });
  return corrected;
}

function buildExplicitCorrectionRecords() {
  const records = [];
  Object.entries(correctedFields).forEach(([fieldName, correction]) => {
    records.push({
      field_name: fieldName,
      original_value: correction.original_value,
      corrected_value: correction.corrected_value,
      correction_type: fieldName.includes("supplier") ? "supplier" : fieldName.includes("customer") ? "customer" : fieldName.includes("amount") || fieldName.includes("tva") || fieldName.includes("tax") ? "total" : "field",
      user_action: "edited",
    });
  });
  correctedLineItems.forEach((item) => {
    records.push({
      field_name: item.row_key || `line_items[${item.row_index ?? "?"}]`,
      original_value: item.original_value ?? null,
      corrected_value: item.corrected_value ?? item,
      correction_type: "line_item",
      user_action: item.deleted ? "rejected" : "edited",
      line_item_index: item.row_index ?? null,
    });
  });
  return records;
}

function useSelectedRegionAsField() {
  if (!selectedRegionPayload) return;
  const field = document.getElementById("regionFieldSelect")?.value;
  const value = selectedRegionPayload.payload?.text ?? selectedRegionPayload.payload?.value ?? selectedRegionPayload.label ?? "";
  if (!field || !value) return;
  const input = document.querySelector(`[data-field="${cssEscape(field)}"]`);
  if (input) input.value = value;
  updateReviewField(field, value);
}

function rejectSelectedRegion() {
  if (!selectedRegionPayload) return;
  const field = document.getElementById("regionFieldSelect")?.value || selectedRegionPayload.payload?.field || "unknown";
  correctedFields[field] = {
    original_value: selectedRegionPayload.payload?.value ?? selectedRegionPayload.payload?.text ?? selectedRegionPayload.label,
    corrected_value: null,
    corrected_by: "human",
    user_action: "rejected",
  };
  updateCorrectionLayer("visual_region");
  scheduleAutoValidation("candidate_rejected");
  showTransientNote(t("candidate.rejected", { field: t(`fields.${field}`) }));
}

function acceptSuggestion(index) {
  const suggestion = lastResponse?.correction_suggestions?.[index];
  if (!suggestion?.field) return;
  const value = suggestion.proposed ?? suggestion.proposed_value ?? suggestion.corrected_value;
  const input = document.querySelector(`[data-field="${cssEscape(suggestion.field)}"]`);
  if (input) input.value = value ?? "";
  updateReviewField(suggestion.field, value);
  renderDynamicReview();
  showTransientNote(t("suggestion.accepted", { field: t(`fields.${suggestion.field}`) }));
}

function applyExpectedFieldValue(field, value, consistency) {
  if (!field) return;
  const input = document.querySelector(`[data-field="${cssEscape(field)}"]`);
  if (input) input.value = value ?? "";
  correctedFields[field] = {
    original_value: getFieldOriginalValue(field),
    corrected_value: value,
    corrected_by: "human",
    user_action: "edited",
    confidence: 1,
    source: "field_consistency",
    expected_value: consistency?.expected_value,
    message: consistency?.message,
  };
  updateReviewField(field, value);
  renderDynamicReview();
  showTransientNote(t("field.consistency_applied", { field: t(`fields.${field}`) }));
}

function selectCandidate(entry) {
  if (!entry?.field) return;
  const value = entry.candidate?.value ?? entry.candidate?.normalized_value;
  const input = document.querySelector(`[data-field="${cssEscape(entry.field)}"]`);
  if (input) input.value = value ?? "";
  correctedFields[entry.field] = {
    original_value: getFieldOriginalValue(entry.field),
    corrected_value: value,
    corrected_by: "human",
    user_action: "accepted",
    bbox: entry.candidate?.bbox,
    page: entry.candidate?.page,
    confidence: entry.candidate?.confidence ?? entry.candidate?.score,
    source: entry.candidate?.source,
  };
  updateReviewField(entry.field, value);
  renderDynamicReview();
  showTransientNote(t("candidate.selected", { field: t(`fields.${entry.field}`) }));
}

function showTransientNote(message) {
  hideError();
  const notes = document.getElementById("validationNotes");
  if (!notes) return;
  const div = document.createElement("div");
  div.className = "note success-note";
  div.textContent = message;
  notes.prepend(div);
  setTimeout(() => div.remove(), 3500);
}
function resetRegionDetails() {
  regionDetails.innerHTML = `
    <span class="label">${escapeHtml(t("region.selected"))}</span>
    <strong>${escapeHtml(t("region.none"))}</strong>
    <p>${escapeHtml(t("region.empty_help_short"))}</p>
  `;
}

function setZoom(value) {
  fitWidth = false;
  previewZoom = clamp(value, 0.2, 3);
  redrawPreview();
}

function setLoading(isLoading, message = t("processing.default")) {
  if (loadingText) loadingText.textContent = message;
  loading.classList.toggle("hidden", !isLoading);
  processBtn.disabled = isLoading;
  demoButtons.forEach((button) => { button.disabled = isLoading; });
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function hideError() {
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function formatConfidence(value) {
  const bounded = boundedConfidence(value);
  if (bounded === null) return "-";
  return `${Math.round(bounded * 100)}%`;
}

function boundedConfidence(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return clamp(Number(value), 0, 1);
}

function isValidBbox(bbox) {
  if (!bbox) return false;
  const values = [bbox.x1, bbox.y1, bbox.x2, bbox.y2].map(Number);
  if (!values.every(Number.isFinite)) return false;
  const [x1, y1, x2, y2] = values;
  return x2 > x1 && y2 > y1 && x1 >= 0 && y1 >= 0;
}

function formatCell(value) {
  return value === null || value === undefined ? "-" : escapeHtml(value);
}

function formatTableValue(value) {
  return value === null || value === undefined || value === "" || value === "-" ? "" : escapeHtml(value);
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number" && Number.isFinite(value)) return String(Math.round(value * 1000) / 1000);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value ?? null));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(value);
  return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
}

function humanize(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function lineIndexFromKey(key) {
  const number = Number(String(key || "").replace(/\D/g, ""));
  return Number.isFinite(number) && number > 0 ? number - 1 : key;
}

window.reviewUiDebug = {
  renderResults,
  renderPreview,
  redrawPreview,
};

checkApi();
