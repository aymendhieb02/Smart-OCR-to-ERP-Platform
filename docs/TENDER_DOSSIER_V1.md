# Tender dossier domain contract (v1)

## Purpose
Relate independently processed tender documents into one reviewable dossier. This layer performs no OCR, LLM, embeddings, or network access. Inputs are `ProcessedTenderDocument` adapters that carry existing DocumentResult, analyzer output, metadata, and optional fact observations.

## Domain
`TenderDossier` stores dossier id, optional title/reference, documents, relationships, shared metadata, potential conflicts, and diagnostics. Each `TenderDossierDocument` retains document id, role assessment, source metadata, analyzer output, original document result, and optional content digest. Duplicate documents remain attached and are diagnosed.

## Roles
Supported roles: NOTICE, CDC, DAO, CCAP, CCTP, REGLEMENT, BOQ, BPU, DQE, DEVIS, SOUMISSION, ANNEX, OTHER, UNKNOWN. Explicit French/English filename/text anchors assign a role. No anchor results in UNKNOWN; multiple anchors result in UNKNOWN with REVIEW and alternatives. OTHER is reserved for an explicit future caller assignment, never a weak fallback.

## Grouping
Equal explicit dossier ids, tender references, or consultation numbers produce deterministic matches; different comparable identifiers produce no-match. Organization plus related filenames is only a review suggestion. Missing or weak signals require review. Dossier construction never automatically groups separate uploads.

## Cross-document facts
Callers adapt existing analyzer facts into FactObservation. Exact fact keys are compared after deterministic numeric/text normalization. A PotentialConflict preserves each value and Evidence (document id, field, source text, page, bbox, confidence). Equal normalized values produce no conflict. The layer does not resolve precedence or legal correctness.

## Integration and limits
Call `build_tender_dossier` after each source followed DocumentProcessor → DocumentResult → its appropriate analyzer. The function only attaches outputs, classifies filename/text role cues, reports duplicate digests, and compares supplied facts. Adapters and fact extraction belong to existing processors/analyzers. Identifier/fact extraction is not implemented in this layer; callers must provide metadata and evidence. Relationships are represented but not inferred.
