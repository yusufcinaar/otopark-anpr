"""Gunluk rapor zamanlayicisi (APScheduler) + guvenilir gonderim.

- Rapor her sabah 09:00'da onceki gun 09:00 - bugun 08:59:59 icin hazirlanir.
- Ayni tarih icin basarili gonderim varsa tekrar gonderilmez (idempotent).
- Basarisiz gonderimde 5/15/30 dk araliklarla en fazla 5 deneme yapilir.
- Uygulama rapor saatinde kapaliysa acilista kacirilan raporlar kontrol edilir.
"""
import json
import traceback
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import db
from app.reports.queries import build_daily_report_data
from app.reports.excel_report import build_excel
from app.reports.pdf_report import build_pdf
from app.reports import mailer
from app.utils import TZ, now, now_iso, parse_dt

RETRY_DELAYS_MIN = [5, 15, 30, 30, 30]
MAX_ATTEMPTS = 5


def generate_report_files(report_date: date) -> tuple[dict, list[str]]:
    """Rapor verisini hazirlar, Excel + PDF dosyalarini uretir."""
    data = build_daily_report_data(report_date)
    files = []
    errors = []
    try:
        files.append(build_excel(data))
    except Exception as exc:
        errors.append(f"Excel: {type(exc).__name__}: {exc}")
    try:
        files.append(build_pdf(data))
    except Exception as exc:
        errors.append(f"PDF: {type(exc).__name__}: {exc}")
    if errors:
        db.audit("sistem", "RAPOR_DOSYA_HATASI", target=report_date.isoformat(),
                 note=" | ".join(errors))
    if not files:
        raise RuntimeError("Rapor dosyalari olusturulamadi: " + " | ".join(errors))
    return data, files


def send_daily_report_for(report_date: date, force: bool = False) -> dict:
    """Belirtilen gunun raporunu olusturur ve gonderir. Idempotent."""
    date_str = report_date.isoformat()
    existing = db.get_report_log_for_date(date_str)
    if existing and existing["status"] == "GONDERILDI" and not force:
        return {"status": "ALREADY_SENT", "report_id": existing["id"]}

    report_id = existing["id"] if existing and existing["status"] != "GONDERILDI" \
        else db.create_report_log(date_str)

    recipients = [r["email"] for r in db.list_email_recipients() if r["kind"] == "TO"]
    cc = [r["email"] for r in db.list_email_recipients() if r["kind"] == "CC"]

    data, files = generate_report_files(report_date)
    db.update_report_log(report_id, files=json.dumps(files), status="GONDERIM_BEKLIYOR",
                         recipients=", ".join(recipients + cc))

    if not recipients:
        db.update_report_log(report_id, status="BASARISIZ", error="Alici tanimlanmamis.")
        return {"status": "NO_RECIPIENTS", "report_id": report_id, "files": files}

    attempts = (existing["attempts"] if existing else 0) + 1
    try:
        db.update_report_log(report_id, status="GONDERILIYOR", attempts=attempts)
        response = mailer.send_daily_report(data, recipients, cc, files)
        db.update_report_log(report_id, status="GONDERILDI", sent_at=now_iso(), error=None)
        db.audit("sistem", "GUNLUK_RAPOR_GONDERILDI", target=date_str, new_value=response)
        return {"status": "SENT", "report_id": report_id, "files": files}
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        if attempts >= MAX_ATTEMPTS:
            db.update_report_log(report_id, status="BASARISIZ", error=err, next_retry=None)
        else:
            delay = RETRY_DELAYS_MIN[min(attempts - 1, len(RETRY_DELAYS_MIN) - 1)]
            next_retry = (now() + timedelta(minutes=delay)).isoformat()
            db.update_report_log(report_id, status="YENIDEN_DENENECEK", error=err,
                                 next_retry=next_retry)
        db.audit("sistem", "GUNLUK_RAPOR_HATA", target=date_str, note=err)
        return {"status": "FAILED", "report_id": report_id, "error": err, "files": files}


def _daily_job():
    yesterday = now().date() - timedelta(days=1)
    send_daily_report_for(yesterday)


def _retry_job():
    """Yeniden denenecek raporlari kontrol eder."""
    for r in db.list_report_logs(20):
        if r["status"] == "YENIDEN_DENENECEK" and r.get("next_retry"):
            if now() >= parse_dt(r["next_retry"]):
                try:
                    send_daily_report_for(date.fromisoformat(r["report_date"]))
                except Exception:
                    traceback.print_exc()


def check_missed_reports():
    """Acilista: dun icin basarili gonderim yoksa raporu olustur/gonder."""
    yesterday = now().date() - timedelta(days=1)
    existing = db.get_report_log_for_date(yesterday.isoformat())
    report_time = db.get_setting("report_time", "09:00")
    hh, mm = report_time.split(":")
    scheduled_today = now().replace(hour=int(hh), minute=int(mm), second=0)
    if now() >= scheduled_today and (not existing or existing["status"] != "GONDERILDI"):
        if db.get_setting("report_active", "1") == "1":
            send_daily_report_for(yesterday)


def start_scheduler() -> BackgroundScheduler | None:
    if db.get_setting("report_active", "1") != "1":
        return None
    report_time = db.get_setting("report_time", "09:00")
    try:
        hh, mm = report_time.split(":")
        scheduler = BackgroundScheduler(timezone=TZ)
        scheduler.add_job(_daily_job, CronTrigger(hour=int(hh), minute=int(mm)),
                          id="daily_report", replace_existing=True)
        scheduler.add_job(_retry_job, "interval", minutes=5, id="report_retry",
                          replace_existing=True)
        scheduler.start()
        return scheduler
    except (ValueError, TypeError):
        return None
