from __future__ import annotations

from typing import Any

from app.core.schemas import (
    CorrectionMetadata,
    DynamicTable,
    DynamicTableColumn,
    DynamicTableRow,
    ERPLayer,
    ERPInvoiceJSON,
    ExtractedInvoiceFields,
    ExtractionLayer,
    FieldExtractionDetail,
    LayoutBlock,
    OCRLine,
    ValidationResult,
)


ERP_REQUIRED_FIELDS = {
    "supplier_name",
    "supplier_tax_id",
    "invoice_number",
    "invoice_date",
    "currency",
    "amount_ttc",
}

ERP_INCLUDED_FIELDS = {
    "supplier_name",
    "supplier_address",
    "supplier_tax_id",
    "customer_name",
    "customer_address",
    "customer_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "amount_ht",
    "tva_amount",
    "amount_ttc",
    "tax_rate",
    "purchase_order_number",
}

FIELD_LABELS = {
    "supplier_name": "Supplier Name",
    "supplier_address": "Supplier Address",
    "supplier_tax_id": "Supplier Tax ID",
    "supplier_email": "Supplier Email",
    "phone_number": "Phone Number",
    "bank_rib": "RIB",
    "bank_iban": "IBAN",
    "bank_swift": "SWIFT / BIC",
    "customer_name": "Customer Name",
    "customer_address": "Customer Address",
    "customer_tax_id": "Customer Tax ID",
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "due_date": "Due Date",
    "currency": "Currency",
    "amount_ht": "Subtotal HT",
    "tva_amount": "TVA / VAT Amount",
    "amount_ttc": "Total TTC",
    "tax_rate": "Tax Rate",
    "purchase_order_number": "Purchase Order Number",
    "client_reference": "Client Reference",
    "discount_amount": "Discount",
    "payment_terms": "Payment Terms",
    "raw_text_length": "Raw Text Length",
}

LINE_ITEM_COLUMNS = [
    ("row_number", "Row"),
    ("reference", "Reference"),
    ("description", "Description"),
    ("quantity", "Quantity"),
    ("unit", "Unit"),
    ("unit_price", "Unit Price"),
    ("discount", "Discount"),
    ("tax_rate", "Tax Rate"),
    ("amount_ht", "Amount HT"),
    ("tax_amount", "Tax Amount"),
    ("amount_ttc", "Amount TTC"),
    ("confidence", "Confidence"),
    ("source", "Source"),
    ("page", "Page"),
]


def build_dynamic_review_payload(
    *,
    fields: ExtractedInvoiceFields,
    expanded_fields: dict[str, FieldExtractionDetail],
    layout_blocks: list[LayoutBlock],
    ocr_blocks: list[OCRLine],
    validation: ValidationResult,
    erp_json: ERPInvoiceJSON,
) -> tuple[list[DynamicTable], ExtractionLayer, ERPLayer]:
    dynamic_tables = [
        _erp_fields_table(expanded_fields),
        _all_extracted_fields_table(expanded_fields),
        _line_items_table(fields),
        _tax_summary_table(fields),
        _payment_details_table(expanded_fields, fields),
        _layout_blocks_table(layout_blocks),
        _ocr_blocks_table(ocr_blocks),
        _unmapped_text_table(layout_blocks, ocr_blocks),
    ]
    dynamic_tables = sorted(dynamic_tables, key=lambda table: table.priority)
    extraction_layer = ExtractionLayer(
        all_fields={key: value.model_dump(mode="json") for key, value in expanded_fields.items()},
        tables={table.id: table for table in dynamic_tables},
        layout_blocks=layout_blocks,
        ocr_blocks=ocr_blocks,
    )
    erp_layer = ERPLayer(
        selected_fields={key: value for key, value in fields.model_dump(mode="json").items() if key != "line_items"},
        erp_json=erp_json.model_dump(mode="json"),
        export_allowed=validation.status == "valid",
        blocking_reasons=list(validation.errors),
    )
    return dynamic_tables, extraction_layer, erp_layer


def _erp_fields_table(expanded_fields: dict[str, FieldExtractionDetail]) -> DynamicTable:
    rows = [
        _field_row(key, detail, required_for_erp=key in ERP_REQUIRED_FIELDS, included_in_erp=True)
        for key, detail in expanded_fields.items()
        if key in ERP_INCLUDED_FIELDS
    ]
    return DynamicTable(
        id="erp_fields",
        title="ERP Required Fields",
        type="key_value",
        priority=1,
        rows=rows,
    )


def _all_extracted_fields_table(expanded_fields: dict[str, FieldExtractionDetail]) -> DynamicTable:
    rows = [
        _field_row(
            key,
            detail,
            required_for_erp=key in ERP_REQUIRED_FIELDS,
            included_in_erp=key in ERP_INCLUDED_FIELDS,
        )
        for key, detail in sorted(expanded_fields.items())
    ]
    return DynamicTable(
        id="all_extracted_fields",
        title="All Extracted Fields",
        type="key_value",
        priority=2,
        rows=rows,
    )


def _line_items_table(fields: ExtractedInvoiceFields) -> DynamicTable:
    rows = []
    for index, item in enumerate(fields.line_items, start=1):
        amount_ht = item.line_total_ht
        amount_ttc = item.line_total_ttc if item.line_total_ttc is not None else item.total
        tax_amount = item.tax_amount
        if amount_ht is not None and amount_ttc is not None:
            tax_amount = tax_amount if tax_amount is not None else round(amount_ttc - amount_ht, 3)
        values = {
            "row_number": index,
            "reference": item.reference,
            "description": item.description,
            "quantity": item.quantity,
            "unit": item.unit,
            "unit_price": item.unit_price,
            "discount": item.discount,
            "tax_rate": item.tax_rate,
            "amount_ht": amount_ht,
            "tax_amount": tax_amount,
            "amount_ttc": amount_ttc,
            "confidence": item.confidence,
            "source": item.source or "line item extraction",
            "page": item.page,
        }
        row_status = "needs_review" if "review" in (item.source or "").lower() else "validated"
        rows.append(DynamicTableRow(
            key=f"line_item_{index}",
            label=f"Line {index}",
            values=values,
            source="line item extraction",
            included_in_erp=True,
            editable=True,
            status=row_status,
            correction=CorrectionMetadata(original_value=values),
        ))
    return DynamicTable(
        id="line_items",
        title="Product / Service Lines",
        type="table",
        priority=3,
        columns=[DynamicTableColumn(key=key, label=label) for key, label in LINE_ITEM_COLUMNS],
        rows=rows,
    )


def _tax_summary_table(fields: ExtractedInvoiceFields) -> DynamicTable:
    rows = []
    if fields.tax_rate is not None or fields.tva_amount is not None:
        rows.append(DynamicTableRow(
            key="tax_summary_1",
            label="TVA / VAT",
            values={
                "tax_rate": fields.tax_rate,
                "base": fields.amount_ht,
                "tax_amount": fields.tva_amount,
                "total": fields.amount_ttc,
                "currency": fields.currency,
            },
            source="totals extraction",
            included_in_erp=True,
            editable=True,
            status="ok",
            correction=CorrectionMetadata(original_value={
                "tax_rate": fields.tax_rate,
                "base": fields.amount_ht,
                "tax_amount": fields.tva_amount,
                "total": fields.amount_ttc,
                "currency": fields.currency,
            }),
        ))
    return DynamicTable(
        id="tax_summary",
        title="Tax Summary",
        type="table",
        priority=4,
        columns=[
            DynamicTableColumn(key="tax_rate", label="Tax Rate"),
            DynamicTableColumn(key="base", label="Base"),
            DynamicTableColumn(key="tax_amount", label="Tax Amount"),
            DynamicTableColumn(key="total", label="Total"),
            DynamicTableColumn(key="currency", label="Currency"),
        ],
        rows=rows,
    )


def _payment_details_table(expanded_fields: dict[str, FieldExtractionDetail], fields: ExtractedInvoiceFields) -> DynamicTable:
    payment_keys = ("payment_terms", "bank_rib", "bank_iban", "bank_swift")
    rows = [
        _field_row(key, expanded_fields[key], required_for_erp=False, included_in_erp=False)
        for key in payment_keys
        if key in expanded_fields
    ]
    if fields.due_date is not None:
        rows.append(_field_row(
            "payment_due_date",
            FieldExtractionDetail(value=fields.due_date, confidence=None, source="invoice due date"),
            required_for_erp=False,
            included_in_erp=False,
        ))
    return DynamicTable(
        id="payment_details",
        title="Payment Details",
        type="key_value",
        priority=5,
        rows=rows,
    )


def _layout_blocks_table(layout_blocks: list[LayoutBlock]) -> DynamicTable:
    rows = [
        DynamicTableRow(
            key=f"layout_{index}",
            label=block.block_type,
            value=block.text,
            confidence=block.confidence,
            source="layout analyzer",
            page=block.page,
            bbox=block.bbox,
            editable=False,
            status="unmapped" if block.block_type == "unknown" else "ok",
        )
        for index, block in enumerate(layout_blocks, start=1)
    ]
    return DynamicTable(
        id="layout_blocks",
        title="Detected Layout Blocks",
        type="blocks",
        priority=6,
        rows=rows,
    )


def _ocr_blocks_table(ocr_blocks: list[OCRLine]) -> DynamicTable:
    rows = [
        DynamicTableRow(
            key=f"ocr_{index}",
            label=f"Line {block.line_index if block.line_index is not None else index}",
            value=block.text,
            confidence=block.confidence,
            source="ocr",
            page=block.page_number,
            bbox=block.bbox,
            editable=False,
            status=_status_for_confidence(block.confidence),
        )
        for index, block in enumerate(ocr_blocks, start=1)
    ]
    return DynamicTable(
        id="ocr_blocks",
        title="All OCR Text Blocks",
        type="ocr_blocks",
        priority=7,
        rows=rows,
    )


def _unmapped_text_table(layout_blocks: list[LayoutBlock], ocr_blocks: list[OCRLine]) -> DynamicTable:
    classified_blocks = [block for block in layout_blocks if block.block_type != "unknown"]
    unknown_rows = [
        DynamicTableRow(
            key=f"unknown_layout_{index}",
            label="Unknown layout block",
            value=block.text,
            confidence=block.confidence,
            source="layout analyzer",
            page=block.page,
            bbox=block.bbox,
            editable=False,
            status="unmapped",
        )
        for index, block in enumerate(layout_blocks, start=1)
        if block.block_type == "unknown"
    ]
    ocr_rows = [
        DynamicTableRow(
            key=f"unmapped_ocr_{index}",
            label="Unmapped OCR text",
            value=block.text,
            confidence=block.confidence,
            source="ocr not assigned to logical block",
            page=block.page_number,
            bbox=block.bbox,
            editable=False,
            status="unmapped",
        )
        for index, block in enumerate(ocr_blocks, start=1)
        if block.bbox is not None and not _is_inside_any_block(block, classified_blocks)
    ]
    return DynamicTable(
        id="unmapped_text",
        title="Unmapped / Extra Text",
        type="text_blocks",
        priority=8,
        rows=unknown_rows + ocr_rows,
    )


def _field_row(
    key: str,
    detail: FieldExtractionDetail,
    *,
    required_for_erp: bool,
    included_in_erp: bool,
) -> DynamicTableRow:
    status = "missing" if detail.value in (None, "") else _status_for_confidence(detail.confidence)
    return DynamicTableRow(
        key=key,
        label=FIELD_LABELS.get(key, _humanize(key)),
        value=detail.value,
        confidence=detail.confidence,
        source=detail.source,
        page=detail.page,
        bbox=detail.bbox,
        required_for_erp=required_for_erp,
        included_in_erp=included_in_erp,
        editable=True,
        status=status,
        correction=CorrectionMetadata(original_value=detail.value),
    )


def _status_for_confidence(confidence: float | None) -> str:
    if confidence is None:
        return "ok"
    return "low_confidence" if confidence < 0.65 else "ok"


def _humanize(key: str) -> str:
    return key.replace("_", " ").title()


def _is_inside_any_block(ocr_block: OCRLine, layout_blocks: list[LayoutBlock]) -> bool:
    if not ocr_block.bbox:
        return False
    for layout in layout_blocks:
        bbox = layout.bbox
        if (
            ocr_block.page_number == layout.page
            and ocr_block.bbox.x1 >= bbox.x1
            and ocr_block.bbox.y1 >= bbox.y1
            and ocr_block.bbox.x2 <= bbox.x2
            and ocr_block.bbox.y2 <= bbox.y2
        ):
            return True
    return False

