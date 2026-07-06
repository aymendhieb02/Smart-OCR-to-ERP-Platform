import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main() -> None:
    sample_dir = ROOT / "app" / "samples"
    image_dir = ROOT / "dataset" / "images"
    label_dir = ROOT / "dataset" / "labels"
    sample_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(img)
    blue = "#003A99"
    black = "#111111"
    grid = "#B8B8B8"

    draw.text((60, 60), "VITAL DISTRIBUTION", fill=blue, font=font(38, True))
    draw.text((850, 60), "FACTURE", fill=blue, font=font(48, True))

    supplier = [
        "Vital Distribution",
        "15 Rue des Entrepreneurs",
        "1002 Tunis, Tunisie",
        "Tel: +216 71 123 456",
        "MF: 1234567A/M/000",
        "ICE: 001234567890123",
        "Email: contact@vital-distribution.tn",
    ]
    y = 170
    for line in supplier:
        draw.text((60, y), line, fill=black, font=font(22, line == "Vital Distribution"))
        y += 36

    meta = [
        "N° Facture: FAC-2026-0099",
        "Date: 20/06/2026",
        "Date d'echeance: 05/07/2026",
        "Ref. Client: CLI-1200",
    ]
    y = 170
    for line in meta:
        draw.text((760, y), line, fill=black, font=font(23, True))
        y += 48

    draw.rounded_rectangle((720, 360, 1130, 590), radius=8, outline="#8BA9E6", width=2)
    client = [
        ("Client", True),
        ("PHARMA TEST SARL", True),
        ("Avenue Habib Bourguiba, 10", False),
        ("1000 Tunis, Tunisie", False),
        ("MF: 9988776B/M/000", False),
        ("ICE: 009988776655443", False),
        ("Email: contact@pharmatest.tn", False),
    ]
    y = 382
    for line, is_bold in client:
        draw.text((745, y), line, fill=black, font=font(21, is_bold))
        y += 30

    table_y = 660
    draw.rectangle((60, table_y, 1130, table_y + 55), fill=blue)
    headers = ["#", "Designation", "Code Produit", "Qte", "Prix Unit. (TND)", "TVA (%)", "Total (TND)"]
    xs = [80, 150, 470, 650, 760, 950, 1040]
    for x, header in zip(xs, headers):
        draw.text((x, table_y + 15), header, fill="white", font=font(20, True))

    rows = [
        ["1", "Paracetamol 500mg", "PAR-500", "50", "0.450", "19", "22.500"],
        ["2", "Amoxicilline 1g", "AMX-1G", "30", "1.250", "19", "37.500"],
        ["3", "Vitamine C 1000mg", "VTC-1000", "20", "0.950", "19", "19.000"],
        ["4", "Seringue 5ml", "SYR-5ML", "100", "0.250", "19", "25.000"],
    ]
    y = table_y + 72
    for row in rows:
        draw.line((60, y - 14, 1130, y - 14), fill=grid, width=2)
        for x, value in zip(xs, row):
            draw.text((x, y), value, fill=black, font=font(20))
        y += 58
    draw.rectangle((60, table_y, 1130, y - 10), outline=grid, width=2)

    draw.rounded_rectangle((650, 1040, 1130, 1270), radius=8, outline=grid, width=2)
    totals = [
        "Sous-total HT        104.000",
        "TVA (19%)             19.760",
        "Remise                 0.000",
        "Total TTC            123.760 TND",
    ]
    y = 1065
    for line in totals:
        draw.text((680, y), line, fill=black, font=font(23, line.startswith("Total")))
        y += 48

    notes = [
        "Arrete la presente facture a la somme de:",
        "Cent vingt-trois dinars sept cent soixante millimes",
        "(123.760 TND)",
        "",
        "Conditions de paiement:",
        "- Paiement a 15 jours",
        "- Reglement par virement bancaire",
    ]
    y = 1045
    for line in notes:
        draw.text((70, y), line, fill=black, font=font(18, line.endswith(":")))
        y += 30

    draw.text((430, 1500), "Merci pour votre confiance !", fill=blue, font=font(24, True))

    out_sample = sample_dir / "golden_invoice.png"
    out_dataset = image_dir / "golden_invoice.png"
    img.save(out_sample)
    img.save(out_dataset)

    label = {
        "document_type": "invoice",
        "supplier_name": "Vital Distribution",
        "supplier_tax_id": "1234567A/M/000",
        "customer_name": "PHARMA TEST SARL",
        "customer_tax_id": "9988776B/M/000",
        "invoice_number": "FAC-2026-0099",
        "invoice_date": "2026-06-20",
        "due_date": "2026-07-05",
        "currency": "TND",
        "amount_ht": 104.0,
        "tva_amount": 19.76,
        "amount_ttc": 123.76,
        "tax_rate": 19.0,
    }
    (label_dir / "golden_invoice.json").write_text(json.dumps(label, indent=2), encoding="utf-8")
    print(out_sample)


if __name__ == "__main__":
    main()
