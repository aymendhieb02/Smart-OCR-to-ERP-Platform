from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import settings


OFFICIAL_REPOSITORY = "https://github.com/microsoft/table-transformer"
OFFICIAL_STRUCTURE_MODEL_ID = "microsoft/table-transformer-structure-recognition"


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
            cache_dir = self.model_path
            cache_dir.mkdir(parents=True, exist_ok=True)
            downloaded = not _has_hf_snapshot(cache_dir)
            processor = AutoImageProcessor.from_pretrained(
                OFFICIAL_STRUCTURE_MODEL_ID,
                cache_dir=str(cache_dir),
                local_files_only=not download,
            )
            model = TableTransformerForObjectDetection.from_pretrained(
                OFFICIAL_STRUCTURE_MODEL_ID,
                cache_dir=str(cache_dir),
                local_files_only=not download,
            )
            model.to(self.device)
            model.eval()
            return TableTransformerLoadResult(True, model=model, processor=processor, device=self.device, model_path=cache_dir, downloaded=downloaded)
        except Exception as exc:
            return TableTransformerLoadResult(False, device=self.device, model_path=self.model_path, error=str(exc))


def _has_hf_snapshot(cache_dir: Path) -> bool:
    return any(cache_dir.glob("models--microsoft--table-transformer-structure-recognition/snapshots/*"))


def _normalize_device(value: str) -> str:
    text = (value or "cpu").strip().lower()
    if text.startswith("cuda"):
        return "cuda"
    return "cpu"
