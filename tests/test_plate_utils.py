"""Madde 28: Plaka normalizasyonu ve benzer plaka bulma testleri."""
import pytest

from app.services.plate_utils import (
    normalize_plate, format_plate, suggest_similar_plates, similarity,
)


@pytest.mark.parametrize("raw, expected", [
    ("34GGG01", "34 GGG 01"),
    ("34 ggg 01", "34 GGG 01"),
    ("34-GGG-01", "34 GGG 01"),
    ("01AB001", "01 AB 001"),
    ("06A1234", "06 A 1234"),
    ("06A12345", "06 A 12345"),
    ("34 çğş 123", "34 CGS 123"),
    ("B-AB 1234", "B-AB 1234"),
    ("GIRIS-1", "GIRIS-1"),
    ("— — — —", "— — — —"),
    ("-", "-"),
    ("", ""),
    (None, ""),
])
def test_plate_display(raw, expected):
    assert format_plate(raw) == expected
    assert format_plate(expected) == expected
    assert normalize_plate(format_plate(raw)) == normalize_plate(raw)


def test_spaced_exit_matches_existing_compact_entry(svc, isolated_db):
    from unittest.mock import MagicMock
    from tests.conftest import make_event

    logs = []
    svc.event_logged.connect(logs.append)
    svc.barriers.open = MagicMock(side_effect=AssertionError("No hardware allowed"))
    entry = svc.ingest_camera_event(make_event(plate="34GGG01"))
    isolated_db.set_setting("free_pass_mode", "1")
    result = svc.ingest_camera_event(make_event(
        direction="EXIT", gate="CIKIS-1", plate="34 GGG 01"))
    assert result["status"] == "FREE_PASS_EXIT"
    assert result["session_id"] == entry["session_id"]
    assert isolated_db.list_inside_sessions() == []
    assert isolated_db.get_session(entry["session_id"])["plate"] == "34GGG01"
    assert any("SERBEST GECIS CIKISI: 34 GGG 01" in line for line in logs)
    svc.barriers.open.assert_not_called()


class TestNormalizePlate:
    def test_bosluklu_plaka(self):
        assert normalize_plate("34 abc 123") == "34ABC123"

    def test_tireli_plaka(self):
        assert normalize_plate("34-ABC-123") == "34ABC123"

    def test_noktali_plaka(self):
        assert normalize_plate("34.ABC.123") == "34ABC123"

    def test_karisik_karakterler(self):
        assert normalize_plate("  34_ABC 123! ") == "34ABC123"

    def test_turkce_karakterler(self):
        assert normalize_plate("34 çğöşü 123") == "34CGOSU123"

    def test_kucuk_harf(self):
        assert normalize_plate("34abc123") == "34ABC123"

    def test_bos_string(self):
        assert normalize_plate("") == ""

    def test_none(self):
        assert normalize_plate(None) == ""

    def test_sadece_sayilar(self):
        assert normalize_plate("34 123") == "34123"


class TestSimilarPlates:
    def test_benzer_plaka_onerisi(self):
        # 34ABC123 yerine 34A8C123 okunursa, 34ABC123 onerilmeli
        candidates = ["34ABC123", "06XYZ42", "35DEF789"]
        result = suggest_similar_plates("34A8C123", candidates)
        plates = [p for p, _ in result]
        assert "34ABC123" in plates

    def test_eslesme_yuksek_benzerlik(self):
        candidates = ["34ABC123"]
        result = suggest_similar_plates("34ABC123", candidates)
        assert result[0][0] == "34ABC123"
        assert result[0][1] == 1.0

    def test_dusuk_benzerlik_eleme(self):
        candidates = ["34ABC123", "06XYZ999"]
        result = suggest_similar_plates("34A8C123", candidates)
        plates = [p for p, _ in result]
        assert "06XYZ999" not in plates  # cok dusuk benzerlik

    def test_bos_aday_listesi(self):
        result = suggest_similar_plates("34ABC123", [])
        assert result == []

    def test_limit(self):
        candidates = ["34ABC123", "34ABC124", "34ABC125", "34ABC126", "34ABC127", "34ABC128"]
        result = suggest_similar_plates("34ABC123", candidates, limit=3)
        assert len(result) <= 3
