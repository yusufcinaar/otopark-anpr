"""Madde 28: Kamera olay alimi testleri - guven orani, idempotency, mukerrer giris, giris/çıkış."""
from app.services.parking_service import ParkingService
from app.utils import new_uuid, now_iso
from tests.conftest import make_event


class TestConfidenceThresholds:
    """Kamera guven orani esikleri (madde 6)."""

    def test_yuksek_guven_otomatik(self, svc, isolated_db):
        event = make_event(confidence=95)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "ENTRY_OK"

    def test_dusuk_guven_manuel_plaka(self, svc, isolated_db):
        event = make_event(confidence=50)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "MANUAL_PLATE_REQUIRED"

    def test_orta_guven_operator_onayi(self, svc, isolated_db):
        event = make_event(confidence=80)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "OPERATOR_CONFIRM_REQUIRED"

    def test_esik_degeri_90_otomatik(self, svc, isolated_db):
        event = make_event(confidence=90)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "ENTRY_OK"

    def test_esik_alti_89_operator(self, svc, isolated_db):
        event = make_event(confidence=89)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "OPERATOR_CONFIRM_REQUIRED"

    def test_operator_onayi_ile_isleme(self, svc, isolated_db):
        event = make_event(confidence=80)
        result = svc.ingest_camera_event(event, operator_confirmed=True)
        assert result["status"] == "ENTRY_OK"


class TestIdempotency:
    """Ayni event_id iki kez gelirse mukerrer kayit olusmaz (madde 6)."""

    def test_ayni_event_id_iki_kez(self, svc, isolated_db):
        event = make_event(confidence=95)
        r1 = svc.ingest_camera_event(event)
        assert r1["status"] == "ENTRY_OK"
        r2 = svc.ingest_camera_event(event)
        assert r2["status"] == "DUPLICATE_EVENT"

    def test_farkli_event_id_ayni_plaka_mukerrer_giris(self, svc, isolated_db):
        e1 = make_event(plate="34ABC123", confidence=95)
        r1 = svc.ingest_camera_event(e1)
        assert r1["status"] == "ENTRY_OK"

        e2 = make_event(plate="34ABC123", confidence=95)
        r2 = svc.ingest_camera_event(e2)
        assert r2["status"] == "DUPLICATE_ENTRY"
        assert "existing_session" in r2


class TestEntryFlow:
    """Giris kaydi olusturma (madde 5)."""

    def test_giris_kaydi_olusturulur(self, svc, isolated_db):
        event = make_event(plate="34ABC123", confidence=96)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "ENTRY_OK"
        assert result["plate"] == "34ABC123"
        session = isolated_db.get_session(result["session_id"])
        assert session["status"] == "ICERIDE"
        assert session["plate"] == "34ABC123"
        assert session["raw_plate"] == "34ABC123"
        assert session["entry_lane"] == "GIRIS-1"
        assert session["entry_camera"] == "LPR-GIRIS-01"
        assert session["confidence"] == 96.0
        assert session["tariff_snapshot"]  # snapshot alinmali
        assert session["tariff_name"]
        assert session["created_by"] == "KAMERA"

    def test_giriste_bariyer_komutu_gonderilmez(self, svc, isolated_db):
        event = make_event(plate="34ABC123", confidence=96)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "ENTRY_OK"
        assert svc.barriers.status("GIRIS-1") == "KAPALI"

    def test_normalize_edilmis_plaka_kaydedilir(self, svc, isolated_db):
        event = make_event(plate="34 abc 123", confidence=96)
        result = svc.ingest_camera_event(event)
        session = isolated_db.get_session(result["session_id"])
        assert session["plate"] == "34ABC123"
        assert session["raw_plate"] == "34 abc 123"


class TestEventValidation:
    def test_cikis_2_devre_disidir(self, svc, isolated_db):
        event = make_event(direction="EXIT", gate="CIKIS-2")
        result = svc.ingest_camera_event(event)
        assert result["status"] == "INVALID_EVENT"
        assert isolated_db.list_camera_events(10) == []

    def test_yon_ve_kapi_birbiriyle_uyumlu_olmalidir(self, svc, isolated_db):
        event = make_event(direction="ENTRY", gate="CIKIS-1")
        result = svc.ingest_camera_event(event)
        assert result["status"] == "INVALID_EVENT"

    def test_event_id_zorunludur(self, svc, isolated_db):
        event = make_event()
        event["event_id"] = ""
        result = svc.ingest_camera_event(event)
        assert result["status"] == "INVALID_EVENT"


class TestDefaultSiteDrivers:
    def test_temiz_kurulum_metcom_ve_led_ayarlarini_olusturur(self, isolated_db):
        isolated_db.ensure_standard_site_drivers()
        barrier = isolated_db.get_device_by_gate("CIKIS-1", "BARRIER")
        assert barrier is not None
        assert barrier["protocol"] == "MOCK"
        assert barrier["ip"] == ""
        assert barrier["online"] == 0
        assert isolated_db.get_setting("arma_led_ip") == ""
        assert isolated_db.get_setting("arma_led_enabled") == "0"

        # İkinci açılış kullanıcının sonradan yaptığı ayarı ezmemeli.
        isolated_db.update_device(barrier["id"], ip="198.51.100.7")
        isolated_db.ensure_standard_site_drivers()
        assert isolated_db.get_device_by_gate("CIKIS-1", "BARRIER")["ip"] == "198.51.100.7"


class TestExitFlow:
    """Cikis kaydi eslestirme (madde 8)."""

    def test_cikis_eslesmesi(self, svc, isolated_db, open_shift):
        # giris
        entry_event = make_event(plate="34ABC123", confidence=96)
        svc.ingest_camera_event(entry_event)

        # cikis
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        result = svc.ingest_camera_event(exit_event)
        assert result["status"] == "ODEME_BEKLIYOR"
        assert result["plate"] == "34ABC123"

    def test_cikis_bariyeri_odemesiz_kapali(self, svc, isolated_db, open_shift):
        entry_event = make_event(plate="34ABC123", confidence=96)
        svc.ingest_camera_event(entry_event)

        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        assert svc.barriers.status("CIKIS-1") != "ACIK"

    def test_eslesmeyen_cikis_benzer_oneri(self, svc, isolated_db):
        # 34ABC123 giris yapti
        entry_event = make_event(plate="34ABC123", confidence=96)
        svc.ingest_camera_event(entry_event)

        # cikista kamera 34A8C123 okudu
        exit_event = make_event(direction="EXIT", plate="34A8C123", confidence=95, gate="CIKIS-1")
        result = svc.ingest_camera_event(exit_event)
        assert result["status"] == "NO_MATCH"
        suggested_plates = [p for p, _ in result["suggestions"]]
        assert "34ABC123" in suggested_plates

    def test_manuel_eslestirme(self, svc, isolated_db, open_shift, admin):
        entry_event = make_event(plate="34ABC123", confidence=96)
        entry_result = svc.ingest_camera_event(entry_event)

        exit_event = make_event(direction="EXIT", plate="34A8C123", confidence=95, gate="CIKIS-1")
        no_match = svc.ingest_camera_event(exit_event)
        assert no_match["status"] == "NO_MATCH"

        result = svc.manual_match_exit(no_match["event"], entry_result["session_id"], admin, "kamera karistirdi")
        assert result["status"] == "ODEME_BEKLIYOR"

        # audit log kontrolu
        audits = isolated_db.list_audit_logs(50)
        assert any(a["action"] == "MANUEL_PLAKA_ESLESTIRME" for a in audits)
