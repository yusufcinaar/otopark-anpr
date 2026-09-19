"""Madde 28: Tarife motoru testleri - blok tarife, sinir dakikalar, gece yarisi, cok gunlu, snapshot."""
from datetime import datetime, timedelta
from decimal import Decimal

from app.services.tariff_engine import (
    calculate_fee, calculate_fee_from_rules, snapshot_str, rules_from_snapshot, format_duration,
)
from app.utils import TZ, money

# Baslangic test tarifesi (madde 10)
TEST_RULES = {
    "type": "blocks",
    "free_minutes": 15,
    "blocks": [
        {"upto_minutes": 15, "price": "0"},
        {"upto_minutes": 60, "price": "80"},
        {"upto_minutes": 120, "price": "120"},
        {"upto_minutes": 240, "price": "180"},
        {"upto_minutes": 480, "price": "250"},
        {"upto_minutes": 720, "price": "350"},
        {"upto_minutes": 1440, "price": "500"},
    ],
    "extra_day_price": "500",
    "currency": "TL",
}


class TestTariffBoundaries:
    """Tarife sinir dakikalarinin dogru ucretlendirmesi."""

    def _fee_for_minutes(self, minutes):
        return calculate_fee_from_rules(TEST_RULES, minutes)

    def test_0_dakika_ucretsiz(self):
        assert self._fee_for_minutes(0) == money(0)

    def test_15_dakika_ucretsiz(self):
        assert self._fee_for_minutes(15) == money(0)

    def test_16_dakika_80_tl(self):
        assert self._fee_for_minutes(16) == money(80)

    def test_60_dakika_80_tl(self):
        assert self._fee_for_minutes(60) == money(80)

    def test_61_dakika_120_tl(self):
        assert self._fee_for_minutes(61) == money(120)

    def test_120_dakika_120_tl(self):
        assert self._fee_for_minutes(120) == money(120)

    def test_121_dakika_180_tl(self):
        assert self._fee_for_minutes(121) == money(180)

    def test_240_dakika_180_tl(self):
        assert self._fee_for_minutes(240) == money(180)

    def test_241_dakika_250_tl(self):
        assert self._fee_for_minutes(241) == money(250)

    def test_480_dakika_250_tl(self):
        assert self._fee_for_minutes(480) == money(250)

    def test_481_dakika_350_tl(self):
        assert self._fee_for_minutes(481) == money(350)

    def test_720_dakika_350_tl(self):
        assert self._fee_for_minutes(720) == money(350)

    def test_721_dakika_500_tl(self):
        assert self._fee_for_minutes(721) == money(500)

    def test_1440_dakika_500_tl(self):
        assert self._fee_for_minutes(1440) == money(500)


class TestTariffMultiDay:
    """Birden fazla gun kalan araclar ve ek gun ucreti."""

    def test_24_saatten_az_ek_gun_yok(self):
        assert calculate_fee_from_rules(TEST_RULES, 1441) == money(1000)  # 500 + 500

    def test_2_gun_1000_tl(self):
        assert calculate_fee_from_rules(TEST_RULES, 2880) == money(1000)

    def test_3_gun_1500_tl(self):
        assert calculate_fee_from_rules(TEST_RULES, 4320) == money(1500)


class TestTariffMidnight:
    """Gece yarisini gecen park suresi."""

    def test_gece_yarisi_gecisi(self):
        entry = datetime(2026, 8, 14, 23, 0, tzinfo=TZ)
        exit_ = datetime(2026, 8, 15, 1, 0, tzinfo=TZ)  # 2 saat
        minutes, fee = calculate_fee(TEST_RULES, entry, exit_)
        assert minutes == 120
        assert fee == money(120)

    def test_gece_yarisi_cok_gunlu(self):
        entry = datetime(2026, 8, 14, 22, 0, tzinfo=TZ)
        exit_ = datetime(2026, 8, 16, 3, 0, tzinfo=TZ)  # ~29 saat
        minutes, fee = calculate_fee(TEST_RULES, entry, exit_)
        assert fee == money(1000)  # 24 saat 500 + ek gun 500


class TestTariffSnapshot:
    """Tarife snapshot sistemi: giris anindaki tarife korunur."""

    def test_snapshot_string_uretilir(self):
        s = snapshot_str(TEST_RULES)
        assert isinstance(s, str)
        assert "blocks" in s

    def test_snapshot_geri_yuklenir(self):
        s = snapshot_str(TEST_RULES)
        rules = rules_from_snapshot(s)
        assert rules["type"] == "blocks"
        assert len(rules["blocks"]) == 7

    def test_snapshot_bos_fallback(self):
        rules = rules_from_snapshot(None, fallback=TEST_RULES)
        assert rules == TEST_RULES

    def test_snapshot_gecersiz_fallback(self):
        rules = rules_from_snapshot("gecersiz json", fallback=TEST_RULES)
        assert rules == TEST_RULES

    def test_snapshot_gecersiz_fallback_yok(self):
        rules = rules_from_snapshot("gecersiz json")
        assert rules == {}

    def test_tarife_degisikligi_snapshot_korunur(self):
        """Giris anindaki tarife snapshot'a yazilir; sonradaki degisiklik etkilemez."""
        original_rules = dict(TEST_RULES)
        snapshot = snapshot_str(original_rules)

        # tarife degistirildi: tum ucretler 1000 TL
        new_rules = dict(TEST_RULES)
        new_rules["blocks"] = [{"upto_minutes": 1440, "price": "1000"}]
        new_rules["free_minutes"] = 0

        # eski snapshot ile ucret hesapla
        old_rules = rules_from_snapshot(snapshot)
        fee_old = calculate_fee_from_rules(old_rules, 120)
        # yeni tarife ile ucret hesapla
        fee_new = calculate_fee_from_rules(new_rules, 120)

        assert fee_old == money(120)  # eski tarife
        assert fee_new == money(1000)  # yeni tarife
        assert fee_old != fee_new


class TestHourlyTariff:
    """Saatlik tarife turu."""

    def test_saatlik_tarife(self):
        rules = {"type": "hourly", "free_minutes": 0, "hourly_price": "20"}
        assert calculate_fee_from_rules(rules, 60) == money(20)
        assert calculate_fee_from_rules(rules, 61) == money(40)  # 2 baslayan saat
        assert calculate_fee_from_rules(rules, 120) == money(40)

    def test_saatlik_ucretsiz_bekleme(self):
        rules = {"type": "hourly", "free_minutes": 15, "hourly_price": "20"}
        assert calculate_fee_from_rules(rules, 15) == money(0)
        assert calculate_fee_from_rules(rules, 16) == money(20)


class TestFormatDuration:
    def test_dakika(self):
        assert format_duration(45) == "45 dakika"

    def test_saat_dakika(self):
        assert format_duration(90) == "1 saat 30 dakika"

    def test_gun_saat_dakika(self):
        assert format_duration(1500) == "1 gün 1 saat"
