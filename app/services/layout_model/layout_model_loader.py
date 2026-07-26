from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import settings


OFFICIAL_MODEL_ID = "juliozhao/DocLayout-YOLO-DocStructBench"
OFFICIAL_MODEL_FILENAME = "doclayout_yolo_docstructbench_imgsz1024.pt"
OFFICIAL_REPOSITORY = "https://huggingface.co/juliozhao/DocLayout-YOLO-DocStructBench"


@dataclass
class LayoutModelLoadResult:
    available: bool
    model: Any | None = None
    device: str = "cpu"
    model_path: Path | None = None
    error: str | None = None
    downloaded: bool = False
    source: str = OFFICIAL_MODEL_ID


class LayoutModelLoader:
    """Defensive lazy singleton loader for optional DocLayout-YOLO weights."""

    _lock = Lock()
    _singleton: LayoutModelLoadResult | None = None

    def __init__(self, model_path: Path | None = None, device: str | None = None) -> None:
        self.model_path = Path(model_path or settings.layout_model_path)
        self.device = _normalize_device(device or settings.layout_model_device)

    def load(self, *, download: bool = False) -> LayoutModelLoadResult:
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

    def _load(self, *, download: bool) -> LayoutModelLoadResult:
        try:
            from doclayout_yolo import YOLOv10  # type: ignore
        except Exception as exc:
            return LayoutModelLoadResult(False, device=self.device, model_path=self.model_path, error=f"optional dependency unavailable: {exc}")

        weights = self._resolve_weights(download=download)
        if weights is None:
            return LayoutModelLoadResult(False, device=self.device, model_path=self.model_path, error=f"weights not found: {self.model_path}")

        try:
            model = YOLOv10(str(weights))
            return LayoutModelLoadResult(True, model=model, device=self.device, model_path=weights, downloaded=False, source=OFFICIAL_MODEL_ID)
        except Exception as exc:
            return LayoutModelLoadResult(False, device=self.device, model_path=weights, error=str(exc))

    def _resolve_weights(self, *, download: bool) -> Path | None:
        if self.model_path.is_file():
            return self.model_path
        candidate = self.model_path / OFFICIAL_MODEL_FILENAME
        if candidate.exists():
            return candidate
        if not download:
            return None
        try:
            from huggingface_hub import hf_hub_download  # type: ignore
            downloaded = hf_hub_download(
                repo_id=OFFICIAL_MODEL_ID,
                filename=OFFICIAL_MODEL_FILENAME,
                cache_dir=str(self.model_path),
            )
            return Path(downloaded)
        except Exception:
            return None


def _normalize_device(value: str) -> str:
    text = (value or "cpu").strip().lower()
    if text.startswith("cuda"):
        return text
    return "cpu"
