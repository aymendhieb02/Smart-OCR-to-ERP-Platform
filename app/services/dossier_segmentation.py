from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from app.core.schemas import OCRLine, OCRResult


@dataclass(frozen=True)
class PageClassification:
    page_number: int
    document_type: str
    document_family: str | None
    match_score: float
    matched_anchors: tuple[str, ...]
    reasons: tuple[str, ...]
    starts_new_document: bool = True


@dataclass(frozen=True)
class LogicalDocumentGroup:
    group_id: str
    pages: tuple[int, ...]
    document_type: str
    document_family: str | None
    page_classifications: tuple[PageClassification, ...]


@dataclass(frozen=True)
class _FamilyRule:
    family: str
    document_type: str
    anchors: tuple[tuple[str, float], ...]


_FAMILY_RULES = (
    _FamilyRule("customs_tradenet_v1", "customs_declaration", (
        ("tradenet", 2.0), ("tradent", 1.5), ("liasse unique", 0.7),
        ("declaration en detail", 0.6), ("تصريح", 0.4),
    )),
    _FamilyRule("customs_douanes_tunisiennes_v1", "customs_declaration", (
        ("douanes tunisiennes", 2.0), ("الديوانة التونسية", 2.0),
        ("douanes tunisennes", 1.5),
        ("declaration en detail", 0.8), ("bureau des douanes", 0.5),
    )),
    _FamilyRule("ciments_enfidha_invoice_v1", "commercial_invoice", (
        ("ciments d enfidha", 2.0), ("ciments d en fidha", 2.0),
        ("ste des ciments d en fidha", 2.0),
        ("societe des ciments d enfidha", 2.0),
    )),
    _FamilyRule("sotacib_kasserine_white_invoice_v1", "commercial_invoice", (
        ("tuniso andalouse", 1.6), ("ciment blanc", 1.2),
        ("sotacib", 0.5), ("feriana", 0.4),
    )),
    _FamilyRule("sotacib_kairouan_grey_invoice_v1", "commercial_invoice", (
        ("sotacib kairouan", 2.0), ("ciment gris", 0.8),
        ("exw kairouan", 0.6),
    )),
    _FamilyRule("ruspina_reinvoice_v1", "commercial_invoice", (
        ("ruspina import export", 1.6), ("ruspina import et export", 1.6),
        ("as per invoice", 1.2), ("bank transfer", 0.3),
    )),
)

_GENERIC_CUSTOMS_ANCHORS = (
    "douane", "customs", "declaration en detail", "تصريح", "الديوانة",
)
_GENERIC_INVOICE_ANCHORS = ("facture", "invoice", "فاتورة")
_CUSTOMS_LAYOUT_ANCHORS = (
    "exportateur", "importateur", "declarant", "designation des marchandises",
    "moyen de transport", "bureau", "pays de provenance", "pays de destination",
)


def classify_page(lines: list[OCRLine], page_number: int) -> PageClassification:
    page_lines = [line for line in lines if line.page_number == page_number]
    match_text = _matching_text("\n".join(line.text for line in page_lines if line.text))

    ranked: list[tuple[float, int, _FamilyRule, tuple[str, ...]]] = []
    for priority, rule in enumerate(_FAMILY_RULES):
        matches = tuple(anchor for anchor, _weight in rule.anchors if _matching_text(anchor) in match_text)
        score = sum(weight for anchor, weight in rule.anchors if _matching_text(anchor) in match_text)
        ranked.append((score, -priority, rule, matches))

    customs_score, _customs_priority, customs_winner, customs_matches = max(
        (item for item in ranked if item[2].document_type == "customs_declaration"),
        key=lambda item: (item[0], item[1]),
    )
    if customs_score >= 1.0:
        return PageClassification(
            page_number=page_number,
            document_type=customs_winner.document_type,
            document_family=customs_winner.family,
            match_score=round(min(1.0, customs_score / 2.5), 3),
            matched_anchors=customs_matches,
            reasons=(f"matched deterministic anchors for {customs_winner.family}",),
        )

    score, _priority, winner, matches = max(ranked, key=lambda item: (item[0], item[1]))
    customs_layout_matches = tuple(anchor for anchor in _CUSTOMS_LAYOUT_ANCHORS if anchor in match_text)
    if winner.document_type == "commercial_invoice" and len(customs_layout_matches) >= 3:
        return PageClassification(
            page_number=page_number,
            document_type="customs_declaration",
            document_family=None,
            match_score=round(min(0.49, 0.2 + len(customs_layout_matches) * 0.05), 3),
            matched_anchors=customs_layout_matches,
            reasons=("multiple customs-form layout anchors override incidental invoice-family text",),
        )
    if score >= 1.0:
        return PageClassification(
            page_number=page_number,
            document_type=winner.document_type,
            document_family=winner.family,
            match_score=round(min(1.0, score / 2.5), 3),
            matched_anchors=matches,
            reasons=(f"matched deterministic anchors for {winner.family}",),
        )

    customs_matches = tuple(anchor for anchor in _GENERIC_CUSTOMS_ANCHORS if _matching_text(anchor) in match_text)
    invoice_matches = tuple(anchor for anchor in _GENERIC_INVOICE_ANCHORS if _matching_text(anchor) in match_text)
    if customs_matches:
        return PageClassification(
            page_number, "customs_declaration", None,
            round(min(0.49, 0.2 + len(customs_matches) * 0.08), 3),
            customs_matches,
            ("customs evidence found, but no known family reached the rule threshold",),
        )
    if invoice_matches:
        return PageClassification(
            page_number, "commercial_invoice", None,
            round(min(0.49, 0.2 + len(invoice_matches) * 0.08), 3),
            invoice_matches,
            ("invoice evidence found, but no known family reached the rule threshold",),
        )
    return PageClassification(
        page_number, "unknown", None, 0.0, (),
        ("no deterministic page-family anchors matched",),
    )


def classify_pages(ocr_result: OCRResult) -> list[PageClassification]:
    page_numbers = set(range(1, max(ocr_result.page_count, 1) + 1))
    page_numbers.update(line.page_number for line in ocr_result.lines)
    return [classify_page(ocr_result.lines, page_number) for page_number in sorted(page_numbers)]


def group_logical_documents(classifications: list[PageClassification]) -> list[LogicalDocumentGroup]:
    groups: list[list[PageClassification]] = []
    for classification in sorted(classifications, key=lambda item: item.page_number):
        previous = groups[-1][-1] if groups else None
        can_continue = bool(
            previous
            and not classification.starts_new_document
            and classification.document_type == previous.document_type
            and classification.document_family == previous.document_family
        )
        if can_continue:
            groups[-1].append(classification)
        else:
            groups.append([classification])

    return [
        LogicalDocumentGroup(
            group_id=f"logical_document_{index}",
            pages=tuple(item.page_number for item in group),
            document_type=group[0].document_type,
            document_family=group[0].document_family,
            page_classifications=tuple(group),
        )
        for index, group in enumerate(groups, start=1)
    ]


def _matching_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^\w\u0600-\u06ff]+", " ", without_marks, flags=re.UNICODE).strip()
