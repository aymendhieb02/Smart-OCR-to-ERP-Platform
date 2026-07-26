from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.services.file_loader import load_document


def _sample_image() -> Image.Image:
    return Image.fromarray(np.full((12, 16, 3), [220, 40, 80], dtype=np.uint8), mode="RGB")


def test_jfif_file_is_accepted_and_decodes(tmp_path: Path) -> None:
    path = tmp_path / "sample.jfif"
    _sample_image().save(path, format="JPEG")

    document = load_document(path, "sample.jfif")

    assert document.extension == ".jfif"
    assert document.images
    assert document.images[0].shape[:2] == (12, 16)


def test_avif_file_is_accepted_and_decodes(tmp_path: Path) -> None:
    pytest.importorskip("pillow_avif")
    path = tmp_path / "sample.avif"
    _sample_image().save(path, format="AVIF")

    document = load_document(path, "sample.avif")

    assert document.extension == ".avif"
    assert document.images
    assert document.images[0].shape[:2] == (12, 16)


def test_unsupported_extension_still_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "sample.webp"
    path.write_bytes(b"not supported")

    with pytest.raises(ValueError, match=r"Unsupported file format: \.webp"):
        load_document(path, "sample.webp")


def test_invalid_supported_image_returns_decode_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.jfif"
    path.write_bytes(b"not a jpeg")

    with pytest.raises(ValueError, match="decode_error"):
        load_document(path, "broken.jfif")
