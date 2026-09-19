"""Turkiye plaka formati normallestirme ve dogrulama.

Format: <il kodu 2 hane><1-3 harf><2-4 hane>  ornek: 34 ABC 123, 06 A 1234
"""
import re
from app.services.plate_utils import format_plate

# Turkiye plakalarinda kullanilmayan harfler: I, O, Q, W, X (ve Turkce'ye ozgu olmayan harfler)
_PLATE_RE = re.compile(r"^(\d{2})([A-PRSTUVYZ]{1,3})(\d{2,4})$")

# OCR'da siklikla karisan karakterler (harf beklenen yerde rakam gorulmesi vb.)
_CONFUSION_TO_LETTER = {"0": "O", "1": "I", "8": "B", "5": "S", "2": "Z"}
_CONFUSION_TO_DIGIT = {"O": "0", "I": "1", "B": "8", "S": "5", "Z": "2", "Q": "0"}


def normalize(raw: str) -> str:
    """Bosluk/tire temizler, buyuk harfe cevirir."""
    text = raw.upper()
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text


def _try_fix(text: str):
    """Basit bolge bazli duzeltme: ilk 2 karakter rakam, ortadaki(ler) harf,
    sondaki(ler) rakam olacak sekilde OCR karisikliklarini gidermeye calisir."""
    if len(text) < 5 or len(text) > 8:
        return None
    # il kodu (ilk 2 karakter) rakam olmali
    head = "".join(_CONFUSION_TO_DIGIT.get(c, c) if not c.isdigit() else c for c in text[:2])
    rest = text[2:]

    # harf blogunu ve rakam blogunu ayirmak icin ortada 1-3 harf, sonda 2-4 rakam ara
    m = re.match(r"^([A-Z0-9]{1,3})([A-Z0-9]{2,4})$", rest)
    if not m:
        return None
    letters_raw, digits_raw = m.group(1), m.group(2)
    letters = "".join(_CONFUSION_TO_LETTER.get(c, c) if c.isdigit() else c for c in letters_raw)
    digits = "".join(_CONFUSION_TO_DIGIT.get(c, c) if not c.isdigit() else c for c in digits_raw)

    candidate = head + letters + digits
    return candidate if _PLATE_RE.match(candidate) else None


def parse_plate(raw: str):
    """Metni Turk plaka formatina uydurmaya calisir.

    Returns: (plaka_str, gecerli_mi) - gecerli_mi False ise `plaka_str` en iyi tahmindir.
    """
    text = normalize(raw)
    if _PLATE_RE.match(text):
        return text, True
    fixed = _try_fix(text)
    if fixed:
        return fixed, True
    return text, False


def format_display(plate: str) -> str:
    """34ABC123 -> 34 ABC 123 seklinde goruntuleme icin bicimlendirir."""
    return format_plate(plate)
