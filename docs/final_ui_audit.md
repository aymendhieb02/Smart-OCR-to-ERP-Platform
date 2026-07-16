# Final UI Audit

Date: 2026-07-15

Scope: visible demo readiness only. OCR, extraction, benchmark, and AI logic were not redesigned in this sprint.

| Screen/component | Problem | Severity | Visible impact | Fix applied |
| --- | --- | --- | --- | --- |
| Landing/upload | The first screen explained upload but did not summarize the platform value or safety rule. | Medium | Jury sees a utility form before understanding the product. | Added a three-card summary for pipeline coverage, safe ERP export, and benchmark honesty. |
| Demo flow | No stable entry point for known demo documents. | High | Live demo depends on file browsing and can drift. | Added `dataset/demo` documents and visible demo buttons. |
| Status labels | Raw statuses such as `needs_review` were visible without explanation. | Medium | Non-engineers may not understand why export is blocked. | Added status guide and short status explanations in validation/readiness panels. |
| ERP readiness | Export button could be disabled without a clear next action. | High | User may not know what to fix. | Added readiness score, blockers, missing fields, and next required action. |
| Product lines | Editable rows had delete but no restore path. | Medium | A reviewer could accidentally overwrite a row and lose the original extraction. | Added restore action in both product line views. |
| Financial checks | Financial reasoning looked like compact debug output. | Medium | The validation logic was hard to present. | Reworked checks into Passed/Warning/Conflict cards with expected, extracted, difference, tolerance, and action. |
| Region inspector | Clicked boxes displayed raw JSON too prominently. | Medium | Important evidence was buried in technical data. | Added human-readable evidence first and moved raw payload into an advanced disclosure. |
| Advanced/debug tabs | OCR blocks and raw JSON appeared next to business review tabs. | Low | Debug features looked like primary workflow. | Added an `Advanced` separator before raw diagnostic tabs. |
| Accessibility | Focus states existed in places but were not consistent. | Low | Keyboard navigation was less visible. | Added shared `:focus-visible` styling. |
| Table usability | Large line tables could scroll, but headers were not always anchored. | Medium | User loses column meaning while reviewing rows. | Added sticky table headers and scroll constraints. |

Remaining visible limitations:

- The visual overlay quality still depends on OCR bounding boxes.
- Demo documents are representative, not proof of accuracy.
- True accuracy requires manually verified ground-truth labels.
