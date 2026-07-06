from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

from app.core.schemas import DocumentPreview, PreviewPage
from app.services.file_loader import LoadedDocument


PREVIEW_DIR = Path(__file__).resolve().parents[1] / "static" / "previews"


def generate_document_preview(document: LoadedDocument) -> DocumentPreview:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(document.source_file)
    pages: list[PreviewPage] = []

    for page_index, image in enumerate(document.images, start=1):
        preview = _normalize_preview_image(image)
        height, width = preview.shape[:2]
        digest = hashlib.sha1(preview.tobytes()[:50000] + document.source_file.encode("utf-8")).hexdigest()[:10]
        filename = f"{stem}_p{page_index}_{digest}.png"
        path = PREVIEW_DIR / filename
        cv2.imwrite(str(path), preview)
        pages.append(PreviewPage(page=page_index, url=f"/static/previews/{filename}", width=width, height=height))

    return DocumentPreview(pages=pages, source_file=document.source_file)


def _normalize_preview_image(image: np.ndarray) -> np.ndarray:
    if len(image.shape) == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem or "document"
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in stem)[:80]
