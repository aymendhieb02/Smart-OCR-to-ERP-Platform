from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable

from app.utils.helpers import strip_accents

try:  # rapidfuzz is preferred, but fuzzy matching must remain defensive.
    from rapidfuzz import fuzz  # type: ignore
except Exception:  # pragma: no cover - exercised when optional dependency is absent
    fuzz = None


DEFAULT_FUZZY_THRESHOLD = 85


def keyword_matches(text: str, keywords: Iterable[str], *, threshold: int = DEFAULT_FUZZY_THRESHOLD) -> bool:
    return bool(matched_keywords(text, keywords, threshold=threshold))


def matched_keywords(text: str, keywords: Iterable[str], *, threshold: int = DEFAULT_FUZZY_THRESHOLD) -> list[str]:
    plain = _normalize(text)
    if not plain:
        return []
    matches: list[str] = []
    for keyword in keywords:
        key = _normalize(keyword)
        if not key:
            continue
        if _exact_keyword_match(plain, key) or _fuzzy_keyword_match(plain, key, threshold=threshold):
            matches.append(keyword)
    return matches


def _exact_keyword_match(text: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in text
    return bool(re.search(rf"\b{re.escape(keyword)}\b", text))


def _fuzzy_keyword_match(text: str, keyword: str, *, threshold: int) -> bool:
    tokens = _candidate_tokens(text, keyword)
    if not tokens:
        return False
    adjusted_threshold = _adjusted_threshold(keyword, threshold)
    for token in tokens:
        score = _score(token, keyword)
        if score >= adjusted_threshold:
            return True
    return False


def _candidate_tokens(text: str, keyword: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text)
    key_words = re.findall(r"[a-z0-9]+", keyword)
    if not words or not key_words:
        return []
    span = max(1, len(key_words))
    candidates = [" ".join(words[index:index + span]) for index in range(0, max(1, len(words) - span + 1))]
    if span == 1 and len(keyword) >= 6:
        candidates.extend(word for word in words if abs(len(word) - len(keyword)) <= 3)
    return candidates


def _score(value: str, keyword: str) -> float:
    value = _normalize_ocr_confusions(value)
    keyword = _normalize_ocr_confusions(keyword)
    if fuzz is not None:
        return max(float(fuzz.ratio(value, keyword)), float(fuzz.partial_ratio(value, keyword)))
    return SequenceMatcher(None, value, keyword).ratio() * 100


def _adjusted_threshold(keyword: str, threshold: int) -> int:
    if len(keyword) <= 4:
        return min(threshold, 75)
    if len(keyword) <= 7:
        return min(threshold, 80)
    if len(keyword) <= 12:
        return min(threshold, 75)
    return min(threshold, 82)


@lru_cache(maxsize=4096)
def _normalize(value: str) -> str:
    value = strip_accents(value or "").lower()
    value = value.replace("œ", "oe").replace("æ", "ae")
    return re.sub(r"\s+", " ", value).strip()


def _normalize_ocr_confusions(value: str) -> str:
    return value.translate(str.maketrans({"0": "o", "1": "l"}))
