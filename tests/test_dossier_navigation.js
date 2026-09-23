const assert = require("node:assert/strict");
const { resolvePhysicalPageSelection, resolveDossierPageIndex } = require("../app/static/dossier-navigation.js");

{
  const docs = [
    { physical_page_numbers: [1] },
    { physical_page_numbers: [2] },
    { physical_page_numbers: [3] },
  ];
  assert.deepEqual(resolvePhysicalPageSelection(docs, 1), { documentIndex: 0, pageWithinDocumentIndex: 0 });
  assert.deepEqual(resolvePhysicalPageSelection(docs, 2), { documentIndex: 1, pageWithinDocumentIndex: 0 });
  assert.deepEqual(resolvePhysicalPageSelection(docs, 3), { documentIndex: 2, pageWithinDocumentIndex: 0 });
  assert.deepEqual(resolvePhysicalPageSelection(docs, 2), { documentIndex: 1, pageWithinDocumentIndex: 0 });
  assert.deepEqual(resolvePhysicalPageSelection(docs, 1), { documentIndex: 0, pageWithinDocumentIndex: 0 });
}

{
  const docs = [
    { physical_page_numbers: [1, 2] },
    { physical_page_numbers: [3] },
    { physical_page_numbers: [4, 5] },
  ];
  assert.deepEqual([1, 2, 3, 4, 5].map((page) => resolvePhysicalPageSelection(docs, page)), [
    { documentIndex: 0, pageWithinDocumentIndex: 0 },
    { documentIndex: 0, pageWithinDocumentIndex: 1 },
    { documentIndex: 1, pageWithinDocumentIndex: 0 },
    { documentIndex: 2, pageWithinDocumentIndex: 0 },
    { documentIndex: 2, pageWithinDocumentIndex: 1 },
  ]);
}

{
  assert.equal(resolvePhysicalPageSelection([{ physical_page_numbers: [1] }], 2), null);
}

{
  const pages = [{ page: 1 }, { page: 2 }, { page: 3 }];
  const docs = [{ physical_page_numbers: [1, 2] }, { physical_page_numbers: [3] }];
  assert.equal(resolveDossierPageIndex(docs[0], 0, pages), 0);
  assert.equal(resolveDossierPageIndex(docs[1], 0, pages), 2);
}

console.log("4 dossier navigation scenarios passed");
