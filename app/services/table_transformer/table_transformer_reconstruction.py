from __future__ import annotations

from collections import defaultdict

from app.core.schemas import LineItem
from app.services.table_transformer.table_cell_mapper import MappedCell
from app.utils.helpers import parse_amount


COLUMN_ALIASES = {
    "description": ("description", "designation", "item", "product", "article", "details"),
    "quantity": ("qty", "qte", "quantity", "quantite"),
    "unit_price": ("unit price", "prix", "price", "rate", "p.u"),
    "tax_rate": ("tax", "tva", "vat"),
    "line_total_ttc": ("total", "amount", "gross", "montant"),
}


def reconstruct_line_items_from_cells(cells: list[MappedCell]) -> list[LineItem]:
    if not cells:
        return []
    columns = _infer_columns(cells)
    rows: dict[int, list[MappedCell]] = defaultdict(list)
    for cell in cells:
        rows[cell.row_index].append(cell)
    items: list[LineItem] = []
    for row_index in sorted(rows):
        row_cells = rows[row_index]
        if _is_header_row(row_cells):
            continue
        values = _row_values(row_cells, columns)
        if not values.get("description"):
            continue
        quantity = parse_amount(values.get("quantity"))
        unit_price = parse_amount(values.get("unit_price"))
        tax_rate = parse_amount(values.get("tax_rate"))
        total = parse_amount(values.get("line_total_ttc"))
        if quantity is None and unit_price is None and total is None:
            continue
        if unit_price is None and quantity and total:
            unit_price = round(total / quantity, 3)
        line_total_ht = round(quantity * unit_price, 3) if quantity is not None and unit_price is not None else total
        boxes = [cell.bbox for cell in row_cells if cell.bbox]
        item = LineItem(
            description=values.get("description"),
            quantity=quantity,
            unit_price=unit_price,
            tax_rate=tax_rate,
            line_total_ht=line_total_ht,
            line_total_ttc=total,
            total=total,
            confidence=_average([cell.confidence for cell in row_cells]),
            bbox=_union_boxes(boxes) if boxes else None,
            source="table transformer cell reconstruction",
        )
        items.append(item)
    return items


def _infer_columns(cells: list[MappedCell]) -> dict[int, str]:
    header_cells = [cell for cell in cells if cell.row_index == min(item.row_index for item in cells)]
    columns: dict[int, str] = {}
    for cell in header_cells:
        text = cell.text.lower()
        for semantic, aliases in COLUMN_ALIASES.items():
            if any(alias in text for alias in aliases):
                columns[cell.column_index] = semantic
                break
    if columns:
        return columns
    ordered = sorted({cell.column_index for cell in cells})
    fallback = ["description", "quantity", "unit_price", "tax_rate", "line_total_ttc"]
    return {column: fallback[index] for index, column in enumerate(ordered[: len(fallback)])}


def _row_values(row_cells: list[MappedCell], columns: dict[int, str]) -> dict[str, str]:
    values: dict[str, list[str]] = defaultdict(list)
    for cell in sorted(row_cells, key=lambda item: item.column_index):
        semantic = columns.get(cell.column_index)
        if semantic:
            values[semantic].append(cell.text)
    return {key: " ".join(part for part in parts if part).strip() for key, parts in values.items()}


def _is_header_row(cells: list[MappedCell]) -> bool:
    text = " ".join(cell.text.lower() for cell in cells)
    hits = sum(1 for aliases in COLUMN_ALIASES.values() for alias in aliases if alias in text)
    return hits >= 2


def _union_boxes(boxes: list[dict[str, float]]) -> dict[str, float]:
    return {
        "x1": min(box["x1"] for box in boxes),
        "y1": min(box["y1"] for box in boxes),
        "x2": max(box["x2"] for box in boxes),
        "y2": max(box["y2"] for box in boxes),
    }


def _average(values: list[float | None]) -> float:
    numeric = [float(value) for value in values if value is not None]
    return round(sum(numeric) / len(numeric), 3) if numeric else 0.0
