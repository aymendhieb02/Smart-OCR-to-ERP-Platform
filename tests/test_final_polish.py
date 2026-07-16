from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_demo_documents_exist_and_are_listed():
    expected = {
        "demo_good_invoice.png",
        "demo_review_invoice.png",
        "demo_noisy_document.png",
    }
    demo_dir = ROOT / "dataset" / "demo"

    assert expected.issubset({path.name for path in demo_dir.iterdir()})

    client = TestClient(app)
    response = client.get("/demo-documents")

    assert response.status_code == 200
    payload = response.json()
    assert payload["demo_mode"] is True
    assert {doc["id"] for doc in payload["documents"]} == {"good", "review", "noisy"}
    assert all(doc["exists"] for doc in payload["documents"])


def test_static_ui_contains_final_polish_controls():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "Load good invoice" in html
    assert "Load review invoice" in html
    assert "Load noisy document" in html
    assert "status-guide" in html
    assert "Confidence is not accuracy" in html
    assert "Advanced" in html
    assert "/demo-documents/" in script
    assert "restoreReviewLineItem" in script
    assert "Advanced evidence" in script
    assert "setLoading(true" in script


def test_final_docs_exist_and_do_not_claim_unverified_accuracy():
    docs = [
        "docs/final_ui_audit.md",
        "docs/final_demo_walkthrough.md",
        "docs/architecture_overview.md",
        "docs/benchmark_summary.md",
        "docs/limitations.md",
        "docs/setup_windows.md",
        "docs/screenshots/README.md",
    ]

    for doc in docs:
        assert (ROOT / doc).exists(), doc

    benchmark = (ROOT / "docs" / "benchmark_summary.md").read_text(encoding="utf-8").lower()
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()

    assert "ocr confidence is not true accuracy" in benchmark
    assert "manually verified ground truth" in benchmark
    assert "ocr confidence is not true accuracy" in readme
