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
      "dossier.label": "Dossier", "dossier.documents_aria": "Dossier documents", "dossier.counts": "{documents} documents · {pages} pages",
      "dossier.summary": "{status} · {valid} validated · {review} to review · {invalid} invalid",
      "dossier.document_supplier_invoice": "Supplier invoice", "dossier.document_ruspina": "RUSPINA invoice", "dossier.document_customs": "Customs declaration", "dossier.document_unknown": "Unclassified document",
      "dossier.document_position": "Document {current} of {total}", "dossier.physical_page": "Physical page {current} of {total}", "dossier.page_within_document": "Page {current} of {total} in document",
      "dossier.relationships": "Dossier relationships", "dossier.relationship_match": "Matches", "dossier.relationship_differs": "Differs", "dossier.relationship_unavailable": "Information unavailable", "dossier.information_unavailable": "Information unavailable",
      "dossier.correction_unavailable": "Structured correction is not available for this document type.", "dossier.erp_unavailable_title": "ERP export unavailable", "dossier.erp_unavailable": "This document can be reviewed, but structured correction and ERP export are not yet available for this document type.",
      "dossier.line_items_not_applicable": "Invoice line editing is not applicable to this document type.",
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
      "review.visual_title": "Dossier visual review", "review.page_controls_aria": "Preview page controls", "review.zoom_controls_aria": "Preview zoom controls",
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
    },
    fr: {
      "app.document_title": "Espace de vérification OCR vers ERP", "app.product_name": "Plateforme intelligente OCR vers ERP",
      "app.heading": "Espace de vérification des factures", "app.subtitle": "Importez une facture, vérifiez les éléments justificatifs, corrigez les valeurs incertaines et exportez uniquement les données ERP validées au format JSON.",
      "api.label": "API", "api.checking": "Vérification...", "api.online": "En ligne", "api.unknown": "État inconnu", "api.offline": "Hors ligne",
      "summary.project_aria": "Présentation du projet", "summary.pipeline_label": "Couverture du traitement", "summary.pipeline_title": "OCR, mise en page, tableaux et validation",
      "summary.pipeline_description": "Un espace unique relie l’aperçu du document, les zones justificatives, les champs modifiables, les lignes de facture, les contrôles financiers et le statut d’export ERP.",
      "summary.safety_label": "Règle de sécurité", "summary.safety_title": "Export ERP sécurisé", "summary.safety_description": "Les valeurs manquantes, contradictoires ou de confiance faible restent bloquées jusqu’à leur confirmation ou correction par un utilisateur.",
      "summary.benchmark_label": "Note d’évaluation", "summary.confidence_title": "La confiance constitue un indice, pas une vérité",
      "summary.confidence_warning": "L’indice de confiance ne mesure pas l’exactitude.", "summary.confidence_description": "L’indice de confiance décrit les éléments d’extraction disponibles. La mesure réelle de l’exactitude exige des données de référence vérifiées.",
      "upload.choose": "Choisir une facture ou un document", "upload.formats": "PDF, PDF numérisé, JPG, PNG, TIFF ou BMP", "upload.process": "Traiter le document", "upload.no_file": "Aucun fichier sélectionné", "upload.choose_first": "Choisissez d’abord un document.",
      "dossier.label": "Dossier", "dossier.documents_aria": "Documents du dossier", "dossier.counts": "{documents} documents · {pages} pages",
      "dossier.summary": "{status} · {valid} validé(s) · {review} à vérifier · {invalid} non valide(s)",
      "dossier.document_supplier_invoice": "Facture fournisseur", "dossier.document_ruspina": "Facture RUSPINA", "dossier.document_customs": "Déclaration douanière", "dossier.document_unknown": "Document non classé",
      "dossier.document_position": "Document {current} sur {total}", "dossier.physical_page": "Page physique {current} sur {total}", "dossier.page_within_document": "Page {current} sur {total} du document",
      "dossier.relationships": "Relations du dossier", "dossier.relationship_match": "Correspond", "dossier.relationship_differs": "Diffère", "dossier.relationship_unavailable": "Information indisponible", "dossier.information_unavailable": "Information indisponible",
      "dossier.correction_unavailable": "La correction structurée n’est pas disponible pour ce type de document.", "dossier.erp_unavailable_title": "Export ERP non disponible", "dossier.erp_unavailable": "Ce document peut être consulté, mais la correction structurée et l’export ERP ne sont pas encore disponibles pour ce type de document.",
      "dossier.line_items_not_applicable": "La modification des lignes de facture ne s’applique pas à ce type de document.",
      "camera.open": "Prendre une photo", "camera.dialog_aria": "Fenêtre de capture photo", "camera.title": "Capturer le document", "camera.description": "Prenez une photo et traitez-la avec le même processus de vérification.",
      "camera.close": "Fermer", "camera.capture": "Prendre la photo", "camera.unavailable": "La caméra n’est pas disponible dans ce navigateur. Utilisez HTTPS ou localhost.",
      "camera.open_failed": "Impossible d’ouvrir la caméra : {message}", "camera.loading": "La caméra est encore en cours de chargement. Réessayez dans un instant.", "camera.capture_failed": "Impossible de capturer l’image.", "camera.captured_file": "{filename} (capture photo)",
      "demo.aria": "Documents de démonstration", "demo.label": "Mode démonstration", "demo.title": "Charger un document préparé", "demo.description": "Ces exemples utilisent le même processus d’extraction et servent aux démonstrations ou aux contrôles de régression rapides.",
      "demo.good_aria": "Charger une facture correcte", "demo.good": "Facture correcte", "demo.review_aria": "Charger une facture à vérifier", "demo.review": "Facture à vérifier",
      "demo.noisy_aria": "Charger un document bruité", "demo.noisy": "Document bruité", "demo.loading": "Chargement de la démonstration : {label}. Exécution du processus d’extraction normal...",
      "demo.selected": "{label} (document de démonstration)", "demo.failed": "Échec du traitement de la démonstration.", "demo.failed_help": "{message} S’il s’agit du premier lancement, vérifiez que les dépendances OCR sont installées.",
      "processing.initial": "Lecture du document, extraction des champs et contrôle du statut d’export ERP...", "processing.document": "Lecture du document, détection de la mise en page, extraction des champs et contrôle du statut d’export ERP...",
      "processing.default": "Exécution de l’OCR, extraction des candidats et validation...", "processing.failed": "Échec du traitement du document.",
      "common.previous": "Précédent", "common.next": "Suivant", "common.copy": "Copier", "common.copied": "Copié", "common.select": "Sélectionner",
      "common.warning": "Avertissement", "common.error": "Erreur", "common.reason": "Motif", "common.action": "Action", "common.status": "Statut", "common.actions": "Actions",
      "common.restore": "Restaurer", "common.delete": "Supprimer", "common.ignore": "Ignorer", "common.accept": "Accepter", "common.reject": "Rejeter",
      "common.value": "Valeur", "common.source": "Source", "common.evidence": "Élément justificatif", "common.yes": "oui", "common.no": "non",
      "summary.validation": "Validation", "summary.document_type": "Type de document", "summary.confidence": "Indice de confiance global", "erp.readiness": "Statut d’export ERP",
      "status.guide_aria": "Guide des statuts", "status.validated": "Validé", "status.needs_review": "À vérifier", "status.invalid": "Non valide", "status.rejected": "Rejeté", "status.corrected": "Corrigé",
      "status.confirmed": "Confirmé", "status.not_extracted": "Non extrait.", "status.not_confirmed": "Non confirmé", "status.validated_help": "Les valeurs obligatoires sont présentes et les contrôles métier sont satisfaits.",
      "status.needs_review_help": "Certaines valeurs doivent être confirmées avant l’export ERP.", "status.invalid_help": "Une anomalie bloquante empêche l’export ERP.", "status.corrected_help": "Une valeur a été modifiée ; la validation est actualisée automatiquement.",
      "status.validated_decorated": "[OK] Validé", "status.needs_review_decorated": "[!] À vérifier", "status.invalid_decorated": "[X] Non valide", "status.corrected_decorated": "[MODIF.] Corrigé manuellement",
      "status.low_confidence": "Confiance faible", "status.missing": "Manquant", "status.unmapped": "Non associé", "status.manually_corrected": "Corrigé manuellement", "status.default_help": "Vérifiez les éléments extraits avant l’export ERP.",
      "review.visual_title": "Vérification visuelle du dossier", "review.page_controls_aria": "Commandes de navigation dans l’aperçu", "review.zoom_controls_aria": "Commandes de zoom de l’aperçu",
      "review.page_empty": "Page - / -", "review.page": "Page {current} / {total}", "review.fit_width": "Adapter à la largeur", "review.preview_empty": "Importez un document pour afficher l’aperçu et les zones justificatives.",
      "review.structured_title": "Vérification des données structurées", "review.structured_help": "Vérifiez chaque valeur extraite indépendamment de la décision finale d’export ERP.",
      "review.save_recheck": "Enregistrer et revérifier", "review.process_before_save": "Traitez un document avant d’enregistrer les modifications.", "review.saving": "Enregistrement des modifications et actualisation de la validation ERP...",
      "review.revalidation_failed": "Impossible de revalider le document corrigé.", "review.saved": "{count} modification(s) enregistrée(s). Statut ERP : {status}",
      "overlay.ocr_boxes": "Zones OCR", "overlay.layout_blocks": "Blocs de mise en page", "overlay.field_boxes": "Zones des champs", "overlay.line_rows": "Lignes de facture", "overlay.confidence_labels": "Étiquettes de confiance",
      "overlay.legend": "Légende des zones", "overlay.products": "Produits", "overlay.totals": "Totaux", "overlay.line_row": "Ligne de facture", "overlay.field": "Champ", "overlay.diagnostics": "Diagnostic des zones",
      "party.supplier": "Fournisseur", "party.seller": "Vendeur", "party.customer": "Client", "party.buyer": "Acheteur", "party.consignee": "Destinataire",
      "validation.summary": "Synthèse de validation", "validation.waiting": "En attente d’un document", "validation.empty_help": "Traitez un document pour consulter les contrôles réussis, les erreurs et les éléments à vérifier.",
      "validation.issues": "Anomalies de validation", "validation.no_issues": "Aucune anomalie de validation détectée.", "validation.no_summary": "Aucune synthèse de validation reçue.",
      "validation.default_action": "Vérifiez les champs manquants, incertains ou incohérents avant l’export ERP.", "validation.explanation": "Détail de la validation", "validation.errors": "Erreurs", "validation.warnings": "Avertissements",
      "validation.rechecking": "Nouvelle vérification...", "validation.queued": "La validation automatique est planifiée pour la dernière modification.",
      "region.selected": "Zone sélectionnée", "region.none": "Aucune zone sélectionnée", "region.empty_help": "Cliquez sur une zone OCR, de mise en page, de champ ou de ligne pour examiner les éléments justificatifs.",
      "region.empty_help_short": "Cliquez sur une zone OCR, de mise en page ou de champ pour l’examiner.", "region.selected_type": "Élément sélectionné : {type}", "region.default": "Zone",
      "region.text_value": "Texte/valeur", "region.confidence": "Indice de confiance", "region.page": "Page", "region.source": "Source", "region.bbox": "Coordonnées d’origine", "region.fields": "Champs",
      "region.use_text": "Utiliser le texte", "region.advanced": "Éléments justificatifs avancés",
      "erp.empty_help": "Traitez un document pour déterminer si l’export ERP est autorisé.", "erp.export_validated": "Exporter les données ERP validées", "erp.export": "Exporter les données ERP",
      "erp.ready": "Prêt pour l’export ERP", "erp.field_missing": "Le champ {field} est manquant", "erp.next_ready": "Action suivante : exporter les données ERP validées ou poursuivre la vérification.",
      "erp.next_fix": "Action suivante : corriger {issue}{more}, puis enregistrer les modifications.", "erp.more_issues": " ainsi que {count} autre(s) anomalie(s)", "erp.next_review": "Action suivante : vérifier les champs de confiance faible, les lignes de facture et les contrôles financiers avant l’export.",
      "erp.blockers_cleared": "Tous les blocages ERP sont levés.", "erp.export_ready_title": "L’export ERP est prêt", "erp.copied": "Données ERP validées copiées.",
      "filter.label": "Filtre", "filter.all": "Tous", "filter.erp_only": "ERP uniquement", "filter.validated": "Validés", "filter.needs_review": "À vérifier", "filter.missing": "Manquants",
      "filter.low_confidence": "Confiance faible", "filter.unmapped": "Non associés", "filter.corrected": "Corrigés manuellement", "filter.no_rows": "Aucune ligne ne correspond au filtre actuel.",
      "tabs.visual": "Vérification visuelle", "tabs.erp_fields": "Champs ERP", "tabs.all_values": "Toutes les valeurs extraites", "tabs.line_items": "Lignes de facture", "tabs.financial": "Contrôle financier",
      "tabs.suggestions": "Suggestions de vérification", "tabs.risk": "Doublons et risques", "tabs.diagnostics": "Diagnostic", "tabs.advanced": "Avancé", "tabs.ocr_blocks": "Blocs OCR",
      "tabs.layout_blocks": "Blocs de mise en page", "tabs.unmapped": "Texte non associé", "tabs.validation_report": "Rapport de validation", "tabs.erp_json": "JSON ERP", "tabs.full_json": "JSON complet",
      "tabs.ocr_text": "Texte OCR", "tabs.candidate_debug": "Diagnostic des candidats", "tabs.api_response": "Réponse API",
      "fields.editable_title": "Champs modifiables", "fields.supplier_name": "Fournisseur", "fields.supplier_address": "Adresse du fournisseur", "fields.supplier_tax_id": "Identifiant fiscal du fournisseur",
      "fields.supplier_phone": "Téléphone du fournisseur", "fields.supplier_email": "E-mail du fournisseur", "fields.supplier_website": "Site web du fournisseur", "fields.supplier_bank_iban": "IBAN du fournisseur",
      "fields.supplier_bank_rib": "RIB du fournisseur", "fields.supplier_bank_swift": "Code SWIFT du fournisseur", "fields.customer_name": "Client", "fields.customer_address": "Adresse du client",
      "fields.customer_tax_id": "Identifiant fiscal du client", "fields.customer_phone": "Téléphone du client", "fields.customer_email": "E-mail du client", "fields.invoice_number": "Numéro de facture",
      "fields.invoice_date": "Date de facture", "fields.due_date": "Date d’échéance", "fields.currency": "Devise", "fields.amount_ht": "Total HT", "fields.tva_amount": "TVA", "fields.amount_ttc": "Total TTC",
      "fields.tax_rate": "Taux de TVA", "fields.purchase_order_number": "Numéro de bon de commande",
      "candidate.warning": "Cette valeur est mathématiquement incohérente avec les montants associés.", "candidate.use_expected": "Utiliser plutôt {value}", "candidate.primary": "Candidat :", "candidate.alternative": "Alternative :",
      "candidate.unknown_source": "candidat", "candidate.heading": "Valeurs candidates", "candidate.use": "Utiliser ce candidat", "candidate.rejected": "Candidat rejeté pour {field}.", "candidate.selected": "Candidat sélectionné pour {field}.",
      "confidence.field_title": "Indice de confiance par champ", "confidence.none": "Aucune donnée de confiance reçue pour les champs",
      "line_items.editable_title": "Lignes de facture modifiables", "line_items.help": "Vérifiez ou corrigez les lignes extraites. La validation est actualisée automatiquement après chaque modification.",
      "line_items.add": "Ajouter une ligne", "line_items.add_row": "Ajouter une ligne", "line_items.save": "Enregistrer le tableau et revérifier", "line_items.description": "Description", "line_items.quantity": "Quantité",
      "line_items.unit": "Unité", "line_items.unit_price": "Prix unitaire", "line_items.total_ht": "Total HT", "line_items.tax": "TVA %", "line_items.total_ttc": "Total TTC",
      "line_items.none": "Aucune ligne de facture n’a été extraite. Ajoutez une ligne manuellement si nécessaire.", "line_items.lines_total": "Total des lignes", "line_items.total_title": "Somme des montants TTC visibles des lignes de facture.",
      "line_items.delete_missing": "Aucune ligne correspondante ne peut être supprimée.", "line_items.restore_missing": "Aucune ligne d’origine ne peut être restaurée.", "line_items.restored": "La ligne {number} a été restaurée à partir de l’extraction d’origine.", "line_items.number": "Ligne {number}",
      "dynamic.process_first": "Traitez un document pour afficher les tableaux d’extraction.", "dynamic.visual_help": "Utilisez la zone de vérification visuelle ci-dessus pour examiner les zones OCR, les blocs de mise en page et les champs sur l’aperçu de la facture.",
      "dynamic.no_data": "Aucune donnée de tableau dynamique n’a été reçue pour cette vue.", "dynamic.summary": "{count} ligne(s) — confiance {confidence}", "dynamic.row": "Ligne",
      "checks.passed": "Validé", "checks.conflict": "Contradiction", "checks.warning": "Avertissement", "checks.no_action": "Aucune action nécessaire.",
      "checks.correct_conflict": "Comparez les totaux du document et corrigez le montant contradictoire avant l’export.", "checks.enter_missing": "Recherchez ou saisissez le montant manquant, puis enregistrez les modifications.",
      "checks.expected": "Attendu", "checks.extracted": "Extrait", "checks.difference": "Écart", "checks.tolerance": "Tolérance", "checks.incomplete": "Des totaux plus complets sont nécessaires pour exécuter les contrôles financiers.",
      "assistant.title": "Assistant de vérification", "assistant.default_summary": "L’assistant de vérification a généré des recommandations.", "assistant.erp_impact": "Impact ERP : {value}", "assistant.advisory": "Les suggestions sont fournies à titre indicatif.",
      "assistant.issue": "Point à vérifier", "assistant.problem": "Problème", "assistant.explanation": "Explication", "assistant.suggested": "Correction proposée", "assistant.no_issues": "L’assistant n’a détecté aucun point supplémentaire à vérifier.",
      "suggestion.title": "Suggestion", "suggestion.original": "Valeur d’origine", "suggestion.proposed": "Valeur proposée", "suggestion.reason": "Motif", "suggestion.none": "Aucune suggestion de correction reçue.",
      "suggestion.rejected": "Suggestion rejetée pour cette session de vérification.", "suggestion.accepted": "Suggestion acceptée pour {field}.",
      "risk.duplicate": "Contrôle des doublons", "risk.possible": "Doublon possible : {value}", "risk.indicators": "Indicateurs de risque automatisés", "risk.none": "Aucun indicateur de doublon ou de risque reçu.",
      "risk.disclaimer": "Ces indicateurs de risque sont automatisés et ne constituent pas une conclusion de fraude.",
      "preview.none": "Aucun aperçu disponible.", "preview.open": "Ouvrir l’aperçu", "preview.alt": "Aperçu du document", "preview.load_failed": "Impossible de charger l’image d’aperçu depuis la réponse API.",
      "field.consistency_applied": "Suggestion de cohérence appliquée à {field}. Enregistrez pour revérifier.", "render.section_error": "Erreur d’affichage dans {section} : {message}"
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
