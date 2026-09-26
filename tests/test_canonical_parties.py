import pytest

from app.core.canonical_parties import RUSPINA_IMPORT_EXPORT_PREFIX, canonicalize_party


@pytest.mark.parametrize("value, expected", [
    ("RUSPINA IMP EXP P-C GROUP BYOUT EZZ LIBYE", "RUSPINA IMPORT ET EXPORT P-C GROUP BYOUT EZZ LIBYE"),
    ("ruspina imp exp p c group byout ezz libye", "RUSPINA IMPORT ET EXPORT P-C group byout ezz libye"),
    ("RUSPINA IMP EXP PC GROUP BYOUT EZZ LIBYE", "RUSPINA IMPORT ET EXPORT P-C GROUP BYOUT EZZ LIBYE"),
    ("RU$PINA IMP EXP PC GROUP BYOUT E2Z L1BYE", "RUSPINA IMPORT ET EXPORT P-C GROUP BYOUT E2Z L1BYE"),
    ("RUSPINA IMPORT EXPORT P-C GROUP BYOUT EZZ LIBYF", "RUSPINA IMPORT ET EXPORT P-C GROUP BYOUT EZZ LIBYF"),
    ("RU$PINA IMP EXP P-C GROUP BYOUT EZZ", "RUSPINA IMPORT ET EXPORT P-C GROUP BYOUT EZZ"),
])
def test_strong_known_importer_variants_canonicalize(value, expected):
    effective, reason = canonicalize_party(value, role="importer", document_family="customs_tradenet_v1")
    assert effective == expected
    assert reason and "RUSPINA" in reason


@pytest.mark.parametrize("value", [
    "GROUP IMPORT EXPORT LIBYE",
    "OTHER COMPANY GROUP LIBYE",
    "RUSPINA GROUP BYOUT LIBYE",
    "ALPHA IMPORT EXPORT GROUP BYOUT EZZ LIBYE",
    "RU$PINA IMP EXP BYOUT E2Z LIBYE",
])
def test_weak_or_different_party_names_are_not_canonicalized(value):
    assert canonicalize_party(value, role="importer", document_family="customs_tradenet_v1") == (value, None)


def test_role_and_document_family_scope_the_registry():
    value = "RUSPINA IMP EXP PC GROUP BYOUT E2Z LIBYE"
    assert canonicalize_party(value, role="exporter", document_family="customs_tradenet_v1") == (value, None)
    effective, reason = canonicalize_party(value, role="importer", document_family="customs_douanes_tunisiennes_v1")
    assert effective == "RUSPINA IMPORT ET EXPORT P-C GROUP BYOUT E2Z LIBYE"
    assert reason and "strong RUSPINA" in reason
    assert canonicalize_party(value, role="importer", document_family="producer_invoice_v1") == (value, None)


@pytest.mark.parametrize("raw", [
    "RUSPINA IMPORT ET EXPORT P-C IMPACT COMPANY",
    "USPINA IMPORT ET EXPORT P-C IMPACT COMPANY",
    "RUSPINA IMPORT ET EXPORT P-CIMPACT COMPANY",
    "USPINA IMPORT ET EXPORT P-CIMPACT COMPANY",
    "RUSPINA IMPORT ET EXPORT P C IMPACT COMPANY",
    "RUSPINA IMPORT ET EXPORT PC IMPACT COMPANY",
])
@pytest.mark.parametrize("family", ["customs_tradenet_v1", "customs_douanes_tunisiennes_v1"])
def test_strong_import_export_prefix_is_canonicalized_with_original_suffix(raw, family):
    effective, reason = canonicalize_party(raw, role="importer", document_family=family)
    assert effective == f"{RUSPINA_IMPORT_EXPORT_PREFIX} IMPACT COMPANY"
    assert reason and "strong RUSPINA" in reason


def test_prefix_normalization_preserves_each_downstream_company_and_raw_value():
    raw = "USPINA IMPORT ET EXPORT P-CGROUP BYOUT EZZ"
    effective, reason = canonicalize_party(raw, role="importer", document_family="customs_tradenet_v1")
    assert effective == "RUSPINA IMPORT ET EXPORT P-C GROUP BYOUT EZZ"
    assert "IMPACT COMPANY" not in effective
    assert raw == "USPINA IMPORT ET EXPORT P-CGROUP BYOUT EZZ"
    assert reason


@pytest.mark.parametrize("raw", [
    "IMPORT EXPORT COMPANY",
    "IMPACT COMPANY",
    "GROUP COMPANY LIBYE",
    "RANDOM IMPORT ET EXPORT",
    "DIFFERENT IMPORTER LTD",
])
def test_unrelated_or_weak_importers_are_not_canonicalized(raw):
    assert canonicalize_party(raw, role="importer", document_family="customs_tradenet_v1") == (raw, None)
