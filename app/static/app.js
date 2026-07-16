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

let selectedFile = null;
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
  fileName.textContent = selectedFile ? selectedFile.name : "No file selected";
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
    showError("Camera is not available in this browser. Try HTTPS or localhost.");
    return;
  }
  hideError();
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    cameraVideo.srcObject = cameraStream;
    cameraModal.classList.remove("hidden");
  } catch (error) {
    showError(`Camera could not be opened: ${error.message}`);
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
    showError("Camera is still loading. Try again in a second.");
    return;
  }
  cameraCanvas.width = cameraVideo.videoWidth;
  cameraCanvas.height = cameraVideo.videoHeight;
  const context = cameraCanvas.getContext("2d");
  context.drawImage(cameraVideo, 0, 0, cameraCanvas.width, cameraCanvas.height);
  cameraCanvas.toBlob((blob) => {
    if (!blob) {
      showError("Could not capture the image.");
      return;
    }
    const timestamp = new Date().toISOString().replaceAll(":", "-").slice(0, 19);
    selectedFile = new File([blob], `camera-invoice-${timestamp}.png`, { type: "image/png" });
    fileName.textContent = `${selectedFile.name} (camera capture)`;
    resetCorrections();
    closeCamera();
  }, "image/png", 0.95);
}
processBtn.addEventListener("click", () => processUploadedFile());

async function processUploadedFile() {
  if (!selectedFile) {
    showError("Choose a document first.");
    return;
  }

  const formData = new FormData();
  formData.append("file", selectedFile);
  setLoading(true, "Running OCR, layout analysis, candidate extraction, and validation...");
  hideError();
  results.classList.add("hidden");

  try {
    const response = await fetch("/process-invoice", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Processing failed.");
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
  setLoading(true, `Loading demo: ${label || demoId}. Running the normal OCR pipeline...`);
  hideError();
  results.classList.add("hidden");
  fileName.textContent = `${label || demoId} (demo document)`;
  resetCorrections();
  try {
    const response = await fetch(`/demo-documents/${encodeURIComponent(demoId)}/process`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Demo processing failed.");
    renderResults(data);
  } catch (error) {
    showError(`${error.message} If this is the first run, confirm OCR dependencies are installed.`);
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
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = "Copy"; }, 1000);
  });
});

async function checkApi() {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    document.getElementById("apiStatus").textContent = data.status === "ok" ? "Online" : "Unknown";
  } catch {
    document.getElementById("apiStatus").textContent = "Offline";
  }
}

function renderResults(data) {
  resetCorrections();
  const normalized = normalizeReviewResponse(data);
  lastResponse = normalized;
  window.__REVIEW_DEBUG__ = {
    rawResponse: data,
    normalizedResponse: normalized,
    overlayCounts: normalized.overlay_counts,
    renderErrors: [],
  };
  currentPageIndex = 0;
  const validation = normalized.validation || {};
  const status = validation.status || (validation.is_valid ? "valid" : "invalid");
  const classification = normalized.document_classification || {};
  const fields = normalized.detected_fields || {};
  const readiness = normalized.erp_readiness || normalized.erp_json?.quality?.erp_readiness || {};
  const confidence = normalized.confidence_breakdown?.overall_confidence ?? normalized.erp_json?.quality?.overall_confidence ?? normalized.erp_json?.metadata?.confidence;
  const confidenceLabel = normalized.confidence_breakdown?.display_name || normalized.erp_json?.quality?.confidence_display_name || "Composite Confidence Index";
  const displayItems = normalized.all_line_items?.length ? normalized.all_line_items : fields.line_items || [];
  originalLineItemsSnapshot = deepClone(displayItems);

  const statusEl = document.getElementById("validationStatus");
  statusEl.textContent = statusLabel(status);
  statusEl.title = statusExplanation(status);
  statusEl.className = `pill ${status}`;
  document.getElementById("documentType").textContent = classification.document_type || "-";
  document.querySelector("#ocrConfidence")?.closest(".summary-card")?.querySelector(".label")?.replaceChildren(document.createTextNode(confidenceLabel));
  document.getElementById("ocrConfidence").textContent = formatConfidence(confidence);
  document.getElementById("erpDecision").textContent = readiness.erp_ready_status || (status === "valid" ? "ERP Ready" : "Needs Review");

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
  if (target) target.innerHTML = `<div class="note error-note">${escapeHtml(section)} render error: ${escapeHtml(error.message)}</div>`;
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
  EDITABLE_FIELDS.forEach((field) => {
    const key = document.createElement("div");
    key.textContent = field;
    const value = document.createElement("div");
    value.className = "field-review-cell";
    const input = document.createElement("input");
    input.className = "edit-input";
    input.dataset.field = field;
    input.value = fields[field] ?? "";
    input.placeholder = "-";
    input.addEventListener("input", () => updateReviewField(field, input.value));
    value.appendChild(input);
    value.appendChild(renderFieldCandidateFallback(field, fields[field]));
    table.append(key, value);
  });
}

function renderFieldCandidateFallback(field, selectedValue) {
  const wrapper = document.createElement("div");
  wrapper.className = "candidate-fallback";
  const candidates = [
    ...(lastResponse?.review_candidates?.[field] || []),
    ...(lastResponse?.rejected_candidates?.[field] || []),
  ].filter((candidate) => candidate?.value !== null && candidate?.value !== undefined && candidate?.value !== "");
  if (selectedValue !== null && selectedValue !== undefined && selectedValue !== "") {
    wrapper.innerHTML = '<span class="candidate-state confirmed">Confirmed</span>';
    return wrapper;
  }
  if (!candidates.length) {
    wrapper.innerHTML = '<span class="candidate-state missing">Not extracted.</span>';
    return wrapper;
  }
  const best = candidates[0];
  const alternatives = candidates.slice(1, 4);
  wrapper.innerHTML = `
    <div class="candidate-state review">Not confirmed</div>
    <div class="candidate-option">
      <strong>Candidate:</strong>
      <span>${escapeHtml(displayValue(best.value ?? best.normalized_value))}</span>
      <small>${escapeHtml(best.source || "candidate")} - ${formatConfidence(best.confidence ?? best.score)}</small>
      <button class="ghost small" type="button" data-field-candidate="${escapeAttribute(field)}" data-candidate-index="0">Select</button>
    </div>
    ${alternatives.map((candidate, index) => `
      <div class="candidate-option alternative">
        <strong>Alternative:</strong>
        <span>${escapeHtml(displayValue(candidate.value ?? candidate.normalized_value))}</span>
        <small>${escapeHtml(candidate.source || "candidate")} - ${formatConfidence(candidate.confidence ?? candidate.score)}</small>
        <button class="ghost small" type="button" data-field-candidate="${escapeAttribute(field)}" data-candidate-index="${index + 1}">Select</button>
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
    ...(validation.errors || []).map((message) => ({ type: "Error", message, className: "error-note" })),
    ...(validation.warnings || []).map((message) => ({ type: "Warning", message, className: "warning-note" })),
  ];
  notes.innerHTML = "";
  if (explanation?.reason) {
    const reason = document.createElement("div");
    reason.className = `note ${explanation.status === "valid" ? "success-note" : "warning-note"}`;
    reason.textContent = explanation.reason;
    notes.appendChild(reason);
  }
  if (!items.length && !explanation?.reason) {
    notes.innerHTML = '<div class="note success-note">No validation issues detected.</div>';
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
  const reason = explanation?.reason || "No validation explanation returned.";
  const action = explanation?.suggested_action || "Review low-confidence or missing fields before ERP export.";
  const statusText = statusExplanation(status);
  validationSummary.innerHTML = `
    <span class="label">Validation explanation</span>
    <strong>${escapeHtml(statusLabel(status))}</strong>
    <p>${escapeHtml(statusText)}</p>
    <div class="inspector-list">
      <div class="inspector-row"><span>Reason</span><div>${escapeHtml(reason)}</div></div>
      <div class="inspector-row"><span>Action</span><div>${escapeHtml(action)}</div></div>
      <div class="inspector-row"><span>Errors</span><div>${escapeHtml(String(explanation?.blocking_errors?.length ?? validation?.errors?.length ?? 0))}</div></div>
      <div class="inspector-row"><span>Warnings</span><div>${escapeHtml(String(explanation?.warnings?.length ?? validation?.warnings?.length ?? 0))}</div></div>
    </div>
  `;
}

function statusLabel(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized.includes("valid") && !normalized.includes("invalid")) return "[OK] Validated";
  if (normalized.includes("review")) return "[!] Needs review";
  if (normalized.includes("invalid") || normalized.includes("reject")) return "[X] Invalid";
  if (normalized.includes("manual")) return "[EDIT] Manually corrected";
  return status || "-";
}

function statusExplanation(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized.includes("valid") && !normalized.includes("invalid")) return "Required values are present and the available business checks passed.";
  if (normalized.includes("review")) return "Some values are missing, uncertain, or need human confirmation before ERP export.";
  if (normalized.includes("invalid") || normalized.includes("reject")) return "A blocking extraction or business-rule issue prevents ERP insertion.";
  if (normalized.includes("manual")) return "A reviewer changed this value; save corrections to re-run validation.";
  if (normalized.includes("low")) return "The value was detected with weak OCR or layout evidence.";
  if (normalized.includes("missing")) return "The value is required but was not found confidently.";
  if (normalized.includes("conflict")) return "Two or more detected values disagree and need review.";
  return "Review the extracted evidence before exporting to ERP.";
}

function renderErpReadiness(data) {
  const panel = document.getElementById("erpReadinessPanel");
  if (!panel) return;
  const readiness = data.erp_readiness || data.erp_json?.quality?.erp_readiness || {};
  const status = readiness.erp_ready_status || "Needs Review";
  const score = Number(readiness.erp_ready_score ?? 0);
  const blockers = readiness.blocking_errors || [];
  const missing = readiness.missing_fields || [];
  const disabledReasons = [...blockers, ...missing.map((field) => `${field} is missing`)];
  const className = status === "ERP Ready" ? "ready" : status === "Rejected" ? "rejected" : "review";
  const nextAction = readiness.ready
    ? "Next action: export the validated ERP JSON or keep reviewing the evidence."
    : disabledReasons.length
      ? `Next action: fix ${disabledReasons[0]}${disabledReasons.length > 1 ? ` and ${disabledReasons.length - 1} more issue(s)` : ""}, then save corrections.`
      : "Next action: review low-confidence fields, product rows, and financial checks before export.";
  panel.className = `inspector-card readiness-card ${className}`;
  panel.innerHTML = `
    <span class="label">ERP readiness</span>
    <strong>${escapeHtml(status)}</strong>
    <p>${escapeHtml(statusExplanation(status))}</p>
    <div class="readiness-score">
      <span>${Math.round(score * 100)}%</span>
      <div class="score-bar"><span style="width:${Math.round(score * 100)}%"></span></div>
    </div>
    ${disabledReasons.length ? `<div class="business-list">${disabledReasons.map((reason) => `<div class="note warning-note">${escapeHtml(reason)}</div>`).join("")}</div>` : '<div class="note success-note">All ERP blockers are cleared.</div>'}
    <div class="note">${escapeHtml(nextAction)}</div>
    <button id="erpExportBtn" class="export-button" type="button" ${readiness.ready ? "" : "disabled"} title="${escapeAttribute(disabledReasons.join("; ") || "ERP export is ready")}">Export ERP JSON</button>
  `;
  panel.querySelector("#erpExportBtn")?.addEventListener("click", () => {
    navigator.clipboard?.writeText(pretty(data.validated_erp_json || data.erp_json || {}));
    showTransientNote("ERP JSON copied for export.");
  });
}

function renderConfidences(confidences) {
  const list = document.getElementById("confidenceList");
  list.innerHTML = "";
  const entries = Object.entries(confidences);
  if (!entries.length) {
    list.innerHTML = '<span class="chip">No field confidence data</span>';
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
  const editableItems = items || [];
  if (lastResponse) {
    lastResponse.detected_fields = lastResponse.detected_fields || {};
    lastResponse.detected_fields.line_items = editableItems;
  }
  const rows = editableItems.map((item, index) => editableLineItemRow(item, index, rowValidation[index])).join("");
  box.innerHTML = `
    <div class="panel-head">
      <p class="panel-subtitle">Edit extracted product lines before using the ERP JSON.</p>
      <div class="edit-actions">
        <button class="ghost small" id="addLineItemBtn" type="button">Add line</button>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Description</th><th>Quantity</th><th>Unit</th><th>Unit price</th>
          <th>Total HT</th><th>Tax %</th><th>Total TTC</th><th>Status</th><th>Actions</th>
        </tr>
      </thead>
      <tbody>${rows || '<tr><td colspan="9"><div class="note">No line items parsed. Add one manually if needed.</div></td></tr>'}</tbody>
    </table>
  `;
  box.querySelector("#addLineItemBtn")?.addEventListener("click", () => addReviewLineItem());
  box.querySelectorAll("[data-line-field]").forEach((input) => {
    input.addEventListener("input", () => updateReviewLineItem(Number(input.dataset.index), input.dataset.lineField, input.value));
  });
  box.querySelectorAll("[data-delete-line]").forEach((button) => {
    button.addEventListener("click", () => deleteReviewLineItem(Number(button.dataset.index)));
  });
  box.querySelectorAll("[data-restore-line]").forEach((button) => {
    button.addEventListener("click", () => restoreReviewLineItem(Number(button.dataset.index)));
  });
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
  return `<tr class="${escapeAttribute(status)}" data-line-row="${index + 1}">${cells}<td><span class="status-chip ${escapeAttribute(status)}" title="${escapeAttribute(reason || statusExplanation(status))}">${escapeHtml(status)}</span></td><td><div class="dynamic-actions"><button class="ghost small" type="button" data-restore-line data-index="${index}">Restore</button><button class="ghost small" type="button" data-delete-line data-index="${index}">Delete</button></div></td></tr>`;
}
function resetCorrections() {
  correctedFields = {};
  correctedLineItems = [];
  ignoredRows = [];
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
}

function getFieldOriginalValue(field) {
  return lastResponse?.expanded_fields?.[field]?.value ?? lastResponse?.detected_fields?.[field] ?? null;
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
  if (!lastResponse.expanded_fields?.[field]) return;
  lastResponse.expanded_fields[field].value = value;
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
  syncLineItemsToResponse();
  correctedLineItems.push({ row_index: index, field, corrected_value: value, corrected_by: "human" });
  updateCorrectionLayer("line_items");
  updateJsonPanels();
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
  items.splice(index, 1);
  syncLineItemsToResponse();
  ignoredRows.push(index);
  correctedLineItems.push({ row_index: index, deleted: true, corrected_by: "human" });
  renderLineItems(items);
  renderDynamicReview();
  updateCorrectionLayer("line_items");
  updateJsonPanels();
}

function restoreReviewLineItem(index) {
  const original = originalLineItemsSnapshot[index];
  if (!original) {
    showTransientNote("No original row is available for restore.");
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
  showTransientNote(`Restored line ${index + 1} to the original extraction.`);
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
    label: `Line ${index + 1}`,
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
    host.innerHTML = '<div class="note">Process a document to see dynamic extraction tables.</div>';
    return;
  }
  if (activeDynamicTab === "visual") {
    host.innerHTML = '<div class="note">Use the visual review panel above to inspect OCR boxes, layout blocks, and field boxes on the invoice preview.</div>';
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
    host.innerHTML = '<div class="note">No dynamic table data returned for this view.</div>';
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
    const label = check.passed ? "Passed" : hasActual ? "Conflict" : "Warning";
    const action = check.passed
      ? "No action needed."
      : hasActual
        ? "Compare the document totals and correct the conflicting amount before export."
        : "Find or enter the missing amount, then save corrections.";
    return `
      <article class="business-item ${status}">
        <header><strong>${escapeHtml(humanize(name))}</strong><span class="status-chip ${status === "pass" ? "validated" : status === "warn" ? "needs_review" : "conflict"}">${label}</span></header>
        <div class="check-grid">
          <span>Expected</span><strong>${escapeHtml(displayValue(check.expected))}</strong>
          <span>Extracted</span><strong>${escapeHtml(displayValue(check.actual))}</strong>
          <span>Difference</span><strong>${escapeHtml(displayValue(check.delta))}</strong>
          <span>Tolerance</span><strong>${escapeHtml(displayValue(reasoning.tolerance))}</strong>
        </div>
        <div class="note">${escapeHtml(action)}</div>
      </article>
    `;
  }).join("");
  host.innerHTML = `
    <div class="business-list">
      ${rows || '<div class="note warning-note">No complete financial checks could be run yet.</div>'}
      ${errors.map((message) => `<div class="note error-note">${escapeHtml(message)}</div>`).join("")}
      ${warnings.map((message) => `<div class="note warning-note">${escapeHtml(message)}</div>`).join("")}
    </div>
  `;
}

function renderCorrectionSuggestions(host) {
  const suggestions = lastResponse.correction_suggestions || [];
  const reviewCandidates = lastResponse.review_candidates || {};
  const candidateCards = Object.entries(reviewCandidates).flatMap(([field, candidates]) => (candidates || []).map((candidate) => ({ field, candidate })));
  host.innerHTML = `
    <div class="candidate-list">
      ${suggestions.length ? suggestions.map((suggestion, index) => `
        <article class="candidate-card">
          <header><strong>${escapeHtml(suggestion.field || "Suggestion")}</strong><span>${formatConfidence(suggestion.confidence)}</span></header>
          <div>Original: ${escapeHtml(suggestion.original ?? "-")}</div>
          <div>Proposed: ${escapeHtml(suggestion.proposed ?? suggestion.proposed_value ?? "-")}</div>
          <div>Reason: ${escapeHtml(suggestion.reason ?? "-")}</div>
          <div class="edit-actions">
            <button class="ghost small" type="button" data-accept-suggestion="${index}">Accept</button>
            <button class="ghost small" type="button" data-reject-suggestion="${index}">Reject</button>
          </div>
        </article>
      `).join("") : '<div class="note success-note">No automatic correction suggestions.</div>'}
      ${candidateCards.length ? `<h2>Field candidates</h2>${candidateCards.map(({ field, candidate }, index) => `
        <article class="candidate-card">
          <header><strong>${escapeHtml(field)}</strong><span>${formatConfidence(candidate.confidence ?? candidate.score)}</span></header>
          <div>Value: ${escapeHtml(candidate.value ?? "-")}</div>
          <div>Source: ${escapeHtml(candidate.source ?? "-")}</div>
          <div>Evidence: ${escapeHtml(candidate.evidence_text ?? "-")}</div>
          <div>Rejected: ${candidate.rejected ? escapeHtml(candidate.rejection_reason || "yes") : "no"}</div>
          <button class="ghost small" type="button" data-select-candidate="${index}">Use candidate</button>
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
      showTransientNote("Suggestion rejected for this review session.");
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
        <strong>Duplicate check</strong>
        <div>Possible duplicate: ${duplicate.possible_duplicate ? "yes" : "no"}</div>
        <pre>${escapeHtml(pretty(duplicate))}</pre>
      </article>
      <article class="business-item ${indicators.length ? "warn" : "pass"}">
        <strong>Automated risk indicators</strong>
        ${indicators.length ? indicators.map((item) => `<div class="note warning-note">${escapeHtml(item)}</div>`).join("") : '<div class="note success-note">No risk indicators returned.</div>'}
        <div class="note">${escapeHtml(fraud.disclaimer || "These are automated risk indicators, not a fraud determination.")}</div>
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
        <span class="dynamic-table-meta">${rows.length} rows - confidence ${confidence}</span>
      </div>
      ${table.id === "line_items" ? '<button class="ghost small" type="button" data-add-line>Add row</button>' : ""}
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
    wrapper.innerHTML = '<div class="note">No rows in this table for the current filter.</div>';
    return wrapper;
  }
  rows.forEach((row) => {
    const div = document.createElement("div");
    div.className = `dynamic-row ${row.status || "ok"}`;
    div.dataset.tableId = table.id;
    div.dataset.rowKey = row.key || "";
    div.innerHTML = `
      <strong>${escapeHtml(row.label || row.key || "Row")}</strong>
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
    wrapper.innerHTML = '<div class="note">No rows in this table for the current filter.</div>';
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
      ? '<td class="dynamic-actions"><button class="ghost small" type="button" data-restore-row>Restore</button><button class="ghost small" type="button" data-ignore-row>Ignore</button><button class="ghost small" type="button" data-delete-row>Delete</button></td>'
      : "";
    return `<tr class="${escapeAttribute(row.status || "ok")}" data-row-key="${escapeAttribute(row.key || "")}">${cells}${actions}</tr>`;
  }).join("");
  wrapper.innerHTML = `
    <table>
      <thead><tr>${headers}${table.id === "line_items" ? "<th>Actions</th>" : ""}</tr></thead>
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
      ignoredRows.push(row.key, lineIndexFromKey(row.key));
      correctedLineItems.push({ row_key: row.key, status: "ignored" });
      renderDynamicReview();
    });
    tr?.querySelector("[data-restore-row]")?.addEventListener("click", (event) => {
      event.stopPropagation();
      restoreReviewLineItem(lineIndexFromKey(row.key));
    });
    tr?.querySelector("[data-delete-row]")?.addEventListener("click", (event) => {
      event.stopPropagation();
      row.status = "ignored";
      ignoredRows.push(row.key, lineIndexFromKey(row.key));
      correctedLineItems.push({ row_key: row.key, deleted: true });
      const tableData = (lastResponse.dynamic_tables || []).find((item) => item.id === table.id);
      if (tableData) tableData.rows = tableData.rows.filter((item) => item.key !== row.key);
      renderDynamicReview();
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
  element.querySelector(".status-chip").textContent = "manually_corrected";
  element.querySelector(".status-chip").className = "status-chip manually_corrected";
  updateCorrectionLayer(tableId);
  updateJsonPanels();
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
}

function addDynamicLineItem(table) {
  const rowNumber = (table.rows || []).length + 1;
  const newRow = {
    key: `manual_line_item_${Date.now()}`,
    label: `Line ${rowNumber}`,
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
      currentPageIndex = targetIndex;
      renderPreview(lastResponse);
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
    previewCanvas.innerHTML = '<div class="note">No preview available.</div>';
    return;
  }

  currentPageIndex = Math.max(0, Math.min(currentPageIndex, pages.length - 1));
  const imageUrl = new URL(page.url, window.location.origin).href;
  previewCanvas.innerHTML = `
    <div class="preview-toolbar">
      <a class="preview-link" href="${escapeAttribute(imageUrl)}" target="_blank" rel="noopener">Open preview</a>
    </div>
    <div class="preview-stage" id="previewStage">
      <img id="previewImage" src="${escapeAttribute(imageUrl)}" alt="Document preview">
    </div>
  `;
  resetRegionDetails();
  const image = document.getElementById("previewImage");
  image.addEventListener("load", () => redrawPreview());
  image.addEventListener("error", () => {
    previewCanvas.innerHTML = '<div class="note error-note">Preview image could not be loaded from the API response.</div>';
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
  stage.querySelectorAll(".overlay-box, .overlay-debug-panel").forEach((box) => box.remove());

  const scaleX = displayWidth / firstPage.width;
  const scaleY = displayHeight / firstPage.height;
  const counts = { ocr: 0, layout: 0, field: 0, row: 0, invalid: 0 };
  if (document.getElementById("toggleLayout").checked) {
    (lastResponse.layout_blocks || [])
      .filter((block) => normalizePage(block.page) === firstPage.page)
      .forEach((block) => addBox(stage, block.bbox, scaleX, scaleY, `layout ${block.block_type}`, block.block_type, block.confidence, block, counts, "layout"));
  }
  if (document.getElementById("toggleOcr").checked) {
    (lastResponse.ocr_blocks || [])
      .filter((block) => normalizePage(block.page_number) === firstPage.page)
      .forEach((block) => addBox(stage, block.bbox, scaleX, scaleY, "ocr", block.text, block.confidence, block, counts, "ocr"));
  }
  if (document.getElementById("toggleFields").checked) {
    (lastResponse.field_boxes || [])
      .filter((box) => normalizePage(box.page) === firstPage.page)
      .forEach((box) => addBox(stage, box.bbox, scaleX, scaleY, "field", box.field, box.confidence, box, counts, "field"));
  }
  if (document.getElementById("toggleRows").checked) {
    getLineItemOverlayRows()
      .filter((row) => normalizePage(row.page) === firstPage.page)
      .forEach((row) => addBox(stage, row.bbox, scaleX, scaleY, "row", row.label, row.confidence, row, counts, "row"));
  }
  renderOverlayDiagnostics(stage, firstPage, displayWidth, displayHeight, counts);
  window.__REVIEW_DEBUG__.overlayCounts = { ...lastResponse.overlay_counts, visible: counts };
}

function setPreviewPage(index) {
  const pages = lastResponse?.document_preview?.pages || [];
  if (!pages.length) return;
  currentPageIndex = clamp(index, 0, pages.length - 1);
  renderPreview(lastResponse);
}

function updatePageControls(totalPages) {
  const indicator = document.getElementById("pageIndicator");
  if (indicator) indicator.textContent = totalPages ? `Page ${currentPageIndex + 1} / ${totalPages}` : "Page - / -";
  const prev = document.getElementById("prevPageBtn");
  const next = document.getElementById("nextPageBtn");
  if (prev) prev.disabled = currentPageIndex <= 0;
  if (next) next.disabled = currentPageIndex >= totalPages - 1;
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
      label: `Line ${index + 1}`,
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
    messages.push("OCR boxes unavailable: backend returned no valid page coordinates.");
  }
  if (document.getElementById("toggleLayout").checked && totalBackend.layout_blocks && counts.layout === 0) {
    messages.push("Layout blocks unavailable: backend returned no valid page coordinates.");
  }
  panel.innerHTML = `
    <strong>Overlay diagnostics</strong>
    <div>Page: ${escapeHtml(page.page)}</div>
    <div>Preview natural size: ${escapeHtml(page.width)} x ${escapeHtml(page.height)}</div>
    <div>Preview rendered size: ${Math.round(renderedWidth)} x ${Math.round(renderedHeight)}</div>
    <div>OCR boxes: ${totalBackend.ocr_blocks || 0} total / ${counts.ocr} visible</div>
    <div>Layout blocks: ${totalBackend.layout_blocks || 0} total / ${counts.layout} visible</div>
    <div>Field boxes: ${totalBackend.field_boxes || 0} total / ${counts.field} visible</div>
    <div>Line rows: ${totalBackend.line_rows || 0} total / ${counts.row} visible</div>
    <div>Invalid boxes: ${counts.invalid}</div>
    <div>Rejected at normalization: ${(totalBackend.rejected_boxes || []).length}</div>
    <div>First invalid reason: ${escapeHtml(totalBackend.first_invalid_reason || "-")}</div>
    <div>Current zoom: ${Math.round(previewZoom * 100)}%</div>
    <div>Selected region: ${escapeHtml(selectedRegionPayload?.label || "-")}</div>
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
    <span class="label">Selected ${escapeHtml(type)}</span>
    <strong>${escapeHtml(label || payload?.field || "Region")}</strong>
    <p>${escapeHtml(reasons.summary)}</p>
    <div class="inspector-list">
      <div class="inspector-row"><span>Text/value</span><div>${escapeHtml(text)}</div></div>
      <div class="inspector-row"><span>Confidence</span><div>${formatConfidence(payload?.confidence)}</div></div>
      <div class="inspector-row"><span>Page</span><div>${escapeHtml(payload?.page ?? payload?.page_number ?? "-")}</div></div>
      <div class="inspector-row"><span>Source</span><div>${escapeHtml(payload?.source ?? "-")}</div></div>
      <div class="inspector-row"><span>Original bbox</span><div>${escapeHtml(payload?.bbox ? JSON.stringify(payload.bbox) : "-")}</div></div>
      <div class="inspector-row"><span>Fields</span><div>${escapeHtml(fields)}</div></div>
    </div>
    <ul class="reason-list">${reasons.items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    <div class="edit-actions region-actions">
      <select id="regionFieldSelect" class="edit-input">
        ${EDITABLE_FIELDS.map((field) => `<option value="${field}">${field}</option>`).join("")}
      </select>
      <button class="ghost small" id="useRegionValueBtn" type="button">Use text</button>
      <button class="ghost small" id="rejectRegionBtn" type="button">Reject</button>
    </div>
    <details class="advanced-details">
      <summary>Advanced evidence</summary>
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
  if (normalizedType.includes("ocr")) items.push("Raw OCR box; use it as evidence before assigning a field.");
  if (payload?.source) items.push(`Source: ${payload.source}.`);
  if (payload?.bbox) items.push("Has page coordinates, so the value can be visually verified.");
  if (payload?.confidence !== undefined && Number(payload.confidence) >= 0.85) items.push("High confidence candidate.");
  if (payload?.confidence !== undefined && Number(payload.confidence) < 0.7) items.push("Low confidence candidate; review before export.");
  if (payload?.rejection_reason) items.push(`Rejected reason: ${payload.rejection_reason}.`);
  if (!items.length) items.push("No detailed scoring evidence was returned for this region.");
  return {
    summary: items[0],
    items: items.slice(1),
  };
}


async function saveCorrections() {
  if (!lastResponse) {
    showError("Process a document before saving corrections.");
    return;
  }
  setLoading(true, "Saving corrections, recomputing totals, and checking ERP readiness...");
  const payload = {
    document_id: lastResponse.erp_json?.metadata?.source_file || lastResponse.document_preview?.source_file || null,
    source_file: lastResponse.erp_json?.metadata?.source_file || null,
    detected_fields: lastResponse.detected_fields || {},
    field_corrections: buildCorrectedFieldPayload(),
    line_item_corrections: lastResponse.detected_fields?.line_items || [],
    ignored_rows: ignoredRows,
    original_payload: lastResponse,
  };
  try {
    const response = await fetch("/review/validate-corrections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not save corrections.");
    lastResponse.corrected_response = data;
    lastResponse.validated_erp_json = data.validated_erp_json;
    lastResponse.erp_json = data.erp_json || data.validated_erp_json;
    lastResponse.detected_fields = data.corrected_fields || lastResponse.detected_fields;
    lastResponse.row_validation = data.row_validation || lastResponse.row_validation;
    lastResponse.financial_reasoning = data.financial_reasoning || lastResponse.financial_reasoning;
    lastResponse.confidence_breakdown = data.confidence_breakdown || lastResponse.confidence_breakdown;
    lastResponse.erp_readiness = data.erp_readiness || lastResponse.erp_readiness;
    lastResponse.invoice_validation_report = data.invoice_validation_report || lastResponse.invoice_validation_report;
    lastResponse.validation = data.validation || lastResponse.validation;
    renderErpReadiness(lastResponse);
    renderNotes(lastResponse);
    renderLineItems(lastResponse.detected_fields?.line_items || [], lastResponse.row_validation || []);
    renderDynamicReview();
    updateJsonPanels();
    showTransientNote(`Revalidated ${data.corrections?.length || 0} correction(s). ERP status: ${data.erp_readiness?.erp_ready_status || data.validation.status}`);
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
}

function buildCorrectedFieldPayload() {
  const corrected = {};
  document.querySelectorAll("[data-field]").forEach((input) => {
    const field = input.dataset.field;
    const current = coerceValue(field, input.value);
    const original = lastResponse.expanded_fields?.[field]?.value ?? null;
    if (current !== original && current !== undefined) {
      corrected[field] = {
        value: current,
        original_value: correctedFields[field]?.original_value ?? original,
        source: correctedFields[field]?.source || "human",
        bbox: correctedFields[field]?.bbox,
        page: correctedFields[field]?.page,
        confidence: correctedFields[field]?.confidence,
        user_action: correctedFields[field]?.user_action || "edited",
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
  showTransientNote(`Rejected candidate for ${field}.`);
}

function acceptSuggestion(index) {
  const suggestion = lastResponse?.correction_suggestions?.[index];
  if (!suggestion?.field) return;
  const value = suggestion.proposed ?? suggestion.proposed_value ?? suggestion.corrected_value;
  const input = document.querySelector(`[data-field="${cssEscape(suggestion.field)}"]`);
  if (input) input.value = value ?? "";
  updateReviewField(suggestion.field, value);
  renderDynamicReview();
  showTransientNote(`Accepted suggestion for ${suggestion.field}.`);
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
  showTransientNote(`Selected candidate for ${entry.field}.`);
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
    <span class="label">Selected region</span>
    <strong>No region selected</strong>
    <p>Click any OCR, layout, or field box to inspect it.</p>
  `;
}

function setZoom(value) {
  fitWidth = false;
  previewZoom = clamp(value, 0.2, 3);
  redrawPreview();
}

function setLoading(isLoading, message = "Running OCR, candidate extraction, and validation...") {
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
