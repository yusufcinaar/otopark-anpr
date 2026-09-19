"""Madde 28: Odeme, para ustu, bariyer kontrolu ve vardiya testleri."""
from datetime import timedelta
from decimal import Decimal

import pytest
from unittest.mock import patch, MagicMock

from app.services.auth import PermissionDenied
from app.services.parking_service import ParkingService
from app.utils import now, now_iso, money
from tests.conftest import make_event


class TestCashPayment:
    """Nakit para ustu hesabi ve eksik odeme engeli (madde 11)."""

    def _setup_session(self, svc, minutes_ago=90):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=minutes_ago)).isoformat()
        event = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(event)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        return r["session_id"]

    def test_nakit_para_ustu(self, svc, isolated_db, open_shift, admin):
        sid = self._setup_session(svc)
        session = isolated_db.get_session(sid)
        fee = money(session["fee_dec"])
        cash = fee + money(50)
        result = svc.record_payment(sid, "NAKIT", str(cash), admin)
        assert result["status"] == "CIKIS_IZNI"
        pays = [p for p in isolated_db.list_payments(10) if p["session_id"] == sid]
        assert pays
        assert money(pays[0]["change_given"]) == money(50)

    def test_eksik_odeme_reddedilir(self, svc, isolated_db, open_shift, admin):
        sid = self._setup_session(svc)
        result = svc.record_payment(sid, "NAKIT", "0.50", admin)
        assert result["status"] == "ERROR"

    def test_tam_odeme(self, svc, isolated_db, open_shift, admin):
        sid = self._setup_session(svc)
        session = isolated_db.get_session(sid)
        fee = str(money(session["fee_dec"]))
        result = svc.record_payment(sid, "NAKIT", fee, admin)
        assert result["status"] == "CIKIS_IZNI"
        pays = [p for p in isolated_db.list_payments(10) if p["session_id"] == sid]
        assert money(pays[0]["change_given"]) == money(0)

    def test_kredi_karti_odemesi(self, svc, isolated_db, open_shift, admin):
        sid = self._setup_session(svc)
        result = svc.record_payment(sid, "KREDI_KARTI", None, admin)
        assert result["status"] == "CIKIS_IZNI"


class TestBarrierControl:
    """Bariyer kontrol: odeme olmadan acilmaz, odeme sonrasi acilir (madde 8, 13)."""

    def test_odeme_yok_bariyer_kapali(self, svc, isolated_db, open_shift):
        entry_event = make_event(plate="34ABC123", confidence=96)
        svc.ingest_camera_event(entry_event)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        assert svc.barriers.status("CIKIS-1") != "ACIK"

    def test_odeme_sonrasi_bariyer_acik(self, svc, isolated_db, open_shift, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        entry_event = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(entry_event)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        svc.record_payment(r["session_id"], "KREDI_KARTI", None, admin)
        assert svc.barriers.status("CIKIS-1") == "ACIK"

    def test_bariyer_acilamazsa_odeme_korunur_ve_cikis_izni_verilmez(
            self, svc, isolated_db, open_shift, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        entry = svc.ingest_camera_event(
            make_event(plate="34ERR123", confidence=96, event_time=entry_time))
        svc.ingest_camera_event(
            make_event(direction="EXIT", plate="34ERR123", confidence=97, gate="CIKIS-1"))
        with patch.object(svc.barriers, "open", return_value=False):
            result = svc.record_payment(entry["session_id"], "KREDI_KARTI", None, admin)
        assert result["status"] == "BARRIER_ERROR"
        session = isolated_db.get_session(entry["session_id"])
        assert session["status"] == "ODENDI"
        assert session["paid"] == 1
        assert len([p for p in isolated_db.list_payments(10)
                    if p["session_id"] == entry["session_id"]]) == 1

    def test_arac_gecince_bariyer_kapanir(self, svc, isolated_db, open_shift, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        entry_event = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(entry_event)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        svc.record_payment(r["session_id"], "KREDI_KARTI", None, admin)
        result = svc.vehicle_passed(r["session_id"])
        assert result["status"] == "TAMAMLANDI"
        assert svc.barriers.status("CIKIS-1") == "KAPALI"

    def test_emniyet_sensoru_kapatmayi_engeller(self, svc, isolated_db):
        svc.barriers.set_sensor("CIKIS-1", True)
        svc.barriers.open("CIKIS-1", reason="test")
        ok = svc.barriers.close("CIKIS-1")
        assert ok is False
        assert svc.barriers.status("CIKIS-1") == "EMNIYET_AKTIF"

    def test_diger_sebebi_aciklama_zorunlu(self, svc, isolated_db, admin):
        with pytest.raises(ValueError, match="aciklama"):
            svc.barriers.manual_open("CIKIS-1", admin["username"], "Diger", note="")

    def test_diger_sebebi_aciklama_ile(self, svc, isolated_db, admin):
        ok = svc.barriers.manual_open("CIKIS-1", admin["username"], "Diger", note="aciklama var")
        assert ok is True


class TestShiftManagement:
    """Vardiya ve kasa yonetimi (madde 17)."""

    def test_vardiya_acma(self, isolated_db):
        sid = isolated_db.open_shift("kasiyer1", "KASA-1", "100.00")
        shift = isolated_db.get_open_shift("kasiyer1")
        assert shift["id"] == sid
        assert shift["status"] == "ACIK"
        assert shift["opening_cash"] == "100.00"

    def test_vardiya_kapatma(self, isolated_db):
        sid = isolated_db.open_shift("kasiyer1", "KASA-1", "100.00")
        isolated_db.close_shift(sid, "200.00", "200.00", "0.00", "temiz kasa")
        assert isolated_db.get_open_shift("kasiyer1") is None

    def test_vardiyasiz_odeme_otomatik_kasaya_yazilir(self, svc, isolated_db, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        entry_event = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(entry_event)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        result = svc.record_payment(r["session_id"], "NAKIT", "200", admin)
        assert result["status"] == "CIKIS_IZNI"
        payment = isolated_db.list_payments(1)[0]
        assert payment["register"] == "OTOMATIK"
        assert payment["shift_id"] is None

    def test_kapali_vardiya_sonrasi_odeme(self, svc, isolated_db, admin):
        from app.utils import now
        entry_time = (now() - timedelta(minutes=90)).isoformat()
        entry_event = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r = svc.ingest_camera_event(entry_event)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)

        sid = isolated_db.open_shift("admin", "KASA-1", "100.00")
        isolated_db.close_shift(sid, "100.00", "100.00", "0.00", "")
        result = svc.record_payment(r["session_id"], "NAKIT", "200", admin)
        assert result["status"] == "CIKIS_IZNI"


class TestSubscription:
    """Abonelik gecerliligi (madde 15)."""

    def test_gecerli_abone_giris(self, svc, isolated_db):
        from datetime import date, timedelta
        end = (date.today() + timedelta(days=30)).isoformat()
        isolated_db.add_subscriber("34ABC123", "Ahmet Yilmaz", "05551234567", end)
        event = make_event(plate="34ABC123", confidence=96)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "ENTRY_OK"
        assert result["is_subscriber"] is True
        session = isolated_db.get_session(result["session_id"])
        assert session["is_subscriber"] == 1

    def test_suresi_dolmus_abone_normal_arac(self, svc, isolated_db):
        from datetime import date, timedelta
        end = (date.today() - timedelta(days=1)).isoformat()
        isolated_db.add_subscriber("34ABC123", "Ahmet Yilmaz", "05551234567", end)
        event = make_event(plate="34ABC123", confidence=96)
        result = svc.ingest_camera_event(event)
        assert result["status"] == "ENTRY_OK"
        assert result["is_subscriber"] is False

    def test_abone_cikis_ucretsiz(self, svc, isolated_db):
        from datetime import date, timedelta
        end = (date.today() + timedelta(days=30)).isoformat()
        isolated_db.add_subscriber("34ABC123", "Ahmet Yilmaz", "05551234567", end)
        entry_event = make_event(plate="34ABC123", confidence=96)
        r = svc.ingest_camera_event(entry_event)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        result = svc.ingest_camera_event(exit_event)
        assert result["status"] == "SUBSCRIBER_EXIT"
        session = isolated_db.get_session(r["session_id"])
        assert session["status"] == "TAMAMLANDI"


class TestMetcomIo:
    XML = b"""<PICTCPIP><dyn0>Kapali</dyn0><dyn1>Kapali</dyn1>
    <dyn2>Acik</dyn2><dyn3>Kapali</dyn3><dyn4>Kapali</dyn4>
    <dyn5>Kapali</dyn5><dyn6>Kapali</dyn6><dyn7>Kapali</dyn7></PICTCPIP>"""

    def test_bariyer3_ve_loop1_durumu_okunur(self):
        from app.barrier import MetcomIoController, ACIK
        response = MagicMock()
        response.__enter__.return_value.read.return_value = self.XML
        with patch("app.barrier.urllib.request.urlopen", return_value=response):
            controller = MetcomIoController("192.0.2.7", output_channel=3, loop_channel=1)
            assert controller.get_status("CIKIS-1") == ACIK
            assert controller.get_loop_status() is False

    def test_dogrulanmamis_role_komutu_gonderilmez(self):
        from app.barrier import MetcomIoController
        controller = MetcomIoController()
        with pytest.raises(RuntimeError, match="dogrulanmadi"):
            controller.open_barrier("CIKIS-1")

    def test_tcp_heartbeat_baglantida_hemen_gonderilir(self):
        from app.barrier import MetcomIoController
        response = MagicMock()
        response.__enter__.return_value.read.return_value = self.XML
        sock = MagicMock()
        with patch("app.barrier.urllib.request.urlopen", return_value=response), \
             patch("app.barrier.socket.create_connection", return_value=sock) as create:
            controller = MetcomIoController(host="192.0.2.7", heartbeat_interval=0)
            assert controller.connect() is True
            assert controller.is_connected() is True
            controller.disconnect()
        create.assert_called_once_with(("192.0.2.7", 8080), timeout=3.0)
        sock.sendall.assert_called_once_with(b"\x00")
        sock.close.assert_called_once()

    def test_yalnizca_dogrulanmis_hex_acma_komutu_gonderilir(self):
        from app.barrier import MetcomIoController
        controller = MetcomIoController(
            trigger_hex="03", trigger_verified=True, heartbeat_interval=0)
        controller._http_connected = True
        controller._sock = MagicMock()
        controller.open_barrier("CIKIS-1")
        controller._sock.sendall.assert_called_once_with(b"\x03")
