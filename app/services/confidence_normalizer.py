from __future__ import annotations

from typing import Any


def normalize_confidence(value: Any, *, selected_value: Any = True) -> float | None:
    """Clamp confidence to [0, 1] and clear it when no value was selected."""
    if selected_value is None or value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, score)), 3)
