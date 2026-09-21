"""Benchmark-only canonical adapters for deterministic tables and SLANet_plus.

This module deliberately has no production imports beyond the existing schema and
table result types.  It does not alter routing or extraction behavior.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable

from app.core.schemas import OCRLine


@dataclass
class CanonicalCell:
    row_index: int | None
    column_index: int | None
    row_span: int | None
    column_span: int | None
    bbox: dict[str, float] | None
    structure_score: float | None = None
    text: str = ""
    source_ocr_line_ids: list[int] = field(default_factory=list)
    overlap_scores: dict[int, float] = field(default_factory=dict)
    assignment_confidence: float | None = None
    semantic_hint: str | None = None


@dataclass
class CanonicalRow:
    row_index: int
    cells: list[CanonicalCell] = field(default_factory=list)
    semantic_values: dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalTable:
    page_number: int
    source: str
    bbox: dict[str, float] | None
    rows: list[CanonicalRow]
    columns: int | None
    cells: list[CanonicalCell]
    headers: list[str]
    confidence: float | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MappingDiagnostics:
    assigned_ocr_line_ids: list[int] = field(default_factory=list)
    unassigned_ocr_line_ids: list[int] = field(default_factory=list)
    ambiguous_ocr_line_ids: list[int] = field(default_factory=list)
    empty_cell_indexes: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _CellHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.row = -1
        self.next_column = 0
        self.occupied: dict[int, set[int]] = {}
        self.cells: list[tuple[int, int, int, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row += 1
            self.next_column = 0
            self.occupied.setdefault(self.row, set())
            return
        if tag not in {"td", "th"} or self.row < 0:
            return
        values = dict(attrs)
        row_span = _positive_int(values.get("rowspan"), 1)
        column_span = _positive_int(values.get("colspan"), 1)
        occupied = self.occupied.setdefault(self.row, set())
        while self.next_column in occupied:
            self.next_column += 1
        column = self.next_column
        self.cells.append((self.row, column, row_span, column_span))
        for target_row in range(self.row, self.row + row_span):
            target = self.occupied.setdefault(target_row, set())
            target.update(range(column, column + column_span))
        self.next_column = column + column_span


def normalize_bbox(value: Any) -> dict[str, float] | None:
    """Convert a polygon or box mapping to an axis-aligned x1/y1/x2/y2 box."""
    if value is None:
        return None
    if isinstance(value, dict):
        if {"x1", "y1", "x2", "y2"}.issubset(value):
            return {key: float(value[key]) for key in ("x1", "y1", "x2", "y2")}
        if {"left", "top", "width", "height"}.issubset(value):
            x1, y1 = float(value["left"]), float(value["top"])
            return {"x1": x1, "y1": y1, "x2": x1 + float(value["width"]), "y2": y1 + float(value["height"])}
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
            x1, y1, x2, y2 = (float(item) for item in value)
            return {"x1": min(x1, x2), "y1": min(y1, y2), "x2": max(x1, x2), "y2": max(y1, y2)}
        flat = list(value)
        if len(flat) % 2 == 0 and all(isinstance(item, (int, float)) for item in flat):
            xs = [float(flat[index]) for index in range(0, len(flat), 2)]
            ys = [float(flat[index]) for index in range(1, len(flat), 2)]
            return {"x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys)}
    return None


def canonicalize_slanet_result(result: Any, *, page_number: int) -> CanonicalTable:
    payload = dict(result) if result is not None else {}
    structure = payload.get("structure") or []
    html = "".join(str(token) for token in structure)
    parser = _CellHTMLParser()
    try:
        parser.feed(html)
    except Exception:
        parser.cells = []
    boxes = payload.get("bbox") or []
    score = _float_or_none(payload.get("structure_score"))
    cells: list[CanonicalCell] = []
    for index, grid in enumerate(parser.cells):
        row, column, row_span, column_span = grid
        cells.append(CanonicalCell(
            row_index=row,
            column_index=column,
            row_span=row_span,
            column_span=column_span,
            bbox=normalize_bbox(boxes[index]) if index < len(boxes) else None,
            structure_score=score,
        ))
    # Preserve unmatched model boxes as cells with unavailable grid coordinates.
    for box in boxes[len(cells):]:
        cells.append(CanonicalCell(None, None, None, None, normalize_bbox(box), score))
    rows = _rows_from_cells(cells)
    return CanonicalTable(
        page_number=page_number,
        source="SLANet_plus",
        bbox=_union_boxes([cell.bbox for cell in cells if cell.bbox]),
        rows=rows,
        columns=_column_count(cells),
        cells=cells,
        headers=[],
        confidence=score,
        metadata={
            "structure_tokens": [str(token) for token in structure],
            "parsed_cell_count": len(parser.cells),
            "model_box_count": len(boxes),
            "malformed_or_mismatched": len(parser.cells) != len(boxes),
        },
    )


def map_ocr_lines_to_cells(
    table: CanonicalTable,
    ocr_lines: Iterable[OCRLine],
    *,
    minimum_score: float = 0.50,
    ambiguity_delta: float = 0.05,
) -> MappingDiagnostics:
    diagnostics = MappingDiagnostics()
    for fallback_id, line in enumerate(ocr_lines):
        line_id = line.line_index if line.line_index is not None else fallback_id
        line_box = normalize_bbox(line.bbox.model_dump() if line.bbox else None)
        if not line_box:
            diagnostics.unassigned_ocr_line_ids.append(line_id)
            continue
        candidates: list[tuple[float, int]] = []
        for cell_index, cell in enumerate(table.cells):
            if not cell.bbox:
                continue
            overlap = _intersection_over_source(line_box, cell.bbox)
            center_inside = _center_inside(line_box, cell.bbox)
            score = max(overlap, 0.75 + 0.25 * overlap if center_inside else 0.0)
            if score >= minimum_score:
                candidates.append((score, cell_index))
        candidates.sort(reverse=True)
        if not candidates:
            diagnostics.unassigned_ocr_line_ids.append(line_id)
            continue
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] <= ambiguity_delta:
            diagnostics.ambiguous_ocr_line_ids.append(line_id)
            continue
        score, cell_index = candidates[0]
        cell = table.cells[cell_index]
        cell.source_ocr_line_ids.append(line_id)
        cell.overlap_scores[line_id] = round(score, 4)
        cell.text = f"{cell.text} {line.text}".strip()
        cell.assignment_confidence = round(
            sum(cell.overlap_scores.values()) / len(cell.overlap_scores), 4
        )
        diagnostics.assigned_ocr_line_ids.append(line_id)
    diagnostics.empty_cell_indexes = [index for index, cell in enumerate(table.cells) if not cell.source_ocr_line_ids]
    _populate_row_semantics(table)
    return diagnostics


def canonicalize_deterministic_result(result: Any, *, page_number: int) -> CanonicalTable:
    columns = list(getattr(result, "columns", []) or [])
    column_indexes = {column.semantic_type: index for index, column in enumerate(columns)}
    cells = [
        CanonicalCell(
            row_index=None,
            column_index=column_indexes.get(cell.column_type),
            row_span=None,
            column_span=None,
            bbox=normalize_bbox(cell.bbox),
            structure_score=_float_or_none(cell.assignment_confidence),
            text=cell.text or "",
            source_ocr_line_ids=list(cell.source_line_ids or []),
            assignment_confidence=_float_or_none(cell.assignment_confidence),
            semantic_hint=cell.column_type,
        )
        for cell in (getattr(result, "cells", []) or [])
    ]
    rows: list[CanonicalRow] = []
    for index, row in enumerate(getattr(result, "rows", []) or []):
        semantic = {
            key: getattr(row, key)
            for key in (
                "description", "reference", "quantity", "unit", "unit_price", "discount",
                "tax_rate", "line_total_ht", "line_total_ttc",
            )
            if getattr(row, key, None) is not None
        }
        rows.append(CanonicalRow(row_index=index, cells=[], semantic_values=semantic))
    regions = list(getattr(result, "regions", []) or [])
    bbox = normalize_bbox(regions[0].bbox) if regions else _union_boxes([cell.bbox for cell in cells if cell.bbox])
    headers = [header.text for header in (getattr(result, "headers", []) or []) if header.text]
    return CanonicalTable(
        page_number=page_number,
        source="deterministic",
        bbox=bbox,
        rows=rows,
        columns=len(columns) if columns else None,
        cells=cells,
        headers=headers,
        confidence=_float_or_none(regions[0].confidence) if regions else None,
        metadata={
            "selected_strategy": getattr(result, "selected_strategy", "UNAVAILABLE"),
            "unresolved_fragment_count": len(getattr(result, "unresolved_fragments", []) or []),
            "line_item_count": len(getattr(result, "line_items", []) or []),
            "explicit_row_cell_relationships": False,
        },
    )


def arithmetic_diagnostics(rows: Iterable[CanonicalRow], *, tolerance: float = 0.05) -> dict[str, int]:
    counts = {"exact": 0, "within_tolerance": 0, "inconsistent": 0, "unavailable": 0}
    for row in rows:
        values = row.semantic_values
        quantity = _float_or_none(values.get("quantity"))
        unit_price = _float_or_none(values.get("unit_price"))
        total = _float_or_none(values.get("line_total_ht") or values.get("line_total") or values.get("line_total_ttc"))
        if quantity is None or unit_price is None or total is None:
            counts["unavailable"] += 1
            continue
        delta = abs(quantity * unit_price - total)
        if delta <= 0.001:
            counts["exact"] += 1
        elif delta <= max(tolerance, abs(total) * 0.01):
            counts["within_tolerance"] += 1
        else:
            counts["inconsistent"] += 1
    return counts


def _populate_row_semantics(table: CanonicalTable) -> None:
    aliases = {
        "description": ("description", "designation", "article", "product", "item"),
        "quantity": ("quantity", "quantite", "qty", "qte"),
        "unit": ("unit", "unite", "u.m"),
        "unit_price": ("unit price", "prix unitaire", "p.u", "price"),
        "line_total": ("total", "montant", "amount"),
    }
    column_semantics: dict[int, str] = {}
    for row in table.rows[:3]:
        for cell in row.cells:
            normalized = _normalize_text(cell.text)
            for semantic, candidates in aliases.items():
                if any(candidate in normalized for candidate in candidates):
                    if cell.column_index is not None:
                        column_semantics[cell.column_index] = semantic
                        cell.semantic_hint = semantic
                    break
    for row in table.rows:
        values: dict[str, str] = {}
        for cell in row.cells:
            semantic = column_semantics.get(cell.column_index) if cell.column_index is not None else None
            if semantic and cell.text:
                values[semantic] = f"{values.get(semantic, '')} {cell.text}".strip()
        row.semantic_values = values
    table.headers = [cell.text for row in table.rows[:3] for cell in row.cells if cell.semantic_hint and cell.text]


def _rows_from_cells(cells: list[CanonicalCell]) -> list[CanonicalRow]:
    indexes = sorted({cell.row_index for cell in cells if cell.row_index is not None})
    return [CanonicalRow(index, [cell for cell in cells if cell.row_index == index]) for index in indexes]


def _column_count(cells: list[CanonicalCell]) -> int | None:
    extents = [cell.column_index + (cell.column_span or 1) for cell in cells if cell.column_index is not None]
    return max(extents) if extents else None


def _intersection_over_source(source: dict[str, float], target: dict[str, float]) -> float:
    width = max(0.0, min(source["x2"], target["x2"]) - max(source["x1"], target["x1"]))
    height = max(0.0, min(source["y2"], target["y2"]) - max(source["y1"], target["y1"]))
    source_area = max(0.0, source["x2"] - source["x1"]) * max(0.0, source["y2"] - source["y1"])
    return width * height / source_area if source_area else 0.0


def _center_inside(source: dict[str, float], target: dict[str, float]) -> bool:
    x = (source["x1"] + source["x2"]) / 2
    y = (source["y1"] + source["y2"]) / 2
    return target["x1"] <= x <= target["x2"] and target["y1"] <= y <= target["y2"]


def _union_boxes(boxes: list[dict[str, float]]) -> dict[str, float] | None:
    if not boxes:
        return None
    return {
        "x1": min(box["x1"] for box in boxes),
        "y1": min(box["y1"] for box in boxes),
        "x2": max(box["x2"] for box in boxes),
        "y2": max(box["y2"] for box in boxes),
    }


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_text(value: str) -> str:
    import unicodedata

    return " ".join(
        "".join(char for char in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(char)).split()
    )
