from __future__ import annotations

import hashlib
from pathlib import Path


def correction_document_id(source_path: Path, logical_document_id: str) -> str:
    digest = hashlib.sha256()
    with source_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"{digest.hexdigest()}:{logical_document_id}"
