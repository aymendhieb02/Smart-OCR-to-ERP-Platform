from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2
import fitz
import numpy as np
from fastapi import UploadFile

from app.utils.helpers import normalize_text


SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


@dataclass
class LoadedDocument:
    source_file: str
    extension: str
    embedded_text: str = ""
    images: list[np.ndarray] = field(default_factory=list)


async def save_upload_to_temp(upload_file: UploadFile) -> Path:
    suffix = Path(upload_file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {suffix or 'unknown'}")

    with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        while chunk := await upload_file.read(1024 * 1024):
            temp_file.write(chunk)
        return Path(temp_file.name)


def load_document(path: Path, original_filename: str | None = None, timing_recorder=None) -> LoadedDocument:
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {extension or 'unknown'}")

    if extension == ".pdf":
        return _load_pdf(path, original_filename or path.name, timing_recorder=timing_recorder)
    return _load_image(path, original_filename or path.name, timing_recorder=timing_recorder)


def _load_pdf(path: Path, source_file: str, timing_recorder=None) -> LoadedDocument:
    doc = fitz.open(path)
    text_parts: list[str] = []
    images: list[np.ndarray] = []

    try:
        for page in doc:
            page_text = normalize_text(page.get_text("text"))
            if page_text:
                text_parts.append(page_text)

            context = timing_recorder.stage("pdf_rendering", page_number=page.number + 1) if timing_recorder else _noop_stage()
            with context:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                if pix.n == 3:
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                images.append(img)
    finally:
        doc.close()

    return LoadedDocument(
        source_file=source_file,
        extension=".pdf",
        embedded_text="\n\n".join(text_parts),
        images=images,
    )


def _load_image(path: Path, source_file: str, timing_recorder=None) -> LoadedDocument:
    context = timing_recorder.stage("image_decoding", input_type=path.suffix.lower()) if timing_recorder else _noop_stage()
    with context:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unreadable image file")
    return LoadedDocument(source_file=source_file, extension=path.suffix.lower(), images=[image])


class _noop_stage:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False
