from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.schemas import BoundingBox, OCRLine
from app.services.document_layout import OCRVisualLine, group_ocr_lines
from app.utils.helpers import strip_accents


@dataclass
class DocumentNode:
    id: str
    text: str
    normalized_text: str
    bbox: BoundingBox | None
    page: int
    confidence: float | None = None
    font_size: float | None = None
    rotation: float | None = None
    node_type: str = "unknown"
    line_index: int | None = None
    source_blocks: list[OCRLine] = field(default_factory=list)
    neighbors: list[str] = field(default_factory=list)
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    reading_order: int | None = None
    alignment: str | None = None
    density: float | None = None
    semantic_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "bbox": self.bbox.model_dump(mode="json") if self.bbox else None,
            "page": self.page,
            "confidence": self.confidence,
            "font_size": self.font_size,
            "rotation": self.rotation,
            "node_type": self.node_type,
            "line_index": self.line_index,
            "neighbors": self.neighbors[:20],
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "reading_order": self.reading_order,
            "alignment": self.alignment,
            "density": self.density,
            "semantic_hints": self.semantic_hints,
        }


@dataclass
class DocumentEdge:
    source_id: str
    target_id: str
    relation_type: str
    distance: float
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "distance": round(self.distance, 3),
            "weight": round(self.weight, 3),
        }


@dataclass
class DocumentGraph:
    nodes: list[DocumentNode]
    edges: list[DocumentEdge]
    blocks: list[dict[str, Any]] = field(default_factory=list)
    document_tree: dict[str, Any] = field(default_factory=dict)

    def node_by_id(self, node_id: str) -> DocumentNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def neighbors(self, node: DocumentNode, relation_type: str | None = None) -> list[tuple[DocumentNode, DocumentEdge]]:
        found: list[tuple[DocumentNode, DocumentEdge]] = []
        for edge in self.edges:
            if edge.source_id != node.id:
                continue
            if relation_type and edge.relation_type != relation_type:
                continue
            target = self.node_by_id(edge.target_id)
            if target:
                found.append((target, edge))
        return sorted(found, key=lambda item: item[1].distance)

    def nodes_of_type(self, *node_types: str) -> list[DocumentNode]:
        wanted = set(node_types)
        return [node for node in self.nodes if node.node_type in wanted]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges[:600]],
            "blocks": self.blocks,
            "document_tree": self.document_tree,
        }


def build_document_graph(blocks: list[OCRLine]) -> DocumentGraph:
    visual_lines = group_ocr_lines(blocks)
    nodes = [_node_from_line(index, line) for index, line in enumerate(visual_lines)]
    _enrich_node_layout_features(nodes)
    semantic_blocks = _cluster_semantic_blocks(nodes)
    _attach_block_membership(nodes, semantic_blocks)
    edges = _build_edges(nodes)
    _attach_neighbors(nodes, edges)
    return DocumentGraph(
        nodes=nodes,
        edges=edges,
        blocks=semantic_blocks,
        document_tree=build_document_tree(nodes, semantic_blocks),
    )


def build_document_tree(nodes: list[DocumentNode], semantic_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a lightweight VDU-style hierarchy for explainability and routing.

    The tree intentionally stays algorithmic: page -> semantic blocks -> lines
    -> OCR words. It gives field extraction a stable structure without adding a
    deep-learning dependency.
    """
    pages: dict[int, dict[str, Any]] = {}
    for node in nodes:
        page = pages.setdefault(node.page, {
            "id": f"page-{node.page}",
            "node_type": "page",
            "page": node.page,
            "bbox": None,
            "children": [],
            "reading_order": node.page,
        })
        if node.bbox:
            page["bbox"] = _merge_box_dicts(page.get("bbox"), node.bbox.model_dump(mode="json"))

    block_lookup = {block["block_type"]: block for block in semantic_blocks}
    for block_type, block in block_lookup.items():
        child_nodes = [node for node in nodes if node.id in set(block.get("node_ids", []))]
        if not child_nodes:
            continue
        page_number = child_nodes[0].page
        pages.setdefault(page_number, {"id": f"page-{page_number}", "node_type": "page", "page": page_number, "children": []})
        pages[page_number]["children"].append({
            "id": f"block-{block_type}",
            "node_type": "block",
            "semantic_labels": _semantic_labels_for_block(block_type),
            "confidence": block.get("confidence"),
            "bbox": block.get("bbox"),
            "reading_order": min((node.reading_order or 0 for node in child_nodes), default=0),
            "density": _average_density(child_nodes),
            "alignment": _dominant_alignment(child_nodes),
            "children": [
                {
                    "id": node.id,
                    "node_type": "line",
                    "text": node.text,
                    "bbox": node.bbox.model_dump(mode="json") if node.bbox else None,
                    "page": node.page,
                    "confidence": node.confidence,
                    "font_height": node.font_size,
                    "alignment": node.alignment,
                    "density": node.density,
                    "semantic_hints": node.semantic_hints,
                    "reading_order": node.reading_order,
                    "children": _word_nodes(node),
                }
                for node in sorted(child_nodes, key=lambda item: item.reading_order or 0)
            ],
        })
    return {
        "id": "document",
        "node_type": "document",
        "pages": [
            {**page, "children": sorted(page.get("children", []), key=lambda item: item.get("reading_order", 0))}
            for _page_no, page in sorted(pages.items())
        ],
    }


def _node_from_line(index: int, line: OCRVisualLine) -> DocumentNode:
    font_size = None
    if line.bbox:
        font_size = round(max(1.0, line.bbox.y2 - line.bbox.y1), 2)
    return DocumentNode(
        id=f"n{index}",
        text=line.text,
        normalized_text=strip_accents(line.text).lower().strip(),
        bbox=line.bbox,
        page=line.page,
        confidence=line.confidence,
        font_size=font_size,
        rotation=0.0,
        line_index=line.line_index if line.line_index is not None else index,
        source_blocks=line.blocks,
    )


def _build_edges(nodes: list[DocumentNode]) -> list[DocumentEdge]:
    edges: list[DocumentEdge] = []
    for source in nodes:
        if not source.bbox:
            continue
        same_page = [node for node in nodes if node.id != source.id and node.page == source.page and node.bbox]
        for target in same_page:
            relations = _relations(source, target)
            if not relations:
                continue
            distance = _distance(source.bbox, target.bbox)
            if distance > 500 and not {"same_column", "vertical_neighbor", "contains", "overlaps", "same_block"} & set(relations):
                continue
            for relation in relations:
                edges.append(DocumentEdge(
                    source_id=source.id,
                    target_id=target.id,
                    relation_type=relation,
                    distance=distance,
                    weight=max(0.0, round(1.0 - min(distance, 500) / 500, 3)),
                ))
    return edges


def _relations(source: DocumentNode, target: DocumentNode) -> list[str]:
    assert source.bbox and target.bbox
    relations: list[str] = []
    same_row = abs(_center_y(source.bbox) - _center_y(target.bbox)) <= 18
    same_column = abs(source.bbox.x1 - target.bbox.x1) <= 55
    target_right = target.bbox.x1 >= source.bbox.x2 - 4
    target_below = target.bbox.y1 >= source.bbox.y2 - 4
    if _contains(source.bbox, target.bbox):
        relations.append("contains")
    if _overlaps(source.bbox, target.bbox):
        relations.append("overlaps")
    if same_row and target_right:
        relations.extend(["right_of", "same_line"])
        relations.append("right_of_label" if _looks_like_label(source.normalized_text) else "horizontal_neighbor")
    if same_row and target.bbox.x2 <= source.bbox.x1 + 4:
        relations.extend(["left_of", "same_line"])
    if same_row:
        relations.append("same_line")
    if target_below and same_column:
        relations.extend(["below", "same_column"])
        relations.append("below_label" if _looks_like_label(source.normalized_text) else "vertical_neighbor")
    if target_below:
        relations.append("below")
    if target.bbox.y2 <= source.bbox.y1 + 4:
        relations.append("above")
    if same_column:
        relations.append("same_column")
    if source.parent_id and source.parent_id == target.parent_id:
        relations.append("same_block")
    if _is_totals_text(source.normalized_text) and target_below:
        relations.append("near_totals_block")
    if _is_table_header_text(source.normalized_text) and target_below:
        relations.append("near_table_header")
    return list(dict.fromkeys(relations))


def _attach_neighbors(nodes: list[DocumentNode], edges: list[DocumentEdge]) -> None:
    neighbor_map: dict[str, list[str]] = {node.id: [] for node in nodes}
    for edge in edges:
        neighbor_map.setdefault(edge.source_id, []).append(edge.target_id)
    for node in nodes:
        node.neighbors = neighbor_map.get(node.id, [])


def _cluster_semantic_blocks(nodes: list[DocumentNode]) -> list[dict[str, Any]]:
    if not nodes:
        return []
    max_y = max((node.bbox.y2 for node in nodes if node.bbox), default=1000)
    max_x = max((node.bbox.x2 for node in nodes if node.bbox), default=1000)
    clusters: dict[str, list[DocumentNode]] = {
        "invoice_header": [],
        "supplier_block": [],
        "customer_block": [],
        "invoice_metadata_block": [],
        "products_table": [],
        "totals_block": [],
        "footer": [],
    }
    for node in nodes:
        if not node.bbox:
            continue
        text = node.normalized_text
        if node.bbox.y1 < max_y * 0.12:
            clusters["invoice_header"].append(node)
        if node.bbox.y1 < max_y * 0.30 and node.bbox.x1 < max_x * 0.45:
            clusters["supplier_block"].append(node)
        if node.bbox.y1 < max_y * 0.45 and node.bbox.x1 > max_x * 0.48:
            clusters["customer_block"].append(node)
        if node.bbox.y1 < max_y * 0.35 and any(word in text for word in ("invoice", "facture", "date", "due", "ref", "number")):
            clusters["invoice_metadata_block"].append(node)
        if any(word in text for word in ("description", "qty", "quantity", "price", "total", "vat", "tva")) or (max_y * 0.22 <= node.bbox.y1 <= max_y * 0.72):
            clusters["products_table"].append(node)
        if node.bbox.y1 > max_y * 0.52 and (node.bbox.x1 > max_x * 0.45 or any(word in text for word in ("subtotal", "tax", "tva", "vat", "total due", "amount due", "grand total"))):
            clusters["totals_block"].append(node)
        if node.bbox.y1 > max_y * 0.85:
            clusters["footer"].append(node)
    results: list[dict[str, Any]] = []
    for block_type, group in clusters.items():
        if not group:
            continue
        boxes = [node.bbox for node in group if node.bbox]
        text = "\n".join(node.text for node in sorted(group, key=lambda item: item.reading_order or 0))
        semantic_scores = _block_semantic_scores(block_type, group)
        results.append({
            "block_type": block_type,
            "semantic_labels": _semantic_labels_for_block(block_type),
            "confidence": semantic_scores.get(block_type, round(sum((node.confidence or 0.6) for node in group) / len(group), 3)),
            "semantic_scores": semantic_scores,
            "bbox": _merge_boxes(boxes).model_dump(mode="json") if boxes else None,
            "text": text,
            "node_ids": [node.id for node in group],
            "reading_order": min((node.reading_order or 0 for node in group), default=0),
            "density": _average_density(group),
            "alignment": _dominant_alignment(group),
        })
    return results


def _enrich_node_layout_features(nodes: list[DocumentNode]) -> None:
    boxes = [node.bbox for node in nodes if node.bbox]
    max_x = max((box.x2 for box in boxes), default=1000)
    for order, node in enumerate(sorted(nodes, key=lambda item: (item.page, item.bbox.y1 if item.bbox else 0, item.bbox.x1 if item.bbox else 0))):
        node.reading_order = order
        if not node.bbox:
            continue
        center_x = _center_x(node.bbox)
        if center_x < max_x * 0.36:
            node.alignment = "left"
        elif center_x > max_x * 0.64:
            node.alignment = "right"
        else:
            node.alignment = "center"
        area = max(1.0, (node.bbox.x2 - node.bbox.x1) * (node.bbox.y2 - node.bbox.y1))
        node.density = round(len(node.text.strip()) / area, 4)
        node.semantic_hints = _semantic_hints(node.normalized_text)


def _attach_block_membership(nodes: list[DocumentNode], semantic_blocks: list[dict[str, Any]]) -> None:
    for block in sorted(semantic_blocks, key=lambda item: _block_priority(item["block_type"])):
        parent_id = f"block-{block['block_type']}"
        for node_id in block.get("node_ids", []):
            node = next((item for item in nodes if item.id == node_id), None)
            if not node or node.parent_id:
                continue
            node.parent_id = parent_id
    membership: dict[str, list[str]] = {}
    for node in nodes:
        if node.parent_id:
            membership.setdefault(node.parent_id, []).append(node.id)
    for node in nodes:
        if node.parent_id:
            node.children_ids = []


def _semantic_hints(text: str) -> list[str]:
    hints = []
    groups = {
        "invoice_metadata": ("invoice", "facture", "ref", "date", "number", "numero"),
        "customer": ("client", "customer", "bill to", "livre", "acheteur"),
        "supplier": ("supplier", "vendor", "seller", "fournisseur"),
        "payment": ("iban", "rib", "swift", "bank", "banque", "payment"),
        "totals": ("total", "subtotal", "tva", "vat", "tax", "ttc", "amount due"),
        "table": ("description", "qty", "quantity", "price", "prix", "designation"),
        "contact": ("email", "tel", "phone", "@", "www"),
        "address": ("street", "road", "avenue", "rue", "route"),
    }
    for hint, keywords in groups.items():
        if any(keyword in text for keyword in keywords):
            hints.append(hint)
    if re.search(r"\d", text):
        hints.append("numeric")
    return hints


def _semantic_labels_for_block(block_type: str) -> list[str]:
    labels = {
        "invoice_header": ["invoice_title"],
        "supplier_block": ["supplier"],
        "customer_block": ["customer"],
        "invoice_metadata_block": ["invoice_metadata"],
        "products_table": ["products_table"],
        "totals_block": ["totals", "vat_summary"],
        "footer": ["footer", "notes"],
    }
    return labels.get(block_type, [block_type])


def _block_semantic_scores(block_type: str, nodes: list[DocumentNode]) -> dict[str, float]:
    text = "\n".join(node.normalized_text for node in nodes)
    hints = [hint for node in nodes for hint in node.semantic_hints]
    confidence = _average([node.confidence for node in nodes], 0.6)
    score = confidence * 0.55
    if block_type == "supplier_block" and ("supplier" in hints or "contact" in hints or "address" in hints):
        score += 0.25
    elif block_type == "customer_block" and ("customer" in hints or "address" in hints):
        score += 0.25
    elif block_type == "invoice_metadata_block" and "invoice_metadata" in hints:
        score += 0.30
    elif block_type == "products_table" and ("table" in hints or len(re.findall(r"\d", text)) > 8):
        score += 0.25
    elif block_type == "totals_block" and "totals" in hints:
        score += 0.30
    elif block_type == "footer" and any(word in text for word in ("thank", "merci", "signature")):
        score += 0.20
    else:
        score += 0.10
    return {block_type: round(max(0.0, min(0.98, score)), 3)}


def _word_nodes(node: DocumentNode) -> list[dict[str, Any]]:
    words = [word for word in re.split(r"\s+", node.text.strip()) if word]
    if not words or not node.bbox:
        return []
    width = max(1.0, node.bbox.x2 - node.bbox.x1)
    step = width / len(words)
    return [
        {
            "id": f"{node.id}-w{index}",
            "node_type": "word",
            "text": word,
            "page": node.page,
            "parent": node.id,
            "reading_order": index,
            "bbox": {
                "x1": round(node.bbox.x1 + step * index, 3),
                "y1": node.bbox.y1,
                "x2": round(node.bbox.x1 + step * (index + 1), 3),
                "y2": node.bbox.y2,
            },
        }
        for index, word in enumerate(words)
    ]


def _average_density(nodes: list[DocumentNode]) -> float | None:
    values = [node.density for node in nodes if node.density is not None]
    return round(sum(values) / len(values), 4) if values else None


def _dominant_alignment(nodes: list[DocumentNode]) -> str | None:
    values = [node.alignment for node in nodes if node.alignment]
    if not values:
        return None
    return max(set(values), key=values.count)


def _average(values: list[float | None], default: float = 0.0) -> float:
    numeric = [float(value) for value in values if value is not None]
    return round(sum(numeric) / len(numeric), 3) if numeric else default


def _merge_box_dicts(existing: dict[str, float] | None, box: dict[str, float]) -> dict[str, float]:
    if existing is None:
        return dict(box)
    return {
        "x1": min(existing["x1"], box["x1"]),
        "y1": min(existing["y1"], box["y1"]),
        "x2": max(existing["x2"], box["x2"]),
        "y2": max(existing["y2"], box["y2"]),
    }


def _block_priority(block_type: str) -> int:
    order = {
        "supplier_block": 1,
        "customer_block": 1,
        "invoice_metadata_block": 1,
        "products_table": 2,
        "totals_block": 2,
        "invoice_header": 3,
        "footer": 4,
    }
    return order.get(block_type, 9)


def _looks_like_label(text: str) -> bool:
    return any(word in text for word in (
        "invoice", "facture", "date", "due", "echeance", "total", "subtotal", "tax", "vat", "tva",
        "client", "customer", "bill to", "ship to", "reference", "ref", "number", "no", "n°",
    ))


def _is_totals_text(text: str) -> bool:
    return any(word in text for word in ("total due", "grand total", "amount due", "total ttc", "subtotal", "sales tax", "shipping"))


def _is_table_header_text(text: str) -> bool:
    keywords = ("description", "item", "quantity", "qty", "price", "amount", "total", "id")
    return sum(1 for keyword in keywords if re.search(rf"\b{re.escape(keyword)}\b", text)) >= 3


def _distance(a: BoundingBox, b: BoundingBox) -> float:
    return math.dist((_center_x(a), _center_y(a)), (_center_x(b), _center_y(b)))


def _contains(a: BoundingBox, b: BoundingBox) -> bool:
    return a.x1 <= b.x1 and a.y1 <= b.y1 and a.x2 >= b.x2 and a.y2 >= b.y2


def _overlaps(a: BoundingBox, b: BoundingBox) -> bool:
    x_overlap = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    y_overlap = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    return x_overlap > 0 and y_overlap > 0


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
