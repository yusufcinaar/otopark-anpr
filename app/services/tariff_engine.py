"""Tarife motoru.

- Blok tarife (sure araligina gore sabit ucret) + her ek 24 saat ucreti destekler.
- Ilk X dakika ucretsiz bekleme suresi uygulanir.
- Para hesaplamalari Decimal ile yapilir (float kullanilmaz).
- Arac giris yaptiginda gecerli tarifenin kurallari oturuma SNAPSHOT olarak
  kaydedilir; sonradan yapilan tarife degisiklikleri gecmis kayitlari etkilemez.

Kural formati (rules_json):
{
  "type": "blocks",
  "free_minutes": 15,
  "blocks": [{"upto_minutes": 60, "price": "80"}, ...],   # artan sirali
  "extra_day_price": "500",
  "daily_max": "500"  (opsiyonel),
  "currency": "TL"
}
Saatlik tarife istenirse: {"type": "hourly", "free_minutes": 0, "hourly_price": "20"}
"""
import json
import math
from datetime import datetime
from decimal import Decimal

from app.utils import money


def calculate_duration_minutes(entry: datetime, exit_: datetime) -> int:
    seconds = (exit_ - entry).total_seconds()
    return max(0, math.ceil(seconds / 60))


def calculate_fee_from_rules(rules: dict, duration_minutes: int) -> Decimal:
    free = int(rules.get("free_minutes", 0) or 0)
    if duration_minutes <= free:
        return money(0)

    rtype = rules.get("type", "blocks")

    if rtype == "hourly":
        billable = duration_minutes - free
        hours = math.ceil(billable / 60)
        fee = money(rules.get("hourly_price", "0")) * hours
        daily_max = rules.get("daily_max")
        if daily_max:
            days = math.ceil(duration_minutes / 1440)
            fee = min(fee, money(daily_max) * days)
        return money(fee)

    # blok tarife
    blocks = rules.get("blocks", [])
    if not blocks:
        return money(0)

    last_block_limit = int(blocks[-1]["upto_minutes"])

    if duration_minutes <= last_block_limit:
        for b in blocks:
            if duration_minutes <= int(b["upto_minutes"]):
                return money(b["price"])
        return money(blocks[-1]["price"])

    # son blogu (ornek: 24 saat) asan kisim: her ek 24 saat icin ek ucret
    fee = money(blocks[-1]["price"])
    extra_day_price = money(rules.get("extra_day_price", blocks[-1]["price"]))
    extra_minutes = duration_minutes - last_block_limit
    extra_days = math.ceil(extra_minutes / 1440)
    fee += extra_day_price * extra_days
    return money(fee)


def calculate_fee(rules: dict, entry: datetime, exit_: datetime) -> tuple[int, Decimal]:
    """Returns (sure_dakika, ucret_Decimal)."""
    minutes = calculate_duration_minutes(entry, exit_)
    return minutes, calculate_fee_from_rules(rules, minutes)


def snapshot_str(rules: dict) -> str:
    return json.dumps(rules, ensure_ascii=False)


def rules_from_snapshot(snapshot: str | None, fallback: dict | None = None) -> dict:
    if snapshot:
        try:
            return json.loads(snapshot)
        except (ValueError, TypeError):
            pass
    return fallback or {}


def format_duration(minutes: int) -> str:
    minutes = max(0, int(minutes or 0))
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} gün")
    if hours:
        parts.append(f"{hours} saat")
    if mins or not parts:
        parts.append(f"{mins} dakika")
    return " ".join(parts)
