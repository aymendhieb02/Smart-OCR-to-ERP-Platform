from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any


@dataclass
class LLMMetrics:
    invoked: bool = False
    skipped_reason: str | None = None
    model: str | None = None
    duration_seconds: float | None = None
    success: bool = False
    error_type: str | None = None
    confidence_before: float | None = None
    confidence_after: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "invoked": self.invoked,
            "skipped_reason": self.skipped_reason,
            "model": self.model,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "error_type": self.error_type,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "metadata": self.metadata,
        }


class LLMMetricTimer:
    def __init__(self) -> None:
        self.started = perf_counter()

    def elapsed(self) -> float:
        return round(perf_counter() - self.started, 4)
