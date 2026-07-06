import pytest

from app.core.schemas import BoundingBox, Candidate, ExtractedInvoiceFields, OCRLine, ProcessInvoiceResponse, ValidationResult
from app.services.erp_mapper import build_erp_json, map_to_flat_erp
from app.services.field_enricher import build_expanded_fields, build_field_boxes
from app.services.layout_analyzer import LayoutAnalyzer


def test_layout_blocks_detect_logical_regions():
    blocks = [
        OCRLine(text="Vital Distribution", confidence=0.9, page_number=1, bbox=BoundingBox(x1=10, y1=10, x2=180, y2=40)),
        OCRLine(text="N Facture FAC-1", confidence=0.9, page_number=1, bbox=BoundingBox(x1=650, y1=20, x2=900, y2=50)),
        OCRLine(text="Client ABC", confidence=0.9, page_number=1, bbox=BoundingBox(x1=650, y1=250, x2=820, y2=280)),
        OCRLine(text="Designation Code Produit Qte", confidence=0.9, page_number=1, bbox=BoundingBox(x1=80, y1=430, x2=620, y2=460)),
        OCRLine(text="Total TTC 123.760 TND", confidence=0.9, page_number=1, bbox=BoundingBox(x1=650, y1=780, x2=920, y2=820)),
        OCRLine(text="RIB 05 012", confidence=0.9, page_number=1, bbox=BoundingBox(x1=80, y1=790, x2=260, y2=820)),
    ]
    layout_blocks = LayoutAnalyzer(blocks).detect_layout_blocks()
    types = {block.block_type for block in layout_blocks}
    assert "supplier" in types
    assert "products" in types
    assert "totals" in types
    assert "payment" in types


def test_preview_generation_writes_static_page(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from app.services.file_loader import LoadedDocument
    from app.services.preview_generator import generate_document_preview

    image = np.full((120, 80, 3), 255, dtype=np.uint8)
    document = LoadedDocument(source_file="unit-test.png", extension=".png", images=[image])
    preview = generate_document_preview(document)
    assert preview.pages
    assert preview.pages[0].url.startswith("/static/previews/")
    assert preview.pages[0].width == 80
    assert preview.pages[0].height == 120


def test_expanded_fields_and_field_boxes_from_candidates():
    fields = ExtractedInvoiceFields(invoice_number="FAC-1", amount_ttc=123.76)
    bbox = BoundingBox(x1=1, y1=2, x2=3, y2=4)
    candidates = {
        "invoice_number": [Candidate(field="invoice_number", value="FAC-1", score=0.9, source="test", page=1, line_index=2, bbox=bbox)]
    }
    expanded = build_expanded_fields(fields, candidates, {"invoice_number": 0.9}, "Email: demo@example.com")
    boxes = build_field_boxes(expanded)
    assert expanded["invoice_number"].bbox == bbox
    assert expanded["supplier_email"].value == "demo@example.com"
    assert boxes[0].field == "invoice_number"


def test_process_response_accepts_visual_extensions():
    sample_erp_json = build_erp_json(
        fields=ExtractedInvoiceFields(invoice_number="FAC-1"),
        validation=ValidationResult(),
        source_file="invoice.png",
        ocr_engine="Tesseract",
        confidence=0.9,
    )
    payload = ProcessInvoiceResponse(
        extracted_text="text",
        detected_fields=ExtractedInvoiceFields(),
        validation=ValidationResult(),
        erp_json=sample_erp_json,
        erp_export=map_to_flat_erp(sample_erp_json),
        layout_blocks=[],
        field_boxes=[],
        expanded_fields={},
    )
    assert payload.extracted_text == "text"
