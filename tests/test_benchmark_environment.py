from __future__ import annotations

import json
from pathlib import Path

from scripts import benchmark_multi_datasets as benchmark
from scripts import generate_multi_dataset_report as report


def test_available_ocr_engines_requires_real_engine() -> None:
    modules = {
        "PaddleOCR": {"available": False},
        "pytesseract": {"available": True},
        "OpenCV": {"available": True},
        "Pillow": {"available": True},
    }

    assert benchmark.available_ocr_engines(modules, tesseract_available=False) == []
    assert benchmark.available_ocr_engines(modules, tesseract_available=True) == ["Tesseract"]


def test_format_environment_status_mentions_broken_env() -> None:
    status = {
        "python_executable": "python.exe",
        "virtualenv": "",
        "modules": {
            "PaddleOCR": {"available": False},
            "PaddlePaddle": {"available": False},
            "pytesseract": {"available": False},
            "OpenCV": {"available": True},
            "Pillow": {"available": True},
            "PyMuPDF": {"available": True},
            "pyarrow": {"available": False},
            "pandas": {"available": True},
            "matplotlib": {"available": True},
            "rapidfuzz": {"available": False},
            "tqdm": {"available": True},
        },
        "tesseract_executable_path": "",
        "status": "BROKEN",
        "failure_message": "BENCHMARK ABORTED: no OCR engine is available.",
    }

    rendered = benchmark.format_environment_status(status)

    assert "Environment status: BROKEN" in rendered
    assert "BENCHMARK ABORTED" in rendered


def test_global_summary_marks_benchmark_invalid_when_everything_is_no_text(tmp_path: Path) -> None:
    output = tmp_path / "report"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        handle.write(
            "dataset_name,split,filename,file_path,label_path,has_ground_truth,status,error_message,error_category,processing_time_seconds,document_type_pred,validation_status,erp_export_allowed,ocr_confidence,overall_confidence,supplier_name_pred,customer_name_pred,invoice_number_pred,invoice_date_pred,due_date_pred,currency_pred,amount_ht_pred,tva_amount_pred,amount_ttc_pred,tax_rate_pred,line_items_count_pred,has_supplier_pred,has_customer_pred,has_invoice_number_pred,has_invoice_date_pred,has_amount_ttc_pred,has_line_items_pred,supplier_name_true,customer_name_true,invoice_number_true,invoice_date_true,amount_ttc_true,document_type_true,supplier_name_correct,customer_name_correct,invoice_number_correct,invoice_date_correct,amount_ttc_correct,document_type_correct,prediction_path\n"
        )
        handle.write(
            "invoiceXpert,test,doc1.png,C:\\doc1.png,,False,error,No text could be extracted from the invoice,no text extracted,1.2,,,,,,,,,,,,,,,,False,False,False,False,False,False,,,,,,,,,,,,,C:\\pred1.json\n"
        )

    report.generate_reports(output)
    summary = json.loads((output / "global_summary.json").read_text(encoding="utf-8"))

    assert summary["benchmark_invalid"] is True
    assert summary["hardest_dataset"] is None
    assert "OCR engine unavailable" in summary["invalid_reason"]
