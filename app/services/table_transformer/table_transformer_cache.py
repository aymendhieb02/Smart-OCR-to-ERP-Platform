from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings


SCHEMA_VERSION = 1


def image_cache_key(image: np.ndarray, *, page: int = 1) -> str:
    digest = hashlib.sha256()
    digest.update(str(page).encode("utf-8"))
    digest.update(str(image.shape).encode("utf-8"))
    digest.update(image.tobytes())
    return digest.hexdigest()


class TableTransformerCache:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = Path(cache_dir or settings.table_transformer_cache_dir)

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if payload.get("schema_version") != SCHEMA_VERSION:
            return None
        return payload.get("result")

    def set(self, key: str, result: dict[str, Any]) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{key}.json"
        path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "result": result}, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
