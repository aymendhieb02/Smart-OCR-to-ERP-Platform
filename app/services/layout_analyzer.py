from __future__ import annotations

import math
import re
from collections import defaultdict

from app.core.schemas import BoundingBox, LayoutBlock, OCRLine
from app.utils.helpers import parse_amount, strip_accents


class LayoutAnalyzer:
    def __init__(self, blocks: list[OCRLine]):
        self.blocks = [block for block in blocks if block.text.strip()]
        boxes = [block.bbox for block in self.blocks if block.bbox]
        self.max_x = max((box.x2 for box in boxes), default=1000)
        self.max_y = max((box.y2 for box in boxes), default=1000)

    def get_blocks_in_zone(self, zone: str) -> list[OCRLine]:
        if zone == "full_page":
            return self.blocks
        x1, y1, x2, y2 = self._zone_rect(zone)
        return [
            block for block in self.blocks
            if block.bbox and block.bbox.x1 >= x1 and block.bbox.x2 <= x2 and block.bbox.y1 >= y1 and block.bbox.y2 <= y2
        ]

    def group_blocks_into_lines(self, blocks: list[OCRLine] | None = None) -> list[str]:
        blocks = blocks or self.blocks
        rows: dict[int, list[OCRLine]] = defaultdict(list)
        for block in blocks:
            if not block.bbox:
                rows[block.line_index or 0].append(block)
                continue
            key = round(block.bbox.y1 / 12)
            rows[key].append(block)
        lines = []
        for row in sorted(rows):
            ordered = sorted(rows[row], key=lambda b: b.bbox.x1 if b.bbox else 0)
            lines.append(" ".join(block.text for block in ordered))
        return lines

    def find_nearest_value_right_of_label(self, labels: list[str]) -> OCRLine | None:
        label_blocks = self._matching_blocks(labels)
        best: tuple[float, OCRLine] | None = None
        for label in label_blocks:
            if not label.bbox:
                continue
            for candidate in self.blocks:
                if candidate is label or not candidate.bbox:
                    continue
                same_row = abs(candidate.bbox.y1 - label.bbox.y1) < 25
                right = candidate.bbox.x1 >= label.bbox.x2
                if not same_row or not right:
                    continue
                distance = candidate.bbox.x1 - label.bbox.x2
                if best is None or distance < best[0]:
                    best = (distance, candidate)
        return best[1] if best else None

    def find_nearest_value_below_label(self, labels: list[str]) -> OCRLine | None:
        label_blocks = self._matching_blocks(labels)
        best: tuple[float, OCRLine] | None = None
        for label in label_blocks:
            if not label.bbox:
                continue
            for candidate in self.blocks:
                if candidate is label or not candidate.bbox:
                    continue
                below = candidate.bbox.y1 >= label.bbox.y2
                near_x = abs(candidate.bbox.x1 - label.bbox.x1) < max(80, (label.bbox.x2 - label.bbox.x1) * 1.8)
                if not below or not near_x:
                    continue
                distance = math.dist((label.bbox.x1, label.bbox.y2), (candidate.bbox.x1, candidate.bbox.y1))
                if best is None or distance < best[0]:
                    best = (distance, candidate)
        return best[1] if best else None

    def find_numbers_near_keyword(self, labels: list[str]) -> list[tuple[float, OCRLine]]:
        found = []
        for block in self._matching_blocks(labels):
            if not block.bbox:
                continue
            for candidate in self.blocks:
                if candidate is block or not candidate.bbox:
                    continue
                if parse_amount(candidate.text) is None:
                    continue
                distance = math.dist((block.bbox.x1, block.bbox.y1), (candidate.bbox.x1, candidate.bbox.y1))
                if distance < 260:
                    found.append((distance, candidate))
        return sorted(found, key=lambda item: item[0])

    def detect_table_region(self) -> list[OCRLine]:
        return self.get_blocks_in_zone("middle_table")

    def detect_layout_blocks(self) -> list[LayoutBlock]:
        blocks: list[LayoutBlock] = []
        for block_type, zone in (
            ("supplier", "top_left"),
            ("invoice_metadata", "top_right"),
            ("customer", "center"),
            ("products", "middle_table"),
            ("totals", "bottom_right"),
            ("payment", "bottom_left"),
            ("footer", "full_page"),
        ):
            zone_blocks = self._filter_blocks_for_type(self.get_blocks_in_zone(zone), block_type)
            if zone_blocks:
                blocks.append(self._build_layout_block(block_type, zone_blocks))

        notes_blocks = self._keyword_blocks(["note", "remarque", "arrete", "arrêté", "conditions", "ملاحظات"])
        if notes_blocks:
            blocks.append(self._build_layout_block("notes", notes_blocks))

        taxes_blocks = self._keyword_blocks(["tva", "vat", "tax", "ضريبة", "الأداء"])
        if taxes_blocks:
            blocks.append(self._build_layout_block("taxes", taxes_blocks))

        known_ids = {id(block) for layout in blocks for block in self._blocks_inside(layout.bbox)}
        unknown = [block for block in self.blocks if block.bbox and id(block) not in known_ids]
        if unknown:
            blocks.append(self._build_layout_block("unknown", unknown[:30], confidence=0.35))
        return blocks

    def _matching_blocks(self, labels: list[str]) -> list[OCRLine]:
        matches = []
        for block in self.blocks:
            normalized = strip_accents(block.text).lower()
            if any(re.search(strip_accents(label).lower(), normalized) for label in labels):
                matches.append(block)
        return matches

    def _filter_blocks_for_type(self, blocks: list[OCRLine], block_type: str) -> list[OCRLine]:
        if block_type == "footer":
            return [block for block in blocks if block.bbox and block.bbox.y1 >= self.max_y * 0.88]
        if block_type == "products":
            keywords = ["designation", "désignation", "description", "code produit", "product code", "qte", "qty", "quantite", "quantity", "الكمية", "الوصف"]
            if any(self._contains(block, keywords) for block in blocks):
                return blocks
            return [block for block in blocks if re.search(r"\b[A-Z]{2,}[A-Z0-9]*-[A-Z0-9]+\b", block.text)]
        if block_type == "totals":
            return [block for block in blocks if self._contains(block, ["total", "ttc", "subtotal", "sous-total", "tva", "vat", "tax", "الإجمالي", "المجموع"])]
        if block_type == "payment":
            return [block for block in blocks if self._contains(block, ["rib", "iban", "swift", "banque", "bank", "payment", "paiement", "virement", "البنك"])]
        if block_type == "customer":
            return [block for block in blocks if block.bbox and (block.bbox.x1 > self.max_x * 0.42 or self._contains(block, ["client", "customer", "livre", "livré", "العميل"]))]
        if block_type == "supplier":
            return [block for block in blocks if not self._contains(block, ["facture", "invoice", "total"])]
        return blocks

    def _keyword_blocks(self, labels: list[str]) -> list[OCRLine]:
        return [block for block in self.blocks if self._contains(block, labels)]

    def _contains(self, block: OCRLine, labels: list[str]) -> bool:
        normalized = strip_accents(block.text).lower()
        return any(strip_accents(label).lower() in normalized for label in labels)

    def _build_layout_block(self, block_type: str, blocks: list[OCRLine], confidence: float | None = None) -> LayoutBlock:
        boxes = [block.bbox for block in blocks if block.bbox]
        bbox = BoundingBox(
            x1=min((box.x1 for box in boxes), default=0),
            y1=min((box.y1 for box in boxes), default=0),
            x2=max((box.x2 for box in boxes), default=0),
            y2=max((box.y2 for box in boxes), default=0),
        )
        text = "\n".join(block.text for block in sorted(blocks, key=lambda item: (item.bbox.y1 if item.bbox else 0, item.bbox.x1 if item.bbox else 0)))
        confidence_values = [block.confidence for block in blocks if block.confidence is not None]
        avg_confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.55
        return LayoutBlock(
            block_type=block_type,
            bbox=bbox,
            confidence=confidence if confidence is not None else avg_confidence,
            text=text,
            fields=[],
            page=blocks[0].page_number if blocks else 1,
        )

    def _blocks_inside(self, bbox: BoundingBox) -> list[OCRLine]:
        return [
            block for block in self.blocks
            if block.bbox
            and block.bbox.x1 >= bbox.x1
            and block.bbox.x2 <= bbox.x2
            and block.bbox.y1 >= bbox.y1
            and block.bbox.y2 <= bbox.y2
        ]

    def _zone_rect(self, zone: str) -> tuple[float, float, float, float]:
        zones = {
            "top_left": (0, 0, self.max_x * 0.52, self.max_y * 0.35),
            "top_right": (self.max_x * 0.45, 0, self.max_x, self.max_y * 0.35),
            "center": (self.max_x * 0.20, self.max_y * 0.20, self.max_x * 0.80, self.max_y * 0.75),
            "middle_table": (0, self.max_y * 0.30, self.max_x, self.max_y * 0.70),
            "bottom_left": (0, self.max_y * 0.60, self.max_x * 0.55, self.max_y),
            "bottom_right": (self.max_x * 0.45, self.max_y * 0.55, self.max_x, self.max_y),
        }
        return zones.get(zone, (0, 0, self.max_x, self.max_y))
