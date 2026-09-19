"""Ortak yardimcilar: saat dilimi (Europe/Istanbul), Decimal para donusumleri."""
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")

TWO_PLACES = Decimal("0.01")


def now() -> datetime:
    """Europe/Istanbul saat diliminde, saniye hassasiyetinde simdiki zaman."""
    return datetime.now(TZ).replace(microsecond=0)


def now_iso() -> str:
    return now().isoformat()


def parse_dt(value: str) -> datetime:
    """ISO metni datetime'a cevirir; saat dilimi yoksa Istanbul varsayilir."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def money(value) -> Decimal:
    """Her turden girdiyi 2 basamakli Decimal'e cevirir. Parada float KULLANMAYIN."""
    if isinstance(value, Decimal):
        return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def money_to_db(value) -> str:
    """Decimal parayi SQLite'ta kayipsiz saklamak icin metne cevirir."""
    return str(money(value))


def money_from_db(value) -> Decimal:
    if value is None or value == "":
        return money(0)
    return money(value)


def fmt_money(value, currency: str = "TL") -> str:
    return f"{money(value):.2f} {currency}"


def new_uuid() -> str:
    return uuid.uuid4().hex
