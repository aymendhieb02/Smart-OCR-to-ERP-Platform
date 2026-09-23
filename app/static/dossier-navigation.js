(function exposeDossierNavigation(root) {
  function normalizePhysicalPage(value) {
    const page = Number(value ?? 1);
    return Number.isFinite(page) && page > 0 ? page : 1;
  }

  function resolvePhysicalPageSelection(logicalDocuments, physicalPage) {
    const target = normalizePhysicalPage(physicalPage);
    const documentIndex = (logicalDocuments || []).findIndex((item) =>
      (item.physical_page_numbers || []).some((page) => normalizePhysicalPage(page) === target)
    );
    if (documentIndex < 0) return null;
    const pages = logicalDocuments[documentIndex].physical_page_numbers || [];
    const pageWithinDocumentIndex = pages.findIndex((page) => normalizePhysicalPage(page) === target);
    if (pageWithinDocumentIndex < 0) return null;
    return { documentIndex, pageWithinDocumentIndex };
  }

  function resolveDossierPageIndex(logicalDocument, pageWithinDocumentIndex, previewPages) {
    const physicalPage = (logicalDocument?.physical_page_numbers || [])[pageWithinDocumentIndex];
    return (previewPages || []).findIndex((page) => normalizePhysicalPage(page.page) === normalizePhysicalPage(physicalPage));
  }

  const api = { normalizePhysicalPage, resolvePhysicalPageSelection, resolveDossierPageIndex };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.DossierNavigation = api;
})(typeof window !== "undefined" ? window : globalThis);
