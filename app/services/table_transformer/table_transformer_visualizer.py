from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.core.config import settings


def draw_table_transformer_overlay(image: np.ndarray, detection: dict[str, Any], output_path: Path | None = None) -> Path:
    output_path = Path(output_path or settings.table_transformer_debug_dir / "overlay.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = image.copy()
    for table in detection.get("tables", []):
        _draw_box(canvas, table.get("bbox"), (0, 128, 255), "table")
        for cell in table.get("cells", []):
            _draw_box(canvas, cell.get("bbox"), (64, 180, 75), "cell")
    cv2.imwrite(str(output_path), canvas)
    return output_path


def _draw_box(image: np.ndarray, bbox: dict[str, float] | None, color: tuple[int, int, int], label: str) -> None:
    if not bbox:
        return
    x1, y1, x2, y2 = [int(float(bbox[key])) for key in ("x1", "y1", "x2", "y2")]
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    cv2.putText(image, label, (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
