import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.core.schemas import BoundingBox, OCRLine, OCRResult
from app.services.performance_timer import PipelineTimer
from app.services.pipeline_runner import process_document_file


class FakeEngine:
    mode = "fast"
    last_timings = {"ocr_mode": "fake", "disk_cache_hit": False}

    def run(self, images, embedded_text=""):
        lines = [
            OCRLine(
                text="Invoice no: INV-100",
                confidence=0.99,
                page_number=1,
                line_index=0,
                bbox=BoundingBox(x1=10, y1=10, x2=180, y2=30),
                page_width=300,
                page_height=180,
                coordinate_space="original_page",
            ),
            OCRLine(
                text="Date: 01/07/2026",
                confidence=0.98,
                page_number=1,
                line_index=1,
                bbox=BoundingBox(x1=10, y1=40, x2=170, y2=60),
                page_width=300,
                page_height=180,
                coordinate_space="original_page",
            ),
            OCRLine(
                text="Supplier: ACME SERVICES",
                confidence=0.97,
                page_number=1,
                line_index=2,
                bbox=BoundingBox(x1=10, y1=70, x2=220, y2=90),
                page_width=300,
                page_height=180,
                coordinate_space="original_page",
            ),
            OCRLine(
                text="Total TTC 120.00",
                confidence=0.97,
                page_number=1,
                line_index=3,
                bbox=BoundingBox(x1=160, y1=140, x2=260, y2=160),
                page_width=300,
                page_height=180,
                coordinate_space="original_page",
            ),
        ]
        return OCRResult(
            raw_text="\n".join(line.text for line in lines),
            lines=lines,
            confidence=0.97,
            engine="FakeOCR",
            page_count=1,
        )

    def run_fallback_regions(self, images, requested_fallbacks):
        return []


def test_timed_pipeline_collects_records_without_changing_public_response(tmp_path) -> None:
    image_path = tmp_path / "invoice.png"
    cv2.imwrite(str(image_path), np.full((180, 300, 3), 255, dtype=np.uint8))
    timer = PipelineTimer(enabled=True)

    response = process_document_file(
        image_path,
        original_filename=image_path.name,
        ocr_engine=FakeEngine(),
        include_preview=False,
        persist_erp_json=False,
        timing_recorder=timer,
    )
    public_payload = response.model_dump(mode="json")
    timing_result = timer.to_result(document=image_path.name, validation_status=response.validation.status)

    assert "total_pipeline" in timing_result["stages"]
    assert "file_loading" in timing_result["stages"]
    assert "image_decoding" in timing_result["stages"]
    assert "ocr_engine_initialization" in timing_result["stages"]
    assert "response_preparation" in timing_result["stages"]
    assert "performance_timings" not in public_payload
    assert "timing_result" not in public_payload
