from app.core.schemas import (
    AmountsERP,
    CustomerERP,
    ERPFlatExport,
    ERPInvoiceJSON,
    ExtractedInvoiceFields,
    FieldExtractionDetail,
    InvoiceERP,
    Metadata,
    SupplierERP,
    ValidationResult,
)


def build_erp_json(
    fields: ExtractedInvoiceFields,
    validation: ValidationResult,
    source_file: str,
    ocr_engine: str,
    confidence: float | None,
    document_type: str = "invoice",
    field_confidences: dict[str, float] | None = None,
    languages: list[str] | None = None,
    expanded_fields: dict[str, FieldExtractionDetail] | None = None,
) -> ERPInvoiceJSON:
    document = InvoiceERP(
        number=fields.invoice_number,
        date=fields.invoice_date,
        due_date=fields.due_date,
        currency=fields.currency,
    )
    return ERPInvoiceJSON(
        document_type=document_type,
        validation_status=validation.status,
        supplier=SupplierERP(name=fields.supplier_name, tax_id=fields.supplier_tax_id, address=fields.supplier_address),
        customer=CustomerERP(name=fields.customer_name, tax_id=fields.customer_tax_id, address=fields.customer_address),
        document=document,
        invoice=document,
        amounts=AmountsERP(
            ht=fields.amount_ht,
            tva=fields.tva_amount,
            ttc=fields.amount_ttc,
            tax_rate=fields.tax_rate,
        ),
        line_items=fields.line_items,
        quality={
            "overall_confidence": confidence,
            "confidence_type": "uncalibrated_composite_index",
            "confidence_display_name": "Composite Confidence Index",
            "field_confidences": field_confidences or {},
            "needs_human_review": validation.status != "valid",
            "languages": languages or ["fr", "en", "ar"],
        },
        expanded_fields=expanded_fields or {},
        validation=validation,
        metadata=Metadata(
            ocr_engine=ocr_engine,
            confidence=confidence,
            source_file=source_file,
        ),
    )


def map_to_flat_erp(erp_json: ERPInvoiceJSON) -> ERPFlatExport:
    status = erp_json.validation.status
    return ERPFlatExport(
        vendor_name=erp_json.supplier.name,
        vendor_tax_id=erp_json.supplier.tax_id,
        invoice_ref=erp_json.invoice.number,
        invoice_date=erp_json.invoice.date,
        due_date=erp_json.invoice.due_date,
        amount_excl_tax=erp_json.amounts.ht,
        tax_amount=erp_json.amounts.tva,
        amount_incl_tax=erp_json.amounts.ttc,
        currency_code=erp_json.invoice.currency,
        validation_status=status,
        source_payload=erp_json.model_dump(mode="json"),
    )
