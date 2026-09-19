"""Plaka normalizasyonu ve benzer plaka onerisi.

Ham okuma (raw) her zaman ayrica saklanir; karsilastirmalar normalize
edilmis deger uzerinden yapilir.
"""
import re
from difflib import SequenceMatcher


def normalize_plate(raw: str) -> str:
    """Buyuk harfe cevirir; bosluk, tire, nokta ve diger gereksiz karakterleri kaldirir.

    Ornek: '34 abc 123' -> '34ABC123', '34-ABC-123' -> '34ABC123'
    """
    text = (raw or "").upper()
    # Turkce karakterleri sadelestir (I dahil degil - plakada zaten yok)
    text = text.replace("Ç", "C").replace("Ğ", "G").replace("Ö", "O").replace("Ş", "S").replace("Ü", "U").replace("İ", "I")
    return re.sub(r"[^A-Z0-9]", "", text)


def format_plate(raw: str | None) -> str:
    """Gosterim: 34GGG01 -> 34 GGG 01; kayit/eslestirme degerini degistirmez.

    Basta bulunan sifirlar korunur. Plakaya benzemeyen metin, yabanci plaka ve
    bos ekran isaretleri oldugu gibi kalir. Bu fonksiyon OCR dogrulamasi yapmaz.
    """
    match = re.fullmatch(r"(\d{2})([A-Z]{1,3})(\d{2,5})", normalize_plate(raw))
    return " ".join(match.groups()) if match else (raw or "").strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def suggest_similar_plates(target: str, candidates: list[str], limit: int = 5,
                            min_ratio: float = 0.6) -> list[tuple[str, float]]:
    """Cikista eslesmeyen plaka icin benzer plaka onerileri (yuksek benzerlikten dusuge).

    Ornek: kamera 34ABC123 yerine 34A8C123 okursa, iceride bulunan 34ABC123 onerilir.
    """
    target = normalize_plate(target)
    scored = []
    for c in candidates:
        r = similarity(target, normalize_plate(c))
        if r >= min_ratio:
            scored.append((c, r))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:limit]
