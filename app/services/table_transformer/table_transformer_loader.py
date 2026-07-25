from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import settings


OFFICIAL_REPOSITORY = "https://github.com/microsoft/table-transformer"
STRUCTURE_MODEL_URL = (
    "https://huggingface.co/microsoft/table-transformer-structure-recognition/"
    "resolve/main/pytorch_model.bin"
)


@dataclass
class TableTransformerLoadResult:
    available: bool
    model: Any | None = None
    processor: Any | None = None
    device: str = "cpu"
    model_path: Path | None = None
    error: str | None = None
    downloaded: bool = False
    source: str = OFFICIAL_REPOSITORY


class TableTransformerLoader:
    """Lazy singleton loader for the optional TATR structure model.

    The implementation is deliberately defensive: missing torch/transformers or
    missing weights return an unavailable result instead of breaking production
    invoice extraction.
    """

    _lock = Lock()
    _singleton: TableTransformerLoadResult | None = None

    def __init__(self, model_path: Path | None = None, device: str | None = None) -> None:
        self.model_path = Path(model_path or settings.table_transformer_model_path)
        self.device = _normalize_device(device or settings.table_transformer_device)

    def load(self, *, download: bool = True) -> TableTransformerLoadResult:
        with self._lock:
            if self.__class__._singleton is not None:
                return self.__class__._singleton
            result = self._load(download=download)
            self.__class__._singleton = result
            return result

    @classmethod
    def reset_singleton(cls) -> None:
        with cls._lock:
            cls._singleton = None

    def _load(self, *, download: bool) -> TableTransformerLoadResult:
        try:
            import torch  # type: ignore
            from transformers import AutoImageProcessor, TableTransformerForObjectDetection  # type: ignore
        except Exception as exc:
            return TableTransformerLoadResult(False, device=self.device, model_path=self.model_path, error=f"optional dependency unavailable: {exc}")

        if self.device == "cuda" and not torch.cuda.is_available():
            return TableTransformerLoadResult(False, device="cuda", model_path=self.model_path, error="CUDA requested but not available")

        try:
            weight_path, downloaded = self.ensure_weights(download=download)
            model_dir = weight_path.parent
            processor = AutoImageProcessor.from_pretrained(str(model_dir), local_files_only=True)
            model = TableTransformerForObjectDetection.from_pretrained(str(model_dir), local_files_only=True)
            model.to(self.device)
            model.eval()
            return TableTransformerLoadResult(True, model=model, processor=processor, device=self.device, model_path=model_dir, downloaded=downloaded)
        except Exception as exc:
            return TableTransformerLoadResult(False, device=self.device, model_path=self.model_path, error=str(exc))

    def ensure_weights(self, *, download: bool = True) -> tuple[Path, bool]:
        self.model_path.mkdir(parents=True, exist_ok=True)
        weight_path = self.model_path / "pytorch_model.bin"
        config_path = self.model_path / "config.json"
        preprocessor_path = self.model_path / "preprocessor_config.json"
        if weight_path.exists() and config_path.exists() and preprocessor_path.exists():
            return weight_path, False
        if not download:
            raise FileNotFoundError(f"Table Transformer weights missing in {self.model_path}")
        # Store minimal metadata beside official weights. In most environments
        # users will populate the folder through HuggingFace/transformers cache;
        # this direct download path keeps the integration self-contained.
        urllib.request.urlretrieve(STRUCTURE_MODEL_URL, weight_path)
        if not config_path.exists():
            config_path.write_text('{"model_type":"table-transformer"}', encoding="utf-8")
        if not preprocessor_path.exists():
            preprocessor_path.write_text('{"do_resize":true}', encoding="utf-8")
        return weight_path, True


def _normalize_device(value: str) -> str:
    text = (value or "cpu").strip().lower()
    if text.startswith("cuda"):
        return "cuda"
    return "cpu"
