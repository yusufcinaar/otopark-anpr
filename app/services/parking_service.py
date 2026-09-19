"""Giris/cikis is akisini yoneten cekirdek servis.

- Kamera olaylari (simulasyon veya webhook) ayni pipeline'dan gecer: idempotency,
  plaka normalizasyonu, guven esigi kontrolu, abone/kara liste kontrolu.
- Tarife, giris aninda oturuma SNAPSHOT olarak yazilir.
- Odeme kaydi olusmadan cikis bariyeri otomatik ACILMAZ.
- Odeme sonrasi varsayilan 15 dk cikis suresi vardir; asilirsa fark ucreti cikar.
"""
from PySide6.QtCore import QObject, Signal

from app import db
from app.barrier import BarrierService, ACIK
from app.hardware import DigitalDisplay
from app.services import auth
from app.services.plate_utils import normalize_plate, format_plate, suggest_similar_plates
from app.services.tariff_engine import (
    calculate_duration_minutes, calculate_fee, calculate_fee_from_rules,
    snapshot_str, rules_from_snapshot, format_duration
)
from app.utils import now, now_iso, parse_dt, money, money_to_db, money_from_db

ENTRY_GATES = ["GIRIS-1", "GIRIS-2"]
EXIT_GATES = ["CIKIS-1"]
EXIT_BARRIER_GATE = "CIKIS-1"  # Tesisteki tek fiziksel bariyer


class ParkingService(QObject):
    event_logged = Signal(str)
    session_updated = Signal()
    alarm_triggered = Signal(str, str)   # (plaka, mesaj)

    def __init__(self):
        super().__init__()
        self.barriers = BarrierService()
        self.barriers.passage_finished.connect(self._barrier_passage_finished)
        self.exit_display = DigitalDisplay()
        self.exit_display.show_idle()
        self.current_user: dict | None = None

    # ------------------------------------------------------------------ log
    def _log(self, text: str):
        self.event_logged.emit(text)

    def _confidence_levels(self):
        auto = float(db.get_setting("confidence_auto", "90"))
        operator = float(db.get_setting("confidence_operator", "70"))
        return auto, operator

    # ------------------------------------------------- KAMERA OLAYI ALIMI ----
    def ingest_camera_event(self, event: dict, operator_confirmed: bool = False) -> dict:
        """Simulasyon ekrani ve HTTP webhook AYNI fonksiyonu kullanir.

        event: {event_id, camera_id, gate_id, direction(ENTRY|EXIT), raw_plate,
                confidence, event_time?, plate_image?, vehicle_image?,
                vehicle_type?, vehicle_color?}
        """
        event = dict(event)
        event_id = str(event.get("event_id") or "").strip()
        direction = str(event.get("direction") or "").strip().upper()
        gate = str(event.get("gate_id") or "").strip().upper().replace(" ", "-")
        if not event_id:
            return {"status": "INVALID_EVENT", "message": "event_id zorunludur"}
        if direction not in ("ENTRY", "EXIT"):
            self._log(f"[Sistem] Gecersiz kamera yonu yoksayildi: {direction or 'bos'}")
            return {"status": "INVALID_EVENT", "message": "direction ENTRY veya EXIT olmalidir"}
        valid_gates = ENTRY_GATES if direction == "ENTRY" else EXIT_GATES
        if gate not in valid_gates:
            self._log(f"[Sistem] Devre disi/gecersiz kamera kapisi yoksayildi: {gate or 'bos'}")
            return {"status": "INVALID_EVENT", "message": f"{direction} icin gecersiz gate_id: {gate}"}
        event["event_id"] = event_id
        event["direction"] = direction
        event["gate_id"] = gate
        raw = event.get("raw_plate", "")
        normalized = normalize_plate(raw)
        event["normalized_plate"] = normalized
        event.setdefault("event_time", now_iso())
        # Farkli PTS markalarinin kullandigi yaygin hiz alanlarini tek formata cevir.
        raw_speed = event.get("speed_kmh", event.get("speed", event.get("hiz")))
        try:
            speed = float(raw_speed) if raw_speed not in (None, "") else None
            event["speed_kmh"] = speed if speed is not None and 0 <= speed <= 300 else None
        except (TypeError, ValueError):
            event["speed_kmh"] = None

        # Idempotency: ayni event_id ikinci kez gelirse islenmez
        if not db.record_camera_event(event):
            self._log(f"[{event.get('gate_id')}] Mukerrer kamera olayi yoksayildi (event_id: {event['event_id']})")
            return {"status": "DUPLICATE_EVENT"}

        if not normalized:
            db.mark_camera_event_processed(event["event_id"], "GECERSIZ_PLAKA")
            return {"status": "INVALID_PLATE"}

        speed_limit = float(db.get_setting("speed_limit_kmh", "0") or 0)
        if speed_limit > 0 and event.get("speed_kmh") is not None and event["speed_kmh"] > speed_limit:
            msg = f"HIZ LIMITI ASILDI: {format_plate(normalized)} ({event['speed_kmh']:.0f} km/sa > {speed_limit:.0f} km/sa)"
            self._log(f"[{event.get('gate_id')}] UYARI: {msg}")
            self.alarm_triggered.emit(normalized, msg)

        # Guven esigi kontrolu
        conf = float(event.get("confidence") or 0)
        auto_level, operator_level = self._confidence_levels()
        unattended = db.get_setting("unattended_mode", "0") == "1"
        if unattended:
            minimum = float(db.get_setting("unattended_min_confidence", "50") or 50)
            if conf < minimum:
                db.mark_camera_event_processed(event["event_id"], "DUSUK_GUVEN_KAYDEDILDI")
                self._log(
                    f"[{event.get('gate_id')}] Otomatik mod dusuk guven kaydi: "
                    f"{format_plate(raw)} ({conf:.0f}%) - bariyer acilmadi")
                return {"status": "LOW_CONFIDENCE_RECORDED", "raw_plate": raw,
                        "confidence": conf}
        elif conf < operator_level and not operator_confirmed:
            db.mark_camera_event_processed(event["event_id"], "DUSUK_GUVEN")
            self._log(f"[{event.get('gate_id')}] Dusuk guven ({conf:.0f}%): {format_plate(raw)} - manuel plaka girisi gerekli.")
            return {"status": "MANUAL_PLATE_REQUIRED", "raw_plate": raw, "confidence": conf}
        if not unattended and conf < auto_level and not operator_confirmed:
            db.mark_camera_event_processed(event["event_id"], "OPERATOR_ONAYI_BEKLIYOR")
            return {"status": "OPERATOR_CONFIRM_REQUIRED", "raw_plate": raw,
                    "normalized_plate": normalized, "confidence": conf, "event": event}

        if event.get("direction") == "EXIT":
            result = self.handle_exit_event(event)
        else:
            result = self.handle_entry_event(event)
        db.mark_camera_event_processed(event["event_id"], result.get("status", "?"))
        return result

    # ------------------------------------------------------------- GIRIS ----
    def handle_entry_event(self, event: dict) -> dict:
        plate = event["normalized_plate"]
        gate = event.get("gate_id", "GIRIS-1")

        blacklisted = db.is_blacklisted(plate)
        if blacklisted:
            msg = f"KARA LISTE ARACI GIRISTE: {format_plate(plate)} ({blacklisted['reason'] or 'sebep yok'})"
            self._log(f"[{gate}] UYARI: {msg}")
            self.alarm_triggered.emit(plate, msg)
            if db.get_setting("unattended_mode", "0") == "1":
                self._log(f"[{gate}] KARA LISTE ENGELLENDI: {format_plate(plate)} - bariyer acilmadi")
                return {"status": "BLACKLIST_BLOCKED", "plate": plate}

        existing = db.get_open_session_by_plate_v2(plate)
        if existing:
            if db.get_setting("unattended_mode", "0") == "1":
                self._log(
                    f"[{gate}] OTOMATIK MUKERRER GIRIS: {format_plate(plate)} mevcut kayitla geciyor "
                    f"(kayit #{existing['id']})")
                return {"status": "DUPLICATE_ENTRY_ALLOWED", "existing_session": existing}
            self._log(
                f"[{gate}] MUKERRER GIRIS: {format_plate(plate)} zaten iceride gorunuyor "
                f"(kayit #{existing['id']}, giris: {existing['entry_time']}). Operator karari bekleniyor."
            )
            return {"status": "DUPLICATE_ENTRY", "existing_session": existing, "event": event}

        return self._create_entry(event)

    def _create_entry(self, event: dict, created_by: str = "KAMERA") -> dict:
        plate = event["normalized_plate"]
        gate = event.get("gate_id", "GIRIS-1")
        subscriber = self._check_subscription(plate, gate)

        tariff = db.get_active_tariff()
        session_id = db.create_entry_full(
            plate=plate,
            raw_plate=event.get("raw_plate", ""),
            entry_time=event.get("event_time"),
            lane=gate,
            image_path=event.get("vehicle_image", ""),
            camera=event.get("camera_id", ""),
            confidence=event.get("confidence"),
            vehicle_type=event.get("vehicle_type"),
            vehicle_color=event.get("vehicle_color"),
            is_subscriber=bool(subscriber),
            tariff_name=tariff["name"] if tariff else None,
            tariff_version=tariff["version"] if tariff else None,
            tariff_snapshot=snapshot_str(tariff["rules"]) if tariff else None,
            created_by=created_by,
            speed_kmh=event.get("speed_kmh"),
        )
        note = " (ABONE)" if subscriber else ""
        speed_txt = f" | Hiz: {event['speed_kmh']:.0f} km/sa" if event.get("speed_kmh") is not None else ""
        self._log(f"[{gate}] GIRIS kaydedildi: {format_plate(plate)}{note} (kayit #{session_id}){speed_txt}")

        # Tesiste giris bariyeri yoktur; giris kameralari yalnizca kayit alir.
        self.session_updated.emit()
        return {"status": "ENTRY_OK", "session_id": session_id, "plate": plate,
                "is_subscriber": bool(subscriber)}

    def force_entry_after_duplicate(self, event: dict, user: dict, action: str, reason: str) -> dict:
        """Mukerrer giris durumunda operator karari: 'close_previous' veya 'new_entry'."""
        auth.require(user, "verify_plate") if user.get("role") == "guvenlik" else None
        plate = event["normalized_plate"]
        existing = db.get_open_session_by_plate_v2(plate)
        if action == "close_previous" and existing:
            db.update_session_fields(existing["id"], {"status": "MANUEL_INCELEME"})
            db.audit(user["username"], "ONCEKI_OTURUM_INCELEMEYE_ALINDI",
                     target=f"session#{existing['id']}", old_value=existing["status"],
                     new_value="MANUEL_INCELEME", note=reason)
        db.audit(user["username"], "MUKERRER_GIRIS_KARARI", target=plate, note=f"{action}: {reason}")
        result = self._create_entry(event, created_by=f"OPERATOR:{user['username']}")
        return result

    def _check_subscription(self, plate: str, gate: str):
        sub = db.get_active_subscriber(plate)
        if not sub:
            return None
        if not sub.get("active", 1):
            return None
        if sub.get("payment_status") not in (None, "", "ODENDI"):
            return None
        gates = (sub.get("allowed_gates") or "").strip()
        if gates and gate not in [g.strip() for g in gates.split(",")]:
            return None
        hours = (sub.get("allowed_hours") or "").strip()  # "08:00-19:00"
        if hours:
            try:
                start_s, end_s = hours.split("-")
                current = now().strftime("%H:%M")
                if not (start_s.strip() <= current <= end_s.strip()):
                    return None
            except ValueError:
                pass
        days = (sub.get("allowed_days") or "").strip()  # "1,2,3,4,5" (Pzt=1)
        if days:
            if str(now().isoweekday()) not in [d.strip() for d in days.split(",")]:
                return None
        return sub

    # ------------------------------------------------------------- CIKIS ----
    def handle_exit_event(self, event: dict) -> dict:
        plate = event["normalized_plate"]
        gate = event.get("gate_id", "CIKIS-1")
        self.exit_display.show_processing()

        blacklisted = db.is_blacklisted(plate)
        if blacklisted:
            msg = f"KARA LISTE ARACI CIKISTA: {format_plate(plate)} ({blacklisted['reason'] or 'sebep yok'})"
            self._log(f"[{gate}] UYARI: {msg}")
            self.alarm_triggered.emit(plate, msg)

        session = db.get_open_session_by_plate_v2(plate)
        if not session:
            inside = db.list_inside_sessions()
            suggestions = suggest_similar_plates(plate, [s["plate"] for s in inside])
            self._log(f"[{gate}] ESLESMEYEN CIKIS: {format_plate(plate)} icin acik kayit yok. "
                      f"Benzer plakalar: {', '.join(format_plate(p) for p, _ in suggestions) or 'yok'}")
            self.exit_display.show_error(f"{format_plate(plate)}\nGIRIS KAYDI BULUNAMADI")
            if db.get_setting("unattended_mode", "0") == "1":
                unknown_id = db.create_unknown_exit(
                    plate, gate, event.get("vehicle_image", ""))
                self._log(f"[{gate}] ESLESMEYEN CIKIS KAYDEDILDI: {format_plate(plate)} "
                          f"(inceleme #{unknown_id}) - bariyer acilmadi")
                self.session_updated.emit()
                return {"status": "UNMATCHED_EXIT_RECORDED", "plate": plate,
                        "session_id": unknown_id}
            return {"status": "NO_MATCH", "plate": plate, "suggestions": suggestions,
                    "inside": inside, "event": event}

        return self._prepare_exit(session, event)

    def manual_match_exit(self, event: dict, matched_session_id: int, user: dict, reason: str) -> dict:
        """Operator, cikis plakasini iceriden dogru kayda elle eslestirir (audit'e yazilir)."""
        session = db.get_session(matched_session_id)
        if not session:
            return {"status": "ERROR", "message": "Kayit bulunamadi"}
        db.audit(user["username"], "MANUEL_PLAKA_ESLESTIRME",
                 target=f"session#{matched_session_id}",
                 old_value=event.get("normalized_plate", ""),
                 new_value=session["plate"], note=reason)
        return self._prepare_exit(session, event)

    def _prepare_exit(self, session: dict, event: dict) -> dict:
        gate = event.get("gate_id", "CIKIS-1")
        plate = session["plate"]
        entry_time = parse_dt(session["entry_time"])
        exit_time = now()

        if db.get_setting("free_pass_mode", "0") == "1":
            # Kuyrukta beklemis kamera olayinda gorulen gecis saatini koru.
            # Bozuk/gelecek tarih ya da bu giristen onceki tarih kaydi bozmasin.
            try:
                observed_time = parse_dt(event.get("event_time", ""))
                if entry_time <= observed_time <= exit_time:
                    exit_time = observed_time
            except (TypeError, ValueError):
                pass
            completed = db.complete_free_pass_exit(session["id"], {
                "exit_time": exit_time.isoformat(), "exit_lane": gate,
                "exit_image": event.get("vehicle_image", ""),
                "exit_camera": event.get("camera_id", ""),
                "exit_speed_kmh": event.get("speed_kmh"),
                "duration_minutes": calculate_duration_minutes(entry_time, exit_time),
            })
            if not completed:
                return {"status": "ALREADY_EXITED", "session_id": session["id"], "plate": plate}
            # Acik tutma mod degisikliginde uygulanir ve baglanti yenilendiginde
            # surucu tarafindan korunur. Her arac icin senkron ag istegi yapmak
            # kamera olaylarini ve ana ekrani bekletmemelidir.
            self._log(f"[{gate}] SERBEST GECIS CIKISI: {format_plate(plate)} | "
                      "Icerideki arac kaydi tamamlandi | Ek ucret uygulanmadi")
            self.session_updated.emit()
            return {"status": "FREE_PASS_EXIT", "session_id": session["id"], "plate": plate,
                    "barrier_command_sent": False}

        rules = rules_from_snapshot(session.get("tariff_snapshot"),
                                    fallback=(db.get_active_tariff() or {}).get("rules"))
        duration_minutes, fee = calculate_fee(rules, entry_time, exit_time)

        if db.get_setting("automatic_free_exit", "0") == "1":
            db.transition_session(session["id"], "TAMAMLANDI", {
                "exit_time": now_iso(), "exit_lane": gate,
                "exit_image": event.get("vehicle_image", ""),
                "exit_camera": event.get("camera_id", ""),
                "exit_speed_kmh": event.get("speed_kmh"),
                "duration_minutes": duration_minutes,
                "fee": 0.0, "fee_dec": money_to_db(0), "paid": 1,
            })
            opened = self.barriers.open(EXIT_BARRIER_GATE, plate=plate, session_id=session["id"],
                                        reason="Otomatik operatorsuz cikis")
            self._log(f"[{gate}] OTOMATIK CIKIS: {format_plate(plate)} | "
                      f"Sure: {format_duration(duration_minutes)} | "
                      f"Bariyer: {'ACILDI' if opened else 'ACILAMADI'}")
            self.session_updated.emit()
            return {"status": "AUTOMATIC_EXIT", "session_id": session["id"],
                    "plate": plate, "barrier_opened": opened}

        if session.get("is_subscriber"):
            # abone: gecerli abonelikte ucret alinmaz, dogrudan cikis izni
            db.transition_session(session["id"], "TAMAMLANDI", {
                "exit_time": now_iso(), "exit_lane": gate,
                "exit_image": event.get("vehicle_image", ""),
                "exit_camera": event.get("camera_id", ""),
                "duration_minutes": duration_minutes,
                "fee": 0.0, "fee_dec": money_to_db(0), "paid": 1,
                "exit_speed_kmh": event.get("speed_kmh"),
            })
            self.exit_display.show_paid(plate)
            opened = self.barriers.open(
                EXIT_BARRIER_GATE, plate=plate, session_id=session["id"], reason="Abone gecisi")
            speed_txt = f" | Hiz: {event['speed_kmh']:.0f} km/sa" if event.get("speed_kmh") is not None else ""
            self._log(f"[{gate}] ABONE CIKISI: {format_plate(plate)} | Sure: {format_duration(duration_minutes)} | "
                      f"Ucretsiz | Bariyer: {'ACILDI' if opened else 'ACILAMADI'}{speed_txt}")
            self.session_updated.emit()
            return {"status": "SUBSCRIBER_EXIT", "session_id": session["id"], "plate": plate,
                    "barrier_opened": opened}

        db.transition_session(session["id"], "ODEME_BEKLIYOR", {
            "exit_time": now_iso(), "exit_lane": gate,
            "exit_image": event.get("vehicle_image", ""),
            "exit_camera": event.get("camera_id", ""),
            "duration_minutes": duration_minutes,
            "fee": float(fee), "fee_dec": money_to_db(fee),
            "exit_speed_kmh": event.get("speed_kmh"),
        })
        self.exit_display.show_fee(plate, format_duration(duration_minutes), float(fee), "TL")
        speed_txt = f" | Hiz: {event['speed_kmh']:.0f} km/sa" if event.get("speed_kmh") is not None else ""
        self._log(f"[{gate}] CIKIS talebi: {format_plate(plate)} | Sure: {format_duration(duration_minutes)} | "
                  f"Ucret: {fee} TL -> Kasiyer odemesi bekleniyor (kayit #{session['id']}){speed_txt}")
        self.session_updated.emit()
        return {"status": "ODEME_BEKLIYOR", "session_id": session["id"], "plate": plate,
                "duration_minutes": duration_minutes, "fee": fee}

    # ---------------------------------------------------- ODEME & CIKIS ----
    def record_payment(self, session_id: int, method: str, cash_received=None,
                        user: dict | None = None, note: str = "") -> dict:
        """Kasiyer odemeyi fiziksel olarak aldiktan sonra sisteme isler.
        Odeme kaydi olusmadan bariyer ACILMAZ. Eksik odeme kabul edilmez."""
        auth.require(user, "record_payment")

        shift = db.get_open_shift(user["username"])

        session = db.get_session(session_id)
        if not session or session["status"] != "ODEME_BEKLIYOR":
            return {"status": "ERROR", "message": "Odeme bekleyen kayit bulunamadi."}

        amount = money_from_db(session.get("fee_dec")) + money_from_db(session.get("extra_fee_dec"))
        change = None
        if method == "NAKIT":
            if cash_received is None:
                return {"status": "ERROR", "message": "Alinan nakit tutari giriniz."}
            received = money(cash_received)
            if received < amount:
                return {"status": "ERROR", "message": f"Eksik odeme kabul edilmez. Tutar: {amount} TL"}
            change = received - amount
        else:
            received = amount

        payment_id = db.create_payment(
            session_id=session_id, plate=session["plate"],
            calculated_amount=money_to_db(amount), collected_amount=money_to_db(amount),
            method=method, cash_received=money_to_db(received) if method == "NAKIT" else None,
            change_given=money_to_db(change) if change is not None else None,
            cashier=user["username"],
            register=shift["register"] if shift else "OTOMATIK",
            shift_id=shift["id"] if shift else None,
            note=note,
        )

        grace = int(db.get_setting("exit_grace_minutes", "15"))
        from datetime import timedelta
        allowed_until = (now() + timedelta(minutes=grace)).isoformat()

        db.transition_session(session_id, "ODENDI", {
            "paid": 1, "paid_at": now_iso(), "exit_allowed_until": allowed_until,
            "extra_fee_dec": None,
        })
        db.audit(user["username"], "ODEME_ALINDI", target=f"session#{session_id}",
                 new_value=f"{amount} TL / {method}")
        self._log(f"ODEME ALINDI: {format_plate(session['plate'])} | {amount} TL | {method} | Kasiyer: {user['username']}"
                  + (f" | Para ustu: {change} TL" if change is not None else ""))
        self.session_updated.emit()

        # odeme tamam -> cikis izni ve bariyer
        return self.request_exit_open(session_id, user)

    def request_exit_open(self, session_id: int, user: dict | None = None) -> dict:
        """ODENDI durumundaki arac icin bariyeri acar. Cikis suresi asildiysa fark ucreti cikarir."""
        session = db.get_session(session_id)
        if not session:
            return {"status": "ERROR", "message": "Kayit yok"}
        gate = session.get("exit_lane") or "CIKIS-1"

        if session["status"] != "ODENDI":
            return {"status": "ERROR", "message": f"Cikis izni icin gecersiz durum: {session['status']}"}

        allowed_until = session.get("exit_allowed_until")
        if allowed_until and now() > parse_dt(allowed_until):
            # cikis suresi asildi: yalnizca olusan farki hesapla, onceki odeme korunur
            rules = rules_from_snapshot(session.get("tariff_snapshot"),
                                        fallback=(db.get_active_tariff() or {}).get("rules"))
            overtime_minutes = int((now() - parse_dt(allowed_until)).total_seconds() // 60) + 1
            extra = calculate_fee_from_rules({**rules, "free_minutes": 0}, overtime_minutes)
            db.transition_session(session_id, "ODEME_BEKLIYOR", {
                "extra_fee_dec": money_to_db(extra), "fee_dec": money_to_db(0),
            })
            self._log(f"CIKIS SURESI ASILDI: {format_plate(session['plate'])} | Ek sure: {format_duration(overtime_minutes)} | "
                      f"Fark ucreti: {extra} TL")
            self.exit_display.show_fee(session["plate"], f"+{format_duration(overtime_minutes)}", float(extra), "TL")
            self.session_updated.emit()
            return {"status": "EXTRA_FEE_REQUIRED", "session_id": session_id, "extra_fee": extra}

        opened = self.barriers.open(
            EXIT_BARRIER_GATE, username=user["username"] if user else "",
            plate=session["plate"], session_id=session_id, reason="Odeme tamamlandi")
        if not opened:
            detail = self.barriers.last_error(EXIT_BARRIER_GATE)
            self.exit_display.show_error(f"{format_plate(session['plate'])}\nBARIYER ACILAMADI")
            self._log(
                f"BARIYER ACILAMADI: {format_plate(session['plate'])} | Odeme kaydi korundu; "
                f"{detail or 'baglanti/komut kontrol edilmeli'}")
            self.session_updated.emit()
            return {
                "status": "BARRIER_ERROR",
                "session_id": session_id,
                "message": "Odeme kaydi korundu ancak fiziksel bariyer acilamadi. "
                           + (detail or "Bariyer baglantisini/komutunu kontrol edip yeniden deneyin."),
            }
        db.transition_session(session_id, "CIKIS_IZNI", {})
        self.exit_display.show_paid(session["plate"])
        self._log(f"CIKIS IZNI: {format_plate(session['plate'])} -> {gate} bariyeri ACILDI")
        self.session_updated.emit()
        return {"status": "CIKIS_IZNI", "session_id": session_id}

    def vehicle_passed(self, session_id: int) -> dict:
        """Arac gecisi dogrulandi: destekleniyorsa kapatir, kaydi tamamlar."""
        session = db.get_session(session_id)
        if not session or session["status"] != "CIKIS_IZNI":
            return {"status": "ERROR", "message": "Cikis izni olan kayit bulunamadi."}
        gate = session.get("exit_lane") or "CIKIS-1"
        db.transition_session(session_id, "TAMAMLANDI", {})
        self.barriers.set_sensor(EXIT_BARRIER_GATE, False)
        if self.barriers.is_close_supported(EXIT_BARRIER_GATE):
            self.barriers.close(EXIT_BARRIER_GATE)
        self.exit_display.show_idle()
        self._log(f"CIKIS TAMAMLANDI: {format_plate(session['plate'])} (kayit #{session_id})")
        self.session_updated.emit()
        return {"status": "TAMAMLANDI"}

    def _barrier_passage_finished(self, gate: str, session_id):
        """Otomatik bariyer kapanisinda ucretli cikis kaydini tamamlar."""
        if not session_id:
            return
        session = db.get_session(int(session_id))
        if not session or session.get("status") != "CIKIS_IZNI":
            return
        db.transition_session(int(session_id), "TAMAMLANDI", {})
        self.exit_display.show_idle()
        self._log(f"CIKIS TAMAMLANDI: {format_plate(session['plate'])} | {gate} bariyeri kapandi")
        self.session_updated.emit()

    # ------------------------------------------------------------ DIGER ----
    def pending_exits(self):
        return [s for s in db.list_recent_sessions(300) if s["status"] == "ODEME_BEKLIYOR"]

    def reopen_session(self, session_id: int, user: dict, reason: str):
        """Tamamlanmis oturumu tekrar acmak yonetici yetkisi ve gerekce ister."""
        auth.require(user, "reopen_session")
        if not reason.strip():
            raise ValueError("Gerekce zorunludur.")
        session = db.get_session(session_id)
        if not session or session["status"] != "TAMAMLANDI":
            raise ValueError("Yalnizca tamamlanmis oturum tekrar acilabilir.")
        db.update_session_fields(session_id, {"status": "MANUEL_INCELEME"})
        db.audit(user["username"], "OTURUM_TEKRAR_ACILDI", target=f"session#{session_id}",
                 old_value="TAMAMLANDI", new_value="MANUEL_INCELEME", note=reason)
        self.session_updated.emit()
