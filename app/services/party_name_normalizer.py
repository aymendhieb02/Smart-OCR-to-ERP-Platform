from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any


LEGAL_SUFFIXES = {
    "bv",
    "corp",
    "corporation",
    "eurl",
    "gmbh",
    "inc",
    "limited",
    "llc",
    "llp",
    "ltd",
    "nv",
    "plc",
    "sa",
    "sarl",
    "sas",
    "sasu",
    "snc",
    "spa",
    "suarl",
}

GENERIC_TOKENS = {
    "company",
    "corp",
    "corporation",
    "enterprise",
    "enterprises",
    "global",
    "group",
    "inc",
    "international",
    "limited",
    "llc",
    "ltd",
    "service",
    "services",
    "solution",
    "solutions",
    "trading",
}

ADDRESS_MARKERS = {
    "allee",
    "apartment",
    "apt",
    "avenue",
    "ave",
    "bloc",
    "block",
    "boulevard",
    "bvd",
    "building",
    "city",
    "country",
    "etage",
    "floor",
    "apo",
    "immeuble",
    "fpo",
    "lot",
    "lotissement",
    "road",
    "route",
    "rue",
    "street",
    "suite",
    "uss",
    "zip",
    "شارع",
    "طريق",
}

CONTACT_MARKERS = {
    "courriel",
    "email",
    "fax",
    "mail",
    "phone",
    "site",
    "tel",
    "telephone",
    "web",
    "website",
    "هاتف",
    "بريد",
}

TAX_MARKERS = {
    "ice",
    "identifiant fiscal",
    "matricule fiscal",
    "mf",
    "registration",
    "tax id",
    "vat",
    "معرف جبائي",
}

METADATA_MARKERS = {
    "bill to",
    "billing",
    "client",
    "customer",
    "date",
    "due date",
    "facture",
    "invoice",
    "invoice number",
    "livre a",
    "ship to",
    "supplier",
    "vendor",
}


@dataclass(frozen=True)
class PartyNormalization:
    raw: str
    normalized_full: str
    canonical_name: str
    canonical_with_legal_suffix: str
    canonical_without_legal_suffix: str
    legal_suffix_stripped: str
    address_removed: bool = False
    contact_removed: bool = False
    tax_id_removed: bool = False
    metadata_removed: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PartyGroundTruth:
    raw_value: Any
    canonical_name: str | None
    address: str | None
    source_schema: str
    normalization_warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PartyComparison:
    strict_exact_match: bool | None
    normalized_full_exact_match: bool | None
    canonical_exact_match: bool | None
    canonical_without_suffix_exact_match: bool | None
    token_set_similarity: float | None
    token_sort_similarity: float | None
    character_similarity: float | None
    containment_match: bool
    match_classification: str
    final_match: bool | None
    mismatch_reason: str
    truth: PartyNormalization | None
    prediction: PartyNormalization | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["truth"] = self.truth.to_dict() if self.truth else None
        payload["prediction"] = self.prediction.to_dict() if self.prediction else None
        return payload


def normalize_party_text(value: Any) -> PartyNormalization:
    raw = "" if value is None else _stringify(value)
    lines = [_normalize_unicode(line) for line in raw.replace("\r", "\n").split("\n")]
    normalized_lines = [_normalize_spacing(line) for line in lines if _normalize_spacing(line)]
    normalized_full = _normalize_for_compare(" ".join(normalized_lines))

    kept: list[str] = []
    address_removed = False
    contact_removed = False
    tax_id_removed = False
    metadata_removed = False
    warnings: list[str] = []
    for line in normalized_lines:
        reason = _line_removal_reason(line)
        if reason == "address":
            address_removed = True
            prefix = _leading_party_before_inline_address(line)
            if prefix:
                kept.append(prefix)
                warnings.append("inline_address_tail_removed")
            continue
        if reason == "contact":
            contact_removed = True
            continue
        if reason == "tax":
            tax_id_removed = True
            continue
        if reason == "metadata":
            metadata_removed = True
            continue
        kept.append(line)

    if not kept and normalized_lines:
        first_plausible = _first_plausible_name_line(normalized_lines)
        if first_plausible:
            kept.append(first_plausible)
            warnings.append("fallback_first_plausible_line")

    canonical = _dedupe_tokens(_normalize_for_compare(" ".join(kept)))
    canonical = _strip_leading_labels(canonical)
    with_suffix = canonical
    without_suffix = _strip_legal_suffix(canonical)
    if canonical and not _has_meaningful_token(canonical):
        warnings.append("generic_or_too_short")

    return PartyNormalization(
        raw=raw,
        normalized_full=normalized_full,
        canonical_name=canonical,
        canonical_with_legal_suffix=with_suffix,
        canonical_without_legal_suffix=without_suffix,
        legal_suffix_stripped=without_suffix,
        address_removed=address_removed,
        contact_removed=contact_removed,
        tax_id_removed=tax_id_removed,
        metadata_removed=metadata_removed,
        warnings=warnings,
    )


def extract_canonical_company_name(value: Any) -> str | None:
    normalized = normalize_party_text(value)
    return normalized.canonical_name or None


def compare_party_names(predicted: Any, truth: Any) -> PartyComparison:
    if predicted in (None, "") and truth in (None, ""):
        return _comparison(None, None, "unavailable", None, "missing prediction and truth")
    if truth in (None, ""):
        return _comparison(None, normalize_party_text(predicted), "unavailable", None, "truth missing")
    if predicted in (None, ""):
        return _comparison(normalize_party_text(truth), None, "mismatch", False, "prediction missing")

    truth_norm = normalize_party_text(truth)
    pred_norm = normalize_party_text(predicted)
    strict = str(predicted).strip() == str(truth).strip()
    full_exact = bool(pred_norm.normalized_full and pred_norm.normalized_full == truth_norm.normalized_full)
    canonical_exact = bool(pred_norm.canonical_name and pred_norm.canonical_name == truth_norm.canonical_name)
    suffix_exact = bool(
        pred_norm.canonical_without_legal_suffix
        and pred_norm.canonical_without_legal_suffix == truth_norm.canonical_without_legal_suffix
    )
    token_set = _token_set_similarity(pred_norm.canonical_name, truth_norm.canonical_name)
    token_sort = _token_sort_similarity(pred_norm.canonical_name, truth_norm.canonical_name)
    char_score = _character_similarity(pred_norm.canonical_name, truth_norm.canonical_name)
    containment = _meaningful_containment(pred_norm, truth_norm)

    classification = "mismatch"
    final_match = False
    reason = "different canonical company names"
    if strict:
        classification = "exact"
        reason = "raw values match exactly"
    elif canonical_exact:
        classification = "canonical_exact"
        reason = "canonical company names match"
    elif suffix_exact:
        classification = "canonical_exact"
        reason = "legal suffix only difference"
    elif _strong_fuzzy(token_set, token_sort, char_score, pred_norm, truth_norm):
        classification = "strong_fuzzy"
        reason = "high similarity with meaningful token overlap"
    elif containment:
        classification = "partial"
        reason = "company-name subset with address/contact removed from truth"
    elif _ambiguous(token_set, token_sort, pred_norm, truth_norm):
        classification = "ambiguous"
        final_match = None
        reason = "moderate similarity or short/generic company name"

    if classification in {"exact", "canonical_exact", "strong_fuzzy", "partial"}:
        final_match = True

    return PartyComparison(
        strict_exact_match=strict,
        normalized_full_exact_match=full_exact,
        canonical_exact_match=canonical_exact,
        canonical_without_suffix_exact_match=suffix_exact,
        token_set_similarity=token_set,
        token_sort_similarity=token_sort,
        character_similarity=char_score,
        containment_match=containment,
        match_classification=classification,
        final_match=final_match,
        mismatch_reason=reason,
        truth=truth_norm,
        prediction=pred_norm,
    )


def classify_party_match(predicted: Any, truth: Any) -> str:
    return compare_party_names(predicted, truth).match_classification


def adapt_party_ground_truth(value: Any) -> PartyGroundTruth:
    raw, schema, warnings = _extract_party_raw(value)
    if raw in (None, "", [], {}):
        return PartyGroundTruth(value, None, None, schema or "unsupported", warnings + ["no usable party value"])
    normalized = normalize_party_text(raw)
    address = _extract_address_text(raw)
    return PartyGroundTruth(
        raw_value=raw,
        canonical_name=normalized.canonical_name or None,
        address=address,
        source_schema=schema,
        normalization_warnings=warnings + normalized.warnings,
    )


def _comparison(
    truth: PartyNormalization | None,
    prediction: PartyNormalization | None,
    classification: str,
    final: bool | None,
    reason: str,
) -> PartyComparison:
    return PartyComparison(
        strict_exact_match=None,
        normalized_full_exact_match=None,
        canonical_exact_match=None,
        canonical_without_suffix_exact_match=None,
        token_set_similarity=None,
        token_sort_similarity=None,
        character_similarity=None,
        containment_match=False,
        match_classification=classification,
        final_match=final,
        mismatch_reason=reason,
        truth=truth,
        prediction=prediction,
    )


def _extract_party_raw(value: Any) -> tuple[Any, str, list[str]]:
    if value in (None, ""):
        return None, "missing", []
    if isinstance(value, str):
        return value, "string", []
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                nested, _schema, _warnings = _extract_party_raw(item)
                if nested:
                    parts.append(str(nested))
        return ("\n".join(parts), "array", []) if parts else (None, "unsupported_array", ["array contained no text"])
    if isinstance(value, dict):
        keys = (
            "name",
            "company_name",
            "supplier_name",
            "vendor_name",
            "customer_name",
            "client_name",
            "seller",
            "buyer",
            "text",
            "value",
            "address",
        )
        parts = []
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                parts.append(item)
            elif isinstance(item, list):
                parts.extend(str(part) for part in item if str(part).strip())
        if parts:
            return "\n".join(parts), "dict:" + ",".join(key for key in keys if key in value), []
        for item in value.values():
            nested, schema, _warnings = _extract_party_raw(item)
            if nested:
                return nested, "nested_" + schema, []
        return None, "unsupported_dict", ["dictionary contained no supported party keys"]
    return None, f"unsupported_{type(value).__name__}", [f"unsupported party value type: {type(value).__name__}"]


def _stringify(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_stringify(item)}" for key, item in value.items())
    return str(value)


def _normalize_unicode(value: str) -> str:
    text = html.unescape(value)
    text = text.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text


def _normalize_spacing(value: str) -> str:
    return " ".join(value.strip().split())


def _normalize_for_compare(value: str) -> str:
    text = _normalize_unicode(value).casefold()
    text = re.sub(r"[\u200f\u200e]", " ", text)
    text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text)
    return _normalize_spacing(text)


def _line_removal_reason(line: str) -> str | None:
    norm = _normalize_for_compare(line)
    tokens = set(norm.split())
    if not norm:
        return "metadata"
    if _contains_phrase(norm, CONTACT_MARKERS) or re.search(r"[\w.+-]+@[\w.-]+\.\w+", line):
        return "contact"
    if re.search(r"\b(?:www|https?)\b", norm):
        return "contact"
    if _contains_phrase(norm, TAX_MARKERS) or re.search(r"\b(?:vat|mf|ice|tax)\b", norm):
        return "tax"
    if re.search(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,}\b", line.replace(" ", ""), re.IGNORECASE):
        return "tax"
    if _contains_phrase(norm, METADATA_MARKERS) and len(tokens) <= 4:
        return "metadata"
    if _looks_like_address(norm):
        return "address"
    return None


def _contains_phrase(norm: str, phrases: set[str]) -> bool:
    padded = f" {norm} "
    return any(f" {_normalize_for_compare(phrase)} " in padded for phrase in phrases)


def _looks_like_address(norm: str) -> bool:
    tokens = norm.split()
    if not tokens:
        return False
    marker_hit = any(token in ADDRESS_MARKERS for token in tokens) or _contains_phrase(norm, ADDRESS_MARKERS)
    has_digit = any(char.isdigit() for char in norm)
    postal = bool(re.search(r"\b\d{4,6}(?:-\d{3,5})?\b", norm))
    if marker_hit and (has_digit or len(tokens) <= 8):
        return True
    if postal and len(tokens) <= 8:
        return True
    if has_digit and len(tokens) >= 3 and any(token in {"ny", "ca", "tn", "tunis", "tunisie", "france", "canada", "usa", "us"} for token in tokens):
        return True
    return False


def _leading_party_before_inline_address(line: str) -> str | None:
    text = _normalize_spacing(line)
    if not text:
        return None
    patterns = [
        r"^(?P<name>.+?)\s+\d{2,}\b",
        r"^(?P<name>.+?)\s+\b(?:apt|suite|road|route|rue|street|avenue|ave|blvd|boulevard)\b",
        r"^(?P<name>.+?)\s+\b(?:fpo|apo)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        name = _normalize_spacing(match.group("name").strip(" ,:-"))
        norm = _normalize_for_compare(name)
        if _has_meaningful_token(norm) and not any(char.isdigit() for char in norm) and not _looks_like_address(norm):
            return name
    return None


def _first_plausible_name_line(lines: list[str]) -> str | None:
    for line in lines:
        norm = _normalize_for_compare(line)
        if _line_removal_reason(line) is None and _has_meaningful_token(norm):
            return line
    return None


def _strip_leading_labels(value: str) -> str:
    labels = {"from", "supplier", "vendor", "seller", "customer", "client", "buyer", "bill to", "ship to"}
    result = value
    for label in sorted(labels, key=len, reverse=True):
        normalized = _normalize_for_compare(label)
        if result == normalized:
            return ""
        if result.startswith(normalized + " "):
            result = result[len(normalized) + 1 :]
            break
    return result


def _strip_legal_suffix(value: str) -> str:
    tokens = value.split()
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _dedupe_tokens(value: str) -> str:
    output: list[str] = []
    previous = None
    for token in value.split():
        if token != previous:
            output.append(token)
        previous = token
    return " ".join(output)


def _tokens(value: str) -> set[str]:
    return {token for token in value.split() if token and token not in GENERIC_TOKENS}


def _has_meaningful_token(value: str) -> bool:
    tokens = _tokens(value)
    return bool(tokens) and not (len(tokens) == 1 and len(next(iter(tokens))) < 4)


def _token_set_similarity(first: str, second: str) -> float:
    try:
        from rapidfuzz.fuzz import token_set_ratio  # type: ignore

        return round(float(token_set_ratio(first, second)), 2)
    except Exception:
        return round(_fallback_ratio(" ".join(sorted(set(first.split()))), " ".join(sorted(set(second.split())))), 2)


def _token_sort_similarity(first: str, second: str) -> float:
    try:
        from rapidfuzz.fuzz import token_sort_ratio  # type: ignore

        return round(float(token_sort_ratio(first, second)), 2)
    except Exception:
        return round(_fallback_ratio(" ".join(sorted(first.split())), " ".join(sorted(second.split()))), 2)


def _character_similarity(first: str, second: str) -> float:
    try:
        from rapidfuzz.fuzz import ratio  # type: ignore

        return round(float(ratio(first, second)), 2)
    except Exception:
        return round(_fallback_ratio(first, second), 2)


def _fallback_ratio(first: str, second: str) -> float:
    if not first or not second:
        return 0.0
    import difflib

    return difflib.SequenceMatcher(None, first, second).ratio() * 100


def _meaningful_containment(pred: PartyNormalization, truth: PartyNormalization) -> bool:
    pred_name = pred.canonical_without_legal_suffix or pred.canonical_name
    truth_name = truth.canonical_without_legal_suffix or truth.canonical_name
    if not pred_name or not truth_name:
        return False
    if not _has_meaningful_token(pred_name) or not _has_meaningful_token(truth_name):
        return False
    pred_tokens = _tokens(pred_name)
    truth_tokens = _tokens(truth_name)
    if len(pred_tokens) < 2 and len(truth_tokens) < 2:
        return False
    return pred_name in truth_name or truth_name in pred_name or pred_tokens.issubset(truth_tokens) or truth_tokens.issubset(pred_tokens)


def _strong_fuzzy(token_set: float, token_sort: float, char_score: float, pred: PartyNormalization, truth: PartyNormalization) -> bool:
    if not _has_meaningful_token(pred.canonical_name) or not _has_meaningful_token(truth.canonical_name):
        return False
    overlap = _tokens(pred.canonical_name) & _tokens(truth.canonical_name)
    return bool(overlap) and token_set >= 90 and token_sort >= 85 and char_score >= 80


def _ambiguous(token_set: float, token_sort: float, pred: PartyNormalization, truth: PartyNormalization) -> bool:
    if token_set >= 75 or token_sort >= 75:
        return True
    pred_tokens = _tokens(pred.canonical_name)
    truth_tokens = _tokens(truth.canonical_name)
    return bool(pred_tokens & truth_tokens) and (len(pred_tokens) <= 1 or len(truth_tokens) <= 1)


def _extract_address_text(value: Any) -> str | None:
    lines = _stringify(value).replace("\r", "\n").split("\n")
    address_lines = [line.strip() for line in lines if _line_removal_reason(line) == "address"]
    return "\n".join(address_lines) or None
