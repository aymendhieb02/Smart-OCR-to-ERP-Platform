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
const errorBox = document.getElementById("errorBox");
const results = document.getElementById("results");
const previewCanvas = document.getElementById("previewCanvas");
const regionDetails = document.getElementById("regionDetails");
const validationSummary = document.getElementById("validationSummary");

let selectedFile = null;
let lastResponse = null;
let previewZoom = 1;
let fitWidth = true;
let activeDynamicTab = "visual";
let correctedFields = {};
let correctedLineItems = [];
let cameraStream = null;
let selectedRegionPayload = null;

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
processBtn.addEventListener("click", async () => {
  if (!selectedFile) {
    showError("Choose a document first.");
    return;
  }

  const formData = new FormData();
  formData.append("file", selectedFile);
  setLoading(true);
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
});

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
  lastResponse = data;
  const validation = data.validation || {};
  const status = validation.status || (validation.is_valid ? "valid" : "invalid");
  const classification = data.document_classification || {};
  const fields = data.detected_fields || {};
  const confidence = data.erp_json?.quality?.overall_confidence ?? data.erp_json?.metadata?.confidence;

  const statusEl = document.getElementById("validationStatus");
  statusEl.textContent = status;
  statusEl.className = `pill ${status}`;
  document.getElementById("documentType").textContent = classification.document_type || "-";
  document.getElementById("ocrConfidence").textContent = formatConfidence(confidence);
  document.getElementById("erpDecision").textContent = status === "valid" ? "Ready for ERP" : "Review required";

  renderFields(fields);
  renderNotes(data);
  renderValidationSummary(data.validation_explanation, validation);
  renderDynamicReview();
  renderConfidences(data.field_confidences || {});
  renderLineItems(fields.line_items || []);

  document.getElementById("ocrText").textContent = data.extracted_text || "";
  document.getElementById("debugJson").textContent = pretty(data.extraction_debug || {});
  updateJsonPanels();

  results.classList.remove("hidden");
  renderPreview(data);
  document.querySelector(".visual-review")?.scrollIntoView({ block: "start" });
}

["toggleOcr", "toggleLayout", "toggleFields", "toggleLabels"].forEach((id) => {
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
    const input = document.createElement("input");
    input.className = "edit-input";
    input.dataset.field = field;
    input.value = fields[field] ?? "";
    input.placeholder = "-";
    input.addEventListener("input", () => updateReviewField(field, input.value));
    value.appendChild(input);
    table.append(key, value);
  });
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
  validationSummary.innerHTML = `
    <span class="label">Validation explanation</span>
    <strong>${escapeHtml(status)}</strong>
    <div class="inspector-list">
      <div class="inspector-row"><span>Reason</span><div>${escapeHtml(reason)}</div></div>
      <div class="inspector-row"><span>Action</span><div>${escapeHtml(action)}</div></div>
      <div class="inspector-row"><span>Errors</span><div>${escapeHtml(String(explanation?.blocking_errors?.length ?? validation?.errors?.length ?? 0))}</div></div>
      <div class="inspector-row"><span>Warnings</span><div>${escapeHtml(String(explanation?.warnings?.length ?? validation?.warnings?.length ?? 0))}</div></div>
    </div>
  `;
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

function renderLineItems(items) {
  const box = document.getElementById("lineItems");
  const editableItems = items || [];
  const rows = editableItems.map((item, index) => editableLineItemRow(item, index)).join("");
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
          <th>Total HT</th><th>Tax %</th><th>Total TTC</th><th>Actions</th>
        </tr>
      </thead>
      <tbody>${rows || '<tr><td colspan="8"><div class="note">No line items parsed. Add one manually if needed.</div></td></tr>'}</tbody>
    </table>
  `;
  box.querySelector("#addLineItemBtn")?.addEventListener("click", () => addReviewLineItem());
  box.querySelectorAll("[data-line-field]").forEach((input) => {
    input.addEventListener("input", () => updateReviewLineItem(Number(input.dataset.index), input.dataset.lineField, input.value));
  });
  box.querySelectorAll("[data-delete-line]").forEach((button) => {
    button.addEventListener("click", () => deleteReviewLineItem(Number(button.dataset.index)));
  });
}

function editableLineItemRow(item, index) {
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
  return `<tr>${cells}<td><button class="ghost small" type="button" data-delete-line data-index="${index}">Delete</button></td></tr>`;
}
function resetCorrections() {
  correctedFields = {};
  correctedLineItems = [];
}

function updateReviewField(field, rawValue) {
  if (!lastResponse) return;
  const value = coerceValue(field, rawValue);
  lastResponse.detected_fields = lastResponse.detected_fields || {};
  lastResponse.detected_fields[field] = value;
  correctedFields[field] = {
    original_value: correctedFields[field]?.original_value ?? getFieldOriginalValue(field),
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
  correctedLineItems.push({ row_index: index, deleted: true, corrected_by: "human" });
  renderLineItems(items);
  renderDynamicReview();
  updateCorrectionLayer("line_items");
  updateJsonPanels();
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
      ? '<td class="dynamic-actions"><button class="ghost small" type="button" data-ignore-row>Ignore</button><button class="ghost small" type="button" data-delete-row>Delete</button></td>'
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
      correctedLineItems.push({ row_key: row.key, status: "ignored" });
      renderDynamicReview();
    });
    tr?.querySelector("[data-delete-row]")?.addEventListener("click", (event) => {
      event.stopPropagation();
      row.status = "ignored";
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
  const stage = document.getElementById("previewStage");
  const firstPage = lastResponse?.document_preview?.pages?.[0];
  if (!stage || !firstPage) return;
  const scaleX = (firstPage.width * previewZoom) / firstPage.width;
  const scaleY = (firstPage.height * previewZoom) / firstPage.height;
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
  const firstPage = data.document_preview?.pages?.[0];
  if (!firstPage) {
    previewCanvas.innerHTML = '<div class="note">No preview available.</div>';
    return;
  }

  const imageUrl = new URL(firstPage.url, window.location.origin).href;
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
  const firstPage = lastResponse.document_preview?.pages?.[0];
  const stage = document.getElementById("previewStage");
  const image = document.getElementById("previewImage");
  if (!firstPage || !stage || !image) return;

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

  const scaleX = displayWidth / firstPage.width;
  const scaleY = displayHeight / firstPage.height;
  if (document.getElementById("toggleLayout").checked) {
    (lastResponse.layout_blocks || []).forEach((block) => addBox(stage, block.bbox, scaleX, scaleY, `layout ${block.block_type}`, block.block_type, block.confidence, block));
  }
  if (document.getElementById("toggleOcr").checked) {
    (lastResponse.ocr_blocks || []).forEach((block) => addBox(stage, block.bbox, scaleX, scaleY, "ocr", block.text, block.confidence, block));
  }
  if (document.getElementById("toggleFields").checked) {
    (lastResponse.field_boxes || []).forEach((box) => addBox(stage, box.bbox, scaleX, scaleY, "field", box.field, box.confidence, box));
  }
}

function addBox(stage, bbox, scaleX, scaleY, type, label, confidence, payload) {
  if (!bbox) return;
  const box = document.createElement("button");
  box.type = "button";
  const classes = type.split(" ").map((part) => `overlay-${part}`);
  box.className = `overlay-box ${classes.join(" ")}`;
  box.style.left = `${bbox.x1 * scaleX}px`;
  box.style.top = `${bbox.y1 * scaleY}px`;
  box.style.width = `${Math.max(6, (bbox.x2 - bbox.x1) * scaleX)}px`;
  box.style.height = `${Math.max(6, (bbox.y2 - bbox.y1) * scaleY)}px`;
  box.setAttribute("aria-label", `${type}: ${label}`);
  if (document.getElementById("toggleLabels").checked) {
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
}

function showRegionDetails(type, label, payload) {
  selectedRegionPayload = { type, label, payload };
  const fields = Array.isArray(payload?.fields) && payload.fields.length ? payload.fields.join(", ") : "-";
  const text = payload?.text ?? payload?.value ?? label ?? "-";
  regionDetails.innerHTML = `
    <span class="label">Selected ${escapeHtml(type)}</span>
    <strong>${escapeHtml(label || payload?.field || "Region")}</strong>
    <div class="inspector-list">
      <div class="inspector-row"><span>Text/value</span><div>${escapeHtml(text)}</div></div>
      <div class="inspector-row"><span>Confidence</span><div>${formatConfidence(payload?.confidence)}</div></div>
      <div class="inspector-row"><span>Page</span><div>${escapeHtml(payload?.page ?? payload?.page_number ?? "-")}</div></div>
      <div class="inspector-row"><span>Source</span><div>${escapeHtml(payload?.source ?? "-")}</div></div>
      <div class="inspector-row"><span>Fields</span><div>${escapeHtml(fields)}</div></div>
    </div>
    <div class="edit-actions region-actions">
      <select id="regionFieldSelect" class="edit-input">
        ${EDITABLE_FIELDS.map((field) => `<option value="${field}">${field}</option>`).join("")}
      </select>
      <button class="ghost small" id="useRegionValueBtn" type="button">Use text</button>
      <button class="ghost small" id="rejectRegionBtn" type="button">Reject</button>
    </div>
    <pre>${escapeHtml(pretty(payload))}</pre>
  `;
  document.getElementById("useRegionValueBtn")?.addEventListener("click", () => useSelectedRegionAsField());
  document.getElementById("rejectRegionBtn")?.addEventListener("click", () => rejectSelectedRegion());
}


async function saveCorrections() {
  if (!lastResponse) {
    showError("Process a document before saving corrections.");
    return;
  }
  const payload = {
    document_id: lastResponse.erp_json?.metadata?.source_file || lastResponse.document_preview?.source_file || null,
    source_file: lastResponse.erp_json?.metadata?.source_file || null,
    detected_fields: lastResponse.detected_fields || {},
    corrected_fields: buildCorrectedFieldPayload(),
    corrected_line_items: lastResponse.detected_fields?.line_items || [],
    corrections: buildExplicitCorrectionRecords(),
    original_payload: lastResponse,
  };
  try {
    const response = await fetch("/corrections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not save corrections.");
    lastResponse.corrected_response = data;
    lastResponse.validated_erp_json = data.validated_erp_json;
    lastResponse.erp_json = data.validated_erp_json;
    updateJsonPanels();
    showTransientNote(`Saved ${data.stored_count} correction(s). ERP status: ${data.validation.status}`);
  } catch (error) {
    showError(error.message);
  }
}

function buildCorrectedFieldPayload() {
  const corrected = {};
  document.querySelectorAll("[data-field]").forEach((input) => {
    const field = input.dataset.field;
    const current = coerceValue(field, input.value);
    const original = lastResponse.expanded_fields?.[field]?.value ?? null;
    if (current !== original && current !== undefined) corrected[field] = current;
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

function setLoading(isLoading) {
  loading.classList.toggle("hidden", !isLoading);
  processBtn.disabled = isLoading;
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
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Math.round(Number(value) * 100)}%`;
}

function formatCell(value) {
  return value === null || value === undefined ? "-" : escapeHtml(value);
}

function formatTableValue(value) {
  return value === null || value === undefined || value === "" || value === "-" ? "" : escapeHtml(value);
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

window.reviewUiDebug = {
  renderResults,
  renderPreview,
  redrawPreview,
};

checkApi();
