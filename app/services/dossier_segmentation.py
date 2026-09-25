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
_TRADENET_MASTHEAD_ALIASES = ("tradenet", "tradent", "tradnt", "ttn", "mttn")
_EXPORTER_ALIASES = ("exportateur", "exporteur", "exportaleur", "exportcur")
_IMPORTER_ALIASES = ("importateur", "importateut")
_DECLARATION_HEADER_ALIASES = ("declaration", "declaraton", "dcaratoa")
_DOUANES_MASTHEAD_ALIASES = ("douanes tunisiennes", "douanes tuntsiennes", "douanes tunisenne")


def classify_page(lines: list[OCRLine], page_number: int) -> PageClassification:
    page_lines = [line for line in lines if line.page_number == page_number]
    match_text = _matching_text("\n".join(line.text for line in page_lines if line.text))

    ranked: list[tuple[float, int, _FamilyRule, tuple[str, ...]]] = []
    for priority, rule in enumerate(_FAMILY_RULES):
        matches = tuple(anchor for anchor, _weight in rule.anchors if _matching_text(anchor) in match_text)
        score = sum(weight for anchor, weight in rule.anchors if _matching_text(anchor) in match_text)
        ranked.append((score, -priority, rule, matches))

    tradenet_structure = _tradenet_structure_signals(page_lines)
    if tradenet_structure["douanes_masthead"] and tradenet_structure["customs_structure"]:
        return PageClassification(
            page_number, "customs_declaration", "customs_douanes_tunisiennes_v1", 0.9,
            ("douanes_masthead", "customs_structure"),
            ("Tunisian customs masthead and fixed declaration-form structure",),
        )
    if tradenet_structure["is_tradenet"]:
        matched = tuple(name for name, present in tradenet_structure.items() if present and name != "is_tradenet")
        return PageClassification(
            page_number=page_number,
            document_type="customs_declaration",
            document_family="customs_tradenet_v1",
            match_score=round(min(1.0, 0.55 + 0.1 * len(matched)), 3),
            matched_anchors=matched,
            reasons=("multiple TradeNet masthead, party-role, and structured-header signals",),
        )

    if tradenet_structure["customs_structure"]:
        matched = tuple(name for name, present in tradenet_structure.items() if present and name != "is_tradenet")
        return PageClassification(
            page_number=page_number,
            document_type="customs_declaration",
            document_family=None,
            match_score=round(min(0.49, 0.25 + 0.06 * len(matched)), 3),
            matched_anchors=matched,
            reasons=("multiple customs declaration-header structure signals; family remains uncertain",),
        )

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


def _tradenet_structure_signals(lines: list[OCRLine]) -> dict[str, bool]:
    positioned = [line for line in lines if line.text.strip()]
    normalized_lines = [(_matching_text(line.text), line) for line in positioned]
    boxes = [line.bbox for line in positioned if line.bbox]
    width = max((line.page_width or 0 for line in positioned), default=0) or max((box.x2 for box in boxes), default=0)
    height = max((line.page_height or 0 for line in positioned), default=0) or max((box.y2 for box in boxes), default=0)

    masthead = any(
        any(alias in text for alias in _TRADENET_MASTHEAD_ALIASES)
        or bool(re.search(r"\btt\s+n\b", text))
        for text, line in normalized_lines
        if not line.bbox or not height or line.bbox.y1 / height <= 0.16
    )
    exporter = _party_role_cue(normalized_lines, "exporter", width, height)
    importer = _party_role_cue(normalized_lines, "importer", width, height)
    douanes_masthead = any(
        any(alias in text for alias in _DOUANES_MASTHEAD_ALIASES)
        for text, line in normalized_lines
        if not line.bbox or not height or line.bbox.y1 / height <= 0.10
    )
    declaration_heading = any(
        any(alias in text for alias in _DECLARATION_HEADER_ALIASES)
        and "dae" not in text.replace(" ", "")
        and (not line.bbox or not height or line.bbox.y1 / height <= 0.22)
        for text, line in normalized_lines
    )
    dae_heading = any(
        bool(re.search(r"\bd\s*a\s*e\b|\bdae\b", text))
        and (not line.bbox or not height or line.bbox.y1 / height <= 0.22)
        for text, line in normalized_lines
    )
    paired_header_values = _has_top_number_date_pair(positioned, width, height)
    both_party_roles = exporter and importer

    # Family assignment needs a masthead or an explicit declaration-section
    # cue in addition to both customs party roles and a paired header row.
    is_tradenet = both_party_roles and paired_header_values and (masthead or (declaration_heading and dae_heading))
    customs_structure = both_party_roles and paired_header_values
    return {
        "tradenet_masthead": masthead,
        "douanes_masthead": douanes_masthead,
        "exporter_label": exporter,
        "importer_label": importer,
        "declaration_header": declaration_heading,
        "dae_header": dae_heading,
        "paired_header_values": paired_header_values,
        "customs_structure": customs_structure,
        "is_tradenet": is_tradenet,
    }


def _has_top_number_date_pair(lines: list[OCRLine], width: float, height: float) -> bool:
    if not width or not height:
        return False
    numbers: list[tuple[float, float]] = []
    dates: list[tuple[float, float]] = []
    for line in lines:
        if not line.bbox:
            continue
        box = line.bbox
        center_x = ((box.x1 + box.x2) / 2) / width
        center_y = ((box.y1 + box.y2) / 2) / height
        if center_y > 0.20:
            continue
        normalized = _matching_text(line.text)
        if re.search(r"(?<!\d)\d{4,12}(?!\d)", normalized.replace(" ", "")):
            numbers.append((center_x, center_y))
        if (
            re.search(r"(?<!\d)\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?!\d)", line.text)
            or re.fullmatch(r"[QO0-9]{1,2}[-/.]\d{6}", line.text.strip(), re.IGNORECASE)
            or re.search(r"(?<!\d)[./-]?\d{1,2}[./-]\d{2,4}(?!\d)", line.text)
        ):
            dates.append((center_x, center_y))
    return any(abs(nx - dx) <= 0.28 and abs(ny - dy) <= 0.055 for nx, ny in numbers for dx, dy in dates)


def _party_role_cue(normalized_lines: list[tuple[str, OCRLine]], role: str, width: float, height: float) -> bool:
    aliases = _EXPORTER_ALIASES if role == "exporter" else _IMPORTER_ALIASES
    prefixes = ("expor", "expr", "lxpor") if role == "exporter" else ("impor", "imyx", "inn")
    for text, line in normalized_lines:
        if not line.bbox or not width or not height:
            if any(alias in text for alias in aliases):
                return True
            continue
        center_x = (line.bbox.x1 + line.bbox.x2) / (2 * width)
        center_y = (line.bbox.y1 + line.bbox.y2) / (2 * height)
        if 0.16 <= center_x <= 0.48 and 0.012 <= center_y <= 0.16 and len(text) <= 18:
            if any(alias in text for alias in aliases) or text.startswith(prefixes):
                return True
    return False


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
