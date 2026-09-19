"""Gunluk rapor veri sorgulari (Europe/Istanbul gunune gore)."""
from datetime import datetime, time, timedelta
from decimal import Decimal

from app import db
from app.utils import TZ, money, money_from_db
from app.services.tariff_engine import format_duration


def day_bounds(report_date) -> tuple[str, str]:
    """Rapor gunu: verilen tarihin 09:00'i - ertesi gun 08:59:59'u."""
    start = datetime.combine(report_date, time(hour=9), tzinfo=TZ)
    end = datetime.combine(report_date + timedelta(days=1), time(hour=9), tzinfo=TZ) \
        - timedelta(seconds=1)
    return start.isoformat(), end.isoformat()


def build_daily_report_data(report_date) -> dict:
    start_iso, end_iso = day_bounds(report_date)
    sessions = db.sessions_between(start_iso, end_iso)
    payments = db.payments_between(start_iso, end_iso)
    barrier_events = db.barrier_events_between(start_iso, end_iso)
    inside_now = db.list_inside_sessions()

    entries = [s for s in sessions
               if s.get("created_by") != "UNKNOWN_EXIT"
               and s.get("entry_time") and start_iso <= s["entry_time"] <= end_iso]
    exits = [s for s in sessions if s.get("exit_time") and start_iso <= s["exit_time"] <= end_iso
             and s["status"] in ("TAMAMLANDI", "CIKIS_IZNI", "ODENDI")]

    def method_total(method):
        return sum((money_from_db(p["collected_amount"]) for p in payments
                    if p["method"] == method and p["status"] == "TAMAM"), Decimal("0"))

    total_collected = sum((money_from_db(p["collected_amount"]) for p in payments
                           if p["status"] == "TAMAM"), Decimal("0"))
    cancelled_total = sum((money_from_db(p["collected_amount"]) for p in payments
                           if p["status"] in ("IPTAL", "IADE")), Decimal("0"))

    manual_barrier = [b for b in barrier_events if b["reason"] and b["action"] == "ACILDI"
                      and b.get("username")]
    unread_plates = [e for e in db.list_camera_events(1000)
                     if e.get("result") in ("DUSUK_GUVEN", "GECERSIZ_PLAKA")
                     and start_iso <= e["created_at"] <= end_iso]
    manual_fixes = [a for a in db.list_audit_logs(1000)
                    if a["action"] in ("MANUEL_PLAKA_ESLESTIRME",)
                    and start_iso <= a["ts"] <= end_iso]

    warnings = []
    long_open = [s for s in inside_now if s.get("entry_time") and s["entry_time"] < start_iso]
    if long_open:
        warnings.append(f"{len(long_open)} arac 1 gunden uzun suredir iceride/acik kayit.")
    failed_reports = [r for r in db.list_report_logs(50) if r["status"] == "BASARISIZ"]
    if failed_reports:
        warnings.append(f"{len(failed_reports)} onceki rapor gonderilemedi.")

    return {
        "report_date": report_date.isoformat(),
        "location_name": db.get_setting("location_name", "DEMO OTOPARK"),
        "period_start": start_iso,
        "period_end": end_iso,
        "summary": {
            "toplam_giris": len(entries),
            "toplam_cikis": len(exits),
            "iceride_kalan": len(inside_now),
            "normal_arac": sum(1 for e in entries if not e.get("is_subscriber")),
            "abone_arac": sum(1 for e in entries if e.get("is_subscriber")),
            "toplam_tahsilat": str(money(total_collected)),
            "nakit": str(money(method_total("NAKIT"))),
            "kredi_karti": str(money(method_total("KREDI_KARTI"))),
            "banka_karti": str(money(method_total("BANKA_KARTI"))),
            "havale_eft": str(money(method_total("HAVALE_EFT"))),
            "iptal_iade": str(money(cancelled_total)),
            "manuel_bariyer": len(manual_barrier),
            "okunamayan_plaka": len(unread_plates),
            "elle_duzeltilen": len(manual_fixes),
        },
        "entries": [{
            "plaka": s["plate"], "giris": s.get("entry_time", ""), "kapi": s.get("entry_lane", ""),
            "arac_tipi": s.get("vehicle_type") or "-",
            "abone": "Abone" if s.get("is_subscriber") else "Normal",
            "guven": f"{s['confidence']:.0f}%" if s.get("confidence") else "-",
        } for s in entries],
        "exits": [{
            "plaka": s["plate"], "giris": s.get("entry_time", ""), "cikis": s.get("exit_time", ""),
            "sure": format_duration(s.get("duration_minutes") or 0),
            "kapi": s.get("exit_lane", ""),
            "kamera": s.get("exit_camera") or s.get("exit_lane") or "-",
            "giris_gorseli": s.get("entry_image") or "",
            "cikis_gorseli": s.get("exit_image") or "",
            "giris_hizi": s.get("entry_speed_kmh"),
            "cikis_hizi": s.get("exit_speed_kmh"),
            "aciklama": "ABONE" if s.get("is_subscriber") else
                         ("UCRETSIZ" if not (s.get("fee") or 0) else "UCRETLI"),
            "ucret": str(money_from_db(s.get("fee_dec")) if s.get("fee_dec") else money(s.get("fee") or 0)),
            "odeme_yontemi": next((p["method"] for p in payments
                                   if p["session_id"] == s["id"] and p["status"] == "TAMAM"), "-"),
            "kasiyer": next((p["cashier"] for p in payments
                             if p["session_id"] == s["id"] and p["status"] == "TAMAM"), "-"),
        } for s in exits],
        "inside": [{
            "plaka": s["plate"], "giris": s.get("entry_time", ""),
            "sure": format_duration(s.get("duration_minutes") or _minutes_since(s.get("entry_time"))),
            "kapi": s.get("entry_lane", ""),
            "abone": "Abone" if s.get("is_subscriber") else "Normal",
        } for s in inside_now],
        "payments": [{
            "islem_no": p["payment_uuid"][:8], "plaka": p["plate"],
            "tutar": p["collected_amount"], "yontem": p["method"],
            "kasiyer": p.get("cashier") or "-", "saat": p["paid_at"],
            "durum": p["status"],
        } for p in payments],
        "manual_ops": [{
            "kullanici": b.get("username") or "-", "kapi": b.get("gate") or "-",
            "plaka": b.get("plate") or "-", "neden": b.get("reason") or "-",
            "saat": b["ts"],
        } for b in manual_barrier],
        "warnings": warnings,
    }


def _minutes_since(entry_iso: str | None) -> int:
    if not entry_iso:
        return 0
    from app.utils import now, parse_dt
    return max(0, int((now() - parse_dt(entry_iso)).total_seconds() // 60))
