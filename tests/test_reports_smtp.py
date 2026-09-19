"""Madde 28: Rapor olusturma, Excel/PDF, SMTP ve zamanlayici testleri."""
import os
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from app.utils import now, money


class TestReportGeneration:
    """Gunluk rapor olusturma (madde 18, 21)."""

    def _seed_data(self, isolated_db, admin):
        """Bir giris + cikis + odeme verisi olusturur."""
        from app.services.parking_service import ParkingService
        from tests.conftest import make_event
        svc = ParkingService()
        svc.current_user = admin
        shift_id = isolated_db.open_shift("admin", "KASA-1", "500.00")

        entry_time = (now() - timedelta(minutes=90)).isoformat()
        e1 = make_event(plate="34ABC123", confidence=96, event_time=entry_time)
        r1 = svc.ingest_camera_event(e1)
        exit_event = make_event(direction="EXIT", plate="34ABC123", confidence=97, gate="CIKIS-1")
        svc.ingest_camera_event(exit_event)
        svc.record_payment(r1["session_id"], "NAKIT", "200", admin)
        svc.vehicle_passed(r1["session_id"])
        return svc

    def test_rapor_verisi_uretilir(self, isolated_db, admin):
        self._seed_data(isolated_db, admin)
        from app.reports.queries import build_daily_report_data
        # The hotel's reporting day starts at 09:00. A test run before 09:00
        # (or an entry 90 minutes ago across that boundary) belongs to the prior
        # reporting day, rather than the current calendar date.
        entry_time = datetime.fromisoformat(isolated_db.list_recent_sessions(1)[0]["entry_time"])
        report_date = (entry_time - timedelta(hours=9)).date()
        data = build_daily_report_data(report_date)
        assert "summary" in data
        assert data["summary"]["toplam_giris"] >= 1
        assert "entries" in data
        assert "exits" in data
        assert "inside" in data
        assert "payments" in data

    def test_rapor_araligi_sabah_dokuzdan_dokuza(self):
        from app.reports.queries import day_bounds
        start, end = day_bounds(date(2026, 8, 22))
        assert datetime.fromisoformat(start).strftime("%Y-%m-%d %H:%M:%S") == "2026-08-22 09:00:00"
        assert datetime.fromisoformat(end).strftime("%Y-%m-%d %H:%M:%S") == "2026-08-23 08:59:59"

    def test_excel_raporu_uretilir(self, isolated_db, admin):
        self._seed_data(isolated_db, admin)
        from app.reports.queries import build_daily_report_data
        from app.reports.excel_report import build_excel
        data = build_daily_report_data(now().date())
        path = build_excel(data)
        assert path is not None
        assert os.path.exists(path)
        assert path.endswith(".xlsx")

    def test_pdf_raporu_uretilir(self, isolated_db, admin):
        self._seed_data(isolated_db, admin)
        from app.reports.queries import build_daily_report_data
        from app.reports.pdf_report import build_pdf
        data = build_daily_report_data(now().date())
        path = build_pdf(data)
        assert path is not None
        assert os.path.exists(path)
        assert path.endswith(".pdf")

    def test_rapor_dosya_uretimi(self, isolated_db, admin):
        """generate_report_files hem Excel hem PDF uretir (madde 31, adim 19)."""
        self._seed_data(isolated_db, admin)
        from app.reports.scheduler import generate_report_files
        data, files = generate_report_files(now().date())
        assert len(files) == 2
        for f in files:
            assert os.path.exists(f)


class TestSmtpSending:
    """SMTP gonderim, test e-postasi, yeniden deneme (madde 18, 19)."""

    def test_alici_yok_no_recipients(self, isolated_db):
        from app.reports.scheduler import send_daily_report_for
        result = send_daily_report_for(now().date() - timedelta(days=1), force=True)
        assert result["status"] == "NO_RECIPIENTS"

    def test_basariyla_gonderim(self, isolated_db):
        isolated_db.add_email_recipient("test@example.com", "TO")
        from app.reports.scheduler import send_daily_report_for
        with patch("app.reports.mailer._connect_smtp") as mock_connect, \
             patch("app.reports.mailer.send_daily_report", return_value="OK"):
            # mailer.send_daily_report'i mock'ladik ama scheduler kendi cagrisi farkli
            pass
        # Gercek test: mailer.send_daily_report mock ile
        with patch("app.reports.scheduler.mailer.send_daily_report", return_value="OK"):
            result = send_daily_report_for(now().date() - timedelta(days=1), force=True)
        assert result["status"] == "SENT"

    def test_basarisiz_gonderim_yeniden_deneme(self, isolated_db):
        isolated_db.add_email_recipient("test@example.com", "TO")
        from app.reports.scheduler import send_daily_report_for
        with patch("app.reports.scheduler.mailer.send_daily_report",
                   side_effect=ConnectionError("SMTP baglanamadi")):
            result = send_daily_report_for(now().date() - timedelta(days=1), force=True)
        assert result["status"] == "FAILED"
        # report_log durumunu kontrol et
        log = isolated_db.get_report_log_for_date((now().date() - timedelta(days=1)).isoformat())
        assert log["status"] == "YENIDEN_DENENECEK"
        assert log["attempts"] == 1
        assert log["next_retry"] is not None

    def test_ayni_rapor_iki_kez_gonderilmez(self, isolated_db):
        isolated_db.add_email_recipient("test@example.com", "TO")
        from app.reports.scheduler import send_daily_report_for
        report_date = now().date() - timedelta(days=1)
        with patch("app.reports.scheduler.mailer.send_daily_report", return_value="OK"):
            r1 = send_daily_report_for(report_date, force=False)
            r2 = send_daily_report_for(report_date, force=False)
        assert r1["status"] == "SENT"
        assert r2["status"] == "ALREADY_SENT"

    def test_test_email_gonderim(self, isolated_db):
        from app.reports import mailer
        with patch("app.reports.mailer._connect_smtp") as mock_connect:
            mock_server = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_server
            result = mailer.send_test_email("test@example.com")
        assert result == "OK"
        mock_server.send_message.assert_called_once()

    def test_program_uzerinden_smtp_ayarlari(self, isolated_db):
        from app.reports import mailer
        from app.security import protect_secret
        isolated_db.set_setting("smtp_host", "smtp.example.com")
        isolated_db.set_setting("smtp_port", "587")
        isolated_db.set_setting("smtp_username", "rapor@example.com")
        isolated_db.set_setting("smtp_password_enc", protect_secret("uygulama-sifresi"))
        isolated_db.set_setting("smtp_from_email", "rapor@example.com")
        settings = mailer.get_smtp_settings()
        assert settings["host"] == "smtp.example.com"
        assert settings["username"] == "rapor@example.com"
        assert settings["password"] == "uygulama-sifresi"

    def test_google_workspace_relay_kullanici_sifresi_gondermez(self, isolated_db):
        from app.reports import mailer
        isolated_db.set_setting("smtp_host", "smtp-relay.gmail.com")
        isolated_db.set_setting("smtp_port", "587")
        isolated_db.set_setting("smtp_auth_mode", "relay")
        isolated_db.set_setting("smtp_from_email", "news@example.com")
        isolated_db.set_setting("smtp_use_tls", "1")
        with patch("app.reports.mailer.smtplib.SMTP") as smtp_class:
            server = smtp_class.return_value
            server.has_extn.return_value = True
            assert mailer._connect_smtp() is server
        server.starttls.assert_called_once()
        server.login.assert_not_called()

    def test_gmail_uygulama_sifresi_bosluklari_temizlenir(self, isolated_db):
        from app.reports import mailer
        from app.security import protect_secret
        isolated_db.set_setting("smtp_host", "smtp.gmail.com")
        isolated_db.set_setting("smtp_port", "587")
        isolated_db.set_setting("smtp_auth_mode", "password")
        isolated_db.set_setting("smtp_username", "ana.hesap@example.com")
        isolated_db.set_setting("smtp_password_enc", protect_secret("abcd efgh ijkl mnop"))
        isolated_db.set_setting("smtp_from_email", "news@example.com")
        with patch("app.reports.mailer.smtplib.SMTP") as smtp_class:
            server = smtp_class.return_value
            server.has_extn.return_value = True
            mailer._connect_smtp()
        server.login.assert_called_once_with(
            "ana.hesap@example.com", "abcdefghijklmnop")


class TestMissedReports:
    """Uygulama kapaliyken kacirilan raporun sonradan gonderilmesi (madde 19)."""

    def test_kacirilan_rapor_kontrolu(self, isolated_db):
        isolated_db.add_email_recipient("test@example.com", "TO")
        isolated_db.set_setting("report_active", "1")
        isolated_db.set_setting("report_time", "00:05")
        from app.reports.scheduler import check_missed_reports
        with patch("app.reports.scheduler.mailer.send_daily_report", return_value="OK"):
            check_missed_reports()
        # dun icin rapor kaydi olusmali
        yesterday = (now().date() - timedelta(days=1)).isoformat()
        log = isolated_db.get_report_log_for_date(yesterday)
        assert log is not None
