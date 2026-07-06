from __future__ import annotations

from datetime import date as Date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    reference: str | None = None
    description: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    discount: float | None = None
    line_total_ht: float | None = None
    tax_amount: float | None = None
    tax_rate: float | None = None
    line_total_ttc: float | None = None
    total: float | None = None
    confidence: float | None = None
    bbox: Any = None
    page: int | None = None
    source: str | None = None

class CorrectionMetadata(BaseModel):
    original_value: Any = None
    corrected_value: Any = None
    corrected_by: str | None = None
    corrected_at: datetime | None = None


class DynamicTableColumn(BaseModel):
    key: str
    label: str
    editable: bool = True


class DynamicTableRow(BaseModel):
    key: str | None = None
    label: str | None = None
    value: Any = None
    values: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    source: str | None = None
    page: int | None = None
    bbox: Any = None
    required_for_erp: bool = False
    included_in_erp: bool = False
    editable: bool = True
    status: str = "ok"
    correction: CorrectionMetadata | None = None


class DynamicTable(BaseModel):
    id: str
    title: str
    type: str
    priority: int
    columns: list[DynamicTableColumn] = Field(default_factory=list)
    rows: list[DynamicTableRow] = Field(default_factory=list)


class ExtractionLayer(BaseModel):
    all_fields: dict[str, Any] = Field(default_factory=dict)
    tables: dict[str, Any] = Field(default_factory=dict)
    layout_blocks: list[Any] = Field(default_factory=list)
    ocr_blocks: list[Any] = Field(default_factory=list)


class ERPLayer(BaseModel):
    selected_fields: dict[str, Any] = Field(default_factory=dict)
    erp_json: dict[str, Any] = Field(default_factory=dict)
    export_allowed: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    field: str | None = None
    table: str | None = None
    message: str
    impact: str | None = None
    severity: str | None = None
    suggested_action: str | None = None


class ValidationExplanation(BaseModel):
    status: str
    reason: str
    blocking_errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    suggested_action: str


class ValidationResult(BaseModel):
    is_valid: bool = True
    status: str = "valid"
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float | None = None


class BoundingBox(BaseModel):
    x1: float = 0
    y1: float = 0
    x2: float = 0
    y2: float = 0


class OCRLine(BaseModel):
    text: str
    confidence: float | None = None
    page_number: int
    bbox: BoundingBox | None = None
    line_index: int | None = None


class OCRResult(BaseModel):
    raw_text: str
    lines: list[OCRLine] = Field(default_factory=list)
    confidence: float | None = None
    engine: str
    page_count: int = 1


class DocumentClassification(BaseModel):
    document_type: str = "unknown"
    confidence: float = 0.0
    matched_keywords: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    field: str
    value: Any
    score: float
    source: str
    page: int | None = None
    line_index: int | None = None
    bbox: BoundingBox | None = None


class FieldExtractionDetail(BaseModel):
    value: Any = None
    confidence: float | None = None
    bbox: BoundingBox | None = None
    page: int | None = None
    line_index: int | None = None
    source: str | None = None


class LayoutBlock(BaseModel):
    block_type: str
    bbox: BoundingBox
    confidence: float = 0.0
    text: str = ""
    fields: list[str] = Field(default_factory=list)
    page: int = 1


class FieldBox(BaseModel):
    field: str
    value: Any = None
    confidence: float | None = None
    bbox: BoundingBox | None = None
    page: int | None = None
    source: str | None = None


class PreviewPage(BaseModel):
    page: int
    url: str
    width: int
    height: int


class DocumentPreview(BaseModel):
    pages: list[PreviewPage] = Field(default_factory=list)
    source_file: str | None = None


class ExtractedInvoiceFields(BaseModel):
    supplier_name: str | None = None
    supplier_address: str | None = None
    supplier_phone: str | None = None
    supplier_email: str | None = None
    supplier_website: str | None = None
    supplier_bank_iban: str | None = None
    supplier_bank_rib: str | None = None
    supplier_bank_swift: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    invoice_number: str | None = None
    invoice_date: Date | None = None
    due_date: Date | None = None
    currency: str | None = None
    amount_ht: float | None = None
    tva_amount: float | None = None
    amount_ttc: float | None = None
    tax_rate: float | None = None
    purchase_order_number: str | None = None
    supplier_tax_id: str | None = None
    customer_tax_id: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)


class SupplierERP(BaseModel):
    name: str | None = None
    tax_id: str | None = None
    address: str | None = None


class CustomerERP(BaseModel):
    name: str | None = None
    tax_id: str | None = None
    address: str | None = None


class InvoiceERP(BaseModel):
    number: str | None = None
    date: Date | None = None
    due_date: Date | None = None
    currency: str | None = None


class AmountsERP(BaseModel):
    ht: float | None = None
    tva: float | None = None
    ttc: float | None = None
    tax_rate: float | None = None


class Metadata(BaseModel):
    ocr_engine: str
    confidence: float | None = None
    source_file: str
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ERPInvoiceJSON(BaseModel):
    document_type: str = "invoice"
    validation_status: str | None = None
    supplier: SupplierERP
    customer: CustomerERP | None = None
    document: InvoiceERP | None = None
    invoice: InvoiceERP
    amounts: AmountsERP
    line_items: list[LineItem] = Field(default_factory=list)
    quality: dict[str, Any] = Field(default_factory=dict)
    expanded_fields: dict[str, FieldExtractionDetail] = Field(default_factory=dict)
    validation: ValidationResult
    metadata: Metadata


class ERPFlatExport(BaseModel):
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    invoice_ref: str | None = None
    invoice_date: Date | None = None
    due_date: Date | None = None
    amount_excl_tax: float | None = None
    tax_amount: float | None = None
    amount_incl_tax: float | None = None
    currency_code: str | None = None
    validation_status: str
    source_payload: dict[str, Any] | None = None


class ProcessInvoiceResponse(BaseModel):
    extracted_text: str
    document_preview: DocumentPreview | None = None
    layout_blocks: list[LayoutBlock] = Field(default_factory=list)
    field_boxes: list[FieldBox] = Field(default_factory=list)
    ocr_blocks: list[OCRLine] = Field(default_factory=list)
    document_classification: DocumentClassification | None = None
    detected_fields: ExtractedInvoiceFields
    expanded_fields: dict[str, FieldExtractionDetail] = Field(default_factory=dict)
    field_confidences: dict[str, float] = Field(default_factory=dict)
    extraction_debug: dict[str, Any] = Field(default_factory=dict)
    dynamic_tables: list[DynamicTable] = Field(default_factory=list)
    extraction_layer: ExtractionLayer | None = None
    erp_layer: ERPLayer | None = None
    validation: ValidationResult
    validation_explanation: ValidationExplanation | None = None
    erp_json: ERPInvoiceJSON
    erp_export: ERPFlatExport



