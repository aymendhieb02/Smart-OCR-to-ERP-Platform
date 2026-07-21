from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings


def load_cached_llm_response(fingerprint: str) -> dict[str, Any] | None:
    path = _cache_path(fingerprint)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    return payload


def save_cached_llm_response(fingerprint: str, payload: dict[str, Any]) -> Path:
    path = _cache_path(fingerprint)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = dict(payload)
    safe_payload["fingerprint"] = fingerprint
    with path.open("w", encoding="utf-8") as handle:
        json.dump(safe_payload, handle, ensure_ascii=False, indent=2, default=str)
    return path


def _cache_path(fingerprint: str) -> Path:
    return Path(settings.llm_resolver_cache_dir) / f"{fingerprint}.json"
