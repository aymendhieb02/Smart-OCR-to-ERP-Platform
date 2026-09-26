from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.core.schemas import FieldExtractionDetail
from app.services.correction_identity import correction_document_id
from app.services.pipeline_runner import _apply_tradenet_corrections, _canonicalize_tradenet_importer


def test_persisted_reviewer_value_overrides_canonical_but_keeps_machine_audit_values():
    response = SimpleNamespace(
        expanded_fields={"importer": FieldExtractionDetail(
            value="RUSPINA IMP EXP P-C GROUP BYOUT EZZ LIBYE",
            machine_value="RU$PINA IMP EXP PC GROUP BYOUT E2Z L1BYE",
            canonical_value="RUSPINA IMP EXP P-C GROUP BYOUT EZZ LIBYE",
            evidence_text="RU$PINA IMP EXP PC GROUP BYOUT E2Z L1BYE",
        )},
        field_boxes=[],
        dynamic_tables=[],
    )
    _apply_tradenet_corrections(response, {"importer": {"corrected_value": "SYNTHETIC MANUAL IMPORTER"}})
    detail = response.expanded_fields["importer"]
    assert detail.value == detail.display_value == "SYNTHETIC MANUAL IMPORTER"
    assert detail.canonical_value == "RUSPINA IMP EXP P-C GROUP BYOUT EZZ LIBYE"
    assert detail.machine_value == "RU$PINA IMP EXP PC GROUP BYOUT E2Z L1BYE"
    assert detail.evidence_text == "RU$PINA IMP EXP PC GROUP BYOUT E2Z L1BYE"


def test_saved_document_identity_is_stable_per_file_and_logical_document(tmp_path: Path):
    source = tmp_path / "synthetic-dossier.pdf"
    source.write_bytes(b"synthetic dossier bytes")
    first = correction_document_id(source, "logical_document_3")
    assert correction_document_id(source, "logical_document_3") == first
    assert correction_document_id(source, "logical_document_2") != first
    source.write_bytes(b"different synthetic dossier")
    assert correction_document_id(source, "logical_document_3") != first


def test_pipeline_canonicalizes_only_importer_and_retains_raw_evidence():
    raw = "RU$PINA IMP EXP PC GROUP BYOUT E2Z L1BYE"
    fields = {
        "importer": FieldExtractionDetail(value=raw, evidence_text=raw),
        "exporter": FieldExtractionDetail(value="UNRELATED EXPORTER TEST", evidence_text="UNRELATED EXPORTER TEST"),
    }
    reason = _canonicalize_tradenet_importer(fields, "customs_tradenet_v1")
    importer = fields["importer"]
    assert importer.value == importer.display_value == "RUSPINA IMPORT ET EXPORT P-C GROUP BYOUT E2Z L1BYE"
    assert importer.machine_value == raw and importer.evidence_text == raw
    assert importer.canonical_value == importer.value and reason
    assert fields["exporter"].value == "UNRELATED EXPORTER TEST"
    other = FieldExtractionDetail(value=raw)
    assert _canonicalize_tradenet_importer({"importer": other}, "producer_invoice_v1") is None
    assert other.value == raw


def test_human_importer_correction_overrides_prefix_normalization_without_losing_raw_evidence():
    raw = "USPINA IMPORT ET EXPORT P-CIMPACT COMPANY"
    fields = {"importer": FieldExtractionDetail(
        value=raw, evidence_text=raw, page=3, confidence=0.82, source="TradeNet full-page OCR",
        bbox={"x1": 1, "y1": 2, "x2": 3, "y2": 4},
    )}
    reason = _canonicalize_tradenet_importer(fields, "customs_tradenet_v1")
    assert reason
    assert fields["importer"].value == "RUSPINA IMPORT ET EXPORT P-C IMPACT COMPANY"
    assert fields["importer"].canonicalization_reason == reason
    assert fields["importer"].source == "TradeNet full-page OCR"

    _apply_tradenet_corrections(SimpleNamespace(expanded_fields=fields, field_boxes=[], dynamic_tables=[]), {
        "importer": {"corrected_value": "MANUAL IMPORTER TEST"},
    })
    detail = fields["importer"]
    assert detail.value == detail.display_value == "MANUAL IMPORTER TEST"
    assert detail.canonical_value == "RUSPINA IMPORT ET EXPORT P-C IMPACT COMPANY"
    assert detail.machine_value == raw
    assert detail.evidence_text == raw
    assert detail.page == 3 and detail.confidence == 0.82
    assert detail.source == "TradeNet full-page OCR"
    assert detail.bbox.x1 == 1 and detail.canonicalization_reason == reason


def test_tradenet_editor_and_french_labels_use_expanded_values_and_source_display():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    strings = Path("app/static/strings.js").read_text(encoding="utf-8")
    required = (
        "declaration_number", "declaration_date", "declaration_type", "exporter", "importer",
        "ptfn_amount", "currency_conversion_rate", "customs_total_value_tnd",
    )
    assert 'customs_tradenet_v1: { labelKey: "dossier.document_customs", fields: CUSTOMS_TRADENET_REVIEW_FIELDS' in script
    assert "input.value = detail?.display_value ?? detail?.value ?? fields[field] ?? \"\"" in script
    assert "expanded_field_overrides" in script
    assert "correction_document_id" in script
    assert 'fetch("/review/reconcile-dossier"' in script
    for key in required:
        assert f'"fields.{key}"' in strings
    for label in (
        "Numéro de déclaration", "Date de déclaration", "Type de déclaration", "Exportateur", "Importateur",
        "Cours de conversion de la devise", "Valeur douane totale (TND)",
    ):
        assert label in strings


def test_decimal_numeric_semantics_remain_separate_from_source_display():
    detail = FieldExtractionDetail(value="3.2842000", display_value="3.2842000", normalized_value=Decimal("3.2842000"))
    assert detail.display_value == "3.2842000"
    assert Decimal(str(detail.normalized_value)) == Decimal("3.2842")
    assert Decimal("52000.000") == Decimal("52000")
