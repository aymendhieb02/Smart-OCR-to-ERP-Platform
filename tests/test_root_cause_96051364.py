from app.core.schemas import BoundingBox, ExtractedInvoiceFields, OCRLine
from app.services.extraction_quality import apply_extraction_quality_gate
from app.services.field_enricher import build_expanded_fields
from app.services.field_extractor import extract_with_candidates


def b(text: str, x1: float, y1: float, x2: float, y2: float, idx: int, conf: float = 0.99) -> OCRLine:
    return OCRLine(
        text=text,
        confidence=conf,
        page_number=1,
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        line_index=idx,
    )


def invoice_96051364_blocks() -> list[OCRLine]:
    rows = [
        b("Invoice no: 96051364", 196, 110, 805, 161, 0),
        b("Date of issue:", 196, 212, 521, 259, 1),
        b("04/28/2020", 1204, 216, 1469, 256, 2),
        b("Seller:", 192, 665, 388, 716, 3),
        b("Client:", 1236, 657, 1430, 721, 4),
        b("Jackson Ltd", 202, 752, 455, 812, 5),
        b("Williams, Fowler and Phillips", 1244, 753, 1846, 808, 6),
        b("134 Catherine Valley Apt. 756", 210, 811, 842, 859, 7),
        b("115 Mccormick Knolls", 1248, 807, 1710, 863, 8),
        b("Lake Jennifer, NE 09531", 207, 866, 713, 914, 9),
        b("North Kevin, TN 27580", 1248, 866, 1728, 914, 10),
        b("Tax Id: 914-85-0938", 207, 972, 639, 1020, 11),
        b("Tax Id: 963-87-9620", 1248, 972, 1680, 1020, 12),
        b("IBAN: GB61XXNT90578783067262", 210, 1030, 938, 1067, 13),
        b("ITEMS", 188, 1140, 377, 1191, 14),
        b("No.", 233, 1268, 303, 1308, 15),
        b("Description", 358, 1268, 576, 1308, 16),
        b("Qty", 1012, 1261, 1097, 1316, 17),
        b("UM", 1159, 1264, 1237, 1308, 18),
        b("Net price", 1344, 1268, 1528, 1308, 19),
        b("Net worth", 1580, 1264, 1780, 1305, 20),
        b("VAT [%]", 1842, 1268, 2001, 1308, 21),
        b("Gross", 2134, 1261, 2259, 1312, 22),
        b("worth", 2129, 1296, 2261, 1357, 23),
    ]
    body = [
        ("1", '49"x22"Marble Top Center', "Table Carnelian Mosaic Italian", "Art Christmas Gift Arts", "4,00", "each", "2400,00", "9600,00", "10%", "10 560,00", 1400),
        ("2.", "6'x3' Marble Dining Center", "Table With Black Top Floral", "Collectible Inlaid E963", "3,00", "each", "6 040,40", "18 121,20", "10%", "19 933,32", 1570),
        ("3.", "7'x4' Blue Random Marble", "Dining Hallway Table Top Lapis", "Lazuli Inlay Decor E947A", "5,00", "each", "8 194,09", "40 970,45", "10%", "45 067,50", 1740),
        ("4", '36" Marble White Top Counter', "Table Lapis Inlaid Pietradura Art", "Christmas Gifts", "3,00", "each", "3 200,00", "9 600,00", "10%", "10 560,00", 1910),
        ("5", "6'x3' White Marble Dining Table", "Top Multi Inlay Floral Elephant", "Art Decor H4952", "5,00", "each", "6408,49", "32 042,45", "10%", "35246,69", 2085),
        ("6", "6'x3' Marble Dining Center", "Table Marquetry Inlay Italian", "Mosaic Patio Arts H3112", "5,00", "each", "6 647,61", "33 238,05", "10%", "36 561,85", 2258),
    ]
    idx = len(rows)
    for number, desc1, desc2, desc3, qty, unit, price, net, vat, gross, y in body:
        rows.extend([
            b(number, 250, y + 8, 285, y + 35, idx),
            b(desc1, 355, y, 900, y + 40, idx + 1),
            b(qty, 1004, y, 1104, y + 44, idx + 2),
            b(unit, 1141, y, 1252, y + 44, idx + 3),
            b(price, 1364, y, 1533, y + 44, idx + 4),
            b(net, 1600, y, 1788, y + 44, idx + 5),
            b(vat, 1909, y, 2010, y + 44, idx + 6),
            b(gross, 2073, y, 2257, y + 44, idx + 7),
            b(desc2, 355, y + 44, 900, y + 84, idx + 8),
            b(desc3, 355, y + 88, 900, y + 128, idx + 9),
        ])
        idx += 10
    rows.extend([
        b("SUMMARY", 192, 2481, 491, 2532, idx),
        b("VAT [%]", 956, 2605, 1122, 2657, idx + 1),
        b("Net worth", 1388, 2609, 1588, 2649, idx + 2),
        b("VAT", 1780, 2605, 1868, 2649, idx + 3),
        b("Gross worth", 2027, 2609, 2259, 2649, idx + 4),
        b("10%", 993, 2689, 1085, 2744, idx + 5),
        b("143 572,15", 1388, 2697, 1588, 2737, idx + 6),
        b("14 357,22", 1678, 2685, 1869, 2745, idx + 7),
        b("157 929,37", 2064, 2697, 2259, 2737, idx + 8),
        b("Total", 746, 2781, 857, 2832, idx + 9),
        b("$ 143 572,15", 1336, 2784, 1588, 2825, idx + 10),
        b("$14 357,22", 1632, 2788, 1861, 2825, idx + 11),
        b("$ 157 929,37", 2008, 2784, 2256, 2825, idx + 12),
    ])
    return rows


def test_invoice_96051364_root_cause_regression():
    blocks = invoice_96051364_blocks()
    text = "\n".join(block.text for block in blocks)

    fields, candidates, confidences, debug = extract_with_candidates(text, blocks)

    assert fields.invoice_number == "96051364"
    assert str(fields.invoice_date) == "2020-04-28"
    assert fields.supplier_name == "Jackson Ltd"
    assert fields.customer_name == "Williams, Fowler and Phillips"
    assert fields.supplier_tax_id == "914-85-0938"
    assert fields.customer_tax_id == "963-87-9620"
    assert fields.supplier_bank_iban == "GB61XXNT90578783067262"
    assert fields.amount_ht == 143572.15
    assert fields.tva_amount == 14357.22
    assert fields.amount_ttc == 157929.37
    assert fields.tax_rate == 10.0
    assert len(fields.line_items) == 6
    assert fields.line_items[0].line_total_ttc == 10560.0
    assert fields.line_items[1].unit_price == 6040.4
    assert fields.line_items[-1].line_total_ht == 33238.05
    assert all((value is None or 0 <= value <= 1) for value in confidences.values())
    assert "field_traces" in debug
    assert debug["field_traces"]["amount_ttc"]["selected_value"] == 157929.37
    assert candidates["amount_ttc"][0].value is not None

    quality_gate = apply_extraction_quality_gate(fields, candidates, confidences)
    assert quality_gate.sanitized_fields.amount_ht == 143572.15
    assert quality_gate.sanitized_fields.tva_amount == 14357.22
    assert quality_gate.sanitized_fields.amount_ttc == 157929.37
    assert len(quality_gate.sanitized_fields.line_items) == 6


def test_expanded_iban_does_not_cross_line_breaks():
    expanded = build_expanded_fields(
        ExtractedInvoiceFields(),
        candidates={},
        field_confidences={},
        extracted_text="IBAN: GB61XXNT90578783067262\nITEMS\nNo.",
    )

    assert expanded["bank_iban"].value == "GB61XXNT90578783067262"
