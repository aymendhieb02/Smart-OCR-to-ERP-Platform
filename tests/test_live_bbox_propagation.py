import json

import cv2
import numpy as np

from app.core.config import settings
from app.services.ocr_engine import OCREngine, _ocr_cache_key, _preprocess_for_region
from app.services.pipeline_runner import process_document_file
from app.services.table_regions import build_ocr_regions


class DictPaddle:
    def __init__(self):
        self.calls = 0

    def ocr(self, _image, cls=True):
        self.calls += 1
        return {
            "rec_texts": ["Invoice no: 13194726", "Total", "$ 640,12"],
            "rec_scores": [0.99, 0.98, 0.97],
            "rec_boxes": np.array([
                [20, 20, 220, 50],
                [20, 80, 90, 110],
                [120, 80, 220, 110],
            ], dtype=np.float32),
        }


def test_live_like_paddle_bbox_survives_cache_pipeline_and_json(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_cache_dir", tmp_path / "ocr")
    paddle = DictPaddle()
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: paddle)
    image_path = tmp_path / "invoice.png"
    cv2.imwrite(str(image_path), np.full((180, 260, 3), 255, dtype=np.uint8))

    first = process_document_file(
        image_path,
        original_filename="invoice.png",
        ocr_engine=OCREngine(mode="fast"),
        include_preview=True,
        persist_erp_json=False,
    )
    first_json = first.model_dump(mode="json")

    assert paddle.calls == 1
    assert first_json["ocr_blocks"][0]["bbox"] is not None
    assert first_json["ocr_blocks"][0]["page_width"] == 260
    assert first_json["ocr_blocks"][0]["page_height"] == 180
    assert first_json["ocr_blocks"][0]["coordinate_space"] == "original_page"
    assert first_json["extraction_debug"]["stage_timings"]["raw_boxes_count"] == 3
    assert first_json["extraction_debug"]["stage_timings"]["normalized_boxes_count"] == 3
    assert first_json["extraction_debug"]["stage_timings"]["public_boxes_count"] == 3

    second = process_document_file(
        image_path,
        original_filename="invoice.png",
        ocr_engine=OCREngine(mode="fast"),
        include_preview=True,
        persist_erp_json=False,
    )
    second_json = second.model_dump(mode="json")

    assert paddle.calls == 1
    assert second_json["extraction_debug"]["stage_timings"]["ocr_cache_source"] == "disk"
    assert second_json["ocr_blocks"][0]["bbox"] is not None
    assert second_json["ocr_blocks"][0]["page_width"] == 260
    assert second_json["ocr_blocks"][0]["page_height"] == 180
    assert second_json["ocr_blocks"][0]["coordinate_space"] == "original_page"


def test_text_only_schema_v2_cache_is_rejected_and_fresh_ocr_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_cache_dir", tmp_path / "ocr")
    settings.ocr_cache_dir.mkdir(parents=True)
    paddle = DictPaddle()
    monkeypatch.setattr("app.services.ocr_engine._get_paddle_ocr", lambda: paddle)
    image = np.full((80, 120, 3), 255, dtype=np.uint8)
    region = build_ocr_regions(image)[0]
    processed = _preprocess_for_region(region)
    cache_key = _ocr_cache_key(processed, region.image, region.name, 1, "fast", region.coordinates)
    (settings.ocr_cache_dir / f"{cache_key}.json").write_text(json.dumps({
        "schema_version": 2,
        "items": [
            {"text": "Invoice no: 13194726", "confidence": 0.99, "page_number": 1, "bbox": None, "source": "full_page"}
        ],
    }), encoding="utf-8")

    engine = OCREngine(mode="fast")
    lines = engine._run_paddle([image])

    assert paddle.calls == 1
    assert engine.last_timings["disk_cache_invalidated_reason"] == "cache had text but zero bboxes"
    assert any(line.bbox for line in lines)
