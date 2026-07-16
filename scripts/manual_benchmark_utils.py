from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dateutil import parser as date_parser


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK_ROOT = ROOT / "dataset" / "manual_ground_truth_benchmark"
DEFAULT_DATASETS_ROOT = Path(r"D:\Stage_mr_f\sources\datasets")
SUPPORTED_DOCUMENT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".pdf"}
NAME_THRESHOLD = 85
AMOUNT_ABS_TOLERANCE = 0.02
AMOUNT_REL_TOLERANCE = 0.005

FIELD_NAMES = [
    "document_type",
    "supplier_name",
    "customer_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "amount_ht",
    "tax_amount",
    "amount_ttc",
    "tax_rate",
]

REQUIRED_FIELD_NAMES = [
    "supplier_name",
    "customer_name",
    "invoice_number",
    "invoice_date",
    "currency",
    "amount_ttc",
]

FINANCIAL_FIELD_NAMES = ["amount_ht", "tax_amount", "amount_ttc", "tax_rate"]


LABEL_TEMPLATE: dict[str, Any] = {
    "filename": "",
    "source_path": "",
    "document_type": None,
    "supplier_name": None,
    "customer_name": None,
    "invoice_number": None,
    "invoice_date": None,
    "due_date": None,
    "currency": None,
    "amount_ht": None,
    "tax_amount": None,
    "amount_ttc": None,
    "tax_rate": None,
    "line_items": [
        {
            "reference": None,
            "description": None,
            "quantity": None,
            "unit": None,
            "unit_price": None,
            "tax_rate": None,
            "line_total_ht": None,
            "line_total_ttc": None,
        }
    ],
    "notes": "",
    "verified_by_human": False,
}


@dataclass(frozen=True)
class BenchmarkDocument:
    filename: str
    image_path: Path
    label_path: Path
    source_path: Path
    dataset: str
    document_type_hint: str = "invoice"


def ensure_benchmark_structure(benchmark_root: Path) -> None:
    for name in ("images", "labels", "predictions", "reports"):
        (benchmark_root / name).mkdir(parents=True, exist_ok=True)


def safe_json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=safe_json_default), encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_stem(value: str) -> str:
    stem = Path(value).stem or "document"
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in stem)
    return cleaned[:80].strip("_") or "document"


def discover_source_documents(datasets_root: Path) -> list[Path]:
    if not datasets_root.exists():
        return []
    return sorted(
        [
            path
            for path in datasets_root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS
        ],
        key=lambda item: str(item).lower(),
    )


def dataset_name_for(path: Path, datasets_root: Path) -> str:
    try:
        return path.relative_to(datasets_root).parts[0]
    except Exception:
        return path.parent.name or "unknown"


def infer_document_profile(path: Path, datasets_root: Path) -> dict[str, Any]:
    dataset = dataset_name_for(path, datasets_root)
    joined = " ".join(part.lower() for part in path.parts)
    name = path.name.lower()
    document_type = "receipt" if "receipt" in joined else "invoice"
    table_heavy = any(token in joined for token in ("table", "fatura", "invoicexpert", "donut"))
    noisy = any(token in joined for token in ("low", "noise", "scan", "photo", "receipt"))
    multilingual = any(token in joined for token in ("fatura", "facture", "arab", "tunisie", "tn", "french"))
    side_by_side = any(token in joined for token in ("side", "invoicexpert", "fatura")) or name.endswith(".pdf")
    if "high-quality-invoice-images-for-ocr" in joined:
        image_quality = "high"
    else:
        image_quality = "low" if noisy else ("medium" if path.suffix.lower() in {".jpg", ".jpeg"} else "high")
    return {
        "dataset": dataset,
        "document_type": document_type,
        "image_quality": image_quality,
        "table_heavy": table_heavy,
        "multilingual": multilingual,
        "supplier_customer_side_by_side": side_by_side,
    }


def build_selection_candidates(datasets_root: Path, limit: int = 250) -> list[dict[str, Any]]:
    docs = discover_source_documents(datasets_root)
    rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    grouped: dict[str, list[Path]] = {}
    for path in docs:
        grouped.setdefault(dataset_name_for(path, datasets_root), []).append(path)
    per_dataset_limit = max(20, limit // max(1, len(grouped)) + 8)
    balanced_docs: list[Path] = []
    for dataset in sorted(grouped):
        balanced_docs.extend(grouped[dataset][:per_dataset_limit])
    for path in balanced_docs:
        key = f"{path.name.lower()}::{path.stat().st_size if path.exists() else 0}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        profile = infer_document_profile(path, datasets_root)
        recommended = _is_recommended_candidate(profile)
        rows.append({
            "source_path": str(path.resolve()),
            "dataset": profile["dataset"],
            "document_type": profile["document_type"],
            "image_quality": profile["image_quality"],
            "table_heavy": profile["table_heavy"],
            "multilingual": profile["multilingual"],
            "supplier_customer_side_by_side": profile["supplier_customer_side_by_side"],
            "recommended": recommended,
            "selection_reason": selection_reason(profile),
        })
    rows.sort(key=lambda row: (not row["recommended"], row["dataset"], row["source_path"]))
    return rows[:limit]


def _is_recommended_candidate(profile: dict[str, Any]) -> bool:
    return bool(
        profile["document_type"] == "receipt"
        or profile["table_heavy"]
        or profile["image_quality"] in {"low", "medium"}
        or profile["supplier_customer_side_by_side"]
        or profile["multilingual"]
    )


def selection_reason(profile: dict[str, Any]) -> str:
    reasons = []
    if profile["document_type"] == "receipt":
        reasons.append("receipt coverage")
    if profile["table_heavy"]:
        reasons.append("table-heavy layout")
    if profile["image_quality"] in {"low", "medium"}:
        reasons.append(f"{profile['image_quality']} image quality")
    if profile["supplier_customer_side_by_side"]:
        reasons.append("supplier/customer layout")
    if profile["multilingual"]:
        reasons.append("multilingual signal")
    return "; ".join(reasons) or "easy invoice baseline"


def select_representative_documents(candidates: list[dict[str, Any]], target_count: int = 12) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    dataset_counts: dict[str, int] = {}

    buckets = [
        ("easy invoice", 3, lambda row: row["document_type"] == "invoice" and str(row["image_quality"]) == "high" and not truthy(row["table_heavy"])),
        ("table-heavy invoice", 3, lambda row: row["document_type"] == "invoice" and truthy(row["table_heavy"])),
        ("noisy or low-quality", 2, lambda row: str(row["image_quality"]) in {"low", "medium"}),
        ("receipt", 1, lambda row: row["document_type"] == "receipt"),
        ("side-by-side parties", 1, lambda row: truthy(row["supplier_customer_side_by_side"])),
        ("multilingual", 2, lambda row: truthy(row["multilingual"])),
    ]
    for bucket_name, count, predicate in buckets:
        bucket_candidates = [row for row in candidates if row["source_path"] not in used_sources and predicate(row)]
        bucket_candidates.sort(key=lambda row: (dataset_counts.get(row.get("dataset", ""), 0), row.get("dataset", ""), row["source_path"]))
        for row in bucket_candidates[:count]:
            source = row["source_path"]
            item = dict(row)
            item["bucket"] = bucket_name
            selected.append(item)
            used_sources.add(source)
            dataset_counts[item.get("dataset", "")] = dataset_counts.get(item.get("dataset", ""), 0) + 1
    for row in candidates:
        if len(selected) >= target_count:
            break
        if row["source_path"] in used_sources:
            continue
        item = dict(row)
        item["bucket"] = "balanced fill"
        selected.append(item)
        used_sources.add(row["source_path"])
        dataset_counts[item.get("dataset", "")] = dataset_counts.get(item.get("dataset", ""), 0) + 1
    return selected[:target_count]


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def create_manifest_and_blank_labels(benchmark_root: Path, selected_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_benchmark_structure(benchmark_root)
    manifest_docs: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for row in selected_rows:
        source_path = Path(row["source_path"])
        if not source_path.exists():
            continue
        digest = file_hash(source_path)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        index = len(manifest_docs) + 1
        filename = f"{index:02d}_{safe_stem(source_path.name)}{source_path.suffix.lower()}"
        image_path = benchmark_root / "images" / filename
        label_path = benchmark_root / "labels" / f"{Path(filename).stem}.json"
        if not image_path.exists():
            shutil.copy2(source_path, image_path)
        if not label_path.exists():
            label = dict(LABEL_TEMPLATE)
            label["filename"] = filename
            label["source_path"] = str(source_path.resolve())
            write_json(label_path, label)
        manifest_docs.append({
            "filename": filename,
            "source_path": str(source_path.resolve()),
            "dataset": row.get("dataset", ""),
            "document_type_hint": row.get("document_type", "invoice"),
            "bucket": row.get("bucket", ""),
            "image_quality": row.get("image_quality", ""),
            "table_heavy": truthy(row.get("table_heavy")),
            "multilingual": truthy(row.get("multilingual")),
            "supplier_customer_side_by_side": truthy(row.get("supplier_customer_side_by_side")),
            "label_path": str(label_path.relative_to(benchmark_root)),
            "image_path": str(image_path.relative_to(benchmark_root)),
            "file_hash": digest,
        })
    manifest = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "benchmark_root": str(benchmark_root.resolve()),
        "documents": manifest_docs,
        "label_schema_version": 1,
        "requires_verified_by_human": True,
    }
    write_json(benchmark_root / "manifest.json", manifest)
    return manifest


def load_manifest_documents(benchmark_root: Path) -> list[BenchmarkDocument]:
    manifest_path = benchmark_root / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = read_json(manifest_path)
    documents: list[BenchmarkDocument] = []
    for item in manifest.get("documents", []):
        image_path = benchmark_root / item["image_path"]
        label_path = benchmark_root / item["label_path"]
        documents.append(BenchmarkDocument(
            filename=item["filename"],
            image_path=image_path,
            label_path=label_path,
            source_path=Path(item["source_path"]),
            dataset=item.get("dataset", ""),
            document_type_hint=item.get("document_type_hint", "invoice"),
        ))
    return documents


def validate_verified_label(label_path: Path) -> dict[str, Any]:
    label = read_json(label_path)
    if label.get("verified_by_human") is not True:
        raise ValueError(f"Label is not verified by a human: {label_path}")
    return label


def normalize_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = "".join(char.lower() if char.isalnum() else " " for char in text)
    return " ".join(text.split())


def normalize_invoice_number(value: Any) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def normalize_currency(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().upper()
    mapping = {"$": "USD", "US$": "USD", "€": "EUR", "EUR": "EUR", "TND": "TND", "DT": "TND", "د.ت": "TND", "£": "GBP"}
    return mapping.get(text, text[:3] if len(text) >= 3 else text)


def normalize_date(value: Any) -> tuple[str | None, bool]:
    if value in (None, ""):
        return None, False
    if isinstance(value, date):
        return value.isoformat(), False
    candidates: set[str] = set()
    for dayfirst in (False, True):
        try:
            candidates.add(date_parser.parse(str(value), fuzzy=True, dayfirst=dayfirst).date().isoformat())
        except Exception:
            continue
    if not candidates:
        return None, False
    return sorted(candidates)[0], len(candidates) > 1


def normalize_amount(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = "".join(char for char in str(value) if char.isdigit() or char in ",.-")
    if not text:
        return None
    if text.count(",") and text.count("."):
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(",") and not text.count("."):
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def amount_correct(predicted: Any, truth: Any) -> bool | None:
    pred = normalize_amount(predicted)
    actual = normalize_amount(truth)
    if actual is None:
        return None
    if pred is None:
        return False
    diff = abs(pred - actual)
    return diff <= AMOUNT_ABS_TOLERANCE or diff <= abs(actual) * AMOUNT_REL_TOLERANCE


def name_similarity(predicted: Any, truth: Any) -> float | None:
    pred = normalize_text(predicted)
    actual = normalize_text(truth)
    if not actual:
        return None
    if not pred:
        return 0.0
    try:
        from rapidfuzz.fuzz import ratio  # type: ignore

        return float(ratio(pred, actual))
    except Exception:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, pred, actual).ratio() * 100


def name_correct(predicted: Any, truth: Any, threshold: int = NAME_THRESHOLD) -> bool | None:
    score = name_similarity(predicted, truth)
    if score is None:
        return None
    return score >= threshold


def scalar_field_correct(field: str, predicted: Any, truth: Any) -> tuple[bool | None, dict[str, Any]]:
    if truth in (None, "", []):
        return None, {"reason": "ground truth missing"}
    if field in {"supplier_name", "customer_name"}:
        score = name_similarity(predicted, truth)
        return (score is not None and score >= NAME_THRESHOLD), {"similarity": score}
    if field == "invoice_number":
        return normalize_invoice_number(predicted) == normalize_invoice_number(truth), {}
    if field in {"invoice_date", "due_date"}:
        pred_date, pred_ambiguous = normalize_date(predicted)
        truth_date, truth_ambiguous = normalize_date(truth)
        if truth_date is None:
            return None, {"reason": "ground truth date invalid"}
        return pred_date == truth_date, {"predicted_normalized": pred_date, "truth_normalized": truth_date, "ambiguous": pred_ambiguous or truth_ambiguous}
    if field == "currency":
        return normalize_currency(predicted) == normalize_currency(truth), {}
    if field in {"amount_ht", "tax_amount", "amount_ttc", "tax_rate"}:
        return amount_correct(predicted, truth), {}
    return normalize_text(predicted) == normalize_text(truth), {}


def prediction_fields_from_response(response: Any) -> dict[str, Any]:
    fields = response.detected_fields
    return {
        "document_type": response.document_classification.document_type if response.document_classification else None,
        "supplier_name": fields.supplier_name,
        "customer_name": fields.customer_name,
        "invoice_number": fields.invoice_number,
        "invoice_date": fields.invoice_date.isoformat() if fields.invoice_date else None,
        "due_date": fields.due_date.isoformat() if fields.due_date else None,
        "currency": fields.currency,
        "amount_ht": fields.amount_ht,
        "tax_amount": fields.tva_amount,
        "amount_ttc": fields.amount_ttc,
        "tax_rate": fields.tax_rate,
    }


def line_items_from_response(response: Any) -> list[dict[str, Any]]:
    items = response.all_line_items or response.detected_fields.line_items or []
    return [item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) for item in items]


def compare_line_items(predicted: list[dict[str, Any]], truth: list[dict[str, Any]]) -> dict[str, Any]:
    truth_rows = [row for row in truth if meaningful_line_item(row)]
    pred_rows = [row for row in predicted if meaningful_line_item(row)]
    if not truth_rows:
        return {
            "applicable": False,
            "truth_count": 0,
            "predicted_count": len(pred_rows),
            "matched_count": 0,
            "precision": None,
            "recall": None,
            "f1": None,
            "correct_count": None,
            "description_accuracy": None,
            "quantity_accuracy": None,
            "unit_price_accuracy": None,
            "line_total_accuracy": None,
            "matches": [],
        }
    matches = greedy_match_line_items(pred_rows, truth_rows)
    matched_pred = {match["predicted_index"] for match in matches}
    precision = len(matches) / len(pred_rows) if pred_rows else 0.0
    recall = len(matches) / len(truth_rows) if truth_rows else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    metrics = {
        "description_accuracy": field_accuracy(matches, "description_correct"),
        "quantity_accuracy": field_accuracy(matches, "quantity_correct"),
        "unit_price_accuracy": field_accuracy(matches, "unit_price_correct"),
        "line_total_accuracy": field_accuracy(matches, "line_total_correct"),
    }
    return {
        "applicable": True,
        "truth_count": len(truth_rows),
        "predicted_count": len(pred_rows),
        "matched_count": len(matches),
        "unmatched_predicted_count": len(pred_rows) - len(matched_pred),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "correct_count": len(pred_rows) == len(truth_rows),
        **metrics,
        "matches": matches,
    }


def meaningful_line_item(row: dict[str, Any]) -> bool:
    return any(row.get(key) not in (None, "", []) for key in ("description", "reference", "quantity", "unit_price", "line_total_ht", "line_total_ttc", "total"))


def greedy_match_line_items(pred_rows: list[dict[str, Any]], truth_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[tuple[float, int, int]] = []
    for pred_index, pred in enumerate(pred_rows):
        for truth_index, truth in enumerate(truth_rows):
            score = line_item_score(pred, truth)
            if score >= 0.45:
                scored.append((score, pred_index, truth_index))
    scored.sort(reverse=True)
    used_pred: set[int] = set()
    used_truth: set[int] = set()
    matches: list[dict[str, Any]] = []
    for score, pred_index, truth_index in scored:
        if pred_index in used_pred or truth_index in used_truth:
            continue
        used_pred.add(pred_index)
        used_truth.add(truth_index)
        pred = pred_rows[pred_index]
        truth = truth_rows[truth_index]
        desc_score = name_similarity(pred.get("description"), truth.get("description")) or 0.0
        quantity = amount_correct(pred.get("quantity"), truth.get("quantity"))
        unit_price = amount_correct(pred.get("unit_price"), truth.get("unit_price"))
        total = amount_correct(
            pred.get("line_total_ttc", pred.get("total", pred.get("line_total_ht"))),
            truth.get("line_total_ttc", truth.get("line_total_ht")),
        )
        matches.append({
            "predicted_index": pred_index,
            "truth_index": truth_index,
            "score": round(score, 4),
            "description_similarity": round(desc_score, 2),
            "description_correct": desc_score >= NAME_THRESHOLD,
            "quantity_correct": quantity,
            "unit_price_correct": unit_price,
            "line_total_correct": total,
        })
    return sorted(matches, key=lambda item: item["truth_index"])


def line_item_score(pred: dict[str, Any], truth: dict[str, Any]) -> float:
    desc_score = (name_similarity(pred.get("description"), truth.get("description")) or 0.0) / 100
    numeric = []
    for key in ("quantity", "unit_price", "tax_rate"):
        result = amount_correct(pred.get(key), truth.get(key))
        if result is not None:
            numeric.append(1.0 if result else 0.0)
    total_result = amount_correct(
        pred.get("line_total_ttc", pred.get("total", pred.get("line_total_ht"))),
        truth.get("line_total_ttc", truth.get("line_total_ht")),
    )
    if total_result is not None:
        numeric.append(1.0 if total_result else 0.0)
    numeric_score = sum(numeric) / len(numeric) if numeric else 0.0
    return desc_score * 0.55 + numeric_score * 0.45


def field_accuracy(matches: list[dict[str, Any]], key: str) -> float | None:
    values = [match.get(key) for match in matches if match.get(key) is not None]
    if not values:
        return None
    return round(sum(1 for value in values if value) / len(values), 4)


def compare_prediction_to_label(prediction_fields: dict[str, Any], predicted_line_items: list[dict[str, Any]], label: dict[str, Any]) -> dict[str, Any]:
    field_results: dict[str, Any] = {}
    for field in FIELD_NAMES:
        correct, detail = scalar_field_correct(field, prediction_fields.get(field), label.get(field))
        field_results[field] = {
            "predicted": prediction_fields.get(field),
            "truth": label.get(field),
            "correct": correct,
            "applicable": correct is not None,
            **detail,
        }
    line_results = compare_line_items(predicted_line_items, label.get("line_items") or [])
    required_results = [field_results[field]["correct"] for field in REQUIRED_FIELD_NAMES if field_results[field]["applicable"]]
    financial_results = [field_results[field]["correct"] for field in FINANCIAL_FIELD_NAMES if field_results[field]["applicable"]]
    all_applicable = [result["correct"] for result in field_results.values() if result["applicable"]]
    return {
        "fields": field_results,
        "line_items": line_results,
        "all_required_fields_correct": bool(required_results) and all(required_results),
        "all_financial_fields_correct": bool(financial_results) and all(financial_results),
        "fully_correct_document": bool(all_applicable) and all(all_applicable) and (not line_results["applicable"] or line_results["f1"] == 1.0),
    }


def percentage(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 4)


def summarize_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "documents_total": len(rows),
        "documents_success": sum(row.get("status") == "success" for row in rows),
        "documents_error": sum(row.get("status") == "error" for row in rows),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    field_metrics = {}
    for field in FIELD_NAMES:
        applicable = [row for row in rows if row.get(f"{field}_applicable") is True]
        correct = [row for row in applicable if row.get(f"{field}_correct") is True]
        field_metrics[field] = {
            "correct": len(correct),
            "applicable": len(applicable),
            "accuracy": percentage(len(correct), len(applicable)),
        }
    summary["field_metrics"] = field_metrics
    line_rows = [row for row in rows if row.get("line_items_applicable") is True]
    summary["line_item_metrics"] = {
        "documents_applicable": len(line_rows),
        "correct_line_item_count_rate": average_optional([row.get("line_item_count_correct") for row in line_rows]),
        "row_precision": average_optional([row.get("line_item_precision") for row in line_rows]),
        "row_recall": average_optional([row.get("line_item_recall") for row in line_rows]),
        "row_f1": average_optional([row.get("line_item_f1") for row in line_rows]),
        "description_accuracy": average_optional([row.get("line_description_accuracy") for row in line_rows]),
        "quantity_accuracy": average_optional([row.get("line_quantity_accuracy") for row in line_rows]),
        "unit_price_accuracy": average_optional([row.get("line_unit_price_accuracy") for row in line_rows]),
        "line_total_accuracy": average_optional([row.get("line_total_accuracy") for row in line_rows]),
    }
    summary["document_metrics"] = {
        "all_required_fields_correct_rate": average_optional([row.get("all_required_fields_correct") for row in rows]),
        "all_financial_fields_correct_rate": average_optional([row.get("all_financial_fields_correct") for row in rows]),
        "fully_correct_document_rate": average_optional([row.get("fully_correct_document") for row in rows]),
        "erp_ready_and_actually_correct_rate": average_optional([row.get("erp_ready_and_actually_correct") for row in rows]),
        "false_erp_ready_count": sum(row.get("false_erp_ready") is True for row in rows),
        "safely_routed_to_review_count": sum(row.get("safely_routed_to_review") is True for row in rows),
        "incorrect_prediction_count": sum(row.get("incorrect_prediction_count") or 0 for row in rows),
        "missing_prediction_count": sum(row.get("missing_prediction_count") or 0 for row in rows),
    }
    times = [float(row["processing_time_seconds"]) for row in rows if row.get("processing_time_seconds") not in (None, "")]
    summary["processing_time"] = {
        "average_seconds": round(sum(times) / len(times), 4) if times else None,
        "max_seconds": max(times) if times else None,
    }
    summary["dataset_composition"] = composition(rows)
    summary["top_incorrect_fields"] = top_fields(rows, suffix="_correct", target=False)
    summary["top_missing_fields"] = top_missing_predictions(rows)
    return summary


def average_optional(values: list[Any]) -> float | None:
    numeric = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            numeric.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)) and not math.isnan(float(value)):
            numeric.append(float(value))
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 4)


def composition(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_dataset: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in rows:
        by_dataset[str(row.get("dataset") or "unknown")] = by_dataset.get(str(row.get("dataset") or "unknown"), 0) + 1
        by_type[str(row.get("document_type_true") or row.get("document_type_hint") or "unknown")] = by_type.get(str(row.get("document_type_true") or row.get("document_type_hint") or "unknown"), 0) + 1
    return {"by_dataset": by_dataset, "by_document_type": by_type}


def top_fields(rows: list[dict[str, Any]], suffix: str, target: bool) -> list[dict[str, Any]]:
    counts = []
    for field in FIELD_NAMES:
        applicable = [row for row in rows if row.get(f"{field}_applicable") is True]
        bad = [row for row in applicable if row.get(f"{field}{suffix}") is target]
        if bad:
            counts.append({"field": field, "count": len(bad)})
    return sorted(counts, key=lambda item: (-item["count"], item["field"]))[:10]


def top_missing_predictions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = []
    for field in FIELD_NAMES:
        count = sum(row.get(f"{field}_applicable") is True and row.get(f"{field}_prediction_missing") is True for row in rows)
        if count:
            counts.append({"field": field, "count": count})
    return sorted(counts, key=lambda item: (-item["count"], item["field"]))[:10]


def html_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(column, '')))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |")
    return "\n".join(lines)
