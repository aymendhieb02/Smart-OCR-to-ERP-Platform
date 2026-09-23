from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")
STRINGS = (ROOT / "app/static/strings.js").read_text(encoding="utf-8")


def test_dossier_state_and_two_level_navigation_are_explicit():
    assert "let dossierResponse = null" in APP_JS
    assert "let selectedLogicalDocumentIndex = 0" in APP_JS
    assert "let selectedPageWithinLogicalDocument = 0" in APP_JS
    assert "selectedPageWithinLogicalDocument = pageWithinDocumentIndex" in APP_JS
    assert "selectedLogicalDocumentIndex = index" in APP_JS
    assert "selectedPageWithinLogicalDocument = 0" in APP_JS


def test_dossier_normalization_and_single_document_compatibility():
    assert "function normalizeDossierResponse" in APP_JS
    assert "Array.isArray(response?.logical_documents)" in APP_JS
    assert "document_count: 1" in APP_JS
    assert "synthetic" not in HTML.lower()
    assert 'fetch("/process-dossier"' in APP_JS
    assert "demo-documents" in APP_JS


def test_preview_and_overlay_selection_use_physical_page_membership():
    assert "function getSelectedDocumentPreviewPages" in APP_JS
    assert "physical_page_numbers" in APP_JS
    assert "function getSelectedPhysicalPageNumber" in APP_JS
    assert "function getSelectedPageScopedOverlays" in APP_JS
    assert "const pageOverlays = getSelectedPageScopedOverlays()" in APP_JS
    assert "pages: dossierResponse.document_preview?.pages || []" in APP_JS
    assert "window.DossierNavigation?.resolvePhysicalPageSelection" in APP_JS
    assert "renderDossierNavigation();" in APP_JS[APP_JS.index("function selectPhysicalPage"):APP_JS.index("function getSelectedDocumentPages")]


def test_dossier_physical_pager_is_primary_and_overlay_scope_remains_physical():
    assert 't("dossier.physical_page", { current: normalizePage(physicalPage)' in APP_JS
    assert "const onPage = (item) => normalizePage(item.page ?? item.page_number) === normalizePage(page)" in APP_JS
    assert "getSelectedDocumentResponse" in APP_JS
    assert "selectedDocumentPageCount > 1" in APP_JS


def test_presentation_resolver_centralizes_document_behavior():
    assert "const DOCUMENT_PRESENTATION = Object.freeze" in APP_JS
    assert "function resolveDocumentPresentation" in APP_JS
    assert "ruspina_reinvoice_v1" in APP_JS
    assert "customs_declaration" in APP_JS
    assert 'allowCorrections: false, allowInvoiceExport: false' in APP_JS
    assert 'showLineItems: false' in APP_JS


def test_corrections_use_logical_document_identity_and_customs_is_display_only():
    assert "logicalDocument?.logical_document_id" in APP_JS
    assert 'if (!resolveDocumentPresentation().allowCorrections)' in APP_JS
    assert 'if (!presentation.allowInvoiceExport)' in APP_JS
    assert 'if (!presentation.showLineItems)' in APP_JS


def test_document_tabs_are_accessible_and_responsive():
    assert 'role="tablist"' in HTML
    assert 'button.setAttribute("role", "tab")' in APP_JS
    assert 'button.setAttribute("aria-selected"' in APP_JS
    assert "ArrowLeft" in APP_JS and "ArrowRight" in APP_JS
    assert "overflow-x: auto" in CSS
    assert ".logical-document-tab:focus-visible" in CSS


def test_dossier_summary_relationships_and_localization_are_present():
    assert "function summarizeLogicalDocuments" in APP_JS
    assert "function renderDossierRelationships" in APP_JS
    for key in (
        "dossier.document_supplier_invoice", "dossier.document_ruspina", "dossier.document_customs",
        "dossier.document_unknown", "dossier.physical_page", "dossier.relationship_unavailable",
        "dossier.correction_unavailable", "dossier.erp_unavailable",
    ):
        assert STRINGS.count(f'"{key}"') == 2


def test_business_logic_does_not_depend_on_translated_document_labels():
    assert 'presentation.labelKey' in APP_JS
    assert 'document.document_family' in APP_JS
    assert 'document.document_type' in APP_JS
    assert 'if ("Facture' not in APP_JS
    assert '=== "Déclaration douanière"' not in APP_JS


def test_display_only_fields_are_deduplicated_and_have_safe_labels():
    assert "const values = new Map(Object.entries(fields))" in APP_JS
    assert 'translated.startsWith("[missing:") ? humanize(key) : translated' in APP_JS
