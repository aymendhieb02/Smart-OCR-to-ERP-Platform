from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.schemas import BoundingBox, OCRLine
from app.utils.helpers import parse_amount, strip_accents

HEADER_KEYWORDS = ("description", "designation", "item", "product", "quantity", "qty", "qte", "unit", "prix", "price", "total", "amount", "tva", "vat")
DESCRIPTION_WORDS = ("description", "designation", "item", "product", "service", "article")
QUANTITY_WORDS = ("quantity", "qty", "qte", "quantite", "quantité")
UNIT_WORDS = ("unit", "unite", "unité", "uom")
PRICE_WORDS = ("price", "prix", "unit price", "prix unit")
TAX_WORDS = ("tva", "vat", "tax")
TOTAL_WORDS = ("total", "amount", "montant")
FOOTER_WORDS = ("subtotal", "sub total", "sous-total", "sales tax", "shipping", "handling", "total due", "grand total", "amount due", "payment", "iban", "rib", "swift")
BLOCK_KEYWORDS = {
    "invoice_metadata": ("invoice", "facture", "date", "due", "echeance", "échéance", "ref", "numero", "numéro"),
    "customer": ("bill to", "client", "customer", "facture a", "facturé à", "acheteur", "destinataire", "livre a", "livré à"),
    "totals": ("subtotal", "sous-total", "total ht", "tva", "vat", "tax", "total ttc", "grand total", "amount due", "total due"),
    "payment": ("iban", "rib", "swift", "bank", "banque", "payment", "paiement", "virement"),
}


@dataclass
class OCRVisualLine:
    page: int
    text: str
    bbox: BoundingBox | None
    confidence: float | None
    blocks: list[OCRLine] = field(default_factory=list)
    line_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "text": self.text,
            "bbox": self.bbox.model_dump(mode="json") if self.bbox else None,
            "confidence": self.confidence,
            "line_index": self.line_index,
            "ocr_indices": [block.line_index for block in self.blocks],
        }


@dataclass
class ReconstructedTable:
    page: int
    bbox: BoundingBox
    header: OCRVisualLine
    columns: dict[str, dict[str, Any]]
    rows: list[dict[str, Any]]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "bbox": self.bbox.model_dump(mode="json"),
            "header": self.header.to_dict(),
            "columns": self.columns,
            "rows": self.rows,
            "confidence": self.confidence,
        }


def analyze_document_layout(blocks: list[OCRLine]) -> dict[str, Any]:
    visual_lines = group_ocr_lines(blocks)
    tables = reconstruct_tables(blocks, visual_lines)
    logical_blocks = detect_logical_blocks(visual_lines, tables)
    return {
        "ocr_lines": [line.to_dict() for line in visual_lines],
        "blocks": logical_blocks,
        "tables": [table.to_dict() for table in tables],
    }


def group_ocr_lines(blocks: list[OCRLine], y_tolerance: float = 10.0) -> list[OCRVisualLine]:
    positioned = [block for block in blocks if block.bbox and block.text.strip()]
    positioned = sorted(positioned, key=lambda block: (block.page_number, _center_y(block.bbox), block.bbox.x1))
    groups: list[list[OCRLine]] = []
    for block in positioned:
        placed = False
        cy = _center_y(block.bbox)
        for group in groups:
            if group[0].page_number == block.page_number and abs(_avg_center_y(group) - cy) <= y_tolerance:
                group.append(block)
                placed = True
                break
        if not placed:
            groups.append([block])
    lines: list[OCRVisualLine] = []
    for index, group in enumerate(groups):
        ordered = sorted(group, key=lambda block: block.bbox.x1)
        boxes = [block.bbox for block in ordered if block.bbox]
        confidences = [block.confidence for block in ordered if block.confidence is not None]
        lines.append(OCRVisualLine(
            page=ordered[0].page_number,
            text=" ".join(block.text.strip() for block in ordered),
            bbox=_merge_boxes(boxes),
            confidence=round(sum(confidences) / len(confidences), 3) if confidences else None,
            blocks=ordered,
            line_index=index,
        ))
    return sorted(lines, key=lambda line: (line.page, line.bbox.y1 if line.bbox else 0, line.bbox.x1 if line.bbox else 0))


def reconstruct_tables(blocks: list[OCRLine], lines: list[OCRVisualLine] | None = None) -> list[ReconstructedTable]:
    lines = lines or group_ocr_lines(blocks)
    tables: list[ReconstructedTable] = []
    for header in lines:
        header_text = strip_accents(header.text).lower()
        if sum(1 for keyword in HEADER_KEYWORDS if keyword in header_text) < 2:
            continue
        columns = _infer_columns(header)
        if "description" not in columns or len(columns) < 3:
            continue
        table_rows = _build_table_rows(header, lines, columns)
        if not table_rows:
            continue
        boxes = [header.bbox] + [BoundingBox(**row["bbox"]) for row in table_rows if row.get("bbox")]
        confidences = [row.get("confidence") for row in table_rows if row.get("confidence") is not None]
        tables.append(ReconstructedTable(
            page=header.page,
            bbox=_merge_boxes([box for box in boxes if box]),
            header=header,
            columns=columns,
            rows=table_rows,
            confidence=round(sum(confidences) / len(confidences), 3) if confidences else 0.7,
        ))
    return tables[:3]


def detect_logical_blocks(lines: list[OCRVisualLine], tables: list[ReconstructedTable]) -> list[dict[str, Any]]:
    if not lines:
        return []
    max_y = max((line.bbox.y2 for line in lines if line.bbox), default=1000)
    max_x = max((line.bbox.x2 for line in lines if line.bbox), default=1000)
    blocks: list[dict[str, Any]] = []
    for block_type in ("invoice_metadata", "customer", "totals", "payment"):
        selected = [line for line in lines if any(keyword in strip_accents(line.text).lower() for keyword in BLOCK_KEYWORDS[block_type])]
        selected = _expand_nearby_lines(lines, selected, block_type)
        if selected:
            blocks.append(_logical_block_payload(block_type, selected))
    header_lines = [line for line in lines if line.bbox and line.bbox.y1 < max_y * 0.28 and line.bbox.x1 < max_x * 0.62]
    supplier_lines = [line for line in header_lines if not any(keyword in strip_accents(line.text).lower() for keyword in ("invoice", "facture", "date", "total", "client", "customer"))]
    if supplier_lines:
        blocks.append(_logical_block_payload("supplier", supplier_lines[:8]))
    for table in tables:
        blocks.append({
            "block_type": "products",
            "bbox": table.bbox.model_dump(mode="json"),
            "confidence": table.confidence,
            "text": "\n".join(row.get("text", "") for row in table.rows),
            "fields": ["line_items"],
            "page": table.page,
        })
    return blocks


def _infer_columns(header: OCRVisualLine) -> dict[str, dict[str, Any]]:
    columns: dict[str, dict[str, Any]] = {}
    for block in header.blocks:
        text = strip_accents(block.text).lower()
        key = None
        if any(word in text for word in DESCRIPTION_WORDS):
            key = "description"
        elif any(word in text for word in QUANTITY_WORDS):
            key = "quantity"
        elif any(word in text for word in UNIT_WORDS):
            key = "unit"
        elif any(word in text for word in PRICE_WORDS):
            key = "unit_price"
        elif any(word in text for word in TAX_WORDS):
            key = "tax_rate"
        elif any(word in text for word in TOTAL_WORDS):
            key = "total"
        if key:
            columns[key] = {"x1": block.bbox.x1, "x2": block.bbox.x2, "center": _center_x(block.bbox), "label": block.text}
    sorted_cols = sorted(columns.items(), key=lambda item: item[1]["center"])
    for idx, (key, column) in enumerate(sorted_cols):
        left = (sorted_cols[idx - 1][1]["center"] + column["center"]) / 2 if idx else 0
        right = (column["center"] + sorted_cols[idx + 1][1]["center"]) / 2 if idx + 1 < len(sorted_cols) else 1_000_000
        column["left_boundary"] = left
        column["right_boundary"] = right
    return columns


def _build_table_rows(header: OCRVisualLine, lines: list[OCRVisualLine], columns: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    header_y = header.bbox.y2 if header.bbox else 0
    candidate_lines = [line for line in lines if line.page == header.page and line.bbox and line.bbox.y1 > header_y + 1]
    stop_y = _table_stop_y(candidate_lines)
    body_lines = [line for line in candidate_lines if line.bbox.y1 < stop_y]
    row_starts = [line for line in body_lines if _line_has_row_anchor(line, columns)]
    if not row_starts:
        row_starts = [line for line in body_lines if _line_has_description_and_amounts(line, columns)]
    rows: list[dict[str, Any]] = []
    for idx, start in enumerate(row_starts):
        next_start = row_starts[idx + 1] if idx + 1 < len(row_starts) else None
        bottom = next_start.bbox.y1 - 2 if next_start and next_start.bbox else stop_y
        row_lines = [line for line in body_lines if line.bbox and line.bbox.y1 >= start.bbox.y1 - 2 and line.bbox.y1 < bottom]
        row = _reconstruct_row(row_lines, columns)
        if row and not _is_footer_text(row.get("text", "")):
            rows.append(row)
    return rows


def _reconstruct_row(row_lines: list[OCRVisualLine], columns: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    blocks = [block for line in row_lines for block in line.blocks if block.bbox]
    if not blocks:
        return None
    cells: dict[str, list[OCRLine]] = {key: [] for key in columns}
    for block in blocks:
        if re.fullmatch(r"0?\d{1,3}", block.text.strip()) and block.bbox.x2 < columns["description"].get("left_boundary", 0) + 50:
            continue
        key = _column_for_block(block, columns)
        if key:
            cells.setdefault(key, []).append(block)
    values: dict[str, Any] = {}
    cell_bboxes: dict[str, Any] = {}
    for key, cell_blocks in cells.items():
        if not cell_blocks:
            continue
        ordered = sorted(cell_blocks, key=lambda block: (block.bbox.y1, block.bbox.x1))
        text = " ".join(block.text.strip() for block in ordered).strip()
        if key in {"quantity", "unit_price", "tax_rate", "total"}:
            values[key] = _parse_cell_number(text)
        else:
            values[key] = re.sub(r"\s+", " ", text).strip()
        cell_bboxes[key] = _merge_boxes([block.bbox for block in ordered]).model_dump(mode="json")
    description = str(values.get("description") or "").strip()
    if len(description) < 3 or sum(char.isalpha() for char in description) < 3:
        return None
    if values.get("quantity") is None and values.get("total") is None:
        return None
    boxes = [block.bbox for block in blocks]
    confidences = [block.confidence for block in blocks if block.confidence is not None]
    bbox = _merge_boxes(boxes).model_dump(mode="json")
    return {
        "text": " ".join(line.text for line in row_lines),
        "values": values,
        "bbox": bbox,
        "cell_bboxes": cell_bboxes,
        "confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }


def _column_for_block(block: OCRLine, columns: dict[str, dict[str, Any]]) -> str | None:
    center = _center_x(block.bbox)
    for key, column in columns.items():
        if column["left_boundary"] <= center < column["right_boundary"]:
            return key
    return None


def _parse_cell_number(text: str) -> float | None:
    if not re.search(r"\d", text):
        return None
    matches = re.findall(r"[-+]?(?:[$€£]\s*)?\d+(?:[,.]\d+)?", text)
    if not matches:
        return None
    return parse_amount(matches[-1])


def _line_has_row_anchor(line: OCRVisualLine, columns: dict[str, dict[str, Any]]) -> bool:
    description_left = columns["description"].get("x1", 80)
    return any(re.fullmatch(r"0?\d{1,3}", block.text.strip()) and block.bbox.x1 < description_left for block in line.blocks)


def _line_has_description_and_amounts(line: OCRVisualLine, columns: dict[str, dict[str, Any]]) -> bool:
    has_desc = any(_column_for_block(block, columns) == "description" and sum(char.isalpha() for char in block.text) >= 3 for block in line.blocks if block.bbox)
    amount_count = sum(1 for block in line.blocks if block.bbox and _column_for_block(block, columns) in {"quantity", "unit_price", "total"} and _parse_cell_number(block.text) is not None)
    return has_desc and amount_count >= 2


def _table_stop_y(lines: list[OCRVisualLine]) -> float:
    for line in lines:
        if _is_footer_text(line.text):
            return line.bbox.y1 if line.bbox else 1_000_000
    return max((line.bbox.y2 for line in lines if line.bbox), default=1_000_000) + 10


def _is_footer_text(text: str) -> bool:
    plain = strip_accents(text).lower()
    return any(word in plain for word in FOOTER_WORDS)


def _expand_nearby_lines(lines: list[OCRVisualLine], selected: list[OCRVisualLine], block_type: str) -> list[OCRVisualLine]:
    if not selected:
        return []
    expanded = set(id(line) for line in selected)
    result = list(selected)
    for anchor in selected:
        if not anchor.bbox:
            continue
        for line in lines:
            if id(line) in expanded or not line.bbox or line.page != anchor.page:
                continue
            near_y = 0 <= line.bbox.y1 - anchor.bbox.y1 <= (95 if block_type in {"customer", "supplier"} else 70)
            near_x = abs(line.bbox.x1 - anchor.bbox.x1) < 180
            if near_y and near_x:
                expanded.add(id(line))
                result.append(line)
    return sorted(result, key=lambda line: (line.page, line.bbox.y1 if line.bbox else 0, line.bbox.x1 if line.bbox else 0))


def _logical_block_payload(block_type: str, lines: list[OCRVisualLine]) -> dict[str, Any]:
    boxes = [line.bbox for line in lines if line.bbox]
    confidences = [line.confidence for line in lines if line.confidence is not None]
    bbox = _merge_boxes(boxes)
    return {
        "block_type": block_type,
        "bbox": bbox.model_dump(mode="json") if bbox else None,
        "confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.65,
        "text": "\n".join(line.text for line in lines),
        "fields": [],
        "page": lines[0].page if lines else 1,
    }


def _merge_boxes(boxes: list[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        x1=min((box.x1 for box in boxes), default=0),
        y1=min((box.y1 for box in boxes), default=0),
        x2=max((box.x2 for box in boxes), default=0),
        y2=max((box.y2 for box in boxes), default=0),
    )


def _center_x(box: BoundingBox) -> float:
    return (box.x1 + box.x2) / 2


def _center_y(box: BoundingBox) -> float:
    return (box.y1 + box.y2) / 2


def _avg_center_y(blocks: list[OCRLine]) -> float:
    return sum(_center_y(block.bbox) for block in blocks if block.bbox) / max(1, len(blocks))
