from __future__ import annotations

import numpy as np

from app.core.config import settings
from app.core.schemas import BoundingBox, OCRLine
from app.services.table_transformer.table_cell_mapper import map_ocr_to_cells
from app.services.table_transformer.table_transformer_cache import TableTransformerCache, image_cache_key
from app.services.table_transformer.table_transformer_detector import TATRTable, fallback_detect_tables_from_ocr
from app.services.table_transformer.table_transformer_loader import TableTransformerLoader
from app.services.table_transformer.table_transformer_reconstruction import reconstruct_line_items_from_cells
from app.services.table_transformer.table_transformer_router import experimental_table_transformer_enabled


def line(text: str, x1: float, y1: float, x2: float, y2: float, index: int) -> OCRLine:
    return OCRLine(text=text, confidence=0.9, bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2), page_number=1, line_index=index)


def test_table_transformer_config_defaults_off() -> None:
    assert settings.enable_table_transformer is False
    assert experimental_table_transformer_enabled() is False


def test_loader_gracefully_reports_missing_local_weights(tmp_path) -> None:
    TableTransformerLoader.reset_singleton()
    result = TableTransformerLoader(model_path=tmp_path, device="cpu").load(download=False)
    assert result.available in {False, True}
    if not result.available:
        assert result.error


def test_cache_roundtrip(tmp_path) -> None:
    cache = TableTransformerCache(tmp_path)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    key = image_cache_key(image)
    cache.set(key, {"tables": []})
    assert cache.get(key) == {"tables": []}


def test_fallback_detects_table_region_from_ocr() -> None:
    lines = [
        line("Description Qty Price Total", 10, 10, 220, 30, 0),
        line("Service 2 10 20", 10, 40, 220, 60, 1),
    ]
    tables = fallback_detect_tables_from_ocr(lines)
    assert isinstance(tables, list)


def test_cell_mapper_and_reconstruction() -> None:
    table = TATRTable(page=1, bbox={"x1": 0, "y1": 0, "x2": 300, "y2": 100}, confidence=0.9)
    lines = [
        line("Description", 5, 5, 90, 20, 0),
        line("Qty", 110, 5, 135, 20, 1),
        line("Price", 160, 5, 200, 20, 2),
        line("Total", 230, 5, 280, 20, 3),
        line("Service", 5, 40, 90, 55, 4),
        line("2", 110, 40, 135, 55, 5),
        line("10", 160, 40, 200, 55, 6),
        line("20", 230, 40, 280, 55, 7),
    ]
    cells = map_ocr_to_cells(table, lines)
    items = reconstruct_line_items_from_cells(cells)
    assert cells
    assert items
    assert items[0].description


def test_visualizer_imports() -> None:
    from app.services.table_transformer.table_transformer_visualizer import draw_table_transformer_overlay

    assert callable(draw_table_transformer_overlay)

class _FakeLoadResult:
    def __init__(self, *, available=True, processor=None, model=None, error=None):
        self.available = available
        self.processor = processor
        self.model = model
        self.error = error
        self.device = "cpu"


class _FakeLoader:
    def __init__(self, result):
        self.result = result

    def load(self, *, download=True):
        return self.result


class _FakeModel:
    def __init__(self, labels):
        self.config = type("Config", (), {"id2label": labels})()

    def __call__(self, **_kwargs):
        return object()


class _FakeProcessor:
    def __init__(self, *, scores, labels, boxes):
        self.scores = scores
        self.labels = labels
        self.boxes = boxes

    def __call__(self, images, return_tensors="pt"):
        import torch

        return {"pixel_values": torch.zeros((1, 3, 8, 8))}

    def post_process_object_detection(self, outputs, threshold, target_sizes):
        import torch

        rows = []
        for score, label, box in zip(self.scores, self.labels, self.boxes):
            if score >= threshold:
                rows.append((score, label, box))
        return [{
            "scores": torch.tensor([row[0] for row in rows], dtype=torch.float32),
            "labels": torch.tensor([row[1] for row in rows], dtype=torch.int64),
            "boxes": torch.tensor([row[2] for row in rows], dtype=torch.float32) if rows else torch.zeros((0, 4), dtype=torch.float32),
        }]


def test_two_stage_detector_caps_detection_stage_not_structure(monkeypatch) -> None:
    from app.services.table_transformer.table_transformer_detector import TableTransformerDetector

    image = np.zeros((240, 360, 3), dtype=np.uint8)
    detection = _FakeLoadResult(
        processor=_FakeProcessor(scores=[0.95], labels=[0], boxes=[[80, 50, 300, 180]]),
        model=_FakeModel({0: "table"}),
    )
    structure = _FakeLoadResult(
        processor=_FakeProcessor(
            scores=[0.96, 0.94, 0.93, 0.92, 0.91],
            labels=[0, 0, 1, 1, 1],
            boxes=[[8, 10, 210, 45], [8, 55, 210, 95], [10, 8, 70, 98], [80, 8, 140, 98], [150, 8, 210, 98]],
        ),
        model=_FakeModel({0: "table row", 1: "table column"}),
    )
    monkeypatch.setattr(settings, "table_transformer_max_tables", 10)
    detector = TableTransformerDetector(loader=_FakeLoader(structure), detection_loader=_FakeLoader(detection))

    result = detector.detect(image, allow_fallback=False)

    assert result.available is True
    assert result.model_loaded is True
    assert result.source == "table_transformer"
    assert result.detection_count_uncapped == 1
    assert result.structure_regions_processed == 1
    assert len(result.tables) == 1
    assert len(result.tables[0].rows) == 2
    assert len(result.tables[0].columns) == 3
    assert len(result.tables[0].cells) == 6


def test_two_stage_detector_remaps_crop_coordinates_to_page(monkeypatch) -> None:
    from app.services.table_transformer.table_transformer_detector import TableTransformerDetector

    image = np.zeros((300, 400, 3), dtype=np.uint8)
    detection = _FakeLoadResult(
        processor=_FakeProcessor(scores=[0.99], labels=[0], boxes=[[100, 50, 300, 220]]),
        model=_FakeModel({0: "table"}),
    )
    structure = _FakeLoadResult(
        processor=_FakeProcessor(
            scores=[0.96, 0.94],
            labels=[0, 1],
            boxes=[[10, 20, 190, 80], [20, 10, 100, 120]],
        ),
        model=_FakeModel({0: "table row", 1: "table column"}),
    )
    monkeypatch.setattr(settings, "table_transformer_max_tables", 10)
    detector = TableTransformerDetector(loader=_FakeLoader(structure), detection_loader=_FakeLoader(detection))

    table = detector.detect(image, allow_fallback=False).tables[0]

    # Detection crop is padded by 8 px, so crop origin is (92, 42).
    assert table.rows[0]["bbox"] == {"x1": 102.0, "y1": 62.0, "x2": 282.0, "y2": 122.0}
    assert table.columns[0]["bbox"] == {"x1": 112.0, "y1": 52.0, "x2": 192.0, "y2": 162.0}
    assert table.cells[0].bbox == {"x1": 112.0, "y1": 62.0, "x2": 192.0, "y2": 122.0}


def test_two_stage_detector_falls_back_when_detection_model_unavailable() -> None:
    from app.services.table_transformer.table_transformer_detector import TableTransformerDetector

    image = np.zeros((120, 260, 3), dtype=np.uint8)
    lines = [
        line("Description Qty Price Total", 10, 10, 220, 30, 0),
        line("Service 2 10 20", 10, 40, 220, 60, 1),
    ]
    detection = _FakeLoadResult(available=False, error="missing detection weights")
    structure = _FakeLoadResult(processor=_FakeProcessor(scores=[], labels=[], boxes=[]), model=_FakeModel({}))
    detector = TableTransformerDetector(loader=_FakeLoader(structure), detection_loader=_FakeLoader(detection))

    result = detector.detect(image, ocr_lines=lines)

    assert result.available is False
    assert result.source == "geometry_fallback"
    assert result.tables
    assert "detection" in (result.model_load_error or "")


def test_two_stage_detector_falls_back_when_structure_model_unavailable() -> None:
    from app.services.table_transformer.table_transformer_detector import TableTransformerDetector

    image = np.zeros((120, 260, 3), dtype=np.uint8)
    lines = [
        line("Description Qty Price Total", 10, 10, 220, 30, 0),
        line("Service 2 10 20", 10, 40, 220, 60, 1),
    ]
    detection = _FakeLoadResult(processor=_FakeProcessor(scores=[0.98], labels=[0], boxes=[[5, 5, 230, 80]]), model=_FakeModel({0: "table"}))
    structure = _FakeLoadResult(available=False, error="missing structure weights")
    detector = TableTransformerDetector(loader=_FakeLoader(structure), detection_loader=_FakeLoader(detection))

    result = detector.detect(image, ocr_lines=lines)

    assert result.available is False
    assert result.source == "geometry_fallback"
    assert result.tables
    assert "structure" in (result.model_load_error or "")
