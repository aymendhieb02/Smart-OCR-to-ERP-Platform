"""Benchmark-only generic table crop generation and coordinate mapping."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from statistics import median
from typing import Any, Iterable

import numpy as np

from app.core.schemas import OCRLine
from scripts.local_table_structure_adapter import CanonicalTable, arithmetic_diagnostics


HEADER_ALIASES = {
    "description": ("description", "designation", "article", "product", "item", "goods"),
    "quantity": ("quantity", "quantite", "qty", "qte"),
    "unit": ("unit", "unite", "u m"),
    "unit_price": ("unit price", "prix unitaire", "price", "p u"),
    "line_total": ("total price", "line total", "montant", "amount", "total"),
}


@dataclass(frozen=True)
class CropContract:
    page_width: int
    page_height: int
    bbox: dict[str, float]
    crop_width: int
    crop_height: int
    scale_x: float = 1.0
    scale_y: float = 1.0
    source: str = "unknown"
    padding_percent: float = 0.0
    rationale: str = ""


@dataclass
class SemanticCandidate:
    row_index: int
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    financial_status: str = "unavailable"

    @property
    def coherent(self) -> bool:
        return all(self.fields.get(key, {}).get("status") == "available" for key in ("description", "quantity", "unit_price", "line_total"))

    @property
    def complete(self) -> bool:
        return self.coherent and self.fields.get("unit", {}).get("status") == "available"


def make_crop(image: np.ndarray, bbox: dict[str, float], *, padding_percent: float, source: str, rationale: str = "") -> tuple[np.ndarray, CropContract]:
    height, width = image.shape[:2]
    base_width = max(1.0, bbox["x2"] - bbox["x1"])
    base_height = max(1.0, bbox["y2"] - bbox["y1"])
    pad_x, pad_y = base_width * padding_percent, base_height * padding_percent
    x1 = max(0, int(np.floor(bbox["x1"] - pad_x)))
    y1 = max(0, int(np.floor(bbox["y1"] - pad_y)))
    x2 = min(width, int(np.ceil(bbox["x2"] + pad_x)))
    y2 = min(height, int(np.ceil(bbox["y2"] + pad_y)))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("crop bbox has no area")
    crop = image[y1:y2, x1:x2]
    contract = CropContract(
        page_width=width, page_height=height,
        bbox={"x1": float(x1), "y1": float(y1), "x2": float(x2), "y2": float(y2)},
        crop_width=x2 - x1, crop_height=y2 - y1,
        source=source, padding_percent=padding_percent, rationale=rationale,
    )
    return crop, contract


def page_to_crop_bbox(bbox: dict[str, float], contract: CropContract) -> dict[str, float]:
    return {
        "x1": (bbox["x1"] - contract.bbox["x1"]) / contract.scale_x,
        "y1": (bbox["y1"] - contract.bbox["y1"]) / contract.scale_y,
        "x2": (bbox["x2"] - contract.bbox["x1"]) / contract.scale_x,
        "y2": (bbox["y2"] - contract.bbox["y1"]) / contract.scale_y,
    }


def crop_to_page_bbox(bbox: dict[str, float], contract: CropContract) -> dict[str, float]:
    return {
        "x1": bbox["x1"] * contract.scale_x + contract.bbox["x1"],
        "y1": bbox["y1"] * contract.scale_y + contract.bbox["y1"],
        "x2": bbox["x2"] * contract.scale_x + contract.bbox["x1"],
        "y2": bbox["y2"] * contract.scale_y + contract.bbox["y1"],
    }


def remap_table_to_page(table: CanonicalTable, contract: CropContract) -> CanonicalTable:
    for cell in table.cells:
        if cell.bbox:
            cell.bbox = crop_to_page_bbox(cell.bbox, contract)
    if table.bbox:
        table.bbox = crop_to_page_bbox(table.bbox, contract)
    table.metadata["crop_contract"] = contract.__dict__
    return table


def ocr_lines_in_crop(lines: Iterable[OCRLine], contract: CropContract, *, minimum_overlap: float = 0.50) -> list[OCRLine]:
    selected = []
    for line in lines:
        if not line.bbox:
            continue
        box = line.bbox.model_dump()
        overlap = _intersection_over_source(box, contract.bbox)
        if _center_inside(box, contract.bbox) or overlap >= minimum_overlap:
            selected.append(line)
    return selected


def generate_crop_candidates(image: np.ndarray, lines: list[OCRLine], deterministic_result: Any, existing_regions: Iterable[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, region in enumerate(getattr(deterministic_result, "regions", []) or []):
        if region.bbox:
            candidates.append({"source": f"deterministic_region_{index + 1}", "bbox": dict(region.bbox), "rationale": region.detection_method})
    for index, header in enumerate(getattr(deterministic_result, "headers", []) or []):
        expanded = expand_header_region(header.bbox, lines)
        if expanded:
            candidates.append({"source": f"header_expansion_{index + 1}", "bbox": expanded, "rationale": "detected header plus geometrically adjacent dense rows"})
    for region in existing_regions:
        if getattr(region, "name", "") == "line_items_table_area" and region.coordinates:
            x1, y1, x2, y2 = region.coordinates
            candidates.append({"source": "existing_line_items_table_area", "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}, "rationale": "existing generic OCR table region"})
    for index, candidate in enumerate(dense_ocr_regions(lines, image.shape[1], image.shape[0]), start=1):
        candidates.append({"source": f"dense_ocr_region_{index}", "bbox": candidate, "rationale": "generic contiguous rows with header or numeric table evidence"})
    return _deduplicate_candidates(candidates)


def expand_header_region(header_bbox: dict[str, float] | None, lines: list[OCRLine]) -> dict[str, float] | None:
    if not header_bbox:
        return None
    heights = [line.bbox.y2 - line.bbox.y1 for line in lines if line.bbox]
    typical = median(heights) if heights else 20.0
    start = header_bbox["y1"] - typical
    nearby = [line for line in lines if line.bbox and line.bbox.y2 >= start and line.bbox.y1 <= header_bbox["y2"] + typical * 12]
    if not nearby:
        return dict(header_bbox)
    return _union_line_boxes(nearby)


def dense_ocr_regions(lines: list[OCRLine], page_width: int, page_height: int) -> list[dict[str, float]]:
    positioned = [line for line in lines if line.bbox and line.text.strip()]
    if not positioned:
        return []
    heights = [line.bbox.y2 - line.bbox.y1 for line in positioned]
    typical = max(8.0, median(heights))
    rows: list[list[OCRLine]] = []
    for line in sorted(positioned, key=lambda item: ((item.bbox.y1 + item.bbox.y2) / 2, item.bbox.x1)):
        center = (line.bbox.y1 + line.bbox.y2) / 2
        target = next((row for row in rows if abs(center - sum((item.bbox.y1 + item.bbox.y2) / 2 for item in row) / len(row)) <= typical * 0.65), None)
        if target is None:
            rows.append([line])
        else:
            target.append(line)
    rows = [sorted(row, key=lambda item: item.bbox.x1) for row in rows]
    seeds: list[tuple[float, list[OCRLine]]] = []
    for row in rows:
        text = " ".join(line.text for line in row)
        header_hits = sum(_alias_score(text, alias) >= 0.72 for aliases in HEADER_ALIASES.values() for alias in aliases)
        numeric_blocks = sum(bool(re.search(r"\d", line.text)) for line in row)
        spread = max(line.bbox.x2 for line in row) - min(line.bbox.x1 for line in row)
        if header_hits >= 2 or (numeric_blocks >= 2 and len(row) >= 2) or (len(row) >= 4 and spread >= page_width * 0.35):
            seeds.append((sum((line.bbox.y1 + line.bbox.y2) / 2 for line in row) / len(row), row))
    groups: list[list[list[OCRLine]]] = []
    for center, row in seeds:
        if groups:
            last_center = max((line.bbox.y1 + line.bbox.y2) / 2 for item in groups[-1] for line in item)
            if center - last_center <= max(100.0, typical * 6):
                groups[-1].append(row)
                continue
        groups.append([row])
    scored = []
    for group in groups:
        group_lines = [line for row in group for line in row]
        box = _union_line_boxes(group_lines)
        if box:
            # Include immediately adjacent OCR evidence without expanding into the whole page.
            adjacent = [line for line in positioned if line.bbox.y2 >= box["y1"] - typical * 1.5 and line.bbox.y1 <= box["y2"] + typical * 1.5]
            box = _union_line_boxes(adjacent) or box
            area_ratio = (box["x2"] - box["x1"]) * (box["y2"] - box["y1"]) / (page_width * page_height)
            if area_ratio <= 0.65:
                scored.append((len(group_lines), box))
    return [box for _score, box in sorted(scored, key=lambda item: item[0], reverse=True)[:3]]


def reconstruct_semantic_candidates(table: CanonicalTable) -> list[SemanticCandidate]:
    if not table.rows:
        return []
    header_index, mapping = _best_header_mapping(table)
    if header_index is None or len(mapping) < 2:
        return []
    candidates: list[SemanticCandidate] = []
    for row in table.rows:
        if row.row_index <= header_index:
            continue
        grouped: dict[str, list[str]] = {key: [] for key in HEADER_ALIASES}
        for cell in row.cells:
            semantic = mapping.get(cell.column_index)
            if semantic and cell.text.strip():
                grouped[semantic].append(cell.text.strip())
        fields: dict[str, dict[str, Any]] = {}
        values: dict[str, Any] = {}
        for semantic in HEADER_ALIASES:
            texts = grouped[semantic]
            status = "unavailable" if not texts else ("ambiguous" if len(texts) > 1 else "available")
            value: Any = " ".join(texts).strip() if texts else None
            if semantic in {"quantity", "unit_price", "line_total"} and value:
                value = _parse_number(value)
                if value is None:
                    status = "ambiguous"
            fields[semantic] = {"status": status}
            if value is not None:
                values[semantic] = value
        candidate = SemanticCandidate(row.row_index, fields, values)
        if candidate.coherent:
            expected = values["quantity"] * values["unit_price"]
            delta = abs(expected - values["line_total"])
            candidate.financial_status = "consistent" if delta <= max(0.05, abs(values["line_total"]) * 0.01) else "inconsistent"
        candidates.append(candidate)
    return candidates


def _best_header_mapping(table: CanonicalTable) -> tuple[int | None, dict[int, str]]:
    best: tuple[int | None, dict[int, str]] = (None, {})
    for row in table.rows[:5]:
        mapping: dict[int, str] = {}
        for cell in row.cells:
            if cell.column_index is None or not cell.text:
                continue
            scored = [(max(_alias_score(cell.text, alias) for alias in aliases), semantic) for semantic, aliases in HEADER_ALIASES.items()]
            score, semantic = max(scored)
            if score >= 0.72:
                mapping[cell.column_index] = semantic
        if len(mapping) > len(best[1]):
            best = (row.row_index, mapping)
    return best


def _alias_score(text: str, alias: str) -> float:
    normalized = _normalize(text)
    alias_norm = _normalize(alias)
    if alias_norm in normalized:
        return 1.0
    tokens = normalized.split()
    windows = [" ".join(tokens[index:index + len(alias_norm.split())]) for index in range(max(1, len(tokens) - len(alias_norm.split()) + 1))]
    return max([SequenceMatcher(None, window, alias_norm).ratio() for window in windows] or [0.0])


def _parse_number(text: str) -> float | None:
    match = re.search(r"[-+]?\d[\d\s.,]*", text)
    if not match:
        return None
    value = match.group(0).replace(" ", "")
    if "," in value and "." in value:
        value = value.replace(",", "") if value.rfind(".") > value.rfind(",") else value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _normalize(text: str) -> str:
    import unicodedata
    return " ".join("".join(char for char in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(char) and (char.isalnum() or char.isspace())).split())


def _union_line_boxes(lines: list[OCRLine]) -> dict[str, float] | None:
    boxes = [line.bbox for line in lines if line.bbox]
    if not boxes:
        return None
    return {"x1": min(box.x1 for box in boxes), "y1": min(box.y1 for box in boxes), "x2": max(box.x2 for box in boxes), "y2": max(box.y2 for box in boxes)}


def _intersection_over_source(source: dict[str, float], target: dict[str, float]) -> float:
    width = max(0.0, min(source["x2"], target["x2"]) - max(source["x1"], target["x1"]))
    height = max(0.0, min(source["y2"], target["y2"]) - max(source["y1"], target["y1"]))
    area = max(0.0, source["x2"] - source["x1"]) * max(0.0, source["y2"] - source["y1"])
    return width * height / area if area else 0.0


def _center_inside(source: dict[str, float], target: dict[str, float]) -> bool:
    x, y = (source["x1"] + source["x2"]) / 2, (source["y1"] + source["y2"]) / 2
    return target["x1"] <= x <= target["x2"] and target["y1"] <= y <= target["y2"]


def _deduplicate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = []
    for candidate in candidates:
        if any(_iou(candidate["bbox"], existing["bbox"]) >= 0.92 for existing in unique):
            continue
        unique.append(candidate)
    return unique


def _iou(a: dict[str, float], b: dict[str, float]) -> float:
    x1, y1, x2, y2 = max(a["x1"], b["x1"]), max(a["y1"], b["y1"]), min(a["x2"], b["x2"]), min(a["y2"], b["y2"])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
    area_b = (b["x2"] - b["x1"]) * (b["y2"] - b["y1"])
    return intersection / (area_a + area_b - intersection) if area_a + area_b - intersection else 0.0
