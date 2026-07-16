import numpy as np
import json

from app.core.schemas import BoundingBox
from app.core.schemas import OCRLine
from app.core.config import settings
from app.services.ocr_engine import OCREngine
from app.services.ocr_engine import _ensure_color_image
from app.services.ocr_engine import _item_with_page_bbox
from app.services.ocr_engine import _iter_paddle_items
from app.services.ocr_engine import normalize_paddle_bbox
from app.services.table_regions import OCRRegion


class DummyPaddle:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def ocr(self, _image, cls=True):
        self.calls += 1
        return self.result


def test_paddle_old_api_output_is_normalized(monkeypatch):
    result = [
        [
            [
                [[10, 10], [120, 10], [120, 30], [10, 30]],
                ("Invoice Number", 0.98),
            ],
            [
                [[140, 10], [240, 10], [240, 30], [140, 30]],
                ("INV-1001", 0.97),
            ],
        ]
    ]
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: DummyPaddle(result))

    ocr = OCREngine()._run_paddle([np.zeros((50, 300, 3), dtype=np.uint8)])

    assert [line.text for line in ocr] == ["Invoice Number", "INV-1001"]
    assert ocr[0].bbox is not None


def test_paddle_dict_output_is_normalized(monkeypatch):
    result = {
        "result": [
            {"text": "Total Due", "confidence": 0.95, "bbox": [[10, 40], [100, 40], [100, 60], [10, 60]]},
            {"text": "1291.78", "score": 0.93, "box": [[120, 40], [200, 40], [200, 60], [120, 60]]},
        ]
    }
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: DummyPaddle(result))

    ocr = OCREngine()._run_paddle([np.zeros((80, 240, 3), dtype=np.uint8)])

    assert [line.text for line in ocr] == ["Total Due", "1291.78"]
    assert ocr[1].confidence == 0.93


def test_paddle_empty_pages_do_not_crash(monkeypatch):
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: DummyPaddle([None, []]))

    ocr = OCREngine()._run_paddle([np.zeros((30, 30, 3), dtype=np.uint8)])

    assert ocr == []


def test_gray_image_is_converted_to_three_channels():
    gray = np.zeros((10, 12), dtype=np.uint8)
    color = _ensure_color_image(gray)

    assert color.shape == (10, 12, 3)


def test_normalize_paddle_bbox_flat_rectangle():
    bbox = normalize_paddle_bbox([10, 20, 110, 45])

    assert bbox == BoundingBox(x1=10, y1=20, x2=110, y2=45)


def test_normalize_paddle_bbox_polygon():
    bbox = normalize_paddle_bbox([[10, 20], [110, 18], [112, 45], [9, 47]])

    assert bbox == BoundingBox(x1=9, y1=18, x2=112, y2=47)


def test_normalize_paddle_bbox_numpy_flat_rectangle():
    bbox = normalize_paddle_bbox(np.array([10, 20, 110, 45], dtype=np.float32))

    assert bbox == BoundingBox(x1=10, y1=20, x2=110, y2=45)


def test_normalize_paddle_bbox_numpy_polygon():
    bbox = normalize_paddle_bbox(np.array([[10, 20], [110, 18], [112, 45], [9, 47]], dtype=np.float32))

    assert bbox == BoundingBox(x1=9, y1=18, x2=112, y2=47)


def test_normalize_paddle_bbox_dict():
    bbox = normalize_paddle_bbox({"x1": 10, "y1": 20, "x2": 110, "y2": 45})

    assert bbox == BoundingBox(x1=10, y1=20, x2=110, y2=45)


def test_malformed_bbox_is_ignored_without_document_failure():
    items = list(_iter_paddle_items({"text": "Invoice", "confidence": 0.9, "bbox": ["bad"]}, page_number=1))

    assert items[0]["text"] == "Invoice"
    assert items[0]["bbox"] is None


def test_paddle_dict_rec_texts_scores_boxes_align_without_duplicate():
    result = {
        "rec_texts": ["Description", "Total"],
        "rec_scores": [0.91, 0.88],
        "rec_boxes": np.array([[10, 20, 110, 45], [200, 20, 260, 45]], dtype=np.float32),
        "text": "duplicate aggregate",
    }

    items = list(_iter_paddle_items(result, page_number=1))

    assert [item["text"] for item in items] == ["Description", "Total"]
    assert [item["confidence"] for item in items] == [0.91, 0.88]
    assert items[0]["bbox"] == BoundingBox(x1=10, y1=20, x2=110, y2=45)
    assert items[1]["bbox"] == BoundingBox(x1=200, y1=20, x2=260, y2=45)


def test_disk_cache_round_trip_preserves_bbox_and_source(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_cache_dir", tmp_path / "ocr")
    engine = OCREngine(mode="fast")
    region = OCRRegion("full_page", np.zeros((100, 200, 3), dtype=np.uint8), 0, 0, (0, 0, 200, 100))
    processed = np.zeros((100, 200, 3), dtype=np.uint8)
    items = [{
        "text": "Invoice",
        "confidence": 0.94,
        "page_number": 1,
        "bbox": BoundingBox(x1=10, y1=20, x2=110, y2=45),
        "source": "full_page",
        "line_index": 7,
    }]

    engine._write_disk_cache("round-trip", items, region, processed)
    cached = engine._read_disk_cache("round-trip")

    assert cached == [{
        "text": "Invoice",
        "confidence": 0.94,
        "page_number": 1,
        "bbox": {"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 45.0},
        "source": "full_page",
        "line_index": 7,
    }]


def test_old_cache_schema_is_invalidated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_cache_dir", tmp_path / "ocr")
    settings.ocr_cache_dir.mkdir(parents=True)
    (settings.ocr_cache_dir / "old.json").write_text(json.dumps({
        "schema_version": 1,
        "items": [{"text": "Invoice", "bbox": None}],
    }), encoding="utf-8")
    engine = OCREngine(mode="fast")

    assert engine._read_disk_cache("old") is None
    assert engine.last_timings["disk_cache_invalidated_reason"] == "incompatible schema 1"


def test_resized_full_page_bbox_restores_to_original_coordinates():
    region = OCRRegion("full_page", np.zeros((100, 200, 3), dtype=np.uint8), 0, 0, (0, 0, 200, 100))
    processed = np.zeros((200, 400, 3), dtype=np.uint8)

    item = _item_with_page_bbox(
        {"text": "Invoice", "bbox": BoundingBox(x1=40, y1=20, x2=80, y2=60)},
        region,
        1,
        processed,
        "full_page",
    )

    assert item["bbox"] == {"x1": 20.0, "y1": 10.0, "x2": 40.0, "y2": 30.0}
    assert item["source"] == "full_page"


def test_cropped_region_bbox_restores_to_original_coordinates():
    region = OCRRegion("line_items_table_area", np.zeros((100, 200, 3), dtype=np.uint8), 50, 100, (50, 100, 250, 200))
    processed = np.zeros((200, 400, 3), dtype=np.uint8)

    item = _item_with_page_bbox(
        {"text": "Item", "bbox": BoundingBox(x1=40, y1=20, x2=80, y2=60)},
        region,
        1,
        processed,
        "regional_fallback",
    )

    assert item["bbox"] == {"x1": 70.0, "y1": 110.0, "x2": 90.0, "y2": 130.0}
    assert item["source"] == "regional_fallback"


def test_ocr_line_serialization_keeps_bbox():
    line = OCRLine(
        text="Invoice",
        confidence=0.95,
        page_number=1,
        bbox=BoundingBox(x1=10.5, y1=20.0, x2=110.25, y2=45.75),
        source="full_page",
    )

    payload = line.model_dump(mode="json")

    assert payload["bbox"] == {"x1": 10.5, "y1": 20.0, "x2": 110.25, "y2": 45.75}
    assert payload["source"] == "full_page"


def test_ocr_engine_reuses_duplicate_region_results(monkeypatch):
    result = [[[[10, 10], [80, 10], [80, 25], [10, 25]], ("Invoice", 0.9)]]
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: DummyPaddle(result))
    engine = OCREngine(use_disk_cache=False)
    image = np.zeros((50, 100, 3), dtype=np.uint8)

    engine._run_paddle([image])
    first_misses = engine.cache_misses
    engine._run_paddle([image])

    assert first_misses > 0
    assert engine.cache_hits > 0


def test_fast_and_balanced_use_one_paddle_call_per_page(monkeypatch, tmp_path):
    result = [[[[10, 10], [80, 10], [80, 25], [10, 25]], ("Invoice", 0.9)]]
    paddle = DummyPaddle(result)
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: paddle)
    monkeypatch.setattr(settings, "ocr_cache_dir", tmp_path / "ocr")
    image = np.zeros((80, 120, 3), dtype=np.uint8)

    fast = OCREngine(mode="fast")
    fast.run([image])
    balanced = OCREngine(mode="balanced", refresh_cache=True)
    balanced.run([image])

    assert fast.last_timings["total_paddle_calls"] == 1
    assert balanced.last_timings["total_paddle_calls"] == 1


def test_accurate_keeps_regional_recovery_without_duplicate_calls(monkeypatch, tmp_path):
    result = [[[[10, 10], [80, 10], [80, 25], [10, 25]], ("Invoice", 0.9)]]
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: DummyPaddle(result))
    monkeypatch.setattr(settings, "ocr_cache_dir", tmp_path / "ocr")
    image = np.zeros((500, 800, 3), dtype=np.uint8)

    engine = OCREngine(mode="accurate")
    engine.run([image])

    assert engine.last_timings["total_paddle_calls"] >= 2
    assert engine.last_timings["region_ocr_calls"]["full_page"] == 1


def test_disk_cache_survives_engine_restart(monkeypatch, tmp_path):
    result = [[[[10, 10], [80, 10], [80, 25], [10, 25]], ("Invoice", 0.9)]]
    paddle = DummyPaddle(result)
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: paddle)
    monkeypatch.setattr(settings, "ocr_cache_dir", tmp_path / "ocr")
    image = np.zeros((80, 120, 3), dtype=np.uint8)

    OCREngine(mode="fast").run([image])
    second = OCREngine(mode="fast")
    second.run([image])

    assert second.last_timings["disk_cache_hits"] == 1
    assert second.last_timings["total_paddle_calls"] == 0


def test_ocr_cache_invalidates_when_source_or_mode_changes(monkeypatch, tmp_path):
    result = [[[[10, 10], [80, 10], [80, 25], [10, 25]], ("Invoice", 0.9)]]
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: DummyPaddle(result))
    monkeypatch.setattr(settings, "ocr_cache_dir", tmp_path / "ocr")
    image = np.zeros((80, 120, 3), dtype=np.uint8)
    changed = image.copy()
    changed[0, 0, 0] = 255

    OCREngine(mode="fast").run([image])
    changed_engine = OCREngine(mode="fast")
    changed_engine.run([changed])
    accurate_engine = OCREngine(mode="accurate")
    accurate_engine.run([image])

    assert changed_engine.last_timings["cache_misses"] == 1
    assert accurate_engine.last_timings["cache_misses"] >= 1
