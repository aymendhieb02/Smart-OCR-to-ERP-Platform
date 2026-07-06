import re
import unicodedata
from datetime import date, datetime

from dateutil import parser


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def normalize_digits(text: str) -> str:
    arabic_digits = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    return (text or "").translate(arabic_digits)


def parse_amount(value: str | None) -> float | None:
    if not value:
        return None
    value = normalize_digits(value)
    cleaned = re.sub(r"[^\d,.\-]", "", value)
    if not cleaned:
        return None
    sign = "-" if cleaned.startswith("-") else ""
    cleaned = cleaned.lstrip("+-")
    if cleaned.count(".") > 1 and "," not in cleaned:
        parts = cleaned.split(".")
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    elif cleaned.count(",") > 1 and "." not in cleaned:
        parts = cleaned.split(",")
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    elif "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return round(float(sign + cleaned), 3)
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = normalize_digits(value)
    value = _normalize_month_names(value)
    iso_match = re.search(r"\b(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})\b", value)
    if iso_match:
        try:
            return datetime.strptime(iso_match.group(0).replace("/", "-").replace(".", "-"), "%Y-%m-%d").date()
        except ValueError:
            return None
    try:
        parsed = parser.parse(value, dayfirst=True, fuzzy=True)
        return parsed.date()
    except (ValueError, TypeError, OverflowError):
        return None


def _normalize_month_names(value: str) -> str:
    months = {
        "janvier": "january", "fevrier": "february", "février": "february",
        "mars": "march", "avril": "april", "mai": "may", "juin": "june",
        "juillet": "july", "aout": "august", "août": "august",
        "septembre": "september", "octobre": "october",
        "novembre": "november", "decembre": "december", "décembre": "december",
    }
    result = value
    for fr, en in months.items():
        result = re.sub(fr, en, result, flags=re.IGNORECASE)
    return result


def first_match(patterns: list[str], text: str, flags: int = re.IGNORECASE | re.MULTILINE) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            groups = [group for group in match.groups() if group]
            return groups[0].strip() if groups else match.group(0).strip()
    return None
