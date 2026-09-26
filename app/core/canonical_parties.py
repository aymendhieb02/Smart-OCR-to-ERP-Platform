"""Small, role and document scoped business party canonicalization registry."""

from __future__ import annotations

import re
import unicodedata


RUSPINA_TRADE_NET_IMPORTER = "RUSPINA IMP EXP P-C GROUP BYOUT EZZ LIBYE"
RUSPINA_IMPORT_EXPORT_PREFIX = "RUSPINA IMPORT ET EXPORT P-C"
_CUSTOMS_PARTY_FAMILIES = frozenset({"customs_tradenet_v1", "customs_douanes_tunisiennes_v1"})
_RUSPINA_IMPORT_EXPORT_PREFIX = re.compile(
    r"^\s*(?:R?U[S$]P[I1]NA)\s+(?:IMPORT|IMP)\s+(?:ET\s*)?(?:EXPORT|EXP)\s*P\s*[-‐‑‒–—]?\s*C(?P<suffix>.*)$",
    re.IGNORECASE,
)


def canonicalize_party(value: str, *, role: str, document_family: str) -> tuple[str, str | None]:
    """Return a known canonical party only when several distinctive tokens agree."""
    if role != "importer" or document_family not in _CUSTOMS_PARTY_FAMILIES:
        return value, None
    prefix_match = _RUSPINA_IMPORT_EXPORT_PREFIX.match(unicodedata.normalize("NFKC", value))
    if prefix_match:
        suffix = re.sub(r"^[\s:;,]+", "", prefix_match.group("suffix"))
        suffix = " ".join(suffix.split())
        effective = RUSPINA_IMPORT_EXPORT_PREFIX + (f" {suffix}" if suffix else "")
        return effective, "Matched the strong RUSPINA IMPORT/EXPORT P-C importer prefix; preserved downstream company text."
    normalized = _normalize_ocr_party(value)
    tokens = set(normalized.split())
    # Recognize only documented OCR variants for this one registered entity.
    has_ruspina = "ruspina" in tokens
    has_import = bool(tokens & {"imp", "import", "imports"})
    has_export = bool(tokens & {"exp", "export", "exports"})
    has_group = "group" in tokens
    has_byout = "byout" in tokens
    has_ezz = "ezz" in tokens
    core = (has_ruspina, has_import, has_export, has_group, has_byout, has_ezz)
    country = bool(tokens & {"libye", "libya", "libyf"})
    if has_ruspina and has_group and has_byout and sum(core) >= 5:
        reason = "Matched the registered importer using distinctive identity tokens"
        reason += " and country evidence." if country else "; country text was absent or noisy."
        return RUSPINA_TRADE_NET_IMPORTER, reason
    return value, None


def _normalize_ocr_party(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    plain = plain.replace("$", "s").replace("2", "z").replace("1", "i")
    # P-C, PC, and P C are equivalent separators in this registered name.
    return " ".join(re.findall(r"[a-z]+", plain))
