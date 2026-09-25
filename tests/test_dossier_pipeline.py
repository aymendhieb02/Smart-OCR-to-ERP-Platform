import numpy as np

from app.core.schemas import BoundingBox, OCRLine, OCRResult
from app.services.file_loader import LoadedDocument
from app.services.pipeline_runner import process_dossier_file


class FakeDossierEngine:
    mode = "balanced"
    last_timings = {"ocr_mode": "balanced", "total_paddle_calls": 3}

    def __init__(self):
        self.run_calls = 0
        self.fallback_calls = []

    def run(self, images, embedded_text=""):
        self.run_calls += 1
        lines = [
            OCRLine(text="SOTACIB KAIROUAN Facture", confidence=0.9, page_number=1),
            OCRLine(text="RUSPINA IMPORT EXPORT AS PER INVOICE", confidence=0.9, page_number=2),
            OCRLine(text="TRADENET Declaration en detail", confidence=0.9, page_number=3),
        ]
        return OCRResult(raw_text="\n".join(line.text for line in lines), lines=lines, confidence=0.9, engine="fake", page_count=3)

    def run_fallback_regions(self, images, region_names, *, page_numbers=None):
        self.fallback_calls.append((len(images), tuple(region_names), tuple(page_numbers or ())))
        return []


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
            "document_family": kwargs["document_family"],
        })
        return {"page": calls[-1]["pages"]}

    monkeypatch.setattr("app.services.pipeline_runner._process_ocr_document", fake_process)
    engine = FakeDossierEngine()

    result = process_dossier_file(tmp_path / "dossier.pdf", ocr_engine=engine)

    assert engine.run_calls == 1
    assert [item.group.pages for item in result.logical_documents] == [(1,), (2,), (3,)]
    assert engine.fallback_calls == [
        (1, ("header_parties",), (3,)),
        (1, ("tradenet_declaration_header", "tradenet_parties", "tradenet_financial"), (3,)),
        (1, ("tradenet_customs_total",), (3,)),
    ]
    assert calls == [
        {"images": 1, "pages": (1,), "physical_page_numbers": (1,), "document_family": "sotacib_kairouan_grey_invoice_v1"},
        {"images": 1, "pages": (2,), "physical_page_numbers": (2,), "document_family": "ruspina_reinvoice_v1"},
        {"images": 1, "pages": (3,), "physical_page_numbers": (3,), "document_family": "customs_tradenet_v1"},
    ]


def test_tradenet_family_propagates_but_invoice_families_do_not_trigger_header_fallback(monkeypatch, tmp_path):
    source = LoadedDocument(source_file="synthetic.pdf", extension=".pdf", images=[np.zeros((100, 120, 3), dtype=np.uint8) for _ in range(3)])
    monkeypatch.setattr("app.services.pipeline_runner.load_document", lambda *args, **kwargs: source)
    captured = {}

    def fake_process(document, ocr_result, **kwargs):
        captured.update(kwargs)
        return {"page": 3}

    monkeypatch.setattr("app.services.pipeline_runner._process_ocr_document", fake_process)
    engine = FakeDossierEngine()
    engine.run = lambda images, embedded_text="": OCRResult(
        raw_text="TUNETRADNTm DaMach Exportaleur importateut Déclaration 123456 04-02-2025",
        lines=[
            OCRLine(text="TUNETRADNTm DaMach", confidence=0.9, page_number=3, bbox=BoundingBox(x1=25, y1=3, x2=55, y2=8), page_width=120, page_height=100),
            OCRLine(text="Exportaleur", confidence=0.9, page_number=3, bbox=BoundingBox(x1=30, y1=9, x2=48, y2=14), page_width=120, page_height=100),
            OCRLine(text="importateut", confidence=0.9, page_number=3, bbox=BoundingBox(x1=30, y1=11, x2=48, y2=16), page_width=120, page_height=100),
            OCRLine(text="Déclaration", confidence=0.9, page_number=3, bbox=BoundingBox(x1=68, y1=5, x2=84, y2=10), page_width=120, page_height=100),
            OCRLine(text="123456", confidence=0.9, page_number=3, bbox=BoundingBox(x1=70, y1=13, x2=80, y2=18), page_width=120, page_height=100),
            OCRLine(text="04-02-2025", confidence=0.9, page_number=3, bbox=BoundingBox(x1=82, y1=13, x2=105, y2=18), page_width=120, page_height=100),
        ],
        confidence=0.9,
        engine="fake",
        page_count=3,
    )

    result = process_dossier_file(tmp_path / "synthetic.pdf", ocr_engine=engine)

    custom_document = next(item for item in result.logical_documents if item.group.pages == (3,))
    assert custom_document.group.document_family == "customs_tradenet_v1"
    assert captured["document_family"] == "customs_tradenet_v1"
    assert engine.fallback_calls == [
        (1, ("header_parties",), (3,)),
        (1, ("tradenet_declaration_header", "tradenet_parties", "tradenet_financial"), (3,)),
        (1, ("tradenet_customs_total",), (3,)),
    ]


def test_suspected_customs_page_with_unknown_family_gets_bounded_header_fallback(monkeypatch, tmp_path):
    source = LoadedDocument(source_file="synthetic.pdf", extension=".pdf", images=[np.zeros((100, 120, 3), dtype=np.uint8) for _ in range(3)])
    monkeypatch.setattr("app.services.pipeline_runner.load_document", lambda *args, **kwargs: source)
    captured = []

    def fake_process(document, ocr_result, **kwargs):
        captured.append((kwargs["physical_page_numbers"], kwargs["document_family"]))
        if kwargs["physical_page_numbers"] == (3,):
            assert kwargs["fixed_customs_form"] is True
        return {"page": kwargs["physical_page_numbers"]}

    monkeypatch.setattr("app.services.pipeline_runner._process_ocr_document", fake_process)
    engine = FakeDossierEngine()
    engine.run = lambda images, embedded_text="": OCRResult(
        raw_text="SOTACIB KAIROUAN Facture\nRUSPINA IMPORT EXPORT AS PER INVOICE\nExportateur importateur Déclaration 123456 04-02-2025",
        lines=[
            OCRLine(text="SOTACIB KAIROUAN Facture", confidence=0.9, page_number=1),
            OCRLine(text="RUSPINA IMPORT EXPORT AS PER INVOICE", confidence=0.9, page_number=2),
            OCRLine(text="Exportateur", confidence=0.9, page_number=3, bbox=BoundingBox(x1=28, y1=9, x2=50, y2=14), page_width=120, page_height=100),
            OCRLine(text="importateur", confidence=0.9, page_number=3, bbox=BoundingBox(x1=28, y1=11, x2=50, y2=16), page_width=120, page_height=100),
            OCRLine(text="Déclaration", confidence=0.9, page_number=3, bbox=BoundingBox(x1=68, y1=5, x2=90, y2=10), page_width=120, page_height=100),
            OCRLine(text="123456", confidence=0.9, page_number=3, bbox=BoundingBox(x1=70, y1=13, x2=80, y2=18), page_width=120, page_height=100),
            OCRLine(text="04-02-2025", confidence=0.9, page_number=3, bbox=BoundingBox(x1=82, y1=13, x2=105, y2=18), page_width=120, page_height=100),
        ],
        confidence=0.9,
        engine="fake",
        page_count=3,
    )

    process_dossier_file(tmp_path / "synthetic.pdf", ocr_engine=engine)

    assert engine.fallback_calls == [
        (1, ("header_parties",), (3,)),
        (1, ("tradenet_declaration_header", "tradenet_parties", "tradenet_financial"), (3,)),
        (1, ("tradenet_customs_total",), (3,)),
    ]
    assert captured == [((1,), "sotacib_kairouan_grey_invoice_v1"), ((2,), "ruspina_reinvoice_v1"), ((3,), None)]


def test_high_confidence_labeled_customs_total_skips_narrow_retry(monkeypatch, tmp_path):
    source = LoadedDocument(source_file="synthetic.pdf", extension=".pdf", images=[np.zeros((1600, 1200, 3), dtype=np.uint8)])
    monkeypatch.setattr("app.services.pipeline_runner.load_document", lambda *args, **kwargs: source)
    monkeypatch.setattr("app.services.pipeline_runner._process_ocr_document", lambda *args, **kwargs: {"ok": True})
    engine = FakeDossierEngine()
    engine.run = lambda images, embedded_text="": OCRResult(
        raw_text="TRADENET Exportateur Importateur Déclaration 123456 04-02-2025",
        lines=[
            OCRLine(text="TRADENET", confidence=0.9, page_number=1, bbox=BoundingBox(x1=100, y1=30, x2=230, y2=50), page_width=1200, page_height=1600),
            OCRLine(text="Exportateur", confidence=0.9, page_number=1, bbox=BoundingBox(x1=270, y1=65, x2=350, y2=82), page_width=1200, page_height=1600),
            OCRLine(text="Importateur", confidence=0.9, page_number=1, bbox=BoundingBox(x1=270, y1=170, x2=350, y2=190), page_width=1200, page_height=1600),
            OCRLine(text="123456", confidence=0.9, page_number=1, bbox=BoundingBox(x1=680, y1=100, x2=730, y2=120), page_width=1200, page_height=1600),
            OCRLine(text="04-02-2025", confidence=0.9, page_number=1, bbox=BoundingBox(x1=790, y1=100, x2=870, y2=120), page_width=1200, page_height=1600),
        ],
        confidence=0.9, engine="fake", page_count=1,
    )

    def fallback(images, region_names, *, page_numbers=None):
        engine.fallback_calls.append((len(images), tuple(region_names), tuple(page_numbers or ())))
        if "tradenet_financial" not in region_names:
            return []
        return [
            OCRLine(text="Valeur douane totale (en dinars)", confidence=0.98, page_number=1, bbox=BoundingBox(x1=980, y1=438, x2=1100, y2=455), source="regional_fallback"),
            OCRLine(text="38548.750", confidence=0.98, page_number=1, bbox=BoundingBox(x1=980, y1=458, x2=1050, y2=475), source="regional_fallback"),
        ]

    engine.run_fallback_regions = fallback
    process_dossier_file(tmp_path / "synthetic.pdf", ocr_engine=engine)
    assert engine.fallback_calls == [
        (1, ("header_parties",), (1,)),
        (1, ("tradenet_declaration_header", "tradenet_parties", "tradenet_financial"), (1,)),
    ]


def test_existing_single_document_entry_point_signature_remains_compatible():
    from inspect import signature
    from app.services.pipeline_runner import process_document_file

    parameters = signature(process_document_file).parameters

    assert "path" in parameters
    assert "ocr_engine" in parameters
    assert "physical_page_numbers" not in parameters
