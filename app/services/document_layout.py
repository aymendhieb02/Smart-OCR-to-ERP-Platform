from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.schemas import BoundingBox, OCRLine
from app.services.table_reconstruction_engine import reconstruct_line_items as reconstruct_p3_line_items
from app.utils.helpers import parse_amount, strip_accents

HEADER_KEYWORDS = ("description", "designation", "item", "product", "quantity", "qty", "qte", "unit", "prix", "price", "total", "amount", "tva", "vat")
DESCRIPTION_WORDS = ("description", "designation", "item", "product", "service", "article")
REFERENCE_WORDS = ("reference", "ref", "code", "product code", "sku", "article")
QUANTITY_WORDS = ("quantity", "qty", "qte", "quantite", "quantitÃ©")
UNIT_WORDS = ("unit", "unite", "unitÃ©", "uom")
PRICE_WORDS = ("price", "prix", "unit price", "prix unit")
DISCOUNT_WORDS = ("discount", "remise", "rabais")
TAX_WORDS = ("tva", "vat", "tax")
TOTAL_WORDS = ("total", "amount", "montant", "gross", "net", "subtotal", "worth")
FOOTER_WORDS = ("summary", "subtotal", "sub total", "sous-total", "sales tax", "shipping", "handling", "total due", "grand total", "amount due", "payment", "iban", "rib", "swift")
BLOCK_KEYWORDS = {
    "invoice_metadata": ("invoice", "facture", "date", "due", "echeance", "Ã©chÃ©ance", "ref", "numero", "numÃ©ro"),
    "customer": ("bill to", "client", "customer", "facture a", "facturÃ© Ã ", "acheteur", "destinataire", "livre a", "livrÃ© Ã "),
    "totals": ("subtotal", "sous-total", "total ht", "tva", "vat", "tax", "total ttc", "grand total", "amount due", "total due"),
    "payment": ("iban", "rib", "swift", "bank", "banque", "payment", "paiement", "virement"),
    "footer": ("thank you", "merci", "signature", "terms", "conditions", "note", "remarque"),
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


def build_table_extraction_debug(
    blocks: list[OCRLine],
    tables: list[ReconstructedTable] | None = None,
    *,
    validated_rows: list[dict[str, Any]] | None = None,
    review_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return one source of truth for table detection and row accounting."""
    visual_lines = group_ocr_lines(blocks)
    selected_tables = tables if tables is not None else reconstruct_tables(blocks, visual_lines)
    headers = _header_candidates(visual_lines)
    header_groups = []
    for header in headers:
        plain = strip_accents(header.text).lower()
        found = [keyword for keyword in HEADER_KEYWORDS if re.search(rf"\b{re.escape(keyword)}\b", plain)]
        header_groups.append({"text": header.text, "bbox": header.bbox.model_dump(mode="json") if header.bbox else None, "keywords": found, "confidence": header.confidence})
    anchor_candidates = []
    for header in headers:
        anchor_candidates.append({"type": "header", "bbox": header.bbox.model_dump(mode="json") if header.bbox else None, "score": round(min(1.0, len(set(header_groups[headers.index(header)]["keywords"])) / 5), 3), "keywords": header_groups[headers.index(header)]["keywords"]})
    if not anchor_candidates:
        numeric_lines = [line for line in visual_lines if line.bbox and sum(1 for block in line.blocks if block.bbox and _parse_cell_number(block.text) is not None) >= 2]
        if numeric_lines:
            anchor_candidates.append({"type": "numeric_alignment", "bbox": _merge_boxes([line.bbox for line in numeric_lines]).model_dump(mode="json"), "score": 0.42, "keywords": []})
    if not anchor_candidates and selected_tables:
        anchor_candidates.append({"type": "text_sequence", "bbox": selected_tables[0].bbox.model_dump(mode="json"), "score": selected_tables[0].confidence, "keywords": []})
    raw_rows = [row for table in selected_tables for row in table.rows]
    p3_result = reconstruct_p3_line_items(blocks)
    invalid_rows = [row for row in raw_rows if row.get("invalid")]
    inferred_columns = [
        {"name": key, "center": value.get("center"), "confidence": value.get("confidence", table.confidence)}
        for table in selected_tables for key, value in table.columns.items()
    ]
    return {
        "table_anchor_candidates": anchor_candidates,
        "selected_table_region": selected_tables[0].bbox.model_dump(mode="json") if selected_tables else None,
        "header_groups": header_groups,
        "inferred_columns": inferred_columns,
        "raw_candidate_rows": raw_rows,
        "invalid_rows": invalid_rows,
        "validated_rows": validated_rows or [],
        "review_rows": review_rows or [],
        "rejection_reasons": [reason for row in raw_rows for reason in row.get("rejection_reasons", [])],
        "p3_table_reconstruction": p3_result.to_debug_dict(),
        "counts": {
            "raw_candidate_rows": len(raw_rows),
            "invalid_rows": len(invalid_rows),
            "selected_tables": len(selected_tables),
            "p3_candidate_rows": p3_result.diagnostics.get("candidate_row_count", 0),
            "p3_reconstructed_rows": len(p3_result.rows),
            "p3_validated_rows": p3_result.diagnostics.get("validated_row_count", 0),
            "p3_review_rows": p3_result.diagnostics.get("review_row_count", 0),
            "p3_unresolved_fragments": len(p3_result.unresolved_fragments),
        },
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
    line_counter = 0
    for group in groups:
        ordered = sorted(group, key=lambda block: block.bbox.x1)
        for segment in _split_group_by_x_gap(ordered):
            boxes = [block.bbox for block in segment if block.bbox]
            confidences = [block.confidence for block in segment if block.confidence is not None]
            lines.append(OCRVisualLine(
                page=segment[0].page_number,
                text=" ".join(block.text.strip() for block in segment),
                bbox=_merge_boxes(boxes),
                confidence=round(sum(confidences) / len(confidences), 3) if confidences else None,
                blocks=segment,
                line_index=line_counter,
            ))
            line_counter += 1
    return sorted(lines, key=lambda line: (line.page, line.bbox.y1 if line.bbox else 0, line.bbox.x1 if line.bbox else 0))



def _split_group_by_x_gap(blocks: list[OCRLine], gap_threshold: float = 100.0) -> list[list[OCRLine]]:
    if not blocks:
        return []
    segments: list[list[OCRLine]] = [[blocks[0]]]
    for block in blocks[1:]:
        previous = segments[-1][-1]
        gap = block.bbox.x1 - previous.bbox.x2
        if gap >= gap_threshold:
            segments.append([block])
        else:
            segments[-1].append(block)
    return segments

def reconstruct_tables(blocks: list[OCRLine], lines: list[OCRVisualLine] | None = None) -> list[ReconstructedTable]:
    lines = lines or group_ocr_lines(blocks)
    tables: list[ReconstructedTable] = []
    for header in _header_candidates(lines):
        header_text = strip_accents(header.text).lower()
        keyword_hits = sum(1 for keyword in HEADER_KEYWORDS if re.search(rf"\b{re.escape(keyword)}\b", header_text))
        if keyword_hits < 2 and not re.search(r"\bid\s*\|?\s*description\b", header_text):
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
    if not tables:
        weak_table = _reconstruct_numeric_alignment_table(_group_table_visual_lines(blocks))
        if weak_table:
            tables.append(weak_table)
    if not tables and not any(block.bbox for block in blocks):
        text_table = _reconstruct_text_sequence_table(blocks)
        if text_table:
            tables.append(text_table)
    return tables[:3]


def _group_table_visual_lines(blocks: list[OCRLine], y_tolerance: float = 16.0) -> list[OCRVisualLine]:
    """Group table cells by y without splitting wide column gaps."""
    positioned = [block for block in blocks if block.bbox and block.text.strip()]
    groups: list[list[OCRLine]] = []
    for block in sorted(positioned, key=lambda item: (item.page_number, _center_y(item.bbox), item.bbox.x1)):
        center_y = _center_y(block.bbox)
        target = next((group for group in groups if group[0].page_number == block.page_number and abs(_avg_center_y(group) - center_y) <= y_tolerance), None)
        if target is None:
            groups.append([block])
        else:
            target.append(block)
    return [
        OCRVisualLine(
            page=group[0].page_number,
            text=" ".join(item.text.strip() for item in sorted(group, key=lambda value: value.bbox.x1)),
            bbox=_merge_boxes([item.bbox for item in group]),
            confidence=round(sum(item.confidence for item in group if item.confidence is not None) / max(1, len([item for item in group if item.confidence is not None])), 3),
            blocks=sorted(group, key=lambda value: value.bbox.x1),
            line_index=group[0].line_index,
        )
        for group in sorted(groups, key=lambda value: (_center_y(value[0].bbox), value[0].bbox.x1))
    ]


def _reconstruct_numeric_alignment_table(lines: list[OCRVisualLine]) -> ReconstructedTable | None:
    """Find a reviewable table when OCR lost or split the header."""
    body_candidates = [
        line for line in lines
        if line.bbox
        and not _is_footer_text(line.text)
        and sum(char.isalpha() for char in line.text) >= 3
        and sum(1 for block in line.blocks if block.bbox and _parse_cell_number(block.text) is not None) >= 2
    ]
    if len(body_candidates) < 2:
        return None
    grouped = _group_numeric_body_candidates(body_candidates)
    if len(grouped) < 2:
        return None
    columns = _infer_columns_from_numeric_rows(grouped)
    if len(columns) < 3 or "description" not in columns:
        return None
    first_line = min((group[0] for group in grouped), key=lambda line: line.bbox.y1 if line.bbox else 0)
    header_y = max(0.0, (first_line.bbox.y1 if first_line.bbox else 1.0) - 24)
    header_box = BoundingBox(
        x1=min((line.bbox.x1 for group in grouped for line in group if line.bbox), default=0),
        y1=header_y,
        x2=max((line.bbox.x2 for group in grouped for line in group if line.bbox), default=0),
        y2=header_y + 18,
    )
    header = OCRVisualLine(
        page=first_line.page,
        text="inferred description quantity price total",
        bbox=header_box,
        confidence=0.42,
        blocks=[],
        line_index=first_line.line_index,
    )
    rows = []
    for group in grouped:
        row = _reconstruct_row(group, columns)
        if row:
            row["needs_review"] = True
            row["confidence"] = min(row.get("confidence") or 0.42, 0.58)
            rows.append(row)
    if not rows:
        return None
    boxes = [header_box] + [BoundingBox(**row["bbox"]) for row in rows if row.get("bbox")]
    return ReconstructedTable(
        page=first_line.page,
        bbox=_merge_boxes(boxes),
        header=header,
        columns=columns,
        rows=rows,
        confidence=0.42,
    )


def _reconstruct_text_sequence_table(blocks: list[OCRLine]) -> ReconstructedTable | None:
    """Recover rows from ordered OCR text when the engine emitted no boxes."""
    ordered = [block for block in sorted(blocks, key=lambda item: (item.page_number, item.line_index or 0)) if block.text.strip()]
    if not ordered:
        return None
    header_index, header_order = _find_text_table_header(ordered)
    if header_index is None:
        return None
    rows: list[dict[str, Any]] = []
    index = header_index + 1
    while index < len(ordered):
        text = ordered[index].text.strip()
        if _is_footer_text(text):
            break
        description = None
        values: list[float] = []
        start_index = index
        if _numeric_tokens(text):
            values.extend(_numeric_tokens(text))
            if index + 1 < len(ordered) and _looks_like_description_text(ordered[index + 1].text):
                description = ordered[index + 1].text.strip()
                index += 1
                while index + 1 < len(ordered) and len(values) < 3 and _numeric_tokens(ordered[index + 1].text) and not _looks_like_description_text(ordered[index + 1].text):
                    index += 1
                    values.extend(_numeric_tokens(ordered[index].text))
        elif _looks_like_description_text(text):
            description = text
            while index + 1 < len(ordered) and len(values) < 3 and _numeric_tokens(ordered[index + 1].text):
                index += 1
                values.extend(_numeric_tokens(ordered[index].text))
        if description and len(values) >= 2:
            row_values: dict[str, Any] = {"description": description}
            price_before_quantity = bool(header_order and "price" in header_order and "quantity" in header_order and header_order.index("price") < header_order.index("quantity"))
            if len(values) >= 3:
                if price_before_quantity:
                    row_values.update({"unit_price": values[0], "quantity": values[-2], "total": values[-1]})
                else:
                    row_values.update({"quantity": values[0], "unit_price": values[-2], "total": values[-1]})
            else:
                if header_order and "quantity" in header_order and ("price" not in header_order or header_order.index("quantity") < header_order.index("price")):
                    row_values.update({"quantity": values[0], "unit_price": values[-1]})
                else:
                    row_values.update({"unit_price": values[0], "total": values[-1]})
            rows.append({
                "text": " ".join(item.text for item in ordered[start_index:index + 1]),
                "values": row_values,
                "bbox": None,
                "cell_bboxes": {},
                "source_ocr_nodes": [item.line_index for item in ordered[start_index:index + 1] if item.line_index is not None],
                "needs_review": True,
                "confidence": 0.38,
            })
        index += 1
    if not rows:
        return None
    page = ordered[header_index].page_number
    header = OCRVisualLine(page=page, text=ordered[header_index].text, bbox=BoundingBox(x1=0, y1=0, x2=1000, y2=18), confidence=0.38, blocks=[], line_index=ordered[header_index].line_index)
    columns = {
        "description": {"x1": 0, "x2": 450, "center": 225, "label": "text sequence description", "confidence": 0.38},
        "quantity": {"x1": 450, "x2": 600, "center": 525, "label": "text sequence quantity", "confidence": 0.38},
        "unit_price": {"x1": 600, "x2": 800, "center": 700, "label": "text sequence price", "confidence": 0.38},
        "total": {"x1": 800, "x2": 1000, "center": 900, "label": "text sequence total", "confidence": 0.38},
    }
    return ReconstructedTable(page=page, bbox=BoundingBox(x1=0, y1=0, x2=1000, y2=max(30, len(rows) * 28)), header=header, columns=columns, rows=rows, confidence=0.38)


def _text_is_table_header(text: str) -> bool:
    plain = strip_accents(text).lower()
    return sum(1 for keyword in HEADER_KEYWORDS if re.search(rf"\b{re.escape(keyword)}\b", plain)) >= 2


def _find_text_table_header(ordered: list[OCRLine]) -> tuple[int | None, list[str]]:
    for index, _block in enumerate(ordered):
        context = ordered[index:index + 8]
        found: list[str] = []
        for item in context:
            plain = strip_accents(item.text).lower()
            if any(word in plain for word in DESCRIPTION_WORDS):
                found.append("description")
            if any(word in plain for word in QUANTITY_WORDS):
                found.append("quantity")
            if any(word in plain for word in PRICE_WORDS):
                found.append("price")
            if any(word in plain for word in TOTAL_WORDS):
                found.append("total")
        if len(set(found)) >= 2:
            return index, found
    return None, []


def _numeric_tokens(text: str) -> list[float]:
    return [value for raw in re.findall(r"[-+]?\d+(?:[,.]\d+)?", text) if (value := parse_amount(raw)) is not None]


def _looks_like_description_text(text: str) -> bool:
    plain = strip_accents(text).lower().strip()
    rejected_markers = ("email", "tel", "phone", "address", "site", "invoice", "facture", "buyer", "customer", "supplier", "amonnt", "unk price", "descriation", "decription")
    return bool(plain) and "@" not in plain and sum(char.isalpha() for char in plain) >= 3 and not any(marker in plain for marker in rejected_markers) and not _text_is_table_header(plain) and not _is_footer_text(plain) and len(_numeric_tokens(plain)) == 0


def _group_numeric_body_candidates(lines: list[OCRVisualLine]) -> list[list[OCRVisualLine]]:
    groups: list[list[OCRVisualLine]] = []
    for line in sorted(lines, key=lambda item: (item.page, item.bbox.y1 if item.bbox else 0)):
        if not groups:
            groups.append([line])
            continue
        previous = groups[-1][-1]
        tolerance = max(18.0, (previous.bbox.y2 - previous.bbox.y1) * 1.5) if previous.bbox and line.bbox else 18.0
        if line.page == previous.page and abs(line.bbox.y1 - previous.bbox.y1) <= tolerance:
            groups[-1].append(line)
        else:
            groups.append([line])
    return [group for group in groups if len(group) >= 1 and sum(1 for block in group[0].blocks if block.bbox and _parse_cell_number(block.text) is not None) >= 2]


def _infer_columns_from_numeric_rows(groups: list[list[OCRVisualLine]]) -> dict[str, dict[str, Any]]:
    centers: list[float] = []
    for group in groups:
        for line in group:
            for block in line.blocks:
                if block.bbox and _parse_cell_number(block.text) is not None:
                    centers.append(_center_x(block.bbox))
    clusters: list[list[float]] = []
    for center in sorted(centers):
        if not clusters or center - sum(clusters[-1]) / len(clusters[-1]) > 42:
            clusters.append([center])
        else:
            clusters[-1].append(center)
    numeric_centers = [sum(cluster) / len(cluster) for cluster in clusters if len(cluster) >= 2]
    if len(numeric_centers) < 2:
        return {}
    keys = ["quantity", "unit_price", "tax_rate", "total"] if len(numeric_centers) >= 4 else (["quantity", "unit_price", "total"] if len(numeric_centers) == 3 else ["unit_price", "total"])
    columns: dict[str, dict[str, Any]] = {}
    description_x = min(numeric_centers) - 1
    columns["description"] = {"x1": 0, "x2": description_x, "center": description_x / 2, "label": "inferred description", "confidence": 0.45}
    for key, center in zip(keys, numeric_centers[-len(keys):]):
        columns[key] = {"x1": center - 20, "x2": center + 20, "center": center, "label": f"inferred {key}", "confidence": 0.45}
    sorted_cols = sorted(columns.items(), key=lambda item: item[1]["center"])
    for index, (_key, column) in enumerate(sorted_cols):
        column["left_boundary"] = (sorted_cols[index - 1][1]["center"] + column["center"]) / 2 if index else 0
        column["right_boundary"] = (column["center"] + sorted_cols[index + 1][1]["center"]) / 2 if index + 1 < len(sorted_cols) else 1_000_000
    return columns


def _header_candidates(lines: list[OCRVisualLine], y_tolerance: float = 18.0) -> list[OCRVisualLine]:
    candidates: list[OCRVisualLine] = []
    seen_keys: set[tuple[int, int]] = set()
    for line in lines:
        if not line.bbox:
            continue
        same_row = [
            other for other in lines
            if other.page == line.page
            and other.bbox
            and abs(_center_y(other.bbox) - _center_y(line.bbox)) <= y_tolerance
        ]
        combined_text = " ".join(item.text for item in sorted(same_row, key=lambda item: item.bbox.x1 if item.bbox else 0))
        combined_plain = strip_accents(combined_text).lower()
        keyword_hits = sum(1 for keyword in HEADER_KEYWORDS if re.search(rf"\b{re.escape(keyword)}\b", combined_plain))
        aligned_header = len(same_row) >= 3 and keyword_hits >= 2
        if not aligned_header and keyword_hits < 3 and not re.search(r"\bid\s*\|?\s*description\b", combined_plain):
            continue
        key = (line.page, round(line.bbox.y1))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        boxes = [item.bbox for item in same_row if item.bbox]
        confidences = [item.confidence for item in same_row if item.confidence is not None]
        candidates.append(OCRVisualLine(
            page=line.page,
            text=combined_text,
            bbox=_merge_boxes(boxes),
            confidence=round(sum(confidences) / len(confidences), 3) if confidences else None,
            blocks=[block for item in sorted(same_row, key=lambda value: value.bbox.x1 if value.bbox else 0) for block in item.blocks],
            line_index=line.line_index,
        ))
    return candidates


def detect_logical_blocks(lines: list[OCRVisualLine], tables: list[ReconstructedTable]) -> list[dict[str, Any]]:
    from app.services.semantic_classifier import is_company_candidate_text, is_forbidden_party_name

    if not lines:
        return []
    max_y = max((line.bbox.y2 for line in lines if line.bbox), default=1000)
    max_x = max((line.bbox.x2 for line in lines if line.bbox), default=1000)
    blocks: list[dict[str, Any]] = []
    table_line_ids = _table_line_ids(tables)
    table_regions = _table_regions(tables)
    left_lines, right_lines = _detect_columns(lines, max_x)
    top_lines = [line for line in lines if line.bbox and line.bbox.y1 < max_y * 0.30]
    footer_lines = [line for line in lines if line.bbox and line.bbox.y1 >= max_y * 0.86]
    for block_type in ("totals", "payment", "invoice_metadata", "customer", "footer"):
        selected = [
            line for line in lines
            if id(line) not in table_regions
            and not _line_in_table_region(line, table_regions)
            and any(re.search(rf"\b{re.escape(keyword)}\b", strip_accents(line.text).lower()) for keyword in BLOCK_KEYWORDS[block_type])
        ]
        selected = _expand_nearby_lines(lines, selected, block_type)
        if selected:
            blocks.append(_logical_block_payload(block_type, selected))
    metadata_lines = [
        line for line in right_lines
        if line in top_lines
        and id(line) not in table_regions
        and not _line_in_table_region(line, table_regions)
        and (
            any(re.search(rf"\b{re.escape(keyword)}\b", strip_accents(line.text).lower()) for keyword in BLOCK_KEYWORDS["invoice_metadata"])
            or _contains_date_or_reference(line.text)
        )
    ]
    if metadata_lines and not any(block["block_type"] == "invoice_metadata" for block in blocks):
        blocks.append(_logical_block_payload("invoice_metadata", metadata_lines[:10]))
    header_lines = [
        line for line in left_lines
        if line in top_lines
        and id(line) not in table_regions and not _line_in_table_region(line, table_regions)
    ]
    supplier_lines = [
        line for line in header_lines
        if not is_forbidden_party_name(line.text)
        and is_company_candidate_text(line.text)
        and not any(re.search(rf"\b{re.escape(keyword)}\b", strip_accents(line.text).lower()) for keyword in (
            "invoice", "facture", "date", "total", "client", "customer", "description", "quantity", "qty", "price", "amount",
        ))
        and not _looks_like_address_line(line.text)
        and not _looks_like_product_row(strip_accents(line.text).lower())
    ]
    if supplier_lines:
        blocks.append(_logical_block_payload("supplier", supplier_lines[:8]))
    customer_lines = _collect_customer_lines(lines, right_lines, top_lines, table_regions)
    if customer_lines and not any(block["block_type"] == "customer" for block in blocks):
        blocks.append(_logical_block_payload("customer", customer_lines[:8]))
    if footer_lines and not any(block["block_type"] == "footer" for block in blocks):
        footer_candidates = [line for line in footer_lines if any(keyword in strip_accents(line.text).lower() for keyword in BLOCK_KEYWORDS["footer"])]
        if footer_candidates:
            blocks.append(_logical_block_payload("footer", footer_candidates[:8]))
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



def _table_line_ids(tables: list[ReconstructedTable]) -> set[int]:
    return _table_regions(tables)


def _table_regions(tables: list[ReconstructedTable]) -> set[int]:
    ids: set[int] = set()
    for table in tables:
        ids.add(id(table.header))
        for block in table.header.blocks:
            ids.add(id(block))
        for row in table.rows:
            bbox = row.get("bbox")
            if bbox:
                ids.add(id(row))
    return ids


def _line_in_table_region(line: OCRVisualLine, table_line_ids: set[int]) -> bool:
    if id(line) in table_line_ids:
        return True
    plain = strip_accents(line.text).lower()
    if sum(1 for keyword in HEADER_KEYWORDS if re.search(rf"\b{re.escape(keyword)}\b", plain)) >= 3:
        return True
    return _looks_like_product_row(plain)


def _detect_columns(lines: list[OCRVisualLine], max_x: float) -> tuple[list[OCRVisualLine], list[OCRVisualLine]]:
    midpoint = max_x * 0.5
    left = [line for line in lines if line.bbox and _center_x(line.bbox) <= midpoint]
    right = [line for line in lines if line.bbox and _center_x(line.bbox) > midpoint]
    return left, right


def _contains_date_or_reference(text: str) -> bool:
    plain = strip_accents(text).lower()
    return bool(
        re.search(r"\b(?:\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}|\d{4}[./\-]\d{1,2}[./\-]\d{1,2})\b", text)
        or re.search(r"\b(?:inv|fac|bl|dn|cmd|po)[-_]?\d+", plain)
    )


def _collect_customer_lines(
    lines: list[OCRVisualLine],
    right_lines: list[OCRVisualLine],
    top_lines: list[OCRVisualLine],
    table_regions: set[int],
) -> list[OCRVisualLine]:
    anchors = [
        line for line in right_lines
        if line in top_lines
        and id(line) not in table_regions
        and not _line_in_table_region(line, table_regions)
        and any(re.search(rf"\b{re.escape(keyword)}\b", strip_accents(line.text).lower()) for keyword in BLOCK_KEYWORDS["customer"])
    ]
    if anchors:
        return _expand_nearby_lines(lines, anchors, "customer")
    fallback = [
        line for line in right_lines
        if line in top_lines
        and id(line) not in table_regions
        and not _line_in_table_region(line, table_regions)
        and not _looks_like_address_line(line.text)
        and sum(char.isalpha() for char in line.text) >= 4
    ]
    return fallback[:6]


def _looks_like_product_row(plain: str) -> bool:
    if any(word in plain for word in FOOTER_WORDS):
        return False
    has_table_word = any(re.search(rf"\b{re.escape(word)}\b", plain) for word in ("description", "quantity", "qty", "price", "total", "amount"))
    number_count = len(re.findall(r"\d", plain))
    return has_table_word and number_count >= 2


def _looks_like_address_line(text: str) -> bool:
    plain = strip_accents(text).lower()
    if re.search(r"\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|rue|route|suite|apt|postal|zip)\b", plain):
        return True
    return bool(re.search(r"\b[a-z]{1,3}\s*\d[a-z0-9]\s*\d[a-z0-9]\d\b", text.upper()))


def _infer_columns(header: OCRVisualLine) -> dict[str, dict[str, Any]]:
    columns: dict[str, dict[str, Any]] = {}
    for block in header.blocks:
        text = strip_accents(block.text).lower()
        key = None
        if any(word in text for word in REFERENCE_WORDS):
            key = "reference"
        if any(word in text for word in DESCRIPTION_WORDS):
            key = "description"
        elif any(word in text for word in QUANTITY_WORDS):
            key = "quantity"
        elif "net worth" in text or "net amount" in text:
            key = "line_total_ht"
        elif "gross worth" in text or "gross total" in text:
            key = "total"
        elif any(word in text for word in PRICE_WORDS):
            key = "unit_price"
        elif any(word in text for word in UNIT_WORDS):
            key = "unit"
        elif any(word in text for word in DISCOUNT_WORDS):
            key = "discount"
        elif any(word in text for word in TAX_WORDS):
            key = "tax_rate"
        elif any(word in text for word in TOTAL_WORDS):
            key = "total"
        if key:
            columns[key] = {"x1": block.bbox.x1, "x2": block.bbox.x2, "center": _center_x(block.bbox), "label": block.text}

    if ("description" not in columns or len(columns) < 3) and header.bbox:
        approximate = _infer_columns_from_combined_header(header)
        columns.update({key: value for key, value in approximate.items() if key not in columns})

    sorted_cols = sorted(columns.items(), key=lambda item: item[1]["center"])
    for idx, (key, column) in enumerate(sorted_cols):
        left = (sorted_cols[idx - 1][1]["center"] + column["center"]) / 2 if idx else 0
        right = (column["center"] + sorted_cols[idx + 1][1]["center"]) / 2 if idx + 1 < len(sorted_cols) else 1_000_000
        column["left_boundary"] = left
        column["right_boundary"] = right
    return columns


def _infer_columns_from_combined_header(header: OCRVisualLine) -> dict[str, dict[str, Any]]:
    text = strip_accents(header.text).lower()
    if sum(1 for keyword in HEADER_KEYWORDS if keyword in text) < 3 or not header.bbox:
        return {}
    x1 = header.bbox.x1
    width = max(1.0, header.bbox.x2 - header.bbox.x1)
    keyword_specs: list[tuple[str, tuple[str, ...], float, str]] = [
        ("reference", ("reference", "ref", "code", "sku"), 0.10, "Reference"),
        ("description", ("description", "designation", "product", "service"), 0.24, "Description"),
        ("quantity", ("quantity", "qty", "qte", "quantite"), 0.58, "Quantity"),
        ("unit", ("unit", "uom", "unite"), 0.66, "Unit"),
        ("unit_price", ("unit price", "net price", "price", "prix"), 0.74, "Price"),
        ("discount", ("discount", "remise", "rabais"), 0.80, "Discount"),
        ("line_total_ht", ("net worth", "net amount", "subtotal", "total ht", "amount ht"), 0.86, "Net total"),
        ("tax_rate", ("vat", "tva", "tax"), 0.90, "Tax"),
        ("total", ("gross worth", "gross total", "grand total", "total ttc", "amount due", "total", "amount", "worth"), 0.93, "Total"),
    ]
    specs: list[tuple[str, float, str]] = []
    consumed_ranges: list[tuple[int, int]] = []

    def find_position(aliases: tuple[str, ...]) -> float | None:
        for alias in aliases:
            start = text.find(alias)
            if start == -1:
                continue
            end = start + len(alias)
            overlap = any(not (end <= used_start or start >= used_end) for used_start, used_end in consumed_ranges)
            if overlap:
                continue
            consumed_ranges.append((start, end))
            return (start + end) / 2 / max(len(text), 1)
        return None

    for key, aliases, fallback_ratio, label in keyword_specs:
        ratio = find_position(aliases)
        if ratio is None:
            if key == "reference" and not any(alias in text for alias in ("reference", "ref", "code", "sku")):
                continue
            if key == "description" and not any(alias in text for alias in ("description", "designation", "item", "product", "service")):
                continue
            if key == "quantity" and not any(alias in text for alias in ("qty", "quantity", "qte")):
                continue
            if key == "unit" and not any(alias in text for alias in ("unit", "uom", "unite")):
                continue
            if key == "unit_price" and not any(alias in text for alias in ("price", "prix")):
                continue
            if key == "discount" and not any(alias in text for alias in ("discount", "remise", "rabais")):
                continue
            if key == "line_total_ht" and not any(alias in text for alias in ("net worth", "net amount", "subtotal", "total ht", "amount ht")):
                continue
            if key == "tax_rate" and not any(alias in text for alias in ("vat", "tva", "tax")):
                continue
            if key == "total" and not any(alias in text for alias in ("total", "amount", "gross worth", "gross total", "worth")):
                continue
            ratio = fallback_ratio
        specs.append((key, ratio, label))

    columns: dict[str, dict[str, Any]] = {}
    for key, ratio, label in specs:
        center = x1 + width * ratio
        columns[key] = {"x1": center - 20, "x2": center + 20, "center": center, "label": label}
    return columns


def _build_table_rows(header: OCRVisualLine, lines: list[OCRVisualLine], columns: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    header_y = header.bbox.y2 if header.bbox else 0
    candidate_lines = [line for line in lines if line.page == header.page and line.bbox and line.bbox.y1 > header_y + 1]
    stop_y = _table_stop_y(candidate_lines)
    body_lines = [line for line in candidate_lines if line.bbox.y1 < stop_y]
    row_groups = _group_body_lines_into_rows(body_lines, columns)
    rows: list[dict[str, Any]] = []
    for row_lines in row_groups:
        row = _reconstruct_row(row_lines, columns)
        if row and not _is_footer_text(row.get("text", "")):
            rows.append(row)
        elif row_lines:
            invalid = _invalid_table_row(row_lines, "row could not be reconstructed into safe product cells")
            if invalid:
                rows.append(invalid)
    return rows



def _text_line_has_description_and_amounts(text: str) -> bool:
    if _is_footer_text(text):
        return False
    return sum(char.isalpha() for char in text) >= 3 and len(re.findall(r"[-+]?(?:[$€£]\s*)?\d+(?:[,.]\d+)?", text)) >= 2

def _reconstruct_row(row_lines: list[OCRVisualLine], columns: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    blocks = [block for line in row_lines for block in line.blocks if block.bbox]
    if not blocks:
        return None
    fallback = _reconstruct_row_from_text(row_lines, columns)
    if fallback and len(blocks) <= 2:
        return fallback
    cells: dict[str, list[OCRLine]] = {key: [] for key in columns}
    for block in blocks:
        description_x1 = columns["description"].get("x1", columns["description"].get("left_boundary", 0))
        if re.fullmatch(r"0?\d{1,3}\.?", block.text.strip()) and block.bbox.x2 < description_x1 - 8:
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
        if key in {"quantity", "unit_price", "tax_rate", "total", "discount", "line_total_ht"}:
            values[key] = _parse_cell_number(text)
        else:
            values[key] = re.sub(r"\s+", " ", text).strip()
        cell_bboxes[key] = _merge_boxes([block.bbox for block in ordered]).model_dump(mode="json")
    description = str(values.get("description") or "").strip()
    if len(description) < 3 or sum(char.isalpha() for char in description) < 3:
        return None
    if values.get("quantity") is None and values.get("total") is None and values.get("unit_price") is None:
        return None
    if values.get("reference") is None:
        values["reference"] = _find_reference_text(blocks)
    if values.get("reference") and description.lower().startswith(str(values["reference"]).lower()):
        description = description[len(str(values["reference"])):].strip(" -:|")
        values["description"] = description
    if len(description) < 3 or sum(char.isalpha() for char in description) < 3:
        return None
    if values.get("line_total_ht") is None and values.get("quantity") is not None and values.get("unit_price") is not None:
        values["line_total_ht"] = round(values["quantity"] * values["unit_price"], 3)
    if values.get("total") is None and values.get("quantity") is not None and values.get("unit_price") is not None and values.get("line_total_ht") is None:
        values["total"] = round(values["quantity"] * values["unit_price"], 3)
    if values.get("total") is None and values.get("line_total_ht") is not None:
        values["total"] = values.get("line_total_ht")
    if values.get("line_total_ht") is None and values.get("total") is not None:
        values["line_total_ht"] = values.get("total")
    boxes = [block.bbox for block in blocks]
    confidences = [block.confidence for block in blocks if block.confidence is not None]
    bbox = _merge_boxes(boxes).model_dump(mode="json")
    return {
        "text": " ".join(line.text for line in row_lines),
        "values": values,
        "bbox": bbox,
        "cell_bboxes": cell_bboxes,
        "source_ocr_nodes": [block.line_index for block in blocks if block.line_index is not None],
        "needs_review": values.get("quantity") is None or values.get("total") is None,
        "confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }


def _invalid_table_row(row_lines: list[OCRVisualLine], reason: str) -> dict[str, Any] | None:
    text = " ".join(line.text for line in row_lines).strip()
    if not text or _is_footer_text(text):
        return None
    if sum(char.isalpha() for char in text) < 3 and len(re.findall(r"\d", text)) < 2:
        return None
    boxes = [line.bbox for line in row_lines if line.bbox]
    confidences = [line.confidence for line in row_lines if line.confidence is not None]
    bbox = _merge_boxes(boxes).model_dump(mode="json") if boxes else None
    return {
        "text": text,
        "values": {"description": text if sum(char.isalpha() for char in text) >= 3 else None},
        "bbox": bbox,
        "cell_bboxes": {},
        "source_ocr_nodes": [block.line_index for line in row_lines for block in line.blocks if block.line_index is not None],
        "needs_review": True,
        "invalid": True,
        "rejection_reasons": [reason],
        "confidence": min(0.45, round(sum(confidences) / len(confidences), 3)) if confidences else 0.35,
    }



def _reconstruct_row_from_text(row_lines: list[OCRVisualLine], columns: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    text = " ".join(line.text for line in row_lines).strip()
    if _is_footer_text(text):
        return None
    numbers = re.findall(r"[-+]?(?:[$€£]\s*)?\d+(?:[,.]\d+)?", text)
    parsed = [parse_amount(value) for value in numbers]
    parsed = [value for value in parsed if value is not None]
    if len(parsed) < 1:
        return None
    row_text = re.sub(r"^\s*0?\d{1,3}\s+", "", text)
    first_number = re.search(r"[-+]?(?:[$€£]\s*)?\d", row_text)
    description = row_text[:first_number.start()].strip(" #|:-0123456789") if first_number else row_text
    description = re.sub(r"\s+", " ", description).strip()
    if len(description) < 3 or sum(char.isalpha() for char in description) < 3:
        return None
    values: dict[str, Any] = {"description": description}
    if len(parsed) >= 3:
        values["quantity"] = parsed[-3]
        values["unit_price"] = parsed[-2]
        values["total"] = parsed[-1]
    elif len(parsed) == 2:
        values["unit_price"] = parsed[-2]
        values["total"] = parsed[-1]
    else:
        values["total"] = parsed[-1]
    values["reference"] = _find_reference_text([block for line in row_lines for block in line.blocks])
    if values.get("reference") and description.lower().startswith(str(values["reference"]).lower()):
        description = description[len(str(values["reference"])):].strip(" -:|")
        values["description"] = description
    if len(description) < 3 or sum(char.isalpha() for char in description) < 3:
        return None
    boxes = [line.bbox for line in row_lines if line.bbox]
    confidences = [line.confidence for line in row_lines if line.confidence is not None]
    bbox = _merge_boxes(boxes).model_dump(mode="json") if boxes else None
    return {
        "text": text,
        "values": values,
        "bbox": bbox,
        "cell_bboxes": {"description": bbox} if bbox else {},
        "source_ocr_nodes": [block.line_index for line in row_lines for block in line.blocks if block.line_index is not None],
        "needs_review": values.get("quantity") is None or values.get("unit_price") is None,
        "confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }


def _group_body_lines_into_rows(body_lines: list[OCRVisualLine], columns: dict[str, dict[str, Any]]) -> list[list[OCRVisualLine]]:
    if not body_lines:
        return []
    sorted_lines = sorted(body_lines, key=lambda line: (line.bbox.y1, line.bbox.x1))
    rows: list[list[OCRVisualLine]] = []
    for line in sorted_lines:
        if _is_footer_text(line.text):
            break
        if not rows:
            rows.append([line])
            continue
        previous_group = rows[-1]
        previous = previous_group[-1]
        close_y = abs(line.bbox.y1 - previous.bbox.y1) <= max(18, (previous.bbox.y2 - previous.bbox.y1) * 1.2)
        fragment = _looks_like_wrapped_fragment(line, columns)
        if close_y or fragment:
            previous_group.append(line)
        else:
            rows.append([line])
    filtered: list[list[OCRVisualLine]] = []
    for row in rows:
        row_text = " ".join(item.text for item in row)
        if _line_has_row_anchor(row[0], columns) or _line_has_description_and_amounts(row[0], columns) or _text_line_has_description_and_amounts(row_text):
            filtered.append(row)
        elif filtered and _looks_like_wrapped_fragment(row[0], columns):
            filtered[-1].extend(row)
    return filtered


def _looks_like_wrapped_fragment(line: OCRVisualLine, columns: dict[str, dict[str, Any]]) -> bool:
    if not line.bbox:
        return False
    line_text = line.text.strip()
    if _find_reference_text(line.blocks):
        return False
    if _line_has_row_anchor(line, columns):
        return False
    description_boundary = columns.get("quantity", {}).get("left_boundary", columns.get("total", {}).get("left_boundary", 999999))
    mostly_left = line.bbox.x2 <= description_boundary + 20
    has_words = sum(char.isalpha() for char in line_text) >= 3
    lacks_many_numbers = len(re.findall(r"[-+]?(?:[$€£]\s*)?\d+(?:[,.]\d+)?", line_text)) <= 1
    return mostly_left and has_words and lacks_many_numbers


def _find_reference_text(blocks: list[OCRLine]) -> str | None:
    for block in blocks:
        match = re.search(r"\b[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+\b", block.text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None

def _column_for_block(block: OCRLine, columns: dict[str, dict[str, Any]]) -> str | None:
    center = _center_x(block.bbox)
    for key, column in columns.items():
        if column["left_boundary"] <= center < column["right_boundary"]:
            return key
    return None


def _parse_cell_number(text: str) -> float | None:
    if not re.search(r"\d", text):
        return None
    matches = re.findall(r"[-+]?(?:[$€£]\s*)?(?:\d[\d ]*[,.]\d{2,3}|\d{1,3}(?:[ .]\d{3})+|\d+)", text)
    if not matches:
        return None
    return parse_amount(matches[-1])


def _line_has_row_anchor(line: OCRVisualLine, columns: dict[str, dict[str, Any]]) -> bool:
    description_left = columns["description"].get("x1", 80)
    return any(re.fullmatch(r"0?\d{1,3}", block.text.strip()) and block.bbox.x1 < description_left for block in line.blocks)


def _line_has_description_and_amounts(line: OCRVisualLine, columns: dict[str, dict[str, Any]]) -> bool:
    has_desc = any(_column_for_block(block, columns) == "description" and sum(char.isalpha() for char in block.text) >= 3 for block in line.blocks if block.bbox)
    amount_count = sum(1 for block in line.blocks if block.bbox and _column_for_block(block, columns) in {"quantity", "unit_price", "line_total_ht", "total"} and _parse_cell_number(block.text) is not None)
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







