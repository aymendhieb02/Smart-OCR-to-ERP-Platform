from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Iterator
from uuid import uuid4


@dataclass
class TimingStage:
    name: str
    seconds: float
    success: bool = True
    depth: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None


class PipelineTimer:
    """Low-overhead nested timing recorder for document processing.

    The disabled mode is intentionally tiny: the context manager yields without
    touching time.perf_counter(), so normal application usage pays almost no cost.
    """

    def __init__(self, *, enabled: bool = False, metadata: dict[str, Any] | None = None) -> None:
        self.enabled = enabled
        self.document_id = str(uuid4())
        self.metadata: dict[str, Any] = {
            str(key): _safe_metadata_value(str(key), value)
            for key, value in (metadata or {}).items()
            if value is not None
        }
        self.records: list[TimingStage] = []
        self.started_at = datetime.now(timezone.utc)
        self._stack: list[str] = []

    @contextmanager
    def stage(self, name: str, **metadata: Any) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        depth = len(self._stack)
        self._stack.append(name)
        started = time.perf_counter()
        success = True
        error_type: str | None = None
        try:
            yield
        except Exception as exc:
            success = False
            error_type = type(exc).__name__
            raise
        finally:
            seconds = time.perf_counter() - started
            self._stack.pop()
            self.records.append(TimingStage(
                name=name,
                seconds=seconds,
                success=success,
                depth=depth,
                metadata={key: _safe_metadata_value(key, value) for key, value in metadata.items() if value is not None},
                error_type=error_type,
            ))

    def add_measurement(self, name: str, seconds: float | int | None, **metadata: Any) -> None:
        if not self.enabled or seconds is None:
            return
        try:
            numeric = float(seconds)
        except (TypeError, ValueError):
            return
        self.records.append(TimingStage(
            name=name,
            seconds=max(0.0, numeric),
            success=True,
            depth=max(0, len(self._stack)),
            metadata={key: _safe_metadata_value(key, value) for key, value in metadata.items() if value is not None},
        ))

    def set_metadata(self, **metadata: Any) -> None:
        if not self.enabled:
            return
        for key, value in metadata.items():
            if value is not None:
                self.metadata[key] = _safe_metadata_value(key, value)

    def aggregate(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        if not self.enabled:
            return totals
        for record in self.records:
            totals[record.name] = totals.get(record.name, 0.0) + record.seconds
        return {key: round(value, 6) for key, value in totals.items()}

    def total_seconds(self) -> float:
        totals = self.aggregate()
        if "total_pipeline" in totals:
            return totals["total_pipeline"]
        root = [record.seconds for record in self.records if record.depth == 0]
        return round(sum(root), 6) if root else 0.0

    def percentages(self) -> dict[str, float]:
        total = self.total_seconds()
        if total <= 0:
            return {key: 0.0 for key in self.aggregate()}
        return {key: round((seconds / total) * 100, 3) for key, seconds in self.aggregate().items()}

    def to_result(
        self,
        *,
        document: str | None = None,
        success: bool = True,
        error_type: str | None = None,
        validation_status: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(self.metadata)
        metadata.update({key: _safe_metadata_value(key, value) for key, value in (extra_metadata or {}).items() if value is not None})
        total = self.total_seconds()
        return {
            "document_id": metadata.get("document_id") or self.document_id,
            "document": _safe_document_name(document or metadata.get("document") or metadata.get("filename")),
            "success": success,
            "error_type": error_type,
            "validation_status": validation_status or metadata.get("validation_status"),
            "total_seconds": total,
            "stages": self.aggregate(),
            "stage_percentages": self.percentages(),
            "metadata": metadata,
            "records": [
                {
                    "name": record.name,
                    "seconds": round(record.seconds, 6),
                    "success": record.success,
                    "depth": record.depth,
                    "metadata": record.metadata,
                    "error_type": record.error_type,
                }
                for record in self.records
            ] if self.enabled else [],
        }


def disabled_timer() -> PipelineTimer:
    return PipelineTimer(enabled=False)


def _safe_document_name(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return Path(str(value)).name


def _safe_value(value: Any) -> Any:
    if isinstance(value, Path):
        return value.name
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return str(value)


def _safe_metadata_value(key: str, value: Any) -> Any:
    key_lower = key.lower()
    if any(marker in key_lower for marker in ("path", "file", "document", "source")):
        if isinstance(value, (str, Path)):
            return _safe_document_name(value)
    return _safe_value(value)
