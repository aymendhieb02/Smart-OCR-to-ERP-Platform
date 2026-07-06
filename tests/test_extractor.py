from app.services.field_extractor import extract_invoice_fields


def test_invoice_number_and_dates_are_extracted():
    text = """
    ABC Services
    Facture N° FAC-2026-0015
    Date facture: 12/06/2026
    Echéance: 12/07/2026
    Total HT: 1 000,00
    TVA 19%: 190,00
    Total TTC: 1 190,00 TND
    """
    fields = extract_invoice_fields(text)
    assert fields.invoice_number == "FAC-2026-0015"
    assert fields.invoice_date.isoformat() == "2026-06-12"
    assert fields.due_date.isoformat() == "2026-07-12"
    assert fields.amount_ht == 1000.0
    assert fields.tva_amount == 190.0
    assert fields.amount_ttc == 1190.0
    assert fields.currency == "TND"


def test_english_keywords_are_supported():
    text = """
    Northwind Traders
    Invoice Number: INV-8842
    Invoice Date: 2026-06-10
    Due Date: 2026-07-10
    Subtotal 250.00
    VAT 20% 50.00
    Total Amount USD 300.00
    """
    fields = extract_invoice_fields(text)
    assert fields.invoice_number == "INV-8842"
    assert fields.invoice_date.isoformat() == "2026-06-10"
    assert fields.amount_ht == 250.0
    assert fields.tva_amount == 50.0
    assert fields.amount_ttc == 300.0


def test_vital_distribution_ocr_output_is_extracted():
    text = """
    VITAL FACTURE
    DISTRIBUTION
    N° Facture : FAC-2026-0042
    Date: 06/05/2026
    Vital Distribution Date d’échéance : 21/05/2026
    15 Rue des Entrepreneurs Réf. Client : CLI-1123
    1002 Tunis, Tunisie
    Tél: +216 71123 456 Client
    MF : 1234567A/M/000 PHARMA PLUS
    ICE : 001234567890123 Avenue Habib Bourguiba, 45
    3000 Sfax, Tunisie
    MF : 1467890B/M/000
    ICE : 001246789012345
    Email : contact@pharmaplus.tn
    Email : contact@vital-distribution.tn
    Code Produit
    Paracetamol 500mg PAR-500
    7] Amoxicillin 1g AMO-1G Ss 1.250 = 37.500
    Arrété la présente facture a la somme de:
    Cent quatre dinars sept cent cinquante-et-un millimes
    (104.751 TND)
    Conditions de paiement :
    + Paiement a 15 jours
    Coordonnées bancaires :
    Banque : STB
    RIB —- 05 012 3456789012345678 06
    IBAN : TN59 0501 2345 6789 0123 4567 806
    SWIFT : STBKTINTT
    Sous-total HT 87.000
    TVA (19%) 16.531
    Total TTC 104.751 TND
    """
    fields = extract_invoice_fields(text)
    assert fields.supplier_name == "Vital Distribution"
    assert fields.invoice_number == "FAC-2026-0042"
    assert fields.invoice_date.isoformat() == "2026-05-06"
    assert fields.due_date.isoformat() == "2026-05-21"
    assert fields.currency == "TND"
    assert fields.amount_ht == 87.0
    assert fields.tva_amount == 16.531
    assert fields.amount_ttc == 104.751
    assert fields.tax_rate == 19.0
    assert fields.supplier_tax_id == "1234567A/M/000"
    assert fields.line_items == []


def test_clean_table_line_items_are_extracted_and_bank_lines_are_ignored():
    text = """
    Designation Code Produit Qte Prix Unit. TVA Total
    Paracetamol 500mg PAR-500 50 0.450 19 22.500
    Amoxicillin 1g AMO-1G 30 1.250 19 37.500
    Coordonnees bancaires
    RIB : 05 012 3456789012345678 06
    IBAN : TN59 0501 2345 6789 0123 4567 806
    """
    fields = extract_invoice_fields(text)
    assert len(fields.line_items) == 2
    assert fields.line_items[0].description == "Paracetamol 500mg"
    assert fields.line_items[0].quantity == 50
    assert fields.line_items[0].unit_price == 0.45
    assert fields.line_items[0].total == 22.5


def test_currency_customer_tax_id_and_rows_from_region_ocr_are_extracted():
    text = """
    Vital Distribution
    15 Rue des Entrepreneurs
    MF : 1234567A/M/000
    Client
    PHARMALINE SARL
    MF : 1122334A/M/000
    1 Doliprane 500mg (Paracetamol) DOL-500 80 0.420 19 33.600
    2 Amoxicilline 1g AMX-1G 50 0.750 19 37.500
    Coordonnees bancaires
    RIB 05 012 3456789012345678 06
    Total TTC 169.651 TND
    """
    fields = extract_invoice_fields(text)
    assert fields.currency == "TND"
    assert fields.customer_tax_id == "1122334A/M/000"
    assert len(fields.line_items) == 2
    assert fields.line_items[0].description == "Doliprane 500mg (Paracetamol)"
