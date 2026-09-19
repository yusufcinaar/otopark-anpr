"""Madde 28: Audit log, oturum durum gecisleri ve ek senaryolar."""
from datetime import timedelta

import pytest

from app.services.parking_service import ParkingService
from app.utils import now, new_uuid
from tests.conftest import make_event


class TestAuditLog:
    """Audit log islemleri silinemez (madde 22)."""

    def test_odeme_audit_log(self, svc, isolated_db, open_shift, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        e = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(e)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        svc.record_payment(r["session_id"], "NAKIT", "200", admin)
        audits = isolated_db.list_audit_logs(50)
        assert any(a["action"] == "ODEME_ALINDI" for a in audits)

    def test_manuel_eslestirme_audit(self, svc, isolated_db, open_shift, admin):
        e1 = make_event(plate="34ABC123", confidence=96)
        r1 = svc.ingest_camera_event(e1)
        exit_event = make_event(direction="EXIT", plate="34A8C123", confidence=95, gate="CIKIS-1")
        no_match = svc.ingest_camera_event(exit_event)
        svc.manual_match_exit(no_match["event"], r1["session_id"], admin, "kamera hatasi")
        audits = isolated_db.list_audit_logs(50)
        assert any(a["action"] == "MANUEL_PLAKA_ESLESTIRME" for a in audits)

    def test_manuel_bariyer_audit(self, svc, isolated_db, admin):
        svc.barriers.manual_open("CIKIS-1", admin["username"], "Acil durum", note="test")
        audits = isolated_db.list_audit_logs(50)
        assert any(a["action"] == "MANUEL_BARIYER_ACMA" for a in audits)


class TestSessionStateTransitions:
    """Oturum durum gecisleri - gecersiz gecisler reddedilir (madde 24)."""

    def test_giris_bekliyor_icinde(self, svc, isolated_db):
        e = make_event(plate="34ABC123", confidence=96)
        r = svc.ingest_camera_event(e)
        session = isolated_db.get_session(r["session_id"])
        assert session["status"] == "ICERIDE"

    def test_icinde_odeme_bekliyor(self, svc, isolated_db, open_shift):
        e = make_event(plate="34ABC123", confidence=96)
        r = svc.ingest_camera_event(e)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        result = svc.ingest_camera_event(exit_event)
        assert result["status"] == "ODEME_BEKLIYOR"
        session = isolated_db.get_session(r["session_id"])
        assert session["status"] == "ODEME_BEKLIYOR"

    def test_odeme_bekliyor_odendi(self, svc, isolated_db, open_shift, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        e = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(e)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        svc.record_payment(r["session_id"], "KREDI_KARTI", None, admin)
        session = isolated_db.get_session(r["session_id"])
        assert session["status"] == "CIKIS_IZNI"

    def test_cikis_izni_tamamlandi(self, svc, isolated_db, open_shift, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        e = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(e)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        svc.record_payment(r["session_id"], "KREDI_KARTI", None, admin)
        svc.vehicle_passed(r["session_id"])
        session = isolated_db.get_session(r["session_id"])
        assert session["status"] == "TAMAMLANDI"

    def test_odeme_bekliyor_direkt_tamamlandi_gecersiz(self, svc, isolated_db, open_shift):
        """Odeme bekleyen arac dogrudan tamamlandı durumuna gecemez."""
        e = make_event(plate="34ABC123", confidence=96)
        r = svc.ingest_camera_event(e)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        # ODEME_BEKLIYOR -> TAMAMLANDI gecisi gecersiz
        from app.db import InvalidTransition
        with pytest.raises(InvalidTransition, match="Gecersiz durum gecisi"):
            isolated_db.transition_session(r["session_id"], "TAMAMLANDI")

    def test_tamamlanmis_oturum_tekrar_acma(self, svc, isolated_db, open_shift, admin):
        """Tamamlanmis oturum tekrar acmak yonetici yetkisi ve gerekce ister."""
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        e = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(e)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        svc.record_payment(r["session_id"], "KREDI_KARTI", None, admin)
        svc.vehicle_passed(r["session_id"])

        # yonetici yetkisi ile tekrar acma
        svc.reopen_session(r["session_id"], admin, "musteri itiraz etti")
        session = isolated_db.get_session(r["session_id"])
        assert session["status"] == "MANUEL_INCELEME"

        # gerekcesiz reddedilmeli
        e2 = make_event(plate="34DEF456", confidence=96, event_time=entry_time)
        r2 = svc.ingest_camera_event(e2)
        exit_event2 = make_event(direction="EXIT", plate="34DEF456", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event2)
        svc.record_payment(r2["session_id"], "KREDI_KARTI", None, admin)
        svc.vehicle_passed(r2["session_id"])
        with pytest.raises(ValueError, match="Gerekce"):
            svc.reopen_session(r2["session_id"], admin, "")


class TestDuplicateEntry:
    """Mukerrer giris kontrolu (madde 7)."""

    def test_mukerrer_giris_engellenir(self, svc, isolated_db):
        e1 = make_event(plate="34ABC123", confidence=96)
        svc.ingest_camera_event(e1)
        e2 = make_event(plate="34ABC123", confidence=96)
        result = svc.ingest_camera_event(e2)
        assert result["status"] == "DUPLICATE_ENTRY"
        assert "existing_session" in result


    def test_mukerrer_giris_onceki_kapat(self, svc, isolated_db, admin):
        e1 = make_event(plate="34ABC123", confidence=96)
        r1 = svc.ingest_camera_event(e1)
        e2 = make_event(plate="34ABC123", confidence=96)
        dup = svc.ingest_camera_event(e2)
        assert dup["status"] == "DUPLICATE_ENTRY"

        # onceki oturumu kapat, yeni giris olustur
        result = svc.force_entry_after_duplicate(dup["event"], admin, "close_previous", "onceki kayit hatali")
        assert result["status"] == "ENTRY_OK"
        old_session = isolated_db.get_session(r1["session_id"])
        assert old_session["status"] == "MANUEL_INCELEME"


class TestSpeedMetadata:
    def test_speed_is_stored_on_entry(self, svc, isolated_db):
        event = make_event(plate="34HIZ123")
        event["speed_kmh"] = 28
        result = svc.ingest_camera_event(event)
        session = isolated_db.get_session(result["session_id"])
        assert session["entry_speed_kmh"] == 28
        camera_event = isolated_db.list_camera_events(1)[0]
        assert camera_event["speed_kmh"] == 28

    def test_vendor_speed_alias_is_supported(self, svc, isolated_db):
        event = make_event(plate="34HIZ124")
        event["hiz"] = "17"
        result = svc.ingest_camera_event(event)
        session = isolated_db.get_session(result["session_id"])
        assert session["entry_speed_kmh"] == 17


class TestFreePassMode:
    def test_free_pass_exit_completes_without_payment(self, svc, isolated_db):
        isolated_db.set_setting("free_pass_mode", "1")
        entry = make_event(plate="34SER123")
        result = svc.ingest_camera_event(entry)
        exit_event = make_event(direction="EXIT", plate="34SER123", gate="CIKIS-1")
        exit_result = svc.ingest_camera_event(exit_event)
        session = isolated_db.get_session(result["session_id"])
        assert exit_result["status"] == "FREE_PASS_EXIT"
        assert session["status"] == "TAMAMLANDI"
        assert session["paid"] == 1
        assert session["fee"] == 0


class TestPaymentCancellation:
    """Odeme iptal/iade - silinmez, durum degisikligi ile (madde 11)."""

    def test_odeme_iptal(self, svc, isolated_db, open_shift, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        e = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(e)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        pay_result = svc.record_payment(r["session_id"], "NAKIT", "200", admin)
        pays = [p for p in isolated_db.list_payments(10) if p["session_id"] == r["session_id"]]
        assert pays
        pay_id = pays[0]["id"]
        isolated_db.cancel_payment(pay_id, admin["username"], "musteri iade istedi")
        pays_after = [p for p in isolated_db.list_payments(10) if p["id"] == pay_id]
        assert pays_after[0]["status"] == "IPTAL"
        assert pays_after[0]["cancel_reason"] == "musteri iade istedi"
