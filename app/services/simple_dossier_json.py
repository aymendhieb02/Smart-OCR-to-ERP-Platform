"""Deterministic, UI-language-independent dossier JSON serialization."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.core.schemas import SimpleDossierField, SimpleDossierGroup, SimpleDossierOutput
from app.services.dossier_segmentation import semantic_group_for_document
from app.services.producer_invoice_review import (
    ENFIDHA_FAMILY,
    GENERAL_PRODUCER_FAMILY,
    PRODUCER_REVIEW_FIELDS,
)


RUSPINA_FIELDS = (
    "invoice_number", "invoice_date", "referenced_invoice", "client", "address", "currency", "total",
    "total_amount_words", "gross_weight", "net_weight", "number_of_bags", "delivery", "origin",
    "payment", "iban", "bank", "swift",
)
TRADENET_FIELDS = (
    "declaration_number", "declaration_date", "declaration_type", "exporter", "importer",
    "ptfn_amount", "currency_conversion_rate", "customs_total_value_tnd",
)
GENERAL_PRODUCER_EXTENSIONS = (
    "client_rc", "consignee_address", "proforma_invoice_number", "proforma_invoice_date", "shipment",
    "bank", "iban", "swift", "number_of_bags", "bag_weight", "truck_count",
)
PRODUCER_ALIASES = {
    "seller": ("seller", "supplier_name"),
    "client": ("client", "customer_name", "buyer"),
    "client_address": ("client_address", "customer_address", "address"),
    "consignee": ("consignee",),
    "address": ("address", "client_address", "customer_address"),
    "total": ("total", "amount_ttc"),
}


def build_simple_dossier_output(dossier_result: Any) -> SimpleDossierOutput:
    """Build exactly page1/page2/page3 using the currently effective review values."""
    routed: dict[str, list[tuple[int, Any, str | None]]] = defaultdict(list)
    for item in _documents(dossier_result):
        group = _value(item, "group")
        document_type = _value(item, "document_type") or _value(group, "document_type")
        document_family = _value(item, "document_family") or _value(group, "document_family")
        anchors = _classification_anchors(item)
        semantic_group = semantic_group_for_document(document_type or "unknown", document_family, anchors)
        if semantic_group is None:
            continue
        page_numbers = _value(item, "physical_page_numbers")
        if page_numbers is None:
            page_numbers = _value(group, "pages") or ()
        first_page = min(page_numbers) if page_numbers else 0
        routed[semantic_group].append((first_page, _value(item, "response"), document_family))

    output: dict[str, SimpleDossierGroup] = {}
    for semantic_group in ("page1", "page2", "page3"):
        documents = sorted(routed.get(semantic_group, ()), key=lambda entry: entry[0])
        if not documents:
            output[semantic_group] = SimpleDossierGroup(fields=[])
            continue
        contract = _field_contract(semantic_group, documents[0][2])
        for _page, _response, family in documents[1:]:
            for field_name in _field_contract(semantic_group, family):
                if field_name != "line_items" and field_name not in contract:
                    line_items_index = contract.index("line_items") if "line_items" in contract else len(contract)
                    contract.insert(line_items_index, field_name)
        fields: list[SimpleDossierField] = []
        for field_name in contract:
            if field_name == "line_items":
                values = []
                for _page, response, _family in documents:
                    values.extend(_simple_line_item(item) for item in _line_items(response))
                fields.append(SimpleDossierField(name=field_name, value=values))
                continue
            value = None
            for _page, response, _family in documents:
                value = _effective_field_value(
                    response,
                    field_name,
                    canonical_authoritative=semantic_group == "page1",
                )
                if value is not None:
                    break
            fields.append(SimpleDossierField(name=field_name, value=value))
        output[semantic_group] = SimpleDossierGroup(fields=fields)

    return SimpleDossierOutput(**output)


def _documents(dossier_result: Any) -> list[Any]:
    return list(_value(dossier_result, "logical_documents") or ())


def _classification_anchors(document: Any) -> tuple[str, ...]:
    group = _value(document, "group")
    classifications = _value(document, "page_classifications") or _value(group, "page_classifications") or ()
    anchors: list[str] = []
    for classification in classifications:
        anchors.extend(_value(classification, "matched_anchors") or ())
    return tuple(dict.fromkeys(anchors))


def _field_contract(semantic_group: str, family: str | None) -> list[str]:
    if semantic_group == "page2":
        return [*RUSPINA_FIELDS, "line_items"]
    if semantic_group == "page3":
        return list(TRADENET_FIELDS)
    if family in PRODUCER_REVIEW_FIELDS:
        fields = list(PRODUCER_REVIEW_FIELDS[family])
    elif family in {None, "general_supplier_invoice"}:
        fields = [
            *PRODUCER_REVIEW_FIELDS[GENERAL_PRODUCER_FAMILY],
            *GENERAL_PRODUCER_EXTENSIONS,
        ]
    else:
        fields = list(PRODUCER_REVIEW_FIELDS.get(ENFIDHA_FAMILY, ()))
    return [*fields, "line_items"]


def _effective_field_value(response: Any, field_name: str, *, canonical_authoritative: bool = False) -> Any:
    expanded = _value(response, "expanded_fields") or {}
    aliases = PRODUCER_ALIASES.get(field_name, (field_name,))
    canonical_detail = _mapping_value(expanded, field_name) if canonical_authoritative else None
    if canonical_detail is not None:
        source = str(_value(canonical_detail, "source") or "").casefold()
        value = _value(canonical_detail, "value")
        if any(marker in source for marker in ("human correction", "manual correction", "review correction")):
            return value
        canonical_value = _value(canonical_detail, "canonical_value")
        if canonical_value is not None:
            return canonical_value
        if value is not None:
            return value
        machine_value = _value(canonical_detail, "machine_value")
        if machine_value is not None:
            return machine_value
        # A present-but-empty Page-1 canonical detail means “not extracted”.
        # Do not resurrect a stale generic supplier/customer/amount alias.
        return None
    detail = next((_mapping_value(expanded, alias) for alias in aliases if _mapping_value(expanded, alias) is not None), None)
    if detail is not None:
        source = str(_value(detail, "source") or "").casefold()
        value = _value(detail, "value")
        if any(marker in source for marker in ("human correction", "manual correction", "review correction")):
            return value
        canonical_value = _value(detail, "canonical_value")
        if canonical_value is not None:
            return canonical_value
        if value is not None:
            return value
        machine_value = _value(detail, "machine_value")
        if machine_value is not None:
            return machine_value

    detected = _value(response, "detected_fields")
    detected_map = _as_mapping(detected)
    for alias in aliases:
        value = detected_map.get(alias)
        if value is not None:
            return value
    return None


def _line_items(response: Any) -> list[Any]:
    if response is None:
        return []
    items = _value(response, "all_line_items")
    detected_items = _value(_value(response, "detected_fields"), "line_items")
    if items is None or (not items and detected_items):
        items = detected_items
    return list(items or ())


def _simple_line_item(item: Any) -> dict[str, Any]:
    line_total = _first_non_null(item, ("line_total", "line_total_ht"))
    if line_total is None and _value(item, "line_total_ttc") is None:
        line_total = _value(item, "total")
    return {
        "description": _value(item, "description"),
        "quantity": _value(item, "quantity"),
        "unit": _value(item, "unit"),
        "unit_price": _value(item, "unit_price"),
        "line_total": line_total,
    }


def _first_non_null(source: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = _value(source, name)
        if value is not None:
            return value
    return None


def _mapping_value(mapping: Any, key: str) -> Any:
    return mapping.get(key) if isinstance(mapping, dict) else getattr(mapping, key, None)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    return dump(mode="python") if callable(dump) else {}


def _value(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)
