from app.services.line_item_extractor import extract_line_items


def test_bank_details_are_rejected_as_line_items():
    text = """
    Product ABC-100 2 10.000 19 20.000
    RIB 05 012 3456789012345678 06
    IBAN TN59 0501 2345 6789 0123 4567 806
    """
    items = extract_line_items(text)
    assert len(items) == 1
    assert items[0].description == "Product"

def test_wrapped_invoice_table_rows_are_extracted_from_ocr_boxes():
    from app.core.schemas import BoundingBox, OCRLine

    def block(text, x1, y1, x2, y2, index):
        return OCRLine(
            text=text,
            confidence=0.95,
            page_number=1,
            line_index=index,
            bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        )

    blocks = [
        block("#", 20, 140, 30, 158, 1),
        block("Description", 55, 140, 135, 158, 2),
        block("Quantity", 455, 140, 525, 158, 3),
        block("Price", 560, 140, 610, 158, 4),
        block("Total", 685, 140, 730, 158, 5),
        block("01", 18, 180, 36, 198, 6),
        block("415U Stikit Trim FC 5'xNH 240A 175 discs/roll 6", 55, 176, 400, 194, 7),
        block("rolls/case", 55, 196, 135, 214, 8),
        block("2", 485, 184, 495, 200, 9),
        block("$12.00", 560, 184, 610, 200, 10),
        block("$24.00", 676, 184, 730, 200, 11),
        block("02", 18, 225, 36, 243, 12),
        block("3M 777F RBC Belt 6 x202' 60YF 10belts/case (+/-", 55, 221, 405, 239, 13),
        block("10%", 55, 241, 95, 259, 14),
        block("4", 485, 229, 495, 245, 15),
        block("$11.00", 560, 229, 610, 245, 16),
        block("$44.00", 676, 229, 730, 245, 17),
        block("03", 18, 270, 36, 288, 18),
        block("HD Graphite Canvas 6'x 50yds 50 yards/roll", 55, 266, 390, 284, 19),
        block("5", 485, 274, 495, 290, 20),
        block("$10.00", 560, 274, 610, 290, 21),
        block("$50.00", 676, 274, 730, 290, 22),
        block("04", 18, 315, 36, 333, 23),
        block("3M 201+ Masking Tape TAN 24mm x 55m 36", 55, 311, 390, 329, 24),
        block("rolls/case", 55, 331, 135, 349, 25),
        block("4", 485, 319, 495, 335, 26),
        block("$9.00", 560, 319, 610, 335, 27),
        block("$36.00", 676, 319, 730, 335, 28),
        block("05", 18, 360, 36, 378, 29),
        block("3M 665 D/C Tape 12mm x 33m 72 rolls/case 9", 55, 356, 390, 374, 30),
        block("boxed", 55, 376, 110, 394, 31),
        block("5", 485, 364, 495, 380, 32),
        block("$8.00", 560, 364, 610, 380, 33),
        block("$40.00", 676, 364, 730, 380, 34),
        block("Subtotal", 560, 400, 620, 418, 35),
        block("$194.00", 676, 400, 730, 418, 36),
    ]

    items = extract_line_items("", blocks)

    assert len(items) == 5
    assert items[0].description == "415U Stikit Trim FC 5'xNH 240A 175 discs/roll 6 rolls/case"
    assert items[0].quantity == 2
    assert items[0].unit_price == 12
    assert items[0].line_total_ttc == 24
    assert items[1].description.endswith("10%")
    assert items[4].description.endswith("boxed")
    assert sum(item.line_total_ttc for item in items) == 194
