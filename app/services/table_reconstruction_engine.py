from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.schemas import BoundingBox, LineItem, OCRLine
from app.utils.helpers import parse_amount, strip_accents


@dataclass
class TableRegion:
    page: int
    bbox: dict[str, float] | None
    confidence: float
    detection_method: str
    header_bbox: dict[str, float] | None = None
    body_bbox: dict[str, float] | None = None
    footer_bbox: dict[str, float] | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class TableHeader:
    page: int
    text: str
    bbox: dict[str, float] | None
    confidence: float
    source_line_ids: list[int] = field(default_factory=list)
    aliases_matched: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class TableColumn:
    semantic_type: str
    x_min: float
    x_max: float
    center_x: float
    confidence: float
    header_text: str = ""
    aliases_matched: list[str] = field(default_factory=list)


@dataclass
class TableCell:
    text: str
    bbox: dict[str, float] | None
    column_type: str | None
    source_line_ids: list[int] = field(default_factory=list)
    source_word_ids: list[int] = field(default_factory=list)
    assignment_confidence: float = 0.0
    reason: str = ""


@dataclass
class RowAnchor:
    row_anchor_id: str
    page: int
    baseline_y: float
    bbox: dict[str, float] | None
    anchor_type: str
    confidence: float


@dataclass
class RowFragment:
    text: str
    bbox: dict[str, float] | None
    assigned_columns: dict[str, str]
    baseline_y: float
    row_anchor_id: str | None
    continuation_score: float
    numeric_density: float


@dataclass
class ReconstructedRow:
    row_id: str
    fragments: list[RowFragment]
    description: str | None = None
    reference: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    discount: float | None = None
    tax_rate: float | None = None
    line_total_ht: float | None = None
    line_total_ttc: float | None = None
    confidence: float = 0.0
    reconstruction_reason: str = ""
    validation_status: str = "needs_review"
    warning_codes: list[str] = field(default_factory=list)
    bbox: dict[str, float] | None = None
    cell_bboxes: dict[str, Any] = field(default_factory=dict)
    excluded: bool = False
    exclusion_reason: str | None = None


@dataclass
class ReconstructionStrategyResult:
    name: str
    applicability_score: float
    confidence: float
    evidence: dict[str, Any]
    result: "TableReconstructionResult"
    rejection_reasons: list[str] = field(default_factory=list)


@dataclass
class TableReconstructionResult:
    line_items: list[LineItem]
    regions: list[TableRegion]
    headers: list[TableHeader]
    columns: list[TableColumn]
    cells: list[TableCell]
    row_anchors: list[RowAnchor]
    fragments: list[RowFragment]
    rows: list[ReconstructedRow]
    excluded_rows: list[ReconstructedRow]
    unresolved_fragments: list[RowFragment]
    reconciliation: dict[str, Any]
    diagnostics: dict[str, Any]
    selected_strategy: str = "UNRESOLVED"
    strategy_scores: dict[str, float] = field(default_factory=dict)
    selection_explanation: str = ""

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "regions": [asdict(item) for item in self.regions],
            "headers": [asdict(item) for item in self.headers],
            "columns": [asdict(item) for item in self.columns],
            "cells": [asdict(item) for item in self.cells],
            "row_anchors": [asdict(item) for item in self.row_anchors],
            "fragments": [asdict(item) for item in self.fragments],
            "reconstructed_rows": [asdict(item) for item in self.rows],
            "excluded_rows": [asdict(item) for item in self.excluded_rows],
            "unresolved_fragments": [asdict(item) for item in self.unresolved_fragments],
            "reconciliation": self.reconciliation,
            "diagnostics": self.diagnostics,
            "selected_strategy": self.selected_strategy,
            "strategy_scores": self.strategy_scores,
            "selection_explanation": self.selection_explanation,
        }


ALIASES: dict[str, tuple[str, ...]] = {
    "description": ("description", "designation", "désignation", "article", "produit", "product", "item", "libellé", "libelle", "details"),
    "reference": ("référence", "reference", "ref", "code produit", "product code", "code", "sku", "article no"),
    "quantity": ("quantity", "quantité", "quantite", "qty", "qte", "qt", "qte livree"),
    "unit_price": ("unit price", "prix unitaire", "prix unit", "p.u.", "pu", "unit cost", "net price", "rate", "price", "prix"),
    "discount": ("discount", "remise", "rabais", "disc"),
    "tax_rate": ("tva", "vat", "tax", "taxe", "taux"),
    "line_total_ht": ("total ht", "net amount", "net worth", "amount ht", "montant ht"),
    "line_total_ttc": ("total ttc", "gross amount", "gross worth", "gross", "amount due", "line total", "montant", "amount", "worth", "total"),
    "unit": ("unité", "unite", "unit", "uom"),
}

FOOTER_TERMS = (
    "subtotal",
    "sub total",
    "sous total",
    "sous-total",
    "total ht",
    "total ttc",
    "grand total",
    "total due",
    "balance due",
    "summary",
    "amount paid",
    "amount due",
    "sales tax",
    "vat summary",
    "tax summary",
    "tva",
    "shipping",
    "delivery fee",
    "handling",
    "stamp",
    "timbre",
    "discount",
    "remise",
    "payment",
    "iban",
    "rib",
    "swift",
    "bank",
    "banque",
    "terms",
    "conditions",
)

UNITS = ("each", "piece", "pièce", "piece", "pcs", "pc", "unit", "unite", "unité", "kg", "m", "h")
REFERENCE_RE = re.compile(r"\b[A-Z0-9]{2,}(?:[-_][A-Z0-9]{2,})+\b", re.IGNORECASE)


def reconstruct_line_items(blocks: list[OCRLine]) -> TableReconstructionResult:
    positioned = [block for block in blocks if block.bbox and block.text and block.text.strip()]
    if not positioned:
        return _empty_result("no positioned OCR blocks")

    strategies = [
        _strategy_columnar_table(positioned),
        _strategy_key_value_records(positioned),
        _strategy_repeated_vertical_blocks(positioned),
        _strategy_numeric_anchored_rows(positioned),
        _strategy_headerless_columnar(positioned),
        _strategy_single_item_summary(positioned),
    ]
    selected = _select_strategy(strategies)
    result = selected.result
    result.selected_strategy = selected.name
    result.strategy_scores = {strategy.name: round(strategy.applicability_score, 4) for strategy in strategies}
    result.selection_explanation = _strategy_selection_explanation(selected, strategies)
    result.diagnostics.update({
        "available_strategies": [
            {
                "name": strategy.name,
                "applicability_score": round(strategy.applicability_score, 4),
                "confidence": round(strategy.confidence, 4),
                "evidence": strategy.evidence,
                "rejection_reasons": strategy.rejection_reasons,
                "row_count": len(strategy.result.rows),
            }
            for strategy in strategies
        ],
        "selected_strategy": selected.name,
        "strategy_scores": result.strategy_scores,
        "selection_explanation": result.selection_explanation,
    })
    return result


def _strategy_columnar_table(positioned: list[OCRLine]) -> ReconstructionStrategyResult:
    result = _reconstruct_columnar(positioned, allow_headerless=False)
    diagnostics = result.diagnostics
    score = _score_result(result, base=0.68 if result.headers else 0.0)
    if not result.headers:
        score = 0.0
    return ReconstructionStrategyResult(
        name="COLUMNAR_TABLE",
        applicability_score=score,
        confidence=0.82 if result.line_items else 0.2,
        evidence={
            "header_candidate_found": diagnostics.get("header_candidate_found", False),
            "header_confirmed": diagnostics.get("header_confirmed", False),
            "table_region_detected": diagnostics.get("table_region_detected", False),
            "table_body_detected": diagnostics.get("table_body_detected", False),
            "rows_reconstructed": len(result.rows),
        },
        result=result,
        rejection_reasons=[] if result.line_items else [diagnostics.get("failure_reason") or "no columnar rows reconstructed"],
    )


def _strategy_headerless_columnar(positioned: list[OCRLine]) -> ReconstructionStrategyResult:
    result = _reconstruct_columnar(positioned, allow_headerless=True)
    if result.headers:
        return ReconstructionStrategyResult("HEADERLESS_COLUMNAR", 0.0, 0.0, {"reason": "confirmed header already exists"}, _empty_result("not headerless"))
    score = _score_result(result, base=0.48)
    if not result.line_items:
        score = 0.0
    return ReconstructionStrategyResult(
        name="HEADERLESS_COLUMNAR",
        applicability_score=score,
        confidence=0.54 if result.line_items else 0.1,
        evidence={
            "numeric_columns": len([col for col in result.columns if col.semantic_type != "description"]),
            "row_anchor_detected": bool(result.row_anchors),
            "rows_reconstructed": len(result.rows),
        },
        result=result,
        rejection_reasons=[] if result.line_items else [result.diagnostics.get("failure_reason") or "weak headerless evidence"],
    )


def _strategy_key_value_records(positioned: list[OCRLine]) -> ReconstructionStrategyResult:
    physical_rows = _group_physical_rows(positioned, tolerance=12.0)
    records, evidence = _build_key_value_records(physical_rows)
    rows = [_row_from_values(f"kv_{index}", record["values"], record["blocks"], "key-value semantic labels") for index, record in enumerate(records, start=1)]
    rows = [row for row in rows if row and not row.excluded]
    result = _result_from_rows(
        rows,
        positioned,
        "KEY_VALUE_RECORDS",
        evidence=evidence | {"records_found": len(records)},
        unresolved=[],
    )
    score = 0.0
    if len(rows) >= 2 or (len(rows) == 1 and evidence.get("label_hits", 0) >= 2):
        score = _score_result(result, base=0.58 if len(rows) >= 2 else 0.5)
    return ReconstructionStrategyResult(
        name="KEY_VALUE_RECORDS",
        applicability_score=score,
        confidence=0.68 if rows else 0.1,
        evidence=evidence | {"rows_reconstructed": len(rows)},
        result=result,
        rejection_reasons=[] if score else ["no repeated item key/value records"],
    )


def _strategy_repeated_vertical_blocks(positioned: list[OCRLine]) -> ReconstructionStrategyResult:
    physical_rows = _group_physical_rows(positioned, tolerance=12.0)
    records, evidence = _build_repeated_vertical_records(physical_rows)
    rows = [_row_from_values(f"vb_{index}", record["values"], record["blocks"], "repeated vertical product block") for index, record in enumerate(records, start=1)]
    rows = [row for row in rows if row and not row.excluded]
    result = _result_from_rows(rows, positioned, "REPEATED_VERTICAL_BLOCKS", evidence=evidence | {"records_found": len(records)}, unresolved=[])
    score = _score_result(result, base=0.52) if len(rows) >= 2 and evidence.get("label_hits", 0) >= 4 else 0.0
    return ReconstructionStrategyResult(
        name="REPEATED_VERTICAL_BLOCKS",
        applicability_score=score,
        confidence=0.62 if rows else 0.1,
        evidence=evidence | {"rows_reconstructed": len(rows)},
        result=result,
        rejection_reasons=[] if score else ["no repeated vertical product blocks"],
    )


def _strategy_numeric_anchored_rows(positioned: list[OCRLine]) -> ReconstructionStrategyResult:
    rows, evidence, unresolved = _build_numeric_anchor_rows(positioned)
    result = _result_from_rows(rows, positioned, "NUMERIC_ANCHORED_ROWS", evidence=evidence, unresolved=unresolved)
    score = _score_result(result, base=0.5 if len(rows) >= 2 else 0.42) if rows else 0.0
    return ReconstructionStrategyResult(
        name="NUMERIC_ANCHORED_ROWS",
        applicability_score=score,
        confidence=0.58 if rows else 0.1,
        evidence=evidence | {"rows_reconstructed": len(rows)},
        result=result,
        rejection_reasons=[] if score else ["not enough numeric anchors"],
    )


def _strategy_single_item_summary(positioned: list[OCRLine]) -> ReconstructionStrategyResult:
    rows, evidence = _build_single_item_summary(positioned)
    result = _result_from_rows(rows, positioned, "SINGLE_ITEM_SUMMARY", evidence=evidence, unresolved=[])
    base = 0.46 if evidence.get("over_merge_detected") else 0.36
    score = _score_result(result, base=base) if rows else 0.0
    return ReconstructionStrategyResult(
        name="SINGLE_ITEM_SUMMARY",
        applicability_score=score,
        confidence=0.42 if rows else 0.0,
        evidence=evidence,
        result=result,
        rejection_reasons=[] if score else ["no safe single item summary"],
    )


def _reconstruct_columnar(positioned: list[OCRLine], *, allow_headerless: bool) -> TableReconstructionResult:
    physical_rows = _group_physical_rows(positioned)
    headers = _detect_headers(physical_rows)
    if headers:
        header = headers[0]
        columns = _infer_columns_from_header(header, physical_rows)
        method = "header_aliases"
        header_y = header.bbox["y2"] if header.bbox else 0.0
    else:
        if not allow_headerless:
            return _empty_result("no confirmed table header", header_candidate_found=_header_candidate_found(physical_rows))
        columns = _infer_columns_from_body(physical_rows)
        method = "numeric_alignment"
        header_y = min((_row_bbox(row)["y1"] for row in physical_rows if _row_bbox(row)), default=0.0) - 1
        headers = []
    if len(columns) < 3 or "description" not in {column.semantic_type for column in columns}:
        return _empty_result("no reliable table columns", header_candidate_found=_header_candidate_found(physical_rows), header_confirmed=bool(headers))

    stop_y, footer_bbox = _detect_stop_y(physical_rows, header_y)
    body_rows = [
        row for row in physical_rows
        if (bbox := _row_bbox(row))
        and bbox["y1"] > header_y
        and bbox["y1"] < stop_y
        and not _is_header_row(row)
    ]
    cells = _assign_cells(body_rows, columns)
    logical_groups, anchors, fragments, unresolved = _build_logical_rows(body_rows, columns)
    rows, excluded = _parse_rows(logical_groups, columns)
    if method == "numeric_alignment":
        for row in rows:
            row.validation_status = "needs_review"
            row.confidence = min(row.confidence, 0.58)
            if "AMBIGUOUS_COLUMN_ASSIGNMENT" not in row.warning_codes:
                row.warning_codes.append("AMBIGUOUS_COLUMN_ASSIGNMENT")
            row.reconstruction_reason = (row.reconstruction_reason + "; " if row.reconstruction_reason else "") + "headerless numeric alignment"
    items = [_row_to_line_item(row) for row in rows if not row.excluded]
    items = [item for item in items if item is not None]
    region_bbox = _merge_bbox_dicts([_row_bbox(row) for row in body_rows] + ([headers[0].bbox] if headers else []))
    body_bbox = _merge_bbox_dicts([_row_bbox(row) for row in body_rows])
    region = TableRegion(
        page=positioned[0].page_number if positioned else 1,
        bbox=region_bbox,
        confidence=0.78 if method == "header_aliases" else 0.52,
        detection_method=method,
        header_bbox=headers[0].bbox if headers else None,
        body_bbox=body_bbox,
        footer_bbox=footer_bbox,
        diagnostics={
            "table_region_detection_method": method,
            "header_candidate_found": _header_candidate_found(physical_rows),
            "header_confirmed": bool(headers),
            "table_region_detected": bool(region_bbox and body_rows),
            "table_body_detected": bool(body_rows),
            "row_anchor_detected": bool(anchors),
            "rows_reconstructed": bool(rows),
            "header_detected": bool(headers),
            "boundary_confidence": 0.82 if footer_bbox else 0.55,
            "body_start_y": body_bbox.get("y1") if body_bbox else None,
            "body_end_y": body_bbox.get("y2") if body_bbox else None,
            "exclusion_reason": "" if body_rows else "no body rows inside table bounds",
        },
    )
    reconciliation = _reconcile_rows(items)
    return TableReconstructionResult(
        line_items=items,
        regions=[region],
        headers=headers,
        columns=columns,
        cells=cells,
        row_anchors=anchors,
        fragments=fragments,
        rows=rows,
        excluded_rows=excluded,
        unresolved_fragments=unresolved,
        reconciliation=reconciliation,
        diagnostics={
            "engine": "p3_deterministic_table_reconstruction",
            "header_candidate_found": _header_candidate_found(physical_rows),
            "header_confirmed": bool(headers),
            "table_region_detected": bool(region_bbox and body_rows),
            "table_body_detected": bool(body_rows),
            "row_anchor_detected": bool(anchors),
            "rows_reconstructed": bool(rows),
            "candidate_row_count": len(logical_groups),
            "reconstructed_row_count": len(rows),
            "validated_row_count": sum(row.validation_status == "validated" for row in rows),
            "review_row_count": sum(row.validation_status == "needs_review" for row in rows),
            "invalid_row_count": sum(row.validation_status == "invalid" for row in rows),
            "unresolved_fragment_count": len(unresolved),
        },
        selected_strategy="COLUMNAR_TABLE" if method == "header_aliases" else "HEADERLESS_COLUMNAR",
    )


def _empty_result(reason: str, *, header_candidate_found: bool = False, header_confirmed: bool = False) -> TableReconstructionResult:
    return TableReconstructionResult(
        [], [], [], [], [], [], [], [], [], [], {},
        {
            "engine": "p3_deterministic_table_reconstruction",
            "failure_reason": reason,
            "header_candidate_found": header_candidate_found,
            "header_confirmed": header_confirmed,
            "table_region_detected": False,
            "table_body_detected": False,
            "row_anchor_detected": False,
            "rows_reconstructed": False,
            "candidate_row_count": 0,
            "reconstructed_row_count": 0,
            "validated_row_count": 0,
            "review_row_count": 0,
            "invalid_row_count": 0,
            "unresolved_fragment_count": 0,
        },
    )


def _select_strategy(strategies: list[ReconstructionStrategyResult]) -> ReconstructionStrategyResult:
    columnar = next((strategy for strategy in strategies if strategy.name == "COLUMNAR_TABLE"), None)
    if columnar and columnar.result.line_items:
        challengers = [
            strategy for strategy in strategies
            if strategy.name != "COLUMNAR_TABLE"
            and strategy.result.line_items
            and strategy.applicability_score >= columnar.applicability_score + 0.18
            and _arithmetic_ratio(strategy.result.rows) >= 0.8
        ]
        return max(challengers, key=lambda item: item.applicability_score) if challengers else columnar
    if columnar and columnar.evidence.get("header_confirmed"):
        narrow = [
            strategy for strategy in strategies
            if strategy.result.line_items
            and (
                (strategy.name == "KEY_VALUE_RECORDS" and strategy.evidence.get("colon_label_hits", 0) >= 2)
                or (strategy.name == "SINGLE_ITEM_SUMMARY" and strategy.evidence.get("over_merge_detected"))
            )
            and _strategy_passes_fallback_gate(strategy)
        ]
        return max(narrow, key=lambda item: item.applicability_score) if narrow else _empty_strategy(
            "UNRESOLVED",
            "confirmed header but no safe table body rows",
            header_candidate_found=bool(columnar.evidence.get("header_candidate_found")),
            header_confirmed=bool(columnar.evidence.get("header_confirmed")),
        )
    non_empty = [strategy for strategy in strategies if strategy.result.line_items]
    gated = [strategy for strategy in non_empty if _strategy_passes_fallback_gate(strategy)]
    if gated:
        non_empty = gated
    elif non_empty:
        return _empty_strategy("UNRESOLVED", "fallback evidence below conservative threshold")
    pool = non_empty or strategies
    return max(
        pool,
        key=lambda strategy: (
            strategy.applicability_score,
            _arithmetic_ratio(strategy.result.rows),
            -len(strategy.result.unresolved_fragments),
            -_duplicate_total_penalty(strategy.result.rows),
        ),
    )


def _empty_strategy(name: str, reason: str, *, header_candidate_found: bool = False, header_confirmed: bool = False) -> ReconstructionStrategyResult:
    return ReconstructionStrategyResult(
        name,
        0.0,
        0.0,
        {"reason": reason, "header_candidate_found": header_candidate_found, "header_confirmed": header_confirmed},
        _empty_result(reason, header_candidate_found=header_candidate_found, header_confirmed=header_confirmed),
        [reason],
    )


def _strategy_passes_fallback_gate(strategy: ReconstructionStrategyResult) -> bool:
    if strategy.name == "KEY_VALUE_RECORDS":
        return strategy.applicability_score >= 0.6 and strategy.evidence.get("label_hits", 0) >= 2
    if strategy.name == "REPEATED_VERTICAL_BLOCKS":
        return strategy.applicability_score >= 0.72 and strategy.evidence.get("rows_reconstructed", 0) >= 2 and strategy.evidence.get("label_hits", 0) >= 4
    if strategy.name == "NUMERIC_ANCHORED_ROWS":
        return strategy.applicability_score >= 0.6 and strategy.evidence.get("numeric_anchor_count", 0) >= 1 and strategy.evidence.get("description_anchor_count", 0) >= 1
    if strategy.name == "HEADERLESS_COLUMNAR":
        return strategy.applicability_score >= 0.7 and strategy.evidence.get("row_anchor_detected")
    if strategy.name == "SINGLE_ITEM_SUMMARY":
        return strategy.applicability_score >= 0.62 and (strategy.evidence.get("candidate_rows", 0) == 1 or strategy.evidence.get("over_merge_detected"))
    return strategy.applicability_score > 0


def _strategy_selection_explanation(selected: ReconstructionStrategyResult, strategies: list[ReconstructionStrategyResult]) -> str:
    ordered = sorted(strategies, key=lambda item: item.applicability_score, reverse=True)
    runner_up = next((item for item in ordered if item.name != selected.name), None)
    if runner_up:
        return f"{selected.name} selected with score {selected.applicability_score:.3f}; next best {runner_up.name} scored {runner_up.applicability_score:.3f}."
    return f"{selected.name} selected with score {selected.applicability_score:.3f}."


def _score_result(result: TableReconstructionResult, *, base: float) -> float:
    rows = [row for row in result.rows if not row.excluded]
    if not rows:
        return 0.0
    completeness = sum(_row_completeness(row) for row in rows) / len(rows)
    arithmetic = _arithmetic_ratio(rows)
    unresolved_penalty = min(0.2, len(result.unresolved_fragments) * 0.03)
    duplicate_penalty = min(0.18, _duplicate_total_penalty(rows) * 0.04)
    footer_penalty = 0.18 if any(_is_footer_text(" ".join(filter(None, [row.description or "", str(row.line_total_ttc or "")])), row_like=True) for row in rows) else 0.0
    row_bonus = min(0.12, len(rows) * 0.02)
    return round(max(0.0, min(1.0, base + completeness * 0.16 + arithmetic * 0.16 + row_bonus - unresolved_penalty - duplicate_penalty - footer_penalty)), 4)


def _row_completeness(row: ReconstructedRow) -> float:
    checks = [
        bool(row.description),
        row.quantity is not None,
        row.unit_price is not None,
        row.line_total_ht is not None or row.line_total_ttc is not None,
    ]
    return sum(1 for item in checks if item) / len(checks)


def _arithmetic_ratio(rows: list[ReconstructedRow]) -> float:
    if not rows:
        return 0.0
    solvable = 0
    consistent = 0
    for row in rows:
        total = row.line_total_ht if row.line_total_ht is not None else row.line_total_ttc
        if row.quantity is None or row.unit_price is None or total is None:
            continue
        solvable += 1
        expected = round(row.quantity * row.unit_price - (row.discount or 0), 3)
        if abs(expected - total) <= max(0.05, abs(total) * 0.02):
            consistent += 1
    return consistent / max(1, solvable or len(rows))


def _duplicate_total_penalty(rows: list[ReconstructedRow]) -> int:
    totals = [round(float(row.line_total_ttc if row.line_total_ttc is not None else row.line_total_ht), 2) for row in rows if row.line_total_ttc is not None or row.line_total_ht is not None]
    return max(0, len(totals) - len(set(totals)))


def _header_candidate_found(rows: list[list[OCRLine]]) -> bool:
    for row in rows:
        if _matched_aliases(" ".join(block.text for block in row)):
            return True
    return False


LABEL_TO_FIELD: dict[str, tuple[str, ...]] = {
    "description": ("description", "designation", "item", "article", "product", "produit", "service", "libelle", "libellÃ©"),
    "reference": ("reference", "ref", "code", "sku", "product code", "code produit"),
    "quantity": ("quantity", "qty", "qte", "quantite", "quantitÃ©"),
    "unit_price": ("unit price", "prix unitaire", "prix unit", "price", "prix", "rate"),
    "unit": ("unit", "unite", "unitÃ©", "uom"),
    "discount": ("discount", "remise", "rabais"),
    "tax_rate": ("vat", "tva", "tax", "taxe"),
    "line_total_ttc": ("amount", "total", "montant", "line total", "gross amount", "total ttc"),
}


def _build_key_value_records(physical_rows: list[list[OCRLine]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    label_hits = 0
    colon_label_hits = 0
    for row in physical_rows:
        if _is_footer_row(row):
            if current:
                records.append(current)
                current = None
            break
        field, value = _extract_labeled_value(row)
        if not field:
            if current and _looks_like_unlabeled_kv_continuation(row):
                _assign_unlabeled_continuation(current, row)
            continue
        label_hits += 1
        if ":" in " ".join(block.text for block in row):
            colon_label_hits += 1
        if field == "description" and current and _record_has_item_evidence(current):
            records.append(current)
            current = None
        if current is None:
            current = {"values": {}, "blocks": []}
        if value:
            current["values"][field] = value
        current["blocks"].extend(row)
    if current and _record_has_item_evidence(current):
        records.append(current)
    records = [record for record in records if _record_has_item_evidence(record)]
    return records, {
        "label_hits": label_hits,
        "colon_label_hits": colon_label_hits,
        "description_anchor_count": sum(1 for record in records if record["values"].get("description")),
        "numeric_anchor_count": sum(_record_numeric_count(record) for record in records),
        "table_body_detected": bool(records),
    }


def _extract_labeled_value(row: list[OCRLine]) -> tuple[str | None, str | None]:
    ordered = sorted(row, key=lambda block: block.bbox.x1)
    full_text = " ".join(block.text for block in ordered)
    plain = _norm(full_text)
    for field, aliases in LABEL_TO_FIELD.items():
        for alias in sorted(aliases, key=len, reverse=True):
            normalized = _norm(alias)
            match = re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", plain)
            if not match:
                continue
            value = _value_after_label(full_text, alias)
            if not value and len(ordered) >= 2:
                value_blocks = [block.text for block in ordered[1:] if _norm(alias) not in _norm(block.text)]
                value = " ".join(value_blocks).strip(" :|-")
            if _is_document_total_label(full_text):
                return None, None
            return field, value or None
    return None, None


def _value_after_label(text: str, alias: str) -> str:
    pattern = re.compile(rf"{re.escape(alias)}\s*[:\-|]?\s*(.+)$", flags=re.IGNORECASE)
    match = pattern.search(strip_accents(text))
    if match:
        return match.group(1).strip()
    pieces = re.split(r"[:|]", text, maxsplit=1)
    return pieces[1].strip() if len(pieces) == 2 else ""


def _record_has_item_evidence(record: dict[str, Any]) -> bool:
    values = record.get("values") or {}
    if not values.get("description") or _is_footer_text(str(values.get("description")), row_like=True):
        return False
    return _record_numeric_count(record) >= 1


def _record_numeric_count(record: dict[str, Any]) -> int:
    values = record.get("values") or {}
    return sum(1 for key in ("quantity", "unit_price", "line_total_ttc", "discount", "tax_rate") if _parse_number(str(values.get(key) or "")) is not None)


def _looks_like_unlabeled_kv_continuation(row: list[OCRLine]) -> bool:
    text = " ".join(block.text for block in row)
    return not _is_footer_text(text, row_like=True) and (sum(char.isalpha() for char in text) >= 3 or _parse_number(text) is not None)


def _assign_unlabeled_continuation(record: dict[str, Any], row: list[OCRLine]) -> None:
    text = " ".join(block.text for block in row).strip()
    values = record.setdefault("values", {})
    if _parse_number(text) is not None:
        for key in ("quantity", "unit_price", "line_total_ttc"):
            if key not in values:
                values[key] = text
                break
    elif "description" in values:
        values["description"] = f"{values['description']} {text}".strip()
    else:
        values["description"] = text
    record.setdefault("blocks", []).extend(row)


def _build_repeated_vertical_records(physical_rows: list[list[OCRLine]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    current: list[list[OCRLine]] = []
    previous_y: float | None = None
    gaps: list[float] = []
    for row in physical_rows:
        text = " ".join(block.text for block in row)
        bbox = _row_bbox(row)
        if not bbox or _is_footer_text(text, row_like=True):
            if current:
                candidates.append(_record_from_vertical_group(current))
                current = []
            continue
        is_desc_start = _looks_like_description_start(text) and _row_numeric_count(row) <= 1
        if current and (is_desc_start or (previous_y is not None and bbox["y1"] - previous_y > 44)):
            candidates.append(_record_from_vertical_group(current))
            current = []
        current.append(row)
        if previous_y is not None:
            gaps.append(max(0.0, bbox["y1"] - previous_y))
        previous_y = bbox["y2"]
    if current:
        candidates.append(_record_from_vertical_group(current))
    records = [record for record in candidates if _record_has_item_evidence(record)]
    label_hits = 0
    for row in physical_rows:
        field, _value = _extract_labeled_value(row)
        if field:
            label_hits += 1
    x_ranges = [(_merge_boxes([block.bbox for block in record.get("blocks", [])]) or {}).get("x1") for record in records]
    aligned = len([x for x in x_ranges if x is not None]) >= 2 and (max(x_ranges) - min(x_ranges) <= 90 if all(x is not None for x in x_ranges) else False)
    return records, {
        "description_anchor_count": sum(1 for record in records if record["values"].get("description")),
        "numeric_anchor_count": sum(_record_numeric_count(record) for record in records),
        "label_hits": label_hits,
        "repeated_spacing": bool(gaps and len(gaps) >= 2 and max(gaps) - min(gaps) <= 35),
        "similar_x_ranges": aligned,
    }


def _record_from_vertical_group(group: list[list[OCRLine]]) -> dict[str, Any]:
    blocks = [block for row in group for block in row]
    values: dict[str, Any] = {}
    for row in group:
        field, value = _extract_labeled_value(row)
        if field and value:
            values[field] = value
            continue
        text = " ".join(block.text for block in row)
        if "description" not in values and _looks_like_description_start(text):
            values["description"] = text
        elif _parse_number(text) is not None:
            for key in ("quantity", "unit_price", "line_total_ttc"):
                if key not in values:
                    values[key] = text
                    break
    return {"values": values, "blocks": blocks}


def _build_numeric_anchor_rows(positioned: list[OCRLine]) -> tuple[list[ReconstructedRow], dict[str, Any], list[RowFragment]]:
    physical_rows = _group_physical_rows(positioned, tolerance=16.0)
    rows: list[ReconstructedRow] = []
    unresolved: list[RowFragment] = []
    used_descriptions: set[int] = set()
    description_candidates = [(idx, row) for idx, row in enumerate(physical_rows) if _description_text_loose(row) and not _is_footer_row(row)]
    numeric_anchor_count = 0
    for idx, row in enumerate(physical_rows):
        if _is_footer_row(row):
            continue
        numbers = _row_numbers(row)
        if len(numbers) < 2:
            continue
        numeric_anchor_count += 1
        desc_row_idx, desc_row = _nearest_description_row(idx, row, description_candidates, used_descriptions)
        group = [desc_row] if desc_row is not row else []
        group.append(row)
        used_descriptions.add(desc_row_idx)
        values = _values_from_numeric_row(group)
        rec = _row_from_values(f"na_{len(rows) + 1}", values, [block for part in group for block in part], "numeric-anchor scored assignment")
        if rec:
            if "NUMERIC_ANCHOR_ASSIGNMENT" not in rec.warning_codes:
                rec.warning_codes.append("NUMERIC_ANCHOR_ASSIGNMENT")
            rec.validation_status = "needs_review" if rec.validation_status == "validated" else rec.validation_status
            rec.confidence = min(rec.confidence, 0.62)
            rows.append(rec)
    for desc_idx, desc_row in description_candidates:
        if desc_idx not in used_descriptions:
            unresolved.append(RowFragment(_join_blocks(desc_row), _row_bbox(desc_row), {}, _avg_y(desc_row), None, 0.0, 0.0))
    return rows, {
        "numeric_anchor_count": numeric_anchor_count,
        "description_anchor_count": len(description_candidates),
        "under_merge_detected": bool(unresolved),
        "over_merge_detected": any(len(_row_numbers(row)) >= 5 for row in physical_rows),
    }, unresolved


def _build_single_item_summary(positioned: list[OCRLine]) -> tuple[list[ReconstructedRow], dict[str, Any]]:
    physical_rows = _group_physical_rows(positioned, tolerance=14.0)
    overmerged: list[ReconstructedRow] = []
    for row in physical_rows:
        text = " ".join(block.text for block in row)
        if len(re.findall(r"\b0?\d{1,3}\s+[A-Za-z]", text)) >= 2:
            for index, chunk in enumerate(_split_row_anchor_chunks(text), start=1):
                values = _values_from_text(chunk)
                split = _row_from_values(f"single_split_{index}", values, row, "single-line over-merged summary split")
                if split:
                    split.warning_codes.append("OVER_MERGE_SPLIT")
                    overmerged.append(split)
    if overmerged:
        return overmerged, {"candidate_rows": len(overmerged), "over_merge_detected": True}
    item_rows = [row for row in physical_rows if not _is_footer_row(row) and _description_text_loose(row) and len(_row_numbers(row)) >= 2]
    if len(item_rows) != 1:
        return [], {"candidate_rows": len(item_rows)}
    values = _values_from_numeric_row([item_rows[0]])
    row = _row_from_values("single_1", values, item_rows[0], "single item summary")
    return ([row] if row else []), {"candidate_rows": len(item_rows)}


def _result_from_rows(rows: list[ReconstructedRow], positioned: list[OCRLine], method: str, *, evidence: dict[str, Any], unresolved: list[RowFragment]) -> TableReconstructionResult:
    rows = _split_overmerged_rows(rows)
    items = [item for item in (_row_to_line_item(row) for row in rows if not row.excluded) if item is not None]
    region_bbox = _merge_bbox_dicts([row.bbox for row in rows])
    region = TableRegion(
        page=positioned[0].page_number if positioned else 1,
        bbox=region_bbox,
        confidence=0.58 if rows else 0.0,
        detection_method=method,
        body_bbox=region_bbox,
        diagnostics=evidence,
    ) if rows else None
    diagnostics = {
        "engine": "p3_1_adaptive_table_reconstruction",
        "header_candidate_found": False,
        "header_confirmed": False,
        "table_region_detected": bool(region_bbox and rows),
        "table_body_detected": bool(rows),
        "row_anchor_detected": bool(rows),
        "rows_reconstructed": bool(rows),
        "candidate_row_count": len(rows),
        "reconstructed_row_count": len(rows),
        "validated_row_count": sum(row.validation_status == "validated" for row in rows),
        "review_row_count": sum(row.validation_status == "needs_review" for row in rows),
        "invalid_row_count": sum(row.validation_status == "invalid" for row in rows),
        "unresolved_fragment_count": len(unresolved),
        "numeric_anchors": evidence.get("numeric_anchor_count", 0),
        "description_anchors": evidence.get("description_anchor_count", 0),
        "over_merge_indicators": evidence.get("over_merge_detected", False),
        "under_merge_indicators": evidence.get("under_merge_detected", False),
        "row_hypotheses": [asdict(row) for row in rows],
        "selected_hypothesis": method,
        "rejected_hypotheses": [],
        "unresolved_fragments": [asdict(fragment) for fragment in unresolved],
        "validation_rejection_reasons": [code for row in rows for code in row.warning_codes],
    }
    return TableReconstructionResult(
        line_items=items,
        regions=[region] if region else [],
        headers=[],
        columns=[],
        cells=[],
        row_anchors=[],
        fragments=[fragment for row in rows for fragment in row.fragments],
        rows=rows,
        excluded_rows=[],
        unresolved_fragments=unresolved,
        reconciliation=_reconcile_rows(items),
        diagnostics=diagnostics,
        selected_strategy=method,
    )


def _row_from_values(row_id: str, values: dict[str, Any], blocks: list[OCRLine], reason: str) -> ReconstructedRow | None:
    if _is_document_total_label(" ".join(block.text for block in blocks)):
        return None
    description = _clean_description(str(values.get("description") or ""))
    if not description and blocks:
        description = _description_text_loose(blocks)
    if not description:
        return None
    quantity = _parse_number(str(values.get("quantity") or ""))
    unit_price = _parse_number(str(values.get("unit_price") or ""))
    discount = _parse_number(str(values.get("discount") or ""))
    tax_rate = _parse_number(str(values.get("tax_rate") or ""))
    total = _parse_number(str(values.get("line_total_ttc") or values.get("total") or ""))
    nums = [_parse_number(block.text) for block in blocks]
    nums = [num for num in nums if num is not None]
    if total is None and nums:
        total = nums[-1]
    if quantity is None and len(nums) >= 3:
        quantity = nums[-3]
    if unit_price is None and len(nums) >= 2:
        unit_price = nums[-2]
    if unit_price is None and quantity and total is not None:
        unit_price = round(total / quantity, 3)
    line_total_ht = total
    warnings: list[str] = []
    if quantity is None:
        warnings.append("QUANTITY_MISSING")
    if unit_price is None:
        warnings.append("UNIT_PRICE_MISSING")
    if total is None:
        warnings.append("LINE_TOTAL_MISSING")
    if quantity is not None and unit_price is not None and total is not None:
        expected = round(quantity * unit_price - (discount or 0), 3)
        if abs(expected - total) > max(0.05, abs(total) * 0.02):
            warnings.append("ROW_TOTAL_MISMATCH")
    status = "validated"
    if "ROW_TOTAL_MISMATCH" in warnings:
        status = "invalid"
    elif warnings:
        status = "needs_review"
    bbox = _merge_boxes([block.bbox for block in blocks if block.bbox])
    fragment = RowFragment(" ".join(block.text for block in blocks), bbox, {}, _avg_y(blocks) if blocks else 0.0, row_id, 0.0, round(len(nums) / max(1, len(blocks)), 3))
    return ReconstructedRow(
        row_id=row_id,
        fragments=[fragment],
        description=description,
        reference=_find_reference(blocks),
        quantity=quantity,
        unit=str(values.get("unit") or "") or _extract_unit(" ".join(block.text for block in blocks)),
        unit_price=unit_price,
        discount=discount,
        tax_rate=tax_rate if tax_rate is not None and 0 <= tax_rate <= 100 else None,
        line_total_ht=line_total_ht,
        line_total_ttc=total,
        confidence=0.86 if status == "validated" else (0.6 if status == "needs_review" else 0.32),
        reconstruction_reason=reason if not warnings else f"{reason}; {'; '.join(warnings)}",
        validation_status=status,
        warning_codes=warnings,
        bbox=bbox,
        cell_bboxes={"row": bbox} if bbox else {},
    )


def _split_overmerged_rows(rows: list[ReconstructedRow]) -> list[ReconstructedRow]:
    output: list[ReconstructedRow] = []
    for row in rows:
        if "OVER_MERGE_SPLIT" in row.warning_codes:
            output.append(row)
            continue
        text = " ".join(fragment.text for fragment in row.fragments)
        meaningful = _split_row_anchor_chunks(text)
        if len(meaningful) <= 1:
            output.append(row)
            continue
        for index, chunk in enumerate(meaningful, start=1):
            values = _values_from_text(chunk)
            split = _row_from_values(f"{row.row_id}_split_{index}", values, [], "over-merged row split by repeated row anchor")
            if split:
                split.bbox = row.bbox
                split.warning_codes.append("OVER_MERGE_SPLIT")
                output.append(split)
    return output


def _split_row_anchor_chunks(text: str) -> list[str]:
    if re.search(r"\b(?:description|quantity|unit price|amount|prix|quantite)\s*:", text, flags=re.IGNORECASE):
        return []
    chunks = re.split(r"(?=\b0?\d{1,3}\s+[A-Za-z])", text)
    meaningful = []
    for chunk in chunks:
        stripped = chunk.strip()
        if len(stripped) < 8 or len(re.findall(r"\d+(?:[,.]\d+)?", stripped)) < 2:
            continue
        values = _values_from_text(stripped)
        if values.get("description"):
            meaningful.append(stripped)
    return meaningful


def _values_from_numeric_row(group: list[list[OCRLine]]) -> dict[str, Any]:
    blocks = [block for row in group for block in row]
    text = " ".join(block.text for block in blocks)
    values = _values_from_text(text)
    desc = _description_text_loose(blocks)
    if desc:
        values["description"] = desc
    return values


def _values_from_text(text: str) -> dict[str, Any]:
    text = re.sub(r"^\s*0?\d{1,3}\s+", "", text)
    numbers = [_parse_number(raw) for raw in re.findall(r"[-+]?\d[\d\s]*(?:[,.]\d+)?%?", text)]
    numbers = [value for value in numbers if value is not None]
    first_number = re.search(r"[-+]?\d", text)
    description = text[: first_number.start()] if first_number else text
    description = _clean_description(description)
    values: dict[str, Any] = {"description": description}
    if len(numbers) >= 3:
        values.update({"quantity": str(numbers[-3]), "unit_price": str(numbers[-2]), "line_total_ttc": str(numbers[-1])})
    elif len(numbers) == 2:
        values.update({"unit_price": str(numbers[-2]), "line_total_ttc": str(numbers[-1])})
    elif len(numbers) == 1:
        values.update({"line_total_ttc": str(numbers[-1])})
    return values


def _nearest_description_row(idx: int, row: list[OCRLine], description_candidates: list[tuple[int, list[OCRLine]]], used: set[int]) -> tuple[int, list[OCRLine]]:
    available = [(desc_idx, desc_row) for desc_idx, desc_row in description_candidates if desc_idx not in used and abs(desc_idx - idx) <= 2]
    if not available:
        return idx, row
    return min(available, key=lambda item: (abs(item[0] - idx), abs((_row_bbox(item[1]) or {}).get("x1", 0) - (_row_bbox(row) or {}).get("x1", 0))))


def _description_text_loose(row_or_blocks: list[Any]) -> str:
    blocks = [block for item in row_or_blocks for block in (item if isinstance(item, list) else [item])]
    parts = []
    for block in sorted(blocks, key=lambda item: (item.bbox.y1, item.bbox.x1)):
        text = block.text.strip()
        if _parse_number(text) is not None and sum(char.isalpha() for char in text) < 3:
            continue
        if _is_footer_text(text, row_like=True):
            continue
        parts.append(text)
    return _clean_description(" ".join(parts))


def _looks_like_description_start(text: str) -> bool:
    plain = _norm(text)
    if _is_footer_text(text, row_like=True) or _is_document_total_label(text):
        return False
    if any(alias in plain for alias in ("invoice", "facture", "date", "customer", "client", "supplier", "adresse", "address", "iban", "swift", "vat", "tva", "tax")):
        return False
    return sum(char.isalpha() for char in text) >= 4


def _row_numbers(row: list[OCRLine]) -> list[float]:
    values = []
    for block in row:
        value = _parse_number(block.text)
        if value is not None:
            values.append(value)
    return values


def _row_numeric_count(row: list[OCRLine]) -> int:
    return len(_row_numbers(row))


def _is_document_total_label(text: str) -> bool:
    plain = _norm(text)
    if re.search(r"\b(?:vat|tva|tax)\b", plain) and re.search(r"\d", plain):
        return True
    return any(term in plain for term in ("subtotal", "sub total", "sous total", "sous total ht", "total due", "grand total", "total ttc", "amount due", "montant ttc", "sales tax", "tax summary", "vat summary", "tva 19", "shipping and handling", "iban", "rib", "swift"))


def _group_physical_rows(blocks: list[OCRLine], tolerance: float = 11.0) -> list[list[OCRLine]]:
    rows: list[list[OCRLine]] = []
    for block in sorted(blocks, key=lambda item: (item.page_number, _center_y(item.bbox), item.bbox.x1)):
        center_y = _center_y(block.bbox)
        target = None
        for row in rows:
            if row[0].page_number == block.page_number and abs(_avg_y(row) - center_y) <= tolerance:
                target = row
                break
        if target is None:
            rows.append([block])
        else:
            target.append(block)
    return [sorted(row, key=lambda item: item.bbox.x1) for row in rows]


def _detect_headers(rows: list[list[OCRLine]]) -> list[TableHeader]:
    headers: list[TableHeader] = []
    for index, row in enumerate(rows):
        candidates = [row]
        if index + 1 < len(rows) and _vertical_gap(row, rows[index + 1]) <= 22:
            candidates.append(row + rows[index + 1])
        for blocks in candidates:
            merged_blocks = _merge_header_blocks(blocks)
            aliases = _matched_aliases(" ".join(block.text for block in merged_blocks))
            if len(aliases) < 2:
                continue
            bbox = _merge_boxes([block.bbox for block in merged_blocks])
            confs = [block.confidence for block in merged_blocks if block.confidence is not None]
            headers.append(TableHeader(
                page=merged_blocks[0].page_number,
                text=" ".join(block.text for block in merged_blocks),
                bbox=bbox,
                confidence=round(sum(confs) / len(confs), 3) if confs else 0.7,
                source_line_ids=[block.line_index for block in merged_blocks if block.line_index is not None],
                aliases_matched=aliases,
            ))
            break
    return sorted(headers, key=lambda item: (item.page, item.bbox["y1"] if item.bbox else 0))


def _merge_header_blocks(blocks: list[OCRLine]) -> list[OCRLine]:
    ordered = sorted(blocks, key=lambda item: (item.bbox.y1, item.bbox.x1))
    merged: list[OCRLine] = []
    skip: set[int] = set()
    for index, block in enumerate(ordered):
        if index in skip:
            continue
        text = block.text
        box = block.bbox
        for next_index in range(index + 1, min(index + 3, len(ordered))):
            other = ordered[next_index]
            if other.page_number != block.page_number:
                continue
            close_x = 0 <= other.bbox.x1 - box.x2 <= 45
            close_y = abs(_center_y(other.bbox) - _center_y(box)) <= 18
            combined = f"{text} {other.text}"
            merged_semantic, _merged_aliases = _classify_header_text(combined)
            current_semantic, _ = _classify_header_text(text)
            other_semantic, _ = _classify_header_text(other.text)
            split_unit_price = current_semantic == "unit" and other_semantic == "unit_price" and merged_semantic == "unit_price"
            split_total = current_semantic in {"line_total_ht", "line_total_ttc"} and other_semantic in {"line_total_ht", "line_total_ttc"} and merged_semantic in {"line_total_ht", "line_total_ttc"}
            unclassified_piece = not current_semantic or not other_semantic
            if close_x and close_y and merged_semantic and (split_unit_price or split_total or unclassified_piece):
                text = combined
                box = BoundingBox(x1=min(box.x1, other.bbox.x1), y1=min(box.y1, other.bbox.y1), x2=max(box.x2, other.bbox.x2), y2=max(box.y2, other.bbox.y2))
                skip.add(next_index)
        merged.append(block.model_copy(update={"text": text, "bbox": box}))
    return merged


def _infer_columns_from_header(header: TableHeader, rows: list[list[OCRLine]]) -> list[TableColumn]:
    header_blocks = []
    for row in rows:
        bbox = _row_bbox(row)
        if not bbox or not header.bbox:
            continue
        if header.bbox["y1"] - 2 <= bbox["y1"] <= header.bbox["y2"] + 24:
            header_blocks.extend(row)
    header_blocks = _merge_header_blocks(header_blocks)
    columns: list[TableColumn] = []
    used_types: set[str] = set()
    for block in header_blocks:
        if len(_matched_aliases(block.text)) > 2:
            continue
        semantic, aliases = _classify_header_text(block.text)
        if not semantic or semantic in used_types:
            continue
        used_types.add(semantic)
        columns.append(TableColumn(semantic, block.bbox.x1, block.bbox.x2, _center_x(block.bbox), 0.86, block.text, aliases))
    if len(columns) < 3 and header.bbox:
        columns.extend(_fallback_columns_from_header_text(header, used_types))
    return _refine_columns_from_body(_finalize_columns(columns), rows, header)


def _fallback_columns_from_header_text(header: TableHeader, used: set[str]) -> list[TableColumn]:
    text = _norm(header.text)
    width = max(1.0, header.bbox["x2"] - header.bbox["x1"])
    output = []
    for semantic, aliases in ALIASES.items():
        if semantic in used:
            continue
        best = _best_alias_match(text, aliases)
        if not best:
            continue
        alias, start = best
        center = header.bbox["x1"] + ((start + len(alias) / 2) / max(len(text), 1)) * width
        output.append(TableColumn(semantic, center - 20, center + 20, center, 0.58, alias, [alias]))
    return output


def _infer_columns_from_body(rows: list[list[OCRLine]]) -> list[TableColumn]:
    numeric_centers: list[float] = []
    for row in rows:
        if _is_footer_row(row) or _is_header_row(row):
            continue
        for block in row:
            if _parse_number(block.text) is not None:
                numeric_centers.append(_center_x(block.bbox))
    clusters = _cluster_values(numeric_centers, tolerance=38)
    centers = [sum(cluster) / len(cluster) for cluster in clusters if len(cluster) >= 2]
    if len(centers) < 2:
        return []
    centers = sorted(centers)
    keys = ["quantity", "unit_price", "tax_rate", "line_total_ttc"] if len(centers) >= 4 else (["quantity", "unit_price", "line_total_ttc"] if len(centers) >= 3 else ["unit_price", "line_total_ttc"])
    columns = [TableColumn("description", 0, centers[0] - 30, (centers[0] - 30) / 2, 0.42, "inferred description", [])]
    for key, center in zip(keys, centers[-len(keys):]):
        columns.append(TableColumn(key, center - 20, center + 20, center, 0.42, f"inferred {key}", []))
    return _finalize_columns(columns)


def _refine_columns_from_body(columns: list[TableColumn], rows: list[list[OCRLine]], header: TableHeader) -> list[TableColumn]:
    if not columns or not header.bbox:
        return columns
    numeric_columns = [column for column in columns if column.semantic_type in {"quantity", "unit_price", "discount", "tax_rate", "line_total_ht", "line_total_ttc"}]
    if len(numeric_columns) < 2:
        return columns
    body_centers: list[float] = []
    percent_centers: list[float] = []
    for row in rows:
        bbox = _row_bbox(row)
        if not bbox or bbox["y1"] <= header.bbox["y2"]:
            continue
        if _is_footer_row(row) or _is_header_row(row):
            continue
        for block in row:
            if _is_row_number(block, columns):
                continue
            if _parse_number(block.text) is not None:
                body_centers.append(_center_x(block.bbox))
                if "%" in block.text:
                    percent_centers.append(_center_x(block.bbox))
    clusters = _cluster_values(body_centers, tolerance=34)
    centers = [sum(cluster) / len(cluster) for cluster in clusters if len(cluster) >= 1]
    if len(centers) < len(numeric_columns):
        return columns
    centers = sorted(centers)
    refined = []
    used_centers: set[int] = set()
    for column in columns:
        if column.semantic_type in {item.semantic_type for item in numeric_columns}:
            candidate_centers = centers
            if column.semantic_type == "tax_rate" and percent_centers:
                candidate_centers = [sum(cluster) / len(cluster) for cluster in _cluster_values(percent_centers, tolerance=34)]
            elif column.semantic_type == "tax_rate" and not percent_centers:
                refined.append(column)
                continue
            indexed = sorted(enumerate(candidate_centers), key=lambda item: (abs(item[1] - column.center_x), item[0]))
            center_index, center = next(((idx, value) for idx, value in indexed if idx not in used_centers), indexed[0])
            if candidate_centers is centers:
                used_centers.add(center_index)
            refined.append(TableColumn(column.semantic_type, center - 20, center + 20, center, max(column.confidence, 0.72), column.header_text, column.aliases_matched))
        else:
            refined.append(column)
    return _finalize_columns(refined)


def _finalize_columns(columns: list[TableColumn]) -> list[TableColumn]:
    priority = {"description": 0, "reference": 1, "quantity": 2, "unit": 3, "unit_price": 4, "discount": 5, "tax_rate": 6, "line_total_ht": 7, "line_total_ttc": 8}
    dedup: dict[str, TableColumn] = {}
    for column in sorted(columns, key=lambda item: (-item.confidence, priority.get(item.semantic_type, 99))):
        dedup.setdefault(column.semantic_type, column)
    ordered = sorted(dedup.values(), key=lambda item: item.center_x)
    for index, column in enumerate(ordered):
        left = (ordered[index - 1].center_x + column.center_x) / 2 if index else 0.0
        right = (column.center_x + ordered[index + 1].center_x) / 2 if index + 1 < len(ordered) else 1_000_000.0
        column.x_min = min(column.x_min, left)
        column.x_max = max(column.x_max, right)
    return ordered


def _assign_cells(rows: list[list[OCRLine]], columns: list[TableColumn]) -> list[TableCell]:
    cells: list[TableCell] = []
    for row in rows:
        for block in row:
            semantic, confidence, reason = _assign_column(block, columns)
            cells.append(TableCell(block.text, _bbox_dict(block.bbox), semantic, [block.line_index] if block.line_index is not None else [], [block.line_index] if block.line_index is not None else [], confidence, reason))
    return cells


def _build_logical_rows(rows: list[list[OCRLine]], columns: list[TableColumn]) -> tuple[list[list[list[OCRLine]]], list[RowAnchor], list[RowFragment], list[RowFragment]]:
    groups: list[list[list[OCRLine]]] = []
    anchors: list[RowAnchor] = []
    fragments: list[RowFragment] = []
    unresolved: list[RowFragment] = []
    pending_prefix: list[list[OCRLine]] = []
    for row in rows:
        if _is_footer_row(row):
            break
        fragment = _fragment_from_blocks(row, columns, None)
        if _is_header_row(row):
            continue
        numeric_count = sum(1 for block in row if _parse_number(block.text) is not None)
        desc_text = _description_text(row, columns)
        has_anchor = _has_row_number(row, columns) or (numeric_count >= 2 and bool(desc_text))
        desc_only = bool(desc_text) and numeric_count <= 1 and not _is_footer_row(row)
        if has_anchor:
            row_id = f"row_{len(groups) + 1}"
            group = pending_prefix + [row]
            pending_prefix = []
            groups.append(group)
            bbox = _merge_bbox_dicts([_row_bbox(part) for part in group])
            anchor_type = "row_number" if _has_row_number(row, columns) else "numeric_cells"
            anchors.append(RowAnchor(row_id, row[0].page_number, _avg_y(row), bbox, anchor_type, 0.86 if anchor_type == "row_number" else 0.74))
            fragments.append(_fragment_from_blocks(row, columns, row_id))
            continue
        if desc_only:
            if groups and _vertical_gap(groups[-1][-1], row) <= 26:
                groups[-1].append(row)
                row_id = f"row_{len(groups)}"
                fragments.append(_fragment_from_blocks(row, columns, row_id, continuation_score=0.82))
            else:
                pending_prefix.append(row)
                fragments.append(fragment)
            continue
        if numeric_count and groups and _vertical_gap(groups[-1][-1], row) <= 28:
            groups[-1].append(row)
            fragments.append(_fragment_from_blocks(row, columns, f"row_{len(groups)}", continuation_score=0.62))
        else:
            unresolved.append(fragment)
    unresolved.extend(_fragment_from_blocks(row, columns, None) for row in pending_prefix)
    return groups, anchors, fragments, unresolved


def _parse_rows(groups: list[list[list[OCRLine]]], columns: list[TableColumn]) -> tuple[list[ReconstructedRow], list[ReconstructedRow]]:
    rows: list[ReconstructedRow] = []
    excluded: list[ReconstructedRow] = []
    for index, group in enumerate(groups, start=1):
        blocks = [block for row in group for block in row]
        text = " ".join(block.text.strip() for block in blocks)
        row = _parse_row_blocks(f"row_{index}", group, columns)
        if _is_footer_text(text, row_like=True):
            row.excluded = True
            row.exclusion_reason = "summary/footer row excluded"
            row.warning_codes.append("SUBTOTAL_ROW_EXCLUDED")
            excluded.append(row)
            continue
        rows.append(row)
    return rows, excluded


def _parse_row_blocks(row_id: str, group: list[list[OCRLine]], columns: list[TableColumn]) -> ReconstructedRow:
    blocks = [block for row in group for block in row]
    cell_blocks: dict[str, list[OCRLine]] = {}
    for block in blocks:
        semantic, _confidence, _reason = _assign_column(block, columns)
        if _is_row_number(block, columns):
            continue
        if semantic:
            cell_blocks.setdefault(semantic, []).append(block)
    description = _clean_description(_join_blocks(cell_blocks.get("description", [])))
    recovered_description = _recover_description_from_left_blocks(blocks, columns)
    if len(recovered_description) > len(description):
        description = recovered_description
    reference = _find_reference(blocks) or _clean_description(_join_blocks(cell_blocks.get("reference", []))) or None
    if reference and description:
        description = _clean_description(re.sub(rf"\b{re.escape(reference)}\b", " ", description, flags=re.IGNORECASE))
    unit = _clean_description(_join_blocks(cell_blocks.get("unit", []))) or _extract_unit(" ".join(block.text for block in blocks))
    quantity = _parse_column_number(cell_blocks, "quantity")
    unit_price = _parse_column_number(cell_blocks, "unit_price")
    discount = _parse_column_number(cell_blocks, "discount")
    tax_rate = _parse_column_number(cell_blocks, "tax_rate")
    line_total_ht = _parse_column_number(cell_blocks, "line_total_ht")
    line_total_ttc = _parse_column_number(cell_blocks, "line_total_ttc")
    if line_total_ttc is None:
        line_total_ttc = _parse_column_number(cell_blocks, "total")
    if line_total_ht is None and quantity is not None and unit_price is not None:
        line_total_ht = round(quantity * unit_price - (discount or 0), 3)
    if line_total_ttc is None and line_total_ht is not None:
        line_total_ttc = line_total_ht
    if unit_price is None and quantity and line_total_ht is not None:
        unit_price = round((line_total_ht + (discount or 0)) / quantity, 3)
    bbox = _merge_bbox_dicts([_row_bbox(row) for row in group])
    warnings = []
    if not description:
        warnings.append("DESCRIPTION_MISSING")
    if quantity is None:
        warnings.append("QUANTITY_MISSING")
    if unit_price is None:
        warnings.append("UNIT_PRICE_MISSING")
    if line_total_ht is None and line_total_ttc is None:
        warnings.append("LINE_TOTAL_MISSING")
    expected = round(quantity * unit_price - (discount or 0), 3) if quantity is not None and unit_price is not None else None
    total_for_check = line_total_ht if line_total_ht is not None else line_total_ttc
    if expected is not None and total_for_check is not None and abs(expected - total_for_check) > max(0.05, abs(total_for_check) * 0.015):
        warnings.append("ROW_TOTAL_MISMATCH")
    if len(group) > 1:
        warnings.append("WRAPPED_DESCRIPTION_MERGED")
    blocking_warnings = {
        "DESCRIPTION_MISSING",
        "QUANTITY_MISSING",
        "UNIT_PRICE_MISSING",
        "LINE_TOTAL_MISSING",
        "AMBIGUOUS_COLUMN_ASSIGNMENT",
    }
    status = "validated"
    if "ROW_TOTAL_MISMATCH" in warnings:
        status = "invalid"
    elif any(warning in blocking_warnings for warning in warnings):
        status = "needs_review"
    confidence = 0.9 if status == "validated" else (0.62 if status == "needs_review" else 0.38)
    fragments = [_fragment_from_blocks(row, columns, row_id, continuation_score=0.7 if idx else 0.0) for idx, row in enumerate(group)]
    return ReconstructedRow(
        row_id=row_id,
        fragments=fragments,
        description=description or None,
        reference=reference,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        discount=discount,
        tax_rate=tax_rate if tax_rate is not None and 0 <= tax_rate <= 100 else None,
        line_total_ht=line_total_ht,
        line_total_ttc=line_total_ttc,
        confidence=confidence,
        reconstruction_reason="; ".join(warnings) if warnings else "column-complete row",
        validation_status=status,
        warning_codes=warnings,
        bbox=bbox,
        cell_bboxes={key: _merge_bbox_dicts([_bbox_dict(block.bbox) for block in value]) for key, value in cell_blocks.items()},
    )


def _row_to_line_item(row: ReconstructedRow) -> LineItem | None:
    if not row.description and row.line_total_ttc is None:
        return None
    total = row.line_total_ttc if row.line_total_ttc is not None else row.line_total_ht
    return LineItem(
        reference=row.reference,
        description=row.description,
        quantity=row.quantity,
        unit=row.unit,
        unit_price=row.unit_price,
        discount=row.discount,
        line_total_ht=row.line_total_ht,
        tax_rate=row.tax_rate,
        line_total_ttc=total,
        total=total,
        confidence=row.confidence,
        bbox=row.bbox,
        page=1,
        source="p3 reconstructed table review" if row.validation_status != "validated" else "p3 reconstructed table",
    )


def _reconcile_rows(items: list[LineItem]) -> dict[str, Any]:
    totals = [item.line_total_ht if item.line_total_ht is not None else item.total for item in items]
    totals = [float(value) for value in totals if value is not None]
    return {
        "line_sum": round(sum(totals), 3) if totals else None,
        "document_subtotal": None,
        "difference": None,
        "tolerance": None,
        "reconciliation_status": "not_compared" if totals else "no_line_totals",
        "explanation": "Table rows reconstructed; document-level total comparison is handled by financial reasoning.",
    }


def _detect_stop_y(rows: list[list[OCRLine]], header_y: float) -> tuple[float, dict[str, float] | None]:
    for row in rows:
        bbox = _row_bbox(row)
        if bbox and bbox["y1"] > header_y and _is_footer_row(row):
            return bbox["y1"], bbox
    bottom = max((bbox["y2"] for row in rows if (bbox := _row_bbox(row))), default=header_y + 20)
    return bottom + 20, None


def _assign_column(block: OCRLine, columns: list[TableColumn]) -> tuple[str | None, float, str]:
    center = _center_x(block.bbox)
    containing = [column for column in columns if column.x_min <= center < column.x_max]
    if containing:
        column = min(containing, key=lambda item: abs(center - item.center_x))
        return column.semantic_type, max(0.45, 1 - abs(center - column.center_x) / max(1, column.x_max - column.x_min)), "inside column boundary"
    nearest = min(columns, key=lambda item: abs(center - item.center_x), default=None)
    if nearest and abs(center - nearest.center_x) <= 40:
        return nearest.semantic_type, 0.42, "nearest column"
    return None, 0.0, "unassigned"


def _classify_header_text(text: str) -> tuple[str | None, list[str]]:
    norm = _norm(text)
    matches = []
    for semantic, aliases in ALIASES.items():
        best = _best_alias_match(norm, aliases)
        if best:
            alias, pos = best
            matches.append((semantic, alias, pos, len(alias)))
    if not matches:
        return None, []
    matches.sort(key=lambda item: (-item[3], item[2], _semantic_priority(item[0])))
    return matches[0][0], [matches[0][1]]


def _matched_aliases(text: str) -> dict[str, list[str]]:
    norm = _norm(text)
    found: dict[str, list[str]] = {}
    for semantic, aliases in ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if re.search(rf"(?<!\w){re.escape(_norm(alias))}(?!\w)", norm):
                found.setdefault(semantic, []).append(alias)
                break
    if "unit_price" in found and "unit" in found:
        found.pop("unit", None)
    return found


def _best_alias_match(norm: str, aliases: tuple[str, ...]) -> tuple[str, int] | None:
    hits = []
    for alias in sorted(aliases, key=len, reverse=True):
        normalized = _norm(alias)
        match = re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", norm)
        if match:
            hits.append((normalized, match.start()))
    return hits[0] if hits else None


def _semantic_priority(semantic: str) -> int:
    return {"unit_price": 0, "line_total_ht": 1, "line_total_ttc": 2, "description": 3, "reference": 4, "quantity": 5, "unit": 6, "discount": 7, "tax_rate": 8}.get(semantic, 99)


def _fragment_from_blocks(blocks: list[OCRLine], columns: list[TableColumn], row_id: str | None, continuation_score: float = 0.0) -> RowFragment:
    assigned = {}
    nums = 0
    for block in blocks:
        semantic, _confidence, _reason = _assign_column(block, columns)
        if semantic:
            assigned.setdefault(semantic, "")
            assigned[semantic] = (assigned[semantic] + " " + block.text).strip()
        if _parse_number(block.text) is not None:
            nums += 1
    text = " ".join(block.text for block in blocks)
    return RowFragment(text, _row_bbox(blocks), assigned, _avg_y(blocks), row_id, continuation_score, round(nums / max(1, len(blocks)), 3))


def _description_text(row: list[OCRLine], columns: list[TableColumn]) -> str:
    parts = [block.text for block in row if _assign_column(block, columns)[0] in {"description", "reference"} and not _is_row_number(block, columns)]
    return _clean_description(" ".join(parts))


def _clean_description(text: str) -> str:
    text = re.sub(r"^\s*0?\d{1,3}\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" |:-")
    return text if sum(char.isalpha() for char in text) >= 3 else ""


def _recover_description_from_left_blocks(blocks: list[OCRLine], columns: list[TableColumn]) -> str:
    numeric_left = min((column.x_min for column in columns if column.semantic_type in {"quantity", "unit_price", "discount", "tax_rate", "line_total_ht", "line_total_ttc"}), default=1_000_000)
    parts = []
    for block in sorted(blocks, key=lambda item: (item.bbox.y1, item.bbox.x1)):
        if block.bbox.x1 >= numeric_left - 12:
            continue
        if _is_row_number(block, columns):
            continue
        if _parse_number(block.text) is not None and sum(char.isalpha() for char in block.text) < 3:
            continue
        text = block.text.strip()
        if text:
            parts.append(text)
    return _clean_description(" ".join(parts))


def _join_blocks(blocks: list[OCRLine]) -> str:
    return " ".join(block.text.strip() for block in sorted(blocks, key=lambda item: (item.bbox.y1, item.bbox.x1)))


def _parse_column_number(cells: dict[str, list[OCRLine]], key: str) -> float | None:
    text = _join_blocks(cells.get(key, []))
    return _parse_number(text)


def _parse_number(text: str) -> float | None:
    if not text or not re.search(r"\d", text):
        return None
    cleaned = text.replace("$", " ").replace("€", " ").replace("£", " ")
    match = re.findall(r"\(?[-+]?\d[\d\s]*(?:[,.]\d+)?\)?%?", cleaned)
    if not match:
        return None
    value = match[-1].strip()
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("()% ")
    parsed = parse_amount(value)
    return -parsed if negative and parsed is not None else parsed


def _extract_unit(text: str) -> str | None:
    for unit in UNITS:
        match = re.search(rf"\b{re.escape(unit)}\b", text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def _find_reference(blocks: list[OCRLine]) -> str | None:
    for block in blocks:
        match = REFERENCE_RE.search(block.text)
        if match:
            return match.group(0)
    return None


def _is_header_row(row: list[OCRLine]) -> bool:
    return len(_matched_aliases(" ".join(block.text for block in row))) >= 2


def _is_footer_row(row: list[OCRLine]) -> bool:
    return _is_footer_text(" ".join(block.text for block in row), row_like=True)


def _is_footer_text(text: str, *, row_like: bool = False) -> bool:
    plain = _norm(text)
    if not plain:
        return False
    if "delivery service" in plain or "tax software" in plain or "total care" in plain:
        return False
    footer_hit = any(re.search(rf"\b{re.escape(_norm(term))}\b", plain) for term in FOOTER_TERMS)
    if not footer_hit:
        return False
    numeric_count = len(re.findall(r"\d", plain))
    alpha_words = len(re.findall(r"[a-z\u0600-\u06ff]+", plain))
    return not row_like or alpha_words <= 5 or numeric_count >= 2


def _has_row_number(row: list[OCRLine], columns: list[TableColumn]) -> bool:
    return any(_is_row_number(block, columns) for block in row)


def _is_row_number(block: OCRLine, columns: list[TableColumn]) -> bool:
    description = next((column for column in columns if column.semantic_type == "description"), None)
    if not description:
        return False
    text = block.text.strip().rstrip(".")
    return bool(re.fullmatch(r"0?\d{1,3}", text) and block.bbox.x2 < description.center_x)


def _row_bbox(row: list[OCRLine]) -> dict[str, float] | None:
    return _merge_boxes([block.bbox for block in row if block.bbox])


def _bbox_dict(box: BoundingBox | None) -> dict[str, float] | None:
    return box.model_dump(mode="json") if box else None


def _merge_boxes(boxes: list[BoundingBox | None]) -> dict[str, float] | None:
    real = [box for box in boxes if box]
    if not real:
        return None
    return {"x1": min(box.x1 for box in real), "y1": min(box.y1 for box in real), "x2": max(box.x2 for box in real), "y2": max(box.y2 for box in real)}


def _merge_bbox_dicts(boxes: list[dict[str, float] | None]) -> dict[str, float] | None:
    real = [box for box in boxes if box]
    if not real:
        return None
    return {"x1": min(box["x1"] for box in real), "y1": min(box["y1"] for box in real), "x2": max(box["x2"] for box in real), "y2": max(box["y2"] for box in real)}


def _center_x(box: BoundingBox) -> float:
    return (box.x1 + box.x2) / 2


def _center_y(box: BoundingBox) -> float:
    return (box.y1 + box.y2) / 2


def _avg_y(row: list[OCRLine]) -> float:
    return sum(_center_y(block.bbox) for block in row if block.bbox) / max(1, len([block for block in row if block.bbox]))


def _vertical_gap(first: list[OCRLine], second: list[OCRLine]) -> float:
    first_box = _row_bbox(first)
    second_box = _row_bbox(second)
    if not first_box or not second_box:
        return 999.0
    return second_box["y1"] - first_box["y2"]


def _cluster_values(values: list[float], tolerance: float) -> list[list[float]]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - (sum(clusters[-1]) / len(clusters[-1]))) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return clusters


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z\u0600-\u06ff.%]+", " ", strip_accents(str(text)).casefold()).split())
