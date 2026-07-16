from __future__ import annotations

import re
from typing import Any, Callable

from app.core.schemas import Candidate, OCRLine
from app.services.document_graph import DocumentGraph, DocumentNode, build_document_graph
from app.services.semantic_classifier import classify_graph_nodes, is_company_candidate_text, is_forbidden_party_name
from app.utils.helpers import parse_amount, parse_date, strip_accents

DATE_PATTERN = r"\b(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2})\b"
AMOUNT_PATTERN = r"(?<!\d)(?:[$€£]\s*)?[-+]?(?:\d[\d ]*[,.]\d{2,3}|\d{1,3}(?:[ .]\d{3})+|\d+)(?!\d)"
CUSTOMER_LABEL_TYPES = {"customer_label"}
INVOICE_LABEL_TYPES = {"invoice_label"}
DUE_LABEL_TYPES = {"due_date_label"}
TOTAL_LABEL_TYPES = {"total_label"}
SUBTOTAL_LABEL_TYPES = {"subtotal_label"}
TAX_LABEL_TYPES = {"tax_label"}


def add_graph_field_candidates(add: Callable[..., None], blocks: list[OCRLine]) -> dict[str, Any]:
    if not blocks:
        return {"document_graph": {"nodes": [], "edges": []}, "semantic_nodes": [], "field_scores": {}}
    graph = classify_graph_nodes(build_document_graph(blocks))
    field_scores: dict[str, list[dict[str, Any]]] = {}

    for candidate in _supplier_candidates(graph):
        _emit(add, field_scores, "supplier_name", candidate)
    for candidate in _customer_candidates(graph):
        _emit(add, field_scores, "customer_name", candidate)
    for candidate in _invoice_number_candidates(graph):
        _emit(add, field_scores, "invoice_number", candidate)
    for field, candidate in _date_candidates(graph):
        _emit(add, field_scores, field, candidate)
    for field, candidate in _totals_candidates(graph):
        _emit(add, field_scores, field, candidate)

    return {
        "document_graph": graph.to_dict(),
        "semantic_nodes": [node.to_dict() for node in graph.nodes],
        "field_scores": field_scores,
    }


def build_graph_debug(blocks: list[OCRLine]) -> dict[str, Any]:
    if not blocks:
        return {"document_graph": {"nodes": [], "edges": []}, "semantic_nodes": [], "field_scores": {}}
    graph = classify_graph_nodes(build_document_graph(blocks))
    field_scores = {
        "supplier_name": [_score_payload(item) for item in _supplier_candidates(graph)],
        "customer_name": [_score_payload(item) for item in _customer_candidates(graph)],
        "invoice_number": [_score_payload(item) for item in _invoice_number_candidates(graph)],
        "totals": [_score_payload(item) for _field, item in _totals_candidates(graph)],
    }
    return {
        "document_graph": graph.to_dict(),
        "semantic_nodes": [node.to_dict() for node in graph.nodes],
        "field_scores": field_scores,
    }


def _supplier_candidates(graph: DocumentGraph) -> list[dict[str, Any]]:
    candidates = []
    max_y = _max_y(graph)
    labels = [node for node in graph.nodes if node.node_type == "supplier_label"]
    for node in graph.nodes:
        if node.node_type != "company_candidate":
            continue
        if is_forbidden_party_name(node.text):
            continue
        breakdown = _base_breakdown(node)
        breakdown["semantic_score"] += 0.25
        if node.bbox and node.bbox.y1 <= max_y * 0.30:
            breakdown["layout_score"] += 0.25
        if _near_node_types(graph, node, {"address_candidate", "phone", "email"}, max_distance=180):
            breakdown["business_validation_score"] += 0.18
        if _inside_products_or_totals(graph, node):
            breakdown["penalty_score"] += 0.55
        if _near_customer_label(graph, node):
            breakdown["penalty_score"] += 0.35
        if any(edge.distance <= 260 for label in labels for other, edge in graph.neighbors(label) if other.id == node.id):
            breakdown["label_proximity_score"] += 0.20
        score = _final_score(breakdown)
        if score >= 0.48:
            candidates.append(_candidate_dict("supplier_name", node.text, score, "document graph supplier scoring", node, breakdown))
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _customer_candidates(graph: DocumentGraph) -> list[dict[str, Any]]:
    candidates = []
    labels = [node for node in graph.nodes if node.node_type in CUSTOMER_LABEL_TYPES]
    for label in labels:
        for node, distance, relation_bonus in _nearby_value_nodes(graph, label, max_distance=260):
            if node.node_type not in {"company_candidate", "unknown"}:
                continue
            if is_forbidden_party_name(node.text):
                continue
            if not is_company_candidate_text(node.text):
                continue
            breakdown = _base_breakdown(node)
            breakdown["semantic_score"] += 0.24
            breakdown["label_proximity_score"] += relation_bonus
            breakdown["business_validation_score"] += 0.12
            if distance > 180:
                breakdown["penalty_score"] += 0.12
            score = _final_score(breakdown)
            if score >= 0.55:
                candidates.append(_candidate_dict("customer_name", node.text, score, "document graph customer label proximity", node, breakdown))
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _invoice_number_candidates(graph: DocumentGraph) -> list[dict[str, Any]]:
    candidates = []
    for label in graph.nodes:
        if label.node_type not in INVOICE_LABEL_TYPES:
            continue
        inline = _extract_document_number(label.text)
        if inline:
            breakdown = _base_breakdown(label)
            breakdown["label_proximity_score"] += 0.35
            breakdown["regex_score"] += 0.30
            candidates.append(_candidate_dict("invoice_number", inline, _final_score(breakdown), "document graph invoice label inline", label, breakdown))
        for node, _distance, relation_bonus in _nearby_value_nodes(graph, label, max_distance=230):
            value = _extract_document_number(node.text)
            if not value or _looks_like_po_context(label.text) and not _looks_like_invoice_context(label.text):
                continue
            breakdown = _base_breakdown(node)
            breakdown["label_proximity_score"] += relation_bonus
            breakdown["regex_score"] += 0.28
            score = _final_score(breakdown)
            if score >= 0.58:
                candidates.append(_candidate_dict("invoice_number", value, score, "document graph invoice label proximity", node, breakdown))
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _date_candidates(graph: DocumentGraph) -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    for label in graph.nodes:
        if label.node_type not in INVOICE_LABEL_TYPES and label.node_type not in DUE_LABEL_TYPES:
            continue
        field = "due_date" if label.node_type in DUE_LABEL_TYPES or "due" in label.normalized_text or "echeance" in label.normalized_text else "invoice_date"
        inline = _extract_date(label.text)
        if inline:
            breakdown = _base_breakdown(label)
            breakdown["label_proximity_score"] += 0.32
            breakdown["regex_score"] += 0.25
            candidates.append((field, _candidate_dict(field, inline, _final_score(breakdown), f"document graph {field} inline", label, breakdown)))
        for node, _distance, relation_bonus in _nearby_value_nodes(graph, label, max_distance=230):
            value = _extract_date(node.text)
            if not value:
                continue
            breakdown = _base_breakdown(node)
            breakdown["label_proximity_score"] += relation_bonus
            breakdown["regex_score"] += 0.25
            candidates.append((field, _candidate_dict(field, value, _final_score(breakdown), f"document graph {field} label proximity", node, breakdown)))
    return sorted(candidates, key=lambda item: item[1]["score"], reverse=True)


def _totals_candidates(graph: DocumentGraph) -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    for label in graph.nodes:
        field = None
        if label.node_type in TOTAL_LABEL_TYPES:
            field = "amount_ttc"
        elif label.node_type in SUBTOTAL_LABEL_TYPES:
            field = "amount_ht"
        elif label.node_type in TAX_LABEL_TYPES:
            field = "tva_amount"
        if not field:
            continue
        value = _best_amount_for_label(graph, label)
        if value is None:
            continue
        breakdown = _base_breakdown(label)
        breakdown["semantic_score"] += 0.18
        breakdown["label_proximity_score"] += 0.35
        breakdown["regex_score"] += 0.25
        if label.bbox and label.bbox.y1 > _max_y(graph) * 0.45:
            breakdown["layout_score"] += 0.18
        candidates.append((field, _candidate_dict(field, value, _final_score(breakdown), f"document graph totals label: {label.text}", label, breakdown)))
        if field == "tva_amount":
            rate = _extract_tax_rate(label.text)
            if rate is not None:
                rate_breakdown = dict(breakdown)
                rate_breakdown["regex_score"] += 0.10
                candidates.append(("tax_rate", _candidate_dict("tax_rate", rate, _final_score(rate_breakdown), "document graph tax rate", label, rate_breakdown)))
    candidates.extend(_consistent_total_group_candidates(graph))
    return sorted(candidates, key=lambda item: item[1]["score"], reverse=True)


def _consistent_total_group_candidates(graph: DocumentGraph) -> list[tuple[str, dict[str, Any]]]:
    values: dict[str, tuple[float, DocumentNode, dict[str, float]]] = {}
    for field, candidate in [item for item in _totals_candidates_no_group(graph)]:
        if isinstance(candidate["value"], (int, float)):
            values[field] = (float(candidate["value"]), candidate["node"], candidate["score_breakdown"])
    subtotal = values.get("amount_ht")
    tax = values.get("tva_amount")
    total = values.get("amount_ttc")
    shipping = _shipping_amount(graph)
    if not subtotal or not tax or not total:
        return []
    expected = round(subtotal[0] + tax[0] + (shipping or 0), 2)
    if abs(expected - total[0]) > max(0.10, abs(total[0]) * 0.01):
        return []
    boosted = []
    for field, (value, node, breakdown) in values.items():
        new_breakdown = dict(breakdown)
        new_breakdown["business_validation_score"] += 0.25
        new_breakdown["consistency_score"] += 0.30
        boosted.append((field, _candidate_dict(field, value, _final_score(new_breakdown), "document graph consistent totals group", node, new_breakdown)))
    return boosted


def _totals_candidates_no_group(graph: DocumentGraph) -> list[tuple[str, dict[str, Any]]]:
    candidates = []
    for label in graph.nodes:
        field = None
        if label.node_type in TOTAL_LABEL_TYPES:
            field = "amount_ttc"
        elif label.node_type in SUBTOTAL_LABEL_TYPES:
            field = "amount_ht"
        elif label.node_type in TAX_LABEL_TYPES:
            field = "tva_amount"
        if not field:
            continue
        value = _best_amount_for_label(graph, label)
        if value is None:
            continue
        breakdown = _base_breakdown(label)
        breakdown["semantic_score"] += 0.18
        breakdown["label_proximity_score"] += 0.35
        breakdown["regex_score"] += 0.25
        candidates.append((field, _candidate_dict(field, value, _final_score(breakdown), f"document graph totals label: {label.text}", label, breakdown)))
    return candidates


def _shipping_amount(graph: DocumentGraph) -> float | None:
    for node in graph.nodes:
        if any(word in node.normalized_text for word in ("shipping", "handling", "s&h", "freight")):
            return _best_amount_for_label(graph, node)
    return None


def _best_amount_for_label(graph: DocumentGraph, label: DocumentNode) -> float | None:
    inline_amounts = _extract_amounts(label.text)
    rate = _extract_tax_rate(label.text)
    if label.node_type in TAX_LABEL_TYPES and len(inline_amounts) >= 2:
        return inline_amounts[-1]
    inline_amounts = [amount for amount in inline_amounts if rate is None or abs(amount - rate) > 0.001]
    if inline_amounts:
        return inline_amounts[-1]
    options: list[tuple[float, float]] = []
    for node, distance, relation_bonus in _nearby_value_nodes(graph, label, max_distance=260):
        if node.node_type in {"address_candidate", "table_header", "table_row_text", "random_noise"}:
            continue
        amounts = _extract_amounts(node.text)
        if not amounts:
            continue
        penalty = distance * 0.01 - relation_bonus
        options.append((penalty, amounts[-1]))
    return sorted(options, key=lambda item: item[0])[0][1] if options else None


def _nearby_value_nodes(graph: DocumentGraph, label: DocumentNode, max_distance: float) -> list[tuple[DocumentNode, float, float]]:
    found: list[tuple[DocumentNode, float, float]] = []
    for node, edge in graph.neighbors(label):
        if edge.distance > max_distance:
            continue
        if edge.relation_type in {"right_of_label", "below_label"}:
            bonus = 0.35
        elif edge.relation_type in {"horizontal_neighbor", "vertical_neighbor", "same_row"}:
            bonus = 0.24
        else:
            bonus = 0.12
        found.append((node, edge.distance, bonus))
    return found


def _near_node_types(graph: DocumentGraph, node: DocumentNode, node_types: set[str], max_distance: float) -> bool:
    return any(other.node_type in node_types and edge.distance <= max_distance for other, edge in graph.neighbors(node))


def _near_customer_label(graph: DocumentGraph, node: DocumentNode) -> bool:
    return any(other.node_type == "customer_label" and edge.distance <= 220 for other, edge in graph.neighbors(node))


def _inside_products_or_totals(graph: DocumentGraph, node: DocumentNode) -> bool:
    return any(other.node_type in {"table_header", "table_row_text", "total_label", "subtotal_label", "tax_label"} and edge.distance <= 140 for other, edge in graph.neighbors(node))


def _base_breakdown(node: DocumentNode) -> dict[str, float]:
    confidence = node.confidence if node.confidence is not None else 0.65
    return {
        "layout_score": 0.10,
        "semantic_score": 0.0,
        "label_proximity_score": 0.0,
        "regex_score": 0.0,
        "business_validation_score": 0.08,
        "consistency_score": 0.0,
        "memory_score": 0.0,
        "penalty_score": max(0.0, 0.45 - confidence),
    }


def _final_score(breakdown: dict[str, float]) -> float:
    positive = sum(value for key, value in breakdown.items() if key != "penalty_score")
    score = positive - breakdown.get("penalty_score", 0.0)
    return round(max(0.0, min(0.98, score)), 3)


def _candidate_dict(field: str, value: Any, score: float, source: str, node: DocumentNode, breakdown: dict[str, float]) -> dict[str, Any]:
    return {
        "field": field,
        "value": value,
        "score": score,
        "source": source,
        "page": node.page,
        "line_index": node.line_index,
        "bbox": node.bbox,
        "normalized_value": value,
        "confidence": score,
        "evidence_text": node.text,
        "score_breakdown": {key: round(val, 3) for key, val in breakdown.items()},
        "node": node,
    }


def _emit(add: Callable[..., None], field_scores: dict[str, list[dict[str, Any]]], field: str, candidate: dict[str, Any]) -> None:
    node = candidate.pop("node")
    add(field, candidate["value"], candidate["score"], candidate["source"], node.source_blocks[0] if node.source_blocks else None, candidate["score_breakdown"])
    field_scores.setdefault(field, []).append(_score_payload({**candidate, "node": node}))


def _score_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    node = candidate.get("node")
    return {
        "field": candidate.get("field"),
        "value": candidate.get("value"),
        "score": candidate.get("score"),
        "source": candidate.get("source"),
        "evidence_text": node.text if node else candidate.get("evidence_text"),
        "line_index": node.line_index if node else candidate.get("line_index"),
        "bbox": node.bbox.model_dump(mode="json") if node and node.bbox else None,
        "score_breakdown": candidate.get("score_breakdown", {}),
    }


def _extract_document_number(text: str) -> str | None:
    patterns = [
        r"\b((?:INV|FAC|FACT|BL|DN|CN|CR)[-_ ]?\d{2,}[A-Z0-9_./\-]*)\b",
        r"(?:invoice|facture|reference|ref|no\.?|n°|number|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9_./\-]{3,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip(" :#|-")
            if not _looks_like_date_or_amount(value):
                return value
    return None


def _extract_date(text: str) -> str | None:
    match = re.search(DATE_PATTERN, text)
    if not match:
        return None
    parsed = parse_date(match.group(1))
    return parsed.isoformat() if parsed else match.group(1)


def _extract_amounts(text: str) -> list[float]:
    values = []
    for raw in re.findall(AMOUNT_PATTERN, text):
        amount = parse_amount(raw)
        if amount is not None:
            values.append(amount)
    return values


def _extract_tax_rate(text: str) -> float | None:
    match = re.search(r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%", text)
    return parse_amount(match.group(1)) if match else None


def _looks_like_date_or_amount(value: str) -> bool:
    return bool(re.fullmatch(DATE_PATTERN, value) or re.fullmatch(r"\d+[,.]\d{2,3}", value))


def _looks_like_po_context(text: str) -> bool:
    return bool(re.search(r"\b(?:po|purchase order|commande)\b", strip_accents(text).lower()))


def _looks_like_invoice_context(text: str) -> bool:
    return bool(re.search(r"\b(?:invoice|facture)\b", strip_accents(text).lower()))


def _max_y(graph: DocumentGraph) -> float:
    return max((node.bbox.y2 for node in graph.nodes if node.bbox), default=1000)

def _max_x(graph: DocumentGraph) -> float:
    return max((node.bbox.x2 for node in graph.nodes if node.bbox), default=1000)

