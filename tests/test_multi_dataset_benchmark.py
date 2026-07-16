from __future__ import annotations

import json
from pathlib import Path

from scripts import benchmark_multi_datasets as benchmark
from scripts import dataset_label_adapter as adapter
from scripts import generate_multi_dataset_report as report


def test_label_adapter_reads_nested_invoice_payload(tmp_path: Path) -> None:
    label = tmp_path / "sample.json"
    label.write_text(
        json.dumps(
            {
                "parsed_data": json.dumps(
                    {
                        "json": str(
                            {
                                "header": {
                                    "invoice_no": "INV-42",
                                    "invoice_date": "2026-07-01",
                                    "seller": "Vital Distribution",
                                    "client": "Pharma Plus",
                                },
                                "summary": {"total_gross_worth": "$ 104.75"},
                                "items": [{"item_desc": "Paracetamol"}],
                            }
                        )
                    }
                )
            }
        ),
        encoding="utf-8",
    )

    normalized = adapter.load_ground_truth(label)

    assert normalized["invoice_number"] == "INV-42"
    assert normalized["supplier_name"] == "Vital Distribution"
    assert normalized["customer_name"] == "Pharma Plus"
    assert normalized["invoice_date"] == "2026-07-01"
    assert normalized["amount_ttc"] == 104.75
    assert len(normalized["line_items"]) == 1


def test_discovery_matches_labels_in_exported_folders(tmp_path: Path) -> None:
    datasets_root = tmp_path / "datasets"
    image = datasets_root / "invoiceXpert" / "data" / "exported_parquet" / "images" / "test-0001.png"
    label = datasets_root / "invoiceXpert" / "data" / "exported_parquet" / "labels" / "test-0001.json"
    image.parent.mkdir(parents=True, exist_ok=True)
    label.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"fake")
    label.write_text('{"invoice_number": "A-1"}', encoding="utf-8")

    datasets = benchmark.discover_datasets(datasets_root)

    assert "invoiceXpert" in datasets
    assert datasets["invoiceXpert"][0].label_path == label


def test_name_and_amount_comparison_helpers() -> None:
    assert benchmark.compare_invoice_numbers("INV- 001", "inv001") is True
    assert benchmark.compare_amounts("104.751", "104.75") is True
    assert benchmark.compare_dates("06/05/2026", "2026-05-06") is True


def test_report_generation_writes_global_summary(tmp_path: Path) -> None:
    output = tmp_path / "report"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        handle.write(
            "dataset_name,split,filename,file_path,label_path,has_ground_truth,status,error_message,error_category,processing_time_seconds,document_type_pred,validation_status,erp_export_allowed,ocr_confidence,overall_confidence,supplier_name_pred,customer_name_pred,invoice_number_pred,invoice_date_pred,due_date_pred,currency_pred,amount_ht_pred,tva_amount_pred,amount_ttc_pred,tax_rate_pred,line_items_count_pred,has_supplier_pred,has_customer_pred,has_invoice_number_pred,has_invoice_date_pred,has_amount_ttc_pred,has_line_items_pred,supplier_name_true,customer_name_true,invoice_number_true,invoice_date_true,amount_ttc_true,document_type_true,supplier_name_correct,customer_name_correct,invoice_number_correct,invoice_date_correct,amount_ttc_correct,document_type_correct,prediction_path\n"
        )
        handle.write(
            "invoiceXpert,test,doc1.png,C:\\doc1.png,,False,success,,ok,1.2,invoice,valid,True,0.9,0.8,Supplier,Customer,INV-1,2026-01-01,,TND,10,2,12,19,1,True,True,True,True,True,True,,,,,,, , , , , , ,C:\\pred1.json\n".replace(" ,", ",")
        )

    report.generate_reports(output)

    assert (output / "global_summary.json").exists()
    summary = json.loads((output / "global_summary.json").read_text(encoding="utf-8"))
    assert summary["total_documents_tested"] == 1
