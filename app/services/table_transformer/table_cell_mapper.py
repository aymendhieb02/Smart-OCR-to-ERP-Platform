from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.schemas import OCRLine
from app.services.table_transformer.table_transformer_detector import TATRTable


@dataclass
class MappedCell:
    row_index: int
    column_index: int
    text: str
    bbox: dict[str, float]
    confidence: float
    source_line_ids: list[int] = field(default_factory=list)
    assignment_reason: str = ""


def map_ocr_to_cells(table: TATRTable, ocr_lines: list[OCRLine]) -> list[MappedCell]:
    if table.cells:
        return _map_to_detected_cells(table, ocr_lines)
    return _map_to_virtual_cells(table, ocr_lines)


def _map_to_detected_cells(table: TATRTable, ocr_lines: list[OCRLine]) -> list[MappedCell]:
    mapped: list[MappedCell] = []
    for cell in table.cells:
        assigned = [line for line in ocr_lines if line.bbox and _center_inside(line.bbox.model_dump(), cell.bbox)]
        text = " ".join(line.text for line in sorted(assigned, key=lambda item: (item.bbox.y1, item.bbox.x1)))
        mapped.append(MappedCell(
            row_index=cell.row_index,
            column_index=cell.column_index,
            text=text,
            bbox=cell.bbox,
            confidence=_average_confidence(assigned, default=cell.confidence),
            source_line_ids=[line.line_index for line in assigned if line.line_index is not None],
            assignment_reason="center-point overlap",
        ))
    return mapped


def _map_to_virtual_cells(table: TATRTable, ocr_lines: list[OCRLine]) -> list[MappedCell]:
    inside = [line for line in ocr_lines if line.bbox and _intersects(line.bbox.model_dump(), table.bbox)]
    if not inside:
        return []
    rows = _group_rows(inside)
    x_edges = _infer_column_edges(inside, table.bbox)
    cells: list[MappedCell] = []
    for row_index, row in enumerate(rows):
        for column_index, (x1, x2) in enumerate(zip(x_edges, x_edges[1:])):
            assigned = [line for line in row if line.bbox and _center_x(line.bbox.model_dump()) >= x1 and _center_x(line.bbox.model_dump()) < x2]
            if not assigned:
                continue
            boxes = [line.bbox.model_dump() for line in assigned if line.bbox]
            bbox = _union_boxes(boxes)
            text = " ".join(line.text for line in sorted(assigned, key=lambda item: item.bbox.x1))
            cells.append(MappedCell(
                row_index=row_index,
                column_index=column_index,
                text=text,
                bbox=bbox,
                confidence=_average_confidence(assigned),
                source_line_ids=[line.line_index for line in assigned if line.line_index is not None],
                assignment_reason="virtual cell inferred from OCR rows and x positions",
            ))
    return cells


def _group_rows(lines: list[OCRLine]) -> list[list[OCRLine]]:
    rows: list[list[OCRLine]] = []
    for line in sorted(lines, key=lambda item: (item.bbox.y1, item.bbox.x1)):
        center = (line.bbox.y1 + line.bbox.y2) / 2
        for row in rows:
            row_center = sum((item.bbox.y1 + item.bbox.y2) / 2 for item in row) / len(row)
            if abs(center - row_center) <= 14:
                row.append(line)
                break
        else:
            rows.append([line])
    return [sorted(row, key=lambda item: item.bbox.x1) for row in rows]


def _infer_column_edges(lines: list[OCRLine], bbox: dict[str, float]) -> list[float]:
    centers = sorted((_center_x(line.bbox.model_dump()) for line in lines if line.bbox))
    if len(centers) < 3:
        return [bbox["x1"], bbox["x2"]]
    gaps = sorted(((centers[i + 1] - centers[i], i) for i in range(len(centers) - 1)), reverse=True)
    split_indices = sorted(index for _gap, index in gaps[: min(5, len(gaps))] if _gap >= 24)
    edges = [float(bbox["x1"])]
    for index in split_indices:
        edges.append((centers[index] + centers[index + 1]) / 2)
    edges.append(float(bbox["x2"]))
    return sorted(set(edges))


def _center_inside(line_box: dict[str, float], cell_box: dict[str, float]) -> bool:
    return cell_box["x1"] <= _center_x(line_box) <= cell_box["x2"] and cell_box["y1"] <= _center_y(line_box) <= cell_box["y2"]


def _intersects(a: dict[str, float], b: dict[str, float]) -> bool:
    return not (a["x2"] < b["x1"] or a["x1"] > b["x2"] or a["y2"] < b["y1"] or a["y1"] > b["y2"])


def _center_x(box: dict[str, float]) -> float:
    return (box["x1"] + box["x2"]) / 2


def _center_y(box: dict[str, float]) -> float:
    return (box["y1"] + box["y2"]) / 2


def _union_boxes(boxes: list[dict[str, float]]) -> dict[str, float]:
    return {
        "x1": min(box["x1"] for box in boxes),
        "y1": min(box["y1"] for box in boxes),
        "x2": max(box["x2"] for box in boxes),
        "y2": max(box["y2"] for box in boxes),
    }


def _average_confidence(lines: list[OCRLine], default: float = 0.0) -> float:
    values = [line.confidence for line in lines if line.confidence is not None]
    return round(sum(values) / len(values), 3) if values else default
