const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    this._html = "";
    this.value = "";
  }
  set innerHTML(value) { this._html = String(value); this.children = []; }
  get innerHTML() { return this._html; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener() {}
  setAttribute() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

function walk(element) {
  return [element, ...element.children.flatMap(walk)];
}

function createHarness() {
  const elements = new Map();
  const document = {
    createElement: (tag) => new Element(tag),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const window = {
    AppI18n: { t: (key) => key },
    addEventListener() {},
    __REVIEW_DEBUG__: { rejectedBoxes: [], renderErrors: [] },
  };
  const context = vm.createContext({
    document, window, console,
    fetch: async () => ({ ok: true, json: async () => ({ status: "ok" }) }),
    navigator: {}, setTimeout() {}, clearTimeout() {},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../app/static/app.js"), "utf8"), context, {
    filename: "app/static/app.js",
  });
  return {
    evaluate(dossier, documentIndex = 0) {
      context.auditDossier = dossier;
      context.auditDocumentIndex = documentIndex;
      vm.runInContext(`
        renderDossierNavigation = () => {};
        renderDossierRelationships = () => {};
        renderNotes = () => {};
        renderValidationSummary = () => {};
        renderErpReadiness = () => {};
        renderDynamicReview = () => {};
        renderConfidences = () => {};
        renderPreview = () => {};
        updateJsonPanels = () => {};
        renderResults(auditDossier);
        if (auditDocumentIndex !== 0) {
          selectedLogicalDocumentIndex = auditDocumentIndex;
          renderSelectedLogicalDocument(auditDossier);
        }
        globalThis.auditPresentation = resolveDocumentPresentation();
        globalThis.auditGroups = producerVisibleReviewGroups(auditPresentation, lastResponse.detected_fields);
        globalThis.auditNormalized = window.__REVIEW_DEBUG__.normalizedResponse;
        globalThis.auditDisplayItems = lastResponse.all_line_items?.length
          ? lastResponse.all_line_items : lastResponse.detected_fields.line_items || [];
        globalThis.auditEditable = EDITABLE_FIELDS;
      `, context);
      const inputs = Object.fromEntries(walk(elements.get("fieldsTable"))
        .filter((element) => element.tagName === "INPUT" && element.dataset.field)
        .map((element) => [element.dataset.field, element.value]));
      const normalized = context.auditNormalized;
      return {
        presentationKey: context.auditPresentation.key,
        presentationFields: Array.from(context.auditPresentation.fields),
        editableFields: Array.from(context.auditEditable),
        groups: Array.from(context.auditGroups, ([section, fields]) => [section, Array.from(fields)]),
        expandedFields: normalized.expanded_fields,
        inputs,
        apiRows: dossier.logical_documents[documentIndex].response.all_line_items?.length || 0,
        normalizedRows: normalized.all_line_items.length,
        displayRows: context.auditDisplayItems.length,
        domRows: (elements.get("lineItems").innerHTML.match(/data-line-row="/g) || []).length,
      };
    },
  };
}

module.exports = { createHarness };
