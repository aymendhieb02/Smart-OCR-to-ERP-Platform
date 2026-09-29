const assert = require("node:assert/strict");
const fs = require("node:fs");
const { createHarness } = require("./frontend_review_harness.js");

const ENFIDHA = "ciments_enfidha_invoice_v1";
const RUSPINA = "ruspina_reinvoice_v1";
const RUSPINA_APPROVED = [
  "invoice_number", "invoice_date", "referenced_invoice", "client", "address", "currency", "total",
  "total_amount_words", "gross_weight", "net_weight", "number_of_bags", "delivery", "origin",
  "payment", "iban", "bank", "swift",
];

function syntheticDossier(family, fields, packaging = "Mixed") {
  const expanded_fields = Object.fromEntries(fields.map((field) => [field, {
    value: field === "packaging" ? packaging : `TEST_${field}`,
    display_value: field === "packaging" ? packaging : `TEST_${field}`,
    confidence: 0.9,
  }]));
  const row = { description: "SYNTHETIC_ITEM", quantity: 1, unit: "MT", unit_price: 1000, total: 1000 };
  return { logical_documents: [{ document_family: family, document_type: "commercial_invoice", response: {
    detected_fields: { line_items: [row] }, expanded_fields, all_line_items: [row],
  } }] };
}

function groupedFields(result) { return result.groups.flatMap(([, fields]) => fields); }
function difference(a, b) { return a.filter((field) => !b.includes(field)); }
function hasValue(detail) { return detail?.value !== null && detail?.value !== undefined && String(detail.value).trim() !== ""; }

const harness = createHarness();
const reference = harness.evaluate(syntheticDossier(ENFIDHA, []));
const contract = reference.presentationFields;
const synthetic = harness.evaluate(syntheticDossier(ENFIDHA, contract));
assert.equal(synthetic.presentationKey, ENFIDHA);
assert.deepEqual(difference(contract, synthetic.editableFields), [], "Every declared producer field needs an editable renderer");
assert.deepEqual(difference(contract, groupedFields(synthetic)), [], "Every declared producer field needs a review group");
assert.deepEqual(difference(contract, Object.keys(synthetic.inputs)), [], "Every declared producer field needs a DOM input");
assert.equal(synthetic.inputs.bag_weight, "TEST_bag_weight");

for (const [packaging, hidden] of [["Bags", ["truck_count"]], ["Bulk", ["number_of_bags", "bag_weight"]]]) {
  const result = harness.evaluate(syntheticDossier(ENFIDHA, contract, packaging));
  assert.deepEqual(difference(contract, groupedFields(result)), hidden, `Intentional ${packaging} presentation suppression changed`);
  assert.deepEqual(difference(contract, Object.keys(result.inputs)), hidden);
}

const ruspina = harness.evaluate(syntheticDossier(RUSPINA, RUSPINA_APPROVED));
assert.equal(ruspina.presentationKey, RUSPINA);
assert.deepEqual(ruspina.presentationFields, RUSPINA_APPROVED);
assert.deepEqual(difference(RUSPINA_APPROVED, ruspina.editableFields), []);
assert.deepEqual(difference(RUSPINA_APPROVED, groupedFields(ruspina)), []);
assert.deepEqual(difference(RUSPINA_APPROVED, Object.keys(ruspina.inputs)), []);
assert.equal(ruspina.domRows, 1);

if (process.argv[2]) {
  const saved = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const producerIndex = saved.logical_documents.findIndex((doc) => doc.document_family === ENFIDHA);
  assert.ok(producerIndex >= 0, "Saved live response has no Enfidha document");
  const response = saved.logical_documents[producerIndex].response;
  const result = harness.evaluate(saved, producerIndex);
  const present = Object.keys(response.expanded_fields).filter((field) => hasValue(response.expanded_fields[field]) && contract.includes(field));
  const normalizedMissing = present.filter((field) => {
    const original = response.expanded_fields[field];
    const normalized = result.expandedFields[field];
    return !normalized || original.value !== normalized.value || original.display_value !== normalized.display_value;
  });
  const groupedMissing = difference(present, groupedFields(result));
  const domMissing = difference(present, Object.keys(result.inputs));
  const emptyInputs = present.filter((field) => field in result.inputs && !String(result.inputs[field]).trim());
  console.log(JSON.stringify({
    presentation: result.presentationKey, contract, present,
    contractNotEditable: difference(contract, result.editableFields),
    contractNotGrouped: difference(contract, groupedFields(result)),
    presentNotGrouped: groupedMissing,
    normalizedMissing, domMissing, emptyInputs,
    lineItems: [result.apiRows, result.normalizedRows, result.displayRows, result.domRows],
  }));
  assert.deepEqual(normalizedMissing, []);
  assert.deepEqual(groupedMissing, []);
  assert.deepEqual(domMissing, []);
  assert.deepEqual(emptyInputs, []);
  assert.deepEqual([result.apiRows, result.normalizedRows, result.displayRows, result.domRows], [1, 1, 1, 1]);
}

console.log("Frontend producer and RUSPINA presentation contracts passed");
