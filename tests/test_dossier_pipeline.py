import numpy as np

from app.core.schemas import OCRLine, OCRResult
from app.services.file_loader import LoadedDocument
from app.services.pipeline_runner import process_dossier_file


class FakeDossierEngine:
    mode = "balanced"
    last_timings = {"ocr_mode": "balanced", "total_paddle_calls": 3}

    def __init__(self):
        self.run_calls = 0

    def run(self, images, embedded_text=""):
        self.run_calls += 1
        lines = [
            OCRLine(text="SOTACIB KAIROUAN Facture", confidence=0.9, page_number=1),
            OCRLine(text="RUSPINA IMPORT EXPORT AS PER INVOICE", confidence=0.9, page_number=2),
            OCRLine(text="TRADENET Declaration en detail", confidence=0.9, page_number=3),
        ]
        return OCRResult(raw_text="\n".join(line.text for line in lines), lines=lines, confidence=0.9, engine="fake", page_count=3)


def test_dossier_ocr_runs_once_and_existing_pipeline_receives_page_scoped_evidence(monkeypatch, tmp_path):
    source = LoadedDocument(
        source_file="dossier.pdf",
        extension=".pdf",
        images=[np.zeros((10, 10, 3), dtype=np.uint8) for _ in range(3)],
    )
    monkeypatch.setattr("app.services.pipeline_runner.load_document", lambda *args, **kwargs: source)
    calls = []

    def fake_process(document, ocr_result, **kwargs):
        calls.append({
            "images": len(document.images),
            "pages": tuple(sorted({line.page_number for line in ocr_result.lines})),
            "physical_page_numbers": kwargs["physical_page_numbers"],
        })
        return {"page": calls[-1]["pages"]}

    monkeypatch.setattr("app.services.pipeline_runner._process_ocr_document", fake_process)
    engine = FakeDossierEngine()

    result = process_dossier_file(tmp_path / "dossier.pdf", ocr_engine=engine)

    assert engine.run_calls == 1
    assert [item.group.pages for item in result.logical_documents] == [(1,), (2,), (3,)]
    assert calls == [
        {"images": 1, "pages": (1,), "physical_page_numbers": (1,)},
        {"images": 1, "pages": (2,), "physical_page_numbers": (2,)},
        {"images": 1, "pages": (3,), "physical_page_numbers": (3,)},
    ]


def test_existing_single_document_entry_point_signature_remains_compatible():
    from inspect import signature
    from app.services.pipeline_runner import process_document_file

    parameters = signature(process_document_file).parameters

    assert "path" in parameters
    assert "ocr_engine" in parameters
    assert "physical_page_numbers" not in parameters
