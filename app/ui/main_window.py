import base64
import logging
import os
import re
import threading
from datetime import datetime
from urllib.parse import quote, urlsplit, urlunsplit

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QInputDialog, QMessageBox, QFrame, QDialog, QComboBox,
    QSizePolicy, QGridLayout
)

from app import db
from app.config import CAPTURES_DIR, CAMERAS, WEBHOOK_HOST, WEBHOOK_PORT, SIMULATION_MODE
from app.camera import RecognitionWorker, RtspCameraWorker, OnvifDiscoveryWorker
from app.config import CameraConfig
from app.security import unprotect_secret
from app.sim_utils import random_plate, render_frame_with_plate
from app.services.parking_service import ParkingService
from app.services import auth
from app.services.plate_utils import format_plate
from app.services.tariff_engine import format_duration
from app.utils import now, now_iso, new_uuid
from app.webhook_server import WebhookServer
from app.reports.scheduler import start_scheduler, check_missed_reports
from app.ui.widgets import (
    CameraPanel, DetectionHistoryPanel,
    add_card_shadow, numpy_to_pixmap, classify_event
)
from app.ui.subscribers_dialog import SubscribersDialog
from app.ui.blacklist_dialog import BlacklistDialog
from app.ui.users_dialog import UsersDialog
from app.ui.payment_dialog import PaymentDialog
from app.ui.smtp_dialog import SmtpDialog
from app.ui.reports_dialog import ReportsDialog
from app.ui.audit_dialog import AuditLogDialog
from app.ui.tariff_dialog import TariffDialog
from app.ui.simulation_dialog import SimulationDialog
from app.ui.camera_dialog import CameraDialog
from app.ui.barrier_control_dialog import BarrierControlDialog
from app.ui.barrier_shortcut import ExitBarrierShortcut
from app.ui.barrier_mode_task import FreePassModeTask
from app.ui.image_viewer import ImageViewerDialog
from app.barrier import MANUAL_OPEN_REASONS

PLATE_RE = re.compile(r"\b\d{2}\s?[A-PRSTUVYZ]{1,3}\s?\d{2,5}\b")

GATE_FOR_CAMERA = {
    "entry1": "GIRIS-1", "entry2": "GIRIS-2",
    "exit1": "CIKIS-1",
}
ACTIVE_CAMERA_GATES = {"GIRIS-1", "GIRIS-2", "CIKIS-1"}


class MainWindow(QMainWindow):
    def __init__(self, current_user: dict | None = None):
        super().__init__()
        self.current_user = current_user or {"username": "misafir", "role": "kasiyer"}
        self.setWindowTitle("Otopark Yönetim Sistemi - Plaka Tanıma")
        self.resize(1440, 900)
        self.setMinimumSize(1180, 720)

        # Kameralar 7/24 operator onayi beklemeden kayit alir. Ucretsiz otomatik
        # cikis yalnizca operatorun Serbest Gecis dugmesini actigi surece uygulanir.
        db.set_setting("unattended_mode", "1")
        db.set_setting("automatic_free_exit", "0")
        self.service = ParkingService()
        self.service.current_user = self.current_user
        self._active_workers = []
        self._camera_workers = []
        self._camera_discovery_workers = []
        self._pending_session_id = None      # odeme bekleyen son cikis
        self._exit_ready_session_id = None   # cikis izni verilmis (arac gecisi beklenen)
        self._payment_dialog_open = False
        self._last_frame_for_lane = {}
        self._last_capture_path_for_lane = {}
        self._free_pass_worker = None
        # Kayitli tercih, bu oturumda kart komutunun basarili oldugu anlamina gelmez.
        self._free_pass_applied = None
        self._free_pass_error = ""

        self._build_menu()
        self._build_ui()
        self._wire_signals()
        self._close_after_barrier_shortcut = False
        self.barrier_shortcut = ExitBarrierShortcut(
            self, self.service.barriers, lambda: self.current_user, self.append_log,
            db.get_setting("arma_barrier_shortcut", "F4"))
        self.barrier_shortcut.idle.connect(self._barrier_shortcut_idle)
        self.refresh_all()
        QTimer.singleShot(250, self._start_configured_cameras)
        if db.get_setting("free_pass_mode", "0") == "1":
            QTimer.singleShot(750, self._apply_persisted_free_pass)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

        # Kamera webhook sunucusu (gercek LPR kameralar icin, yerel ag)
        self.webhook = WebhookServer(WEBHOOK_HOST, WEBHOOK_PORT)
        self.webhook.bridge.camera_event_received.connect(self.process_camera_event)
        self._webhook_last_problem = ""
        self._webhook_retry_timer = QTimer(self)
        self._webhook_retry_timer.setInterval(5000)
        self._webhook_retry_timer.timeout.connect(self._start_webhook)
        self._start_webhook()

        # Gunluk rapor zamanlayicisi + kacirilan rapor kontrolu
        self.scheduler = start_scheduler()
        # PDF/Excel uretimi ve SMTP baglantisi acilis penceresini kilitlemesin.
        threading.Thread(target=self._check_missed_reports_safely,
                         name="rapor-kontrol", daemon=True).start()

    def _start_webhook(self):
        if self.webhook.start():
            self._webhook_retry_timer.stop()
            self._webhook_last_problem = ""
            self._set_health(self.webhook_health, "WEBHOOK", "HAZIR", "ok")
            self.webhook_health.setToolTip(
                f"Kamera olaylari icin TCP {self.webhook.port} dinleniyor.")
            mode_txt = "SIMULASYON" if SIMULATION_MODE else "PRODUCTION"
            self.append_log(
                f"[Sistem] Kamera webhook sunucusu aktif ({mode_txt}): "
                f"http://{self.webhook.host}:{self.webhook.port}/api/camera-event")
        else:
            busy = self.webhook.error_kind == "port_in_use"
            status = "PORT DOLU" if busy else "BASLATILAMADI"
            self._set_health(self.webhook_health, "WEBHOOK", status, "error")
            detail = self.webhook.last_error
            if busy:
                detail += (
                    " Ayni portu kullanan diger uygulamayi kapatin. "
                    "Port bosalinca 5 saniye icinde yeniden denenecek.")
            if self.webhook.error_kind == "invalid_configuration":
                self._webhook_retry_timer.stop()
            else:
                # Windows korumali bir dinleyici cakismasini 10013 (erisim
                # reddedildi) olarak da bildirebilir; port bosalinca toparlan.
                self._webhook_retry_timer.start()
                if not busy:
                    detail += " Baglanti 5 saniyede bir yeniden denenecek."
            self.webhook_health.setToolTip(detail)
            if detail != self._webhook_last_problem:
                self.append_log(f"[Sistem] Webhook: {detail}")
                self._webhook_last_problem = detail

    @staticmethod
    def _check_missed_reports_safely():
        try:
            check_missed_reports()
        except Exception:
            pass

    # ------------------------------------------------------------ MENU ----
    def _perm(self, perm: str) -> bool:
        return auth.has_perm(self.current_user, perm)

    def _build_menu(self):
        mb = self.menuBar()

        # --- Operasyon ---
        op_menu = mb.addMenu("Operasyon")
        self._add_menu_item(op_menu, "Manuel Bariyer Aç", self.manual_barrier_open, "manual_barrier_open")
        self._add_menu_item(op_menu, "Ödenmiş Çıkış Bariyerini Yeniden Dene",
                            self.retry_paid_exit, "record_payment")
        self._add_menu_item(op_menu, "Yenile", self.refresh_all)
        if SIMULATION_MODE:
            op_menu.addSeparator()
            self._add_menu_item(op_menu, "Simülasyon Paneli", self.open_simulation)

        # --- Tanimlar ---
        def_menu = mb.addMenu("Tanımlar")
        self._add_menu_item(def_menu, "Kamera Tanımları", self.open_devices, "manage_devices")
        self._add_menu_item(def_menu, "Bariyer ve LED Ayarları", self.open_barrier_control, "manage_devices")
        def_menu.addSeparator()
        self._add_menu_item(def_menu, "Abone Tanımları", self.open_subscribers, "manage_subscriptions")
        self._add_menu_item(def_menu, "Tarife Yönetimi", self.open_tariffs, "manage_tariffs")
        self._add_menu_item(def_menu, "Kara Liste", self.open_blacklist, "manage_blacklist")
        self._add_menu_item(def_menu, "Kullanıcı Tanımları", self.open_users, "manage_users")
        def_menu.addSeparator()
        self._add_menu_item(def_menu, "Hız Limiti Ayarı", self.configure_speed_limit, "manage_settings")
        self._add_menu_item(def_menu, "Hız Ölçüm Kalibrasyonu", self.configure_speed_calibration, "manage_settings")

        # --- Raporlar ---
        rep_menu = mb.addMenu("Raporlar")
        self._add_menu_item(rep_menu, "Araç / Plaka Ara ve Raporlar", self.open_reports, "view_reports")
        report_settings = self._add_menu_item(
            rep_menu, "Otomatik Rapor ve E-posta Ayarları", self.open_smtp)
        report_settings.setEnabled(any(self._perm(perm) for perm in SmtpDialog.ACCESS_PERMISSIONS))
        self._add_menu_item(rep_menu, "İşlem Logları", self.open_audit, "view_audit_logs")

        # --- Yardim ---
        help_menu = mb.addMenu("Yardım")
        self._add_menu_item(help_menu, "Hakkında", self.show_about)
        self._add_menu_item(help_menu, "Programı Kapat", self.close)

    def _add_menu_item(self, menu, title, handler, perm=None):
        act = QAction(title, self)
        act.triggered.connect(handler)
        if perm and not self._perm(perm):
            act.setEnabled(False)
        menu.addAction(act)
        return act

    # -------------------------------------------------------------- UI ----
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 12, 18, 16)
        root.setSpacing(12)

        root.addLayout(self._build_header())
        root.addWidget(self._build_control_strip())

        content = QHBoxLayout()
        content.setSpacing(12)

        history_card = QFrame()
        history_card.setObjectName("controlStrip")
        history_card.setMinimumWidth(285)
        history_card.setMaximumWidth(360)
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(10, 10, 10, 10)
        history_title = QLabel("SON GİREN / ÇIKAN ARAÇLAR")
        history_title.setStyleSheet("font-size: 13px; font-weight: 800; color: #334155;")
        history_layout.addWidget(history_title)
        self.history_panel = DetectionHistoryPanel(history_card)
        history_layout.addWidget(self.history_panel, 1)
        content.addWidget(history_card)

        camera_area = QWidget()
        cams = QGridLayout(camera_area)
        cams.setContentsMargins(0, 0, 0, 0)
        cams.setSpacing(12)
        self.entry1_panel = CameraPanel("Giriş Kamerası 1", "GIRIS-1", is_exit=False)
        self.entry2_panel = CameraPanel("Giriş Kamerası 2", "GIRIS-2", is_exit=False)
        self.exit_panel = CameraPanel("Çıkış Kamerası", "CIKIS-1", is_exit=True)
        cams.addWidget(self.entry1_panel, 0, 0)
        cams.addWidget(self.entry2_panel, 0, 1)
        cams.addWidget(self.exit_panel, 1, 0, 1, 2)
        cams.setColumnStretch(0, 1)
        cams.setColumnStretch(1, 1)
        cams.setRowStretch(0, 1)
        cams.setRowStretch(1, 1)
        content.addWidget(camera_area, 1)
        root.addLayout(content, 1)

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(16)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        t = QLabel("PLAKA TANIMA SİSTEMİ")
        t.setObjectName("appTitle")
        title_col.addWidget(t)
        self.clock_lbl = QLabel("")
        self.clock_lbl.setObjectName("appSubtitle")
        title_col.addWidget(self.clock_lbl)
        self.user_lbl = QLabel(f"Kullanıcı: {self.current_user['username']} ({self.current_user['role']})")
        self.user_lbl.setObjectName("appSubtitle")
        title_col.addWidget(self.user_lbl)
        header.addLayout(title_col)
        header.addStretch()

        return header

    def _build_control_strip(self):
        strip = QFrame()
        strip.setObjectName("controlStrip")
        bar = QHBoxLayout(strip)
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(10)
        # Ana ekranda tek operasyon kontrolu: bariyerleri acik tutan serbest gecis.
        # Kamera, bariyer ve rapor ayarlari ust menulerden acilir.
        self.free_pass_btn = QPushButton(strip)
        self.free_pass_btn.setCheckable(True)
        self.free_pass_btn.setEnabled(self._perm("manual_barrier_open"))
        self._sync_free_pass_button()
        self.free_pass_btn.setMinimumWidth(150)
        bar.addWidget(self.free_pass_btn)
        bar.addStretch()
        self.db_health = QLabel()
        self.webhook_health = QLabel()
        self.pts_health = QLabel()
        self.barrier_health = QLabel()
        self._set_health(self.db_health, "VERİTABANI", "HAZIR", "ok")
        self._set_health(self.webhook_health, "WEBHOOK", "BASLIYOR", "waiting")
        self._set_health(self.pts_health, "PTS", "OLAY BEKLIYOR", "waiting")
        self._set_health(self.barrier_health, "BARIYER", "BEKLIYOR", "waiting")
        for chip in (self.db_health, self.webhook_health, self.pts_health, self.barrier_health):
            bar.addWidget(chip)
        return strip

    def _set_health(self, label: QLabel, name: str, state: str, level: str):
        colors = {"ok": "#22c55e", "error": "#ef4444", "waiting": "#f59e0b"}
        color = colors.get(level, "#8b949e")
        label.setText(f"●  {name}  {state}")
        label.setStyleSheet(
            f"color: {color}; background: #f8fafc; border: 1px solid #d7dee8; "
            "border-radius: 8px; padding: 7px 10px; font-size: 11px; font-weight: 700;"
        )

    def _wire_signals(self):
        if SIMULATION_MODE:
            self.entry1_panel.action_btn.clicked.connect(lambda: self.simulate_from_panel("entry1"))
            self.entry2_panel.action_btn.clicked.connect(lambda: self.simulate_from_panel("entry2"))
            self.exit_panel.action_btn.clicked.connect(lambda: self.simulate_from_panel("exit1"))

        self.service.event_logged.connect(self.append_log)
        self.service.session_updated.connect(self.refresh_all)
        self.service.alarm_triggered.connect(self.show_alarm)
        self.service.barriers.state_changed.connect(self._barrier_state_changed)
        self.free_pass_btn.clicked.connect(self.toggle_free_pass)

    # ------------------------------------------------ KAMERA SIMULASYONU ----
    def simulate_from_panel(self, camera_key: str):
        cfg = CAMERAS[camera_key]
        gate = GATE_FOR_CAMERA[camera_key]
        is_exit = cfg.role == "exit"

        default = random_plate()
        if is_exit:
            inside = db.list_inside_sessions()
            if inside:
                default = inside[0]["plate"]
        plate, ok = QInputDialog.getText(
            self, "Arac Simule Et", f"{gate} kamerasinin okuyacagi plaka:", text=format_plate(default))
        if not ok or not plate.strip():
            return
        plate = plate.strip()

        frame = render_frame_with_plate(plate)
        panel = self._panel_for_gate(gate)
        panel.set_frame(frame)

        worker = RecognitionWorker(frame, {"gate": gate, "camera_key": camera_key,
                                            "direction": "EXIT" if is_exit else "ENTRY",
                                            "frame": frame})
        worker.finished_with_result.connect(self._on_anpr_result)
        self._track_worker(worker)
        worker.start()

    def _on_anpr_result(self, result, metadata):
        gate = metadata["gate"]
        if not result:
            QMessageBox.warning(self, "Plaka Okunamadi",
                                f"[{gate}] ANPR plakayi okuyamadi. Manuel giris yapabilirsiniz.")
            return
        image_path = self._save_capture(metadata["frame"], gate,
                                        "cikis" if metadata["direction"] == "EXIT" else "giris")
        plate_path = self._save_capture(result.crop, gate, "plaka")
        self._panel_for_gate(gate).set_last_plate(result.display)
        self._panel_for_gate(gate).set_last_capture(metadata["frame"])
        self._last_frame_for_lane[gate] = metadata["frame"]

        event = {
            "event_id": new_uuid(),
            "camera_id": f"LPR-{gate}",
            "gate_id": gate,
            "direction": metadata["direction"],
            "raw_plate": result.plate,
            "confidence": round(result.confidence * 100, 1),
            "event_time": now_iso(),
            "vehicle_image": image_path,
            "plate_image": plate_path,
            "speed_kmh": metadata.get("speed_kmh"),
        }
        self.process_camera_event(event)

    # ------------------------------------------------ OLAY ISLEME AKISI ----
    def process_camera_event(self, event: dict):
        gate = event.get("gate_id", "GIRIS-1")
        if gate not in ACTIVE_CAMERA_GATES:
            self.append_log(f"[Sistem] Devre disi kamera olayi yoksayildi: {gate}")
            return
        self._prepare_event_images(event)
        self._set_health(self.pts_health, "PTS", "AKTIF", "ok")
        panel = self._panel_for_gate(gate)
        panel.set_online(True)
        panel.set_last_plate(event.get("raw_plate", "—"), event.get("speed_kmh", event.get("speed", event.get("hiz"))))
        self._last_capture_path_for_lane.pop(gate, None)
        panel.last_capture_lbl.clear_image("Görsel bulunamadı")
        if event.get("vehicle_image"):
            saved_frame = cv2.imread(event["vehicle_image"])
            if saved_frame is not None:
                panel.set_last_capture(saved_frame)
                self._last_frame_for_lane[gate] = saved_frame
                self._last_capture_path_for_lane[gate] = event["vehicle_image"]
        result = self.service.ingest_camera_event(event)
        self._handle_ingest_result(result, event)

    def _handle_ingest_result(self, result: dict, event: dict):
        status = result.get("status")

        if status == "DUPLICATE_EVENT":
            return
        if status == "MANUAL_PLATE_REQUIRED":
            plate, ok = QInputDialog.getText(
                self, "Manuel Plaka Girisi",
                f"Okuma guveni dusuk ({result.get('confidence', 0):.0f}%).\n"
                f"Ham okuma: {format_plate(result.get('raw_plate'))}\nDogru plakayi girin:")
            if ok and plate.strip():
                new_event = dict(event)
                new_event["event_id"] = new_uuid()
                new_event["raw_plate"] = plate.strip()
                new_event["confidence"] = 100.0
                db.audit(self.current_user["username"], "MANUEL_PLAKA_GIRISI",
                         old_value=event.get("raw_plate", ""), new_value=plate.strip())
                self.process_camera_event(new_event)
            return
        if status == "OPERATOR_CONFIRM_REQUIRED":
            answer = QMessageBox.question(
                self, "Operator Onayi",
                f"Plaka: {format_plate(result['normalized_plate'])}\nGuven: {result['confidence']:.0f}%\n\n"
                "Bu okumayi onayliyor musunuz?")
            if answer == QMessageBox.StandardButton.Yes:
                ev = result["event"]
                db.audit(self.current_user["username"], "OPERATOR_PLAKA_ONAYI",
                         target=result["normalized_plate"])
                if ev.get("direction") == "EXIT":
                    r = self.service.handle_exit_event(ev)
                else:
                    r = self.service.handle_entry_event(ev)
                db.mark_camera_event_processed(ev["event_id"], r.get("status", "?"))
                self._handle_ingest_result(r, ev)
            return
        if status == "DUPLICATE_ENTRY":
            self._handle_duplicate_entry(result, event)
            return
        if status == "NO_MATCH":
            self._handle_no_match_exit(result, event)
            return
        if status == "ODEME_BEKLIYOR":
            self._pending_session_id = result["session_id"]
            # Arma PTS'deki gibi cikis algilaninca plaka, iki arac gorseli,
            # park suresi ve ucret ekrani kendiliginden acilir.
            QTimer.singleShot(0, self.take_payment)
            return
        if status == "EXTRA_FEE_REQUIRED":
            self._pending_session_id = result["session_id"]
            QMessageBox.warning(self, "Cikis Suresi Asildi",
                                f"Ek ucret olustu: {result['extra_fee']} TL. Odeme alin.")
            return
        # ENTRY_OK, SUBSCRIBER_EXIT vb. -> loglar zaten dustu

    def _handle_duplicate_entry(self, result: dict, event: dict):
        s = result["existing_session"]
        msg = QMessageBox(self)
        msg.setWindowTitle("Mukerrer Giris")
        msg.setText(
            f"{format_plate(s['plate'])} zaten ICERIDE gorunuyor.\n\n"
            f"Onceki giris: {s['entry_time']}\nKapi: {s.get('entry_lane') or '-'}\n"
            f"Kayit no: #{s['id']}\nGuven: {s.get('confidence') or '-'}\n\n"
            "Ne yapmak istiyorsunuz?")
        new_btn = msg.addButton("Onceki kaydi incelemeye al + Yeni giris", QMessageBox.ButtonRole.AcceptRole)
        only_new = msg.addButton("Sadece yeni giris olustur", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("Iptal", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked not in (new_btn, only_new):
            return
        reason, ok = QInputDialog.getText(self, "Gerekce", "Islem gerekcesi (zorunlu):")
        if not ok or not reason.strip():
            return
        action = "close_previous" if clicked == new_btn else "new_entry"
        self.service.force_entry_after_duplicate(result["event"], self.current_user, action, reason.strip())

    def _handle_no_match_exit(self, result: dict, event: dict):
        suggestions = result.get("suggestions", [])
        inside = result.get("inside", [])
        if not inside:
            QMessageBox.warning(self, "Eslesme Yok",
                                f"{format_plate(result['plate'])} icin acik kayit yok ve iceride arac bulunmuyor.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Cikis Eslestirme - Giris Kaydi Bulunamadi")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"Cikista okunan plaka: {format_plate(result['plate'])}\n"
            "Asagidan dogru araci secip elle eslestirebilirsiniz:"))
        combo = QComboBox()
        ordered = [p for p, _r in suggestions]
        rest = [s for s in inside if s["plate"] not in ordered]
        items = [s for p in ordered for s in inside if s["plate"] == p] + rest
        for s in items:
            combo.addItem(f"{format_plate(s['plate'])}  (giris: {s['entry_time']}, {s.get('entry_lane') or '-'})", s["id"])
        layout.addWidget(combo)
        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Eslestir")
        ok_btn.setObjectName("primaryBtn")
        cancel_btn = QPushButton("Iptal")
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        reason, ok = QInputDialog.getText(self, "Gerekce", "Eslestirme gerekcesi (zorunlu):")
        if not ok or not reason.strip():
            return
        r = self.service.manual_match_exit(result["event"], combo.currentData(),
                                            self.current_user, reason.strip())
        self._handle_ingest_result(r, event)

    # ------------------------------------------------------ ODEME AKISI ----
    def take_payment(self):
        if self._pending_session_id is None:
            # tabloda odeme bekleyen ilk kaydi bul
            pending = self.service.pending_exits()
            if not pending:
                QMessageBox.information(self, "Bilgi", "Odeme bekleyen kayit yok.")
                return
            self._pending_session_id = pending[0]["id"]

        session = db.get_session(self._pending_session_id)
        if session and session["status"] == "ODENDI":
            self.retry_paid_exit()
            return
        if not session or session["status"] != "ODEME_BEKLIYOR":
            self._pending_session_id = None
            return

        if self._payment_dialog_open:
            return

        dialog = PaymentDialog(session, self)
        self._payment_dialog_open = True
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._payment_dialog_open = False
        if not accepted:
            return
        d = dialog.result_data
        try:
            result = self.service.record_payment(
                session["id"], d["method"], d["cash_received"], self.current_user, d["note"])
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return

        if result.get("status") == "CIKIS_IZNI":
            self._exit_ready_session_id = session["id"]
            self._pending_session_id = None
        elif result.get("status") == "BARRIER_ERROR":
            self._pending_session_id = session["id"]
            QMessageBox.critical(
                self, "Bariyer Acilamadi",
                result.get("message") or
                "Odeme kaydi korundu ancak fiziksel bariyer acilamadi. "
                "Baglantiyi kontrol edip Operasyon menusunden yeniden deneyin.",
            )
        elif result.get("status") == "EXTRA_FEE_REQUIRED":
            QMessageBox.warning(self, "Ek Ucret", f"Cikis suresi asildi, fark: {result['extra_fee']} TL")
        elif result.get("status") == "ERROR":
            QMessageBox.warning(self, "Hata", result.get("message", "?"))

    def vehicle_passed(self):
        if self._exit_ready_session_id is None:
            return
        self.service.vehicle_passed(self._exit_ready_session_id)
        self._exit_ready_session_id = None

    def retry_paid_exit(self):
        """Odeme kaydini tekrarlamadan, ODENDI durumundaki son cikisi yeniden dener."""
        session_id = self._pending_session_id
        if session_id is None:
            paid = [s for s in db.list_recent_sessions(300) if s.get("status") == "ODENDI"]
            if not paid:
                QMessageBox.information(self, "Bilgi", "Bariyer bekleyen odenmis cikis bulunamadi.")
                return
            session_id = paid[0]["id"]
        result = self.service.request_exit_open(session_id, self.current_user)
        if result.get("status") == "CIKIS_IZNI":
            self._exit_ready_session_id = session_id
            self._pending_session_id = None
            QMessageBox.information(self, "Basarili", "Bariyer acma komutu basariyla uygulandi.")
        elif result.get("status") == "EXTRA_FEE_REQUIRED":
            self._pending_session_id = session_id
            self.take_payment()
        else:
            self._pending_session_id = session_id
            QMessageBox.warning(self, "Bariyer Acilamadi", result.get("message", "Bariyer komutu basarisiz."))

    def manual_barrier_open(self):
        try:
            auth.require(self.current_user, "manual_barrier_open")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        gate, ok = QInputDialog.getItem(self, "Manuel Bariyer Acma", "Kapi:",
                                        ["CIKIS-1"], 0, False)
        if not ok:
            return
        reason, ok = QInputDialog.getItem(self, "Manuel Bariyer Acma", "Neden:",
                                          MANUAL_OPEN_REASONS, 0, False)
        if not ok:
            return
        note = ""
        if reason == "Diger":
            note, ok = QInputDialog.getText(self, "Aciklama", "Aciklama (zorunlu):")
            if not ok or not note.strip():
                QMessageBox.warning(self, "Hata", "'Diger' secildiginde aciklama zorunludur.")
                return
        try:
            opened = self.service.barriers.manual_open(
                gate, self.current_user["username"], reason, note)
            if opened:
                self.append_log(f"[{gate}] MANUEL BARIYER ACMA: {self.current_user['username']} ({reason})")
            else:
                QMessageBox.critical(
                    self, "Bariyer Acilamadi",
                    self.service.barriers.last_error(gate)
                    or "Fiziksel bariyer acma komutu basarisiz. Bariyer ayarlarini ve ag baglantisini kontrol edin.",
                )
        except ValueError as exc:
            QMessageBox.warning(self, "Hata", str(exc))

    def _barrier_state_changed(self, gate: str, state: str):
        # Sinyal geldiginde diger kapilari ag uzerinden senkron sorgulamak UI'yi
        # kilitliyordu. Yalnizca gelen kapinin son durumunu guncelle.
        level = "error" if state in ("ARIZALI", "BAGLANTI_YOK") else "ok"
        self._set_health(self.barrier_health, "BARIYER", state, level)

    def _panel_for_gate(self, gate: str):
        return {"GIRIS-1": self.entry1_panel, "GIRIS-2": self.entry2_panel,
                "CIKIS-1": self.exit_panel}.get(gate, self.entry1_panel)

    def _start_configured_cameras(self):
        """Veritabanindaki ONVIF/RTSP kameralari ana ekran panellerine baglar."""
        self._stop_camera_workers()
        for device in db.list_devices("CAMERA"):
            if not device.get("online"):
                continue
            gate = (device.get("gate") or "").upper().replace(" ", "-")
            if gate not in ACTIVE_CAMERA_GATES:
                self.append_log(f"[Sistem] Kamera kapisi gecersiz: {device.get('name')} ({gate or 'bos'})")
                continue
            try:
                password = unprotect_secret(device.get("onvif_password_enc") or "")
            except Exception:
                password = ""
            if not device.get("rtsp_url"):
                if not device.get("ip") or not device.get("onvif_username") or not password:
                    self.append_log(f"[{gate}] ONVIF bilgileri eksik; kamera ayarlarini kontrol edin")
                    continue
                discovery = OnvifDiscoveryWorker(device, password, self)
                discovery.stream_found.connect(self._on_onvif_stream_found)
                discovery.discovery_error.connect(self._on_onvif_discovery_error)
                discovery.finished.connect(
                    lambda w=discovery: self._camera_discovery_workers.remove(w)
                    if w in self._camera_discovery_workers else None)
                self._camera_discovery_workers.append(discovery)
                discovery.start()
                self.append_log(f"[{gate}] ONVIF yayin adresi otomatik araniyor...")
                continue
            self._start_camera_device(device, password)

    def _start_camera_device(self, device: dict, password: str):
        gate = (device.get("gate") or "").upper().replace(" ", "-")
        stream_url = self._rtsp_with_credentials(
            device.get("rtsp_url") or "", device.get("onvif_username") or "", password)
        config = CameraConfig(
            name=device.get("name") or gate, role="exit" if gate.startswith("CIKIS") else "entry",
            lane=gate, rtsp_url=stream_url,
            speed_scale=float(db.get_setting("speed_estimation_scale", "3.0") or 3.0))
        worker = RtspCameraWorker(config, self)
        worker.frame_ready.connect(self._on_rtsp_frame)
        worker.plate_recognized.connect(self._on_rtsp_plate)
        worker.connection_error.connect(self._on_rtsp_error)
        worker.finished.connect(lambda w=worker: self._camera_workers.remove(w) if w in self._camera_workers else None)
        self._camera_workers.append(worker)
        self._panel_for_gate(gate).set_online(True)
        worker.start()
        self.append_log(f"[{gate}] ONVIF kamera akisi baslatildi: {device.get('name')}")

    def _on_onvif_stream_found(self, device_id: int, uri: str, profile_token: str):
        db.update_device(device_id, rtsp_url=uri, onvif_profile_token=profile_token,
                         last_seen=now_iso())
        device = next((d for d in db.list_devices("CAMERA") if d["id"] == device_id), None)
        if not device:
            return
        try:
            password = unprotect_secret(device.get("onvif_password_enc") or "")
        except Exception as exc:
            self._on_onvif_discovery_error(device_id, device.get("gate") or "KAMERA", str(exc))
            return
        self._start_camera_device(device, password)

    def _on_onvif_discovery_error(self, _device_id: int, gate: str, message: str):
        self._panel_for_gate(gate).set_online(False)
        self.append_log(f"[{gate}] ONVIF otomatik baglanti hatasi: {message}")

    @staticmethod
    def _rtsp_with_credentials(url: str, username: str, password: str) -> str:
        if not url or not username or "@" in urlsplit(url).netloc:
            return url
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        auth_part = f"{quote(username, safe='')}:{quote(password, safe='')}@"
        return urlunsplit((parts.scheme, auth_part + host, parts.path, parts.query, parts.fragment))

    def _on_rtsp_frame(self, frame, gate: str):
        self._last_frame_for_lane[gate] = frame
        panel = self._panel_for_gate(gate)
        panel.set_online(True)
        panel.set_frame(frame)

    def _on_rtsp_plate(self, result, gate: str, captured_frame):
        if captured_frame is None:
            return
        self._on_anpr_result(result, {
            "gate": gate, "camera_key": gate,
            "direction": "EXIT" if gate.startswith("CIKIS") else "ENTRY",
            "frame": captured_frame.copy(),
            "speed_kmh": getattr(result, "speed_kmh", None),
        })

    def _on_rtsp_error(self, gate: str, message: str):
        self._panel_for_gate(gate).set_online(False)
        self.append_log(f"[{gate}] Kamera baglanti hatasi: {message}")

    def _stop_camera_workers(self):
        for worker in list(self._camera_discovery_workers):
            worker.requestInterruption()
            worker.wait(2500)
        self._camera_discovery_workers.clear()
        for worker in list(self._camera_workers):
            worker.stop()
            worker.wait(1500)
        self._camera_workers.clear()

    def _save_capture(self, frame, gate: str, kind: str) -> str:
        # dosya adinda tek basina plaka kullanilmaz; benzersiz kimlik uretilir
        path = os.path.join(CAPTURES_DIR, f"{kind}_{gate}_{new_uuid()}.jpg")
        cv2.imwrite(path, frame)
        return path

    def _prepare_event_images(self, event: dict):
        """PTS olayinda tam arac karesini ve plaka kirpimini ayri saklar.

        Kamera entegrasyonu tam kareyi ``vehicle_image_base64`` (veya
        ``full_frame_base64``), plaka kirpimini ise ``plate_image_base64``
        alaninda gonderebilir. Oturumlarda her zaman tam arac karesi kullanilir.
        """
        gate = event.get("gate_id", "KAMERA")
        full_path = self._save_base64_event_image(
            event, ("vehicle_image_base64", "full_frame_base64", "image_base64"),
            gate, "arac")
        plate_path = self._save_base64_event_image(
            event, ("plate_image_base64", "plate_crop_base64"), gate, "plaka")
        if full_path:
            event["vehicle_image"] = full_path
        elif not event.get("vehicle_image"):
            event["vehicle_image"] = event.get("full_frame") or event.get("snapshot_path") or ""
        if plate_path:
            event["plate_image"] = plate_path

    def _save_base64_event_image(self, event: dict, keys, gate: str, kind: str) -> str:
        encoded = next((event.get(key) for key in keys if event.get(key)), "")
        if not encoded:
            return ""
        try:
            if isinstance(encoded, str) and "," in encoded and encoded.lstrip().startswith("data:image/"):
                encoded = encoded.split(",", 1)[1]
            raw = base64.b64decode(encoded, validate=True)
            image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                return ""
            return self._save_capture(image, gate, kind)
        except (ValueError, TypeError):
            return ""

    def _track_worker(self, worker):
        self._active_workers.append(worker)
        worker.finished.connect(lambda: self._active_workers.remove(worker) if worker in self._active_workers else None)

    def append_log(self, text: str):
        logging.getLogger("otopark").info(text)
        stamp = now().strftime("%H:%M:%S")
        m = re.match(r"^\[(.*?)\]\s*(.*)$", text)
        lane, message = (m.group(1), m.group(2)) if m else ("Sistem", text)
        plate_m = PLATE_RE.search(message)
        thumb = None
        frame = self._last_frame_for_lane.get(lane)
        if frame is not None:
            thumb = numpy_to_pixmap(frame)
        self.history_panel.add_entry(
            plate_display=plate_m.group(0) if plate_m else lane, lane=lane,
            time_str=stamp, description=message, kind=classify_event(text), thumbnail=thumb,
            image_path=self._last_capture_path_for_lane.get(lane, "") if plate_m else "")

    def refresh_all(self):
        sessions = db.list_recent_sessions(200)

        # Program acildiginda sol panel bos kalmasin; son giris/cikislar
        # kayitli arac fotograflariyla birlikte yuklenir.
        if self.history_panel.count() == 0:
            for session in reversed(sessions[:30]):
                exited = bool(session.get("exit_time"))
                image_path = (session.get("exit_image") if exited else None) or session.get("entry_image") or ""
                thumbnail = QPixmap(image_path) if image_path and os.path.isfile(image_path) else None
                time_text = (session.get("exit_time") if exited else session.get("entry_time")) or "-"
                if "T" in time_text:
                    time_text = time_text.split("T", 1)[1][:8]
                lane = (session.get("exit_lane") if exited else session.get("entry_lane")) or "-"
                speed = session.get("exit_speed_kmh") if exited else session.get("entry_speed_kmh")
                description = f"{'Cikis' if exited else 'Giris'}"
                if speed is not None:
                    description += f"  |  {speed:g} km/sa"
                self.history_panel.add_entry(
                    plate_display=session.get("plate") or "-", lane=lane,
                    time_str=time_text, description=description,
                    kind="cikis" if exited else "giris", thumbnail=thumbnail,
                    image_path=image_path)

        self.user_lbl.setText(
            f"Kullanıcı: {self.current_user['username']} ({self.current_user['role']})")
        self._sync_free_pass_button()

    def _sync_free_pass_button(self):
        if not hasattr(self, "free_pass_btn"):
            return
        active = db.get_setting("free_pass_mode", "0") == "1"
        busy = getattr(self, "_free_pass_worker", None) is not None
        error = getattr(self, "_free_pass_error", "")
        confirmed = active and getattr(self, "_free_pass_applied", None) is True and not error
        self.free_pass_btn.blockSignals(True)
        self.free_pass_btn.setChecked(active)
        if busy:
            text = "Serbest gecis uygulanıyor..."
        elif active and not confirmed:
            text = "Serbest gecis - uygulanamadi" if error else "Serbest gecis - bekliyor"
        else:
            text = "SERBEST GECIS ACIK" if confirmed else "Serbest Gecis"
        self.free_pass_btn.setText(text)
        self.free_pass_btn.setObjectName("successBtn" if confirmed else "")
        self.free_pass_btn.setStyleSheet("background:#166534;color:white;border-color:#22c55e;" if confirmed else "")
        self.free_pass_btn.setToolTip(error)
        self.free_pass_btn.setEnabled(
            not busy and not getattr(self, "_close_after_barrier_shortcut", False)
            and self._perm("manual_barrier_open"))
        self.free_pass_btn.blockSignals(False)

    def _start_free_pass_change(self, enabled: bool, *, restoring=False):
        if (self._free_pass_worker is not None
                or getattr(self, "_close_after_barrier_shortcut", False)):
            self._sync_free_pass_button()
            return
        self._free_pass_request = (enabled, restoring, self.current_user["username"])
        self._free_pass_error = ""
        worker = FreePassModeTask(
            self.service.barriers, enabled, self.current_user["username"], self)
        self._free_pass_worker = worker
        worker.completed.connect(self._free_pass_completed)
        worker.finished.connect(self._free_pass_finished)
        self._sync_free_pass_button()
        worker.start()

    def _apply_persisted_free_pass(self):
        if db.get_setting("free_pass_mode", "0") == "1":
            self._start_free_pass_change(True, restoring=True)

    def toggle_free_pass(self, checked: bool):
        if (self._free_pass_worker is not None
                or getattr(self, "_close_after_barrier_shortcut", False)):
            self._sync_free_pass_button()
            return
        try:
            auth.require(self.current_user, "manual_barrier_open")
        except auth.PermissionDenied as exc:
            self._sync_free_pass_button()
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        if checked:
            answer = QMessageBox.question(
                self, "Serbest Gecis",
                "Serbest gecis acilacak. Tek cikis bariyeri acik kalacak; kameralar giris ve cikislari kaydetmeye devam edecek. Devam?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._sync_free_pass_button()
                return
        self._start_free_pass_change(bool(checked))

    def _free_pass_completed(self, applied: bool, error: str):
        enabled, restoring, username = self._free_pass_request
        if applied:
            self._free_pass_applied = enabled
            self._free_pass_error = ""
            if not restoring:
                db.audit(username, "SERBEST_GECIS_ACILDI" if enabled else "SERBEST_GECIS_KAPATILDI")
            if enabled:
                self.append_log("[Sistem] SERBEST GECIS aktif; bariyeri surekli acik tutma komutu gonderildi.")
            else:
                self.append_log("[Sistem] SERBEST GECIS kapatildi; surekli acik tutma birakildi, normal gecis moduna donuldu.")
        else:
            # Gecikmis/kayip yanit, kartin son komutu uygulamadigini kanitlamaz.
            # Onceki HOLD basarisini, basarisiz RELEASE sonrasinda onay sayma.
            self._free_pass_applied = None
            self._free_pass_error = error or "Serbest gecis komutu uygulanamadi. Bariyer ayarlarini ve baglantiyi kontrol edin."
            self.append_log(f"[Sistem] SERBEST GECIS uygulanamadi: {self._free_pass_error}")
            if not getattr(self, "_close_after_barrier_shortcut", False):
                QMessageBox.warning(self, "Serbest Gecis Uygulanamadi", self._free_pass_error)
        self._sync_free_pass_button()

    def _free_pass_finished(self):
        worker, self._free_pass_worker = self._free_pass_worker, None
        worker.deleteLater()
        self._sync_free_pass_button()
        self._barrier_shortcut_idle()

    def _update_clock(self):
        self.clock_lbl.setText(now().strftime("%d.%m.%Y  %H:%M:%S"))

    # ------------------------------------------------------- DIALOGLAR ----
    def open_subscribers(self):
        try:
            auth.require(self.current_user, "manage_subscriptions")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        SubscribersDialog(self).exec()

    def open_blacklist(self):
        try:
            auth.require(self.current_user, "manage_blacklist")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        BlacklistDialog(self).exec()

    def open_users(self):
        try:
            auth.require(self.current_user, "manage_users")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        UsersDialog(self).exec()

    def open_tariffs(self):
        try:
            auth.require(self.current_user, "manage_tariffs")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        TariffDialog(self.current_user["username"], self).exec()

    def open_reports(self):
        try:
            auth.require(self.current_user, "view_reports")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        ReportsDialog(self.current_user["username"], self).exec()

    def open_smtp(self):
        if not any(self._perm(perm) for perm in SmtpDialog.ACCESS_PERMISSIONS):
            QMessageBox.warning(self, "Yetki", "Rapor ve e-posta ayarları için yetkiniz yok.")
            return
        SmtpDialog(self.current_user["username"], self).exec()
        if self.scheduler:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
        self.scheduler = start_scheduler()

    def open_devices(self):
        try:
            auth.require(self.current_user, "manage_devices")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        CameraDialog(self.current_user["username"], self).exec()
        self._start_configured_cameras()

    def open_barrier_control(self):
        try:
            auth.require(self.current_user, "manage_devices")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        BarrierControlDialog(self.service.barriers, self.current_user["username"], self).exec()

    def open_audit(self):
        try:
            auth.require(self.current_user, "view_audit_logs")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        AuditLogDialog(self).exec()

    def open_simulation(self):
        dialog = SimulationDialog(self.service, self)
        dialog.event_callback = self.process_camera_event
        dialog.exec()

    def show_alarm(self, plate: str, message: str):
        QMessageBox.critical(self, "KARA LISTE ALARMI", f"{format_plate(plate)}\n\n{message}")

    def configure_speed_limit(self):
        try:
            auth.require(self.current_user, "manage_settings")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        current = float(db.get_setting("speed_limit_kmh", "0") or 0)
        value, ok = QInputDialog.getDouble(
            self, "Hiz Limiti Ayari",
            "PTS hiz alarm siniri (km/sa)\n0 girilirse hiz alarmi kapatilir:",
            current, 0, 300, 0,
        )
        if not ok:
            return
        db.set_setting("speed_limit_kmh", str(value))
        db.audit(self.current_user["username"], "HIZ_LIMITI_DEGISTI",
                 old_value=str(current), new_value=str(value))
        QMessageBox.information(
            self, "Kaydedildi",
            "Hiz alarmi kapatildi." if value == 0 else f"Hiz alarm siniri {value:.0f} km/sa olarak ayarlandi.",
        )

    def configure_speed_calibration(self):
        try:
            auth.require(self.current_user, "manage_settings")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return
        current = float(db.get_setting("speed_estimation_scale", "3.0") or 3.0)
        value, ok = QInputDialog.getDouble(
            self, "Hiz Olcum Kalibrasyonu",
            "RTSP hiz olcum katsayisi.\n"
            "Ornek: Program 10, referans cihaz 20 km/sa okuyorsa 2.00 girin.\n"
            "Degisiklik kamera akislarinin yeniden baslatilmasiyla uygulanir:",
            current, 0.10, 10.0, 2,
        )
        if not ok:
            return
        db.set_setting("speed_estimation_scale", str(value))
        db.audit(self.current_user["username"], "HIZ_KALIBRASYONU_DEGISTI",
                 old_value=str(current), new_value=str(value))
        self._start_configured_cameras()
        QMessageBox.information(self, "Kaydedildi", f"Hiz kalibrasyon katsayisi: {value:.2f}")

    def show_about(self):
        QMessageBox.information(
            self, "Hakkında",
            "Otopark Yönetim Sistemi\n\n"
            "FastALPR/ONNX plaka tanıma, tarife ve ödeme, abone yönetimi,\n"
            "Metcom bariyer/LED, işlem logu ve 09:00-09:00 SMTP raporlama.")

    def closeEvent(self, event):
        self.barrier_shortcut.disable()
        self._close_after_barrier_shortcut = True
        if hasattr(self, "free_pass_btn"):
            self.free_pass_btn.setEnabled(False)
        if (self.barrier_shortcut.busy
                or getattr(self, "_free_pass_worker", None) is not None):
            event.ignore()
            return
        for viewer in self.findChildren(ImageViewerDialog):
            viewer.close()
        self._webhook_retry_timer.stop()
        self._stop_camera_workers()
        self.service.exit_display.close()
        if self.scheduler:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
        self.webhook.stop()
        super().closeEvent(event)

    def _barrier_shortcut_idle(self):
        if (self._close_after_barrier_shortcut
                and not self.barrier_shortcut.busy
                and getattr(self, "_free_pass_worker", None) is None):
            QTimer.singleShot(0, self.close)
