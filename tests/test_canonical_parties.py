import pytest

from app.core.canonical_parties import RUSPINA_TRADE_NET_IMPORTER, canonicalize_party


@pytest.mark.parametrize("value", [
    "RUSPINA IMP EXP P-C GROUP BYOUT EZZ LIBYE",
    "ruspina imp exp p c group byout ezz libye",
    "RUSPINA IMP EXP PC GROUP BYOUT EZZ LIBYE",
    "RU$PINA IMP EXP PC GROUP BYOUT E2Z L1BYE",
    "RUSPINA IMPORT EXPORT P-C GROUP BYOUT EZZ LIBYF",
    "RU$PINA IMP EXP P-C GROUP BYOUT EZZ",
])
def test_strong_known_importer_variants_canonicalize(value):
    effective, reason = canonicalize_party(value, role="importer", document_family="customs_tradenet_v1")
    assert effective == RUSPINA_TRADE_NET_IMPORTER
    assert reason and "registered importer" in reason


@pytest.mark.parametrize("value", [
    "GROUP IMPORT EXPORT LIBYE",
    "OTHER COMPANY GROUP LIBYE",
    "RUSPINA GROUP BYOUT LIBYE",
    "ALPHA IMPORT EXPORT GROUP BYOUT EZZ LIBYE",
    "RU$PINA IMP EXP PC BYOUT E2Z LIBYE",
])
def test_weak_or_different_party_names_are_not_canonicalized(value):
    assert canonicalize_party(value, role="importer", document_family="customs_tradenet_v1") == (value, None)


def test_role_and_document_family_scope_the_registry():
    value = "RUSPINA IMP EXP PC GROUP BYOUT E2Z LIBYE"
    assert canonicalize_party(value, role="exporter", document_family="customs_tradenet_v1") == (value, None)
    assert canonicalize_party(value, role="importer", document_family="customs_douanes_tunisiennes_v1") == (value, None)
