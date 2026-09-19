"""Bariyer iletisim paneli: gercek donanim ile bariyer kontrolu.

Bu ekrandan:
  - Her kapi icin bariyer cihazi tanimlanir (protokol, IP, port, komutlar)
  - Baglanti testi yapilir
  - Bariyer manuel acilir/kapatilir
  - Bariyer durum gecmisi goruntulenir
  - Emniyet sensoru durumu gorulur

Desteklenen protokoller: MOCK, METCOM_IO, SERIAL, TCP, HTTP, GPIO
"""
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QMessageBox, QGroupBox, QSpinBox, QTabWidget, QWidget,
    QStackedWidget, QCheckBox
)

from app import db
from app.barrier import (
    BarrierService, KAPALI, ACIK, ARIZALI, BAGLANTI_YOK, EMNIYET_AKTIF,
    MANUAL_OPEN_REASONS, METCOM_CLOSE_UNAVAILABLE
)

GATES = ["CIKIS-1"]
PROTOCOLS = ["MOCK", "METCOM_IO", "SERIAL", "TCP", "HTTP", "GPIO"]


class _BarrierTask(QThread):
    completed = Signal(object, object)  # sonuc, hata

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.completed.emit(self.operation(), None)
        except Exception as exc:
            self.completed.emit(None, exc)


class BarrierControlDialog(QDialog):
    def __init__(self, service: BarrierService, username: str, parent=None):
        super().__init__(parent)
        self.service = service
        self.username = username
        self._tasks = set()
        self._status_busy = False
        self._close_requested = False
        self.setWindowTitle("Bariyer Iletisim ve Kontrol Paneli")
        self.resize(900, 640)

        layout = QVBoxLayout(self)

        # Info
        info = QLabel(
            "Bu panelden her kapi icin bariyer cihazi tanimlanir ve gercek donanim ile\n"
            "iletisim kurulur. Protokol secimine gore ayar alanlari degisir.\n"
            "MOCK = simülasyon (donanim yok), SERIAL = RS-232, TCP = IP soket,\n"
            "METCOM_IO = 192.0.2.7 bariyer / durum / loop, HTTP = REST API, "
            "GPIO = Raspberry Pi pin."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #8b949e; font-size: 11px; padding: 4px;")
        layout.addWidget(info)

        # Arma PTS'den okunan tesis profili. Buradaki alanlar saha cihazlari
        # degistiginde program icinden guncellenebilir.
        arma_group = QGroupBox("Arma Saha Profili (Bariyer / Loop / LED Tabela)")
        arma_form = QFormLayout(arma_group)
        self.arma_barrier_ip = QLineEdit(db.get_setting("arma_barrier_ip", "192.0.2.7"))
        arma_form.addRow("Bariyer Ethernet IP:", self.arma_barrier_ip)
        self.arma_output = QSpinBox()
        self.arma_output.setRange(1, 32)
        self.arma_output.setValue(int(db.get_setting("arma_barrier_output", "3") or 3))
        arma_form.addRow("Bariyer role cikisi:", self.arma_output)
        self.arma_tcp_port = QSpinBox()
        self.arma_tcp_port.setRange(1, 65535)
        self.arma_tcp_port.setValue(int(db.get_setting("arma_barrier_tcp_port", "8080") or 8080))
        arma_form.addRow("Metcom TCP portu:", self.arma_tcp_port)
        self.arma_trigger_hex = QLineEdit(db.get_setting("arma_barrier_trigger_hex", ""))
        self.arma_trigger_hex.setPlaceholderText("Saha kaydından veya üreticiden alınan açma paketi")
        arma_form.addRow("OUT3 açma komutu (HEX):", self.arma_trigger_hex)
        self.arma_trigger_verified = QCheckBox(
            "Komut üretici bilgisiyle / kontrollü saha testinde doğrulandı")
        self.arma_trigger_verified.setChecked(
            db.get_setting("arma_barrier_trigger_verified", "0") == "1")
        arma_form.addRow("", self.arma_trigger_verified)
        trigger_help = QLabel(
            "18.09.2026 Arma kaydı: 192.0.2.7:8080 / OUT3 için "
            "02 00 03 02 03 açma paketi ve kartın yanıtı yakalandı. "
            "Serbest Geçiş ayrı bir sürekli açık tutma komutu kullanır; "
            "mod kapatılınca bariyer normal otomatik kapanma düzenine döner. "
            "Bağlantı testi bariyeri açmaz; açma düğmesiyle fiziksel sonuç kontrol edilir.")
        trigger_help.setWordWrap(True)
        trigger_help.setStyleSheet("color:#9a6700; font-size:10px")
        arma_form.addRow("", trigger_help)
        self.arma_loop_device = QComboBox()
        self.arma_loop_device.addItems(["USB 1", "USB 2", "Kullanilmiyor"])
        self.arma_loop_device.setCurrentText(db.get_setting("arma_loop_device", "USB 1"))
        arma_form.addRow("Gecis loop cihazi:", self.arma_loop_device)
        self.arma_contact = QComboBox()
        self.arma_contact.addItems(["NO", "NC"])
        self.arma_contact.setCurrentText(db.get_setting("arma_loop_contact", "NO"))
        arma_form.addRow("Loop tetik tipi:", self.arma_contact)
        self.arma_wait = QSpinBox()
        self.arma_wait.setRange(1, 60)
        self.arma_wait.setValue(int(db.get_setting("arma_loop_wait_sec", "7") or 7))
        arma_form.addRow("Gecis bekleme suresi:", self.arma_wait)
        self.arma_led_ip = QLineEdit(db.get_setting("arma_led_ip", "192.0.2.5"))
        arma_form.addRow("LED tabela IP (Tip2):", self.arma_led_ip)
        self.arma_led_enabled = QCheckBox("Fiziksel LED tabelaya plaka/ucret gonder")
        self.arma_led_enabled.setChecked(db.get_setting("arma_led_enabled", "0") == "1")
        arma_form.addRow("", self.arma_led_enabled)
        self.arma_led_text = QLineEdit(
            (db.get_setting("arma_led_idle_text", "DEMO\nOTOPARK") or "").replace("\n", " / "))
        arma_form.addRow("LED sabit yazi:", self.arma_led_text)
        self.arma_led_speed = QCheckBox("Arac hizini LED tabelada goster")
        self.arma_led_speed.setChecked(db.get_setting("arma_led_show_speed", "0") == "1")
        arma_form.addRow("", self.arma_led_speed)
        arma_save = QPushButton("Arma Saha Profilini Kaydet")
        arma_save.clicked.connect(self._save_arma_profile)
        arma_form.addRow("", arma_save)
        layout.addWidget(arma_group)

        # Kapı seçici
        gate_row = QHBoxLayout()
        gate_row.addWidget(QLabel("Kapi:"))
        self.gate_combo = QComboBox()
        self.gate_combo.addItems(GATES)
        self.gate_combo.currentTextChanged.connect(self._on_gate_change)
        gate_row.addWidget(self.gate_combo)
        gate_row.addStretch()

        self.status_lbl = QLabel("Durum: -")
        self.status_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        gate_row.addWidget(self.status_lbl)
        layout.addLayout(gate_row)

        # Protocol ayarları
        proto_group = QGroupBox("Bariyer Protokol Ayarlari")
        proto_layout = QFormLayout(proto_group)

        self.proto_combo = QComboBox()
        self.proto_combo.addItems(PROTOCOLS)
        self.proto_combo.currentTextChanged.connect(self._on_proto_change)
        proto_layout.addRow("Protokol:", self.proto_combo)

        # SERIAL / TCP ortak
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("SERIAL: COM3  |  TCP: 192.0.2.50")
        proto_layout.addRow("IP / Port Adi:", self.ip_input)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(5000)
        proto_layout.addRow("TCP Port:", self.port_input)

        self.baud_input = QSpinBox()
        self.baud_input.setRange(1200, 115200)
        self.baud_input.setValue(9600)
        self.baud_input.setSingleStep(100)
        proto_layout.addRow("Baud Rate (SERIAL):", self.baud_input)

        self.open_cmd_input = QLineEdit("OPEN\n")
        proto_layout.addRow("Acma Komutu:", self.open_cmd_input)

        self.close_cmd_input = QLineEdit("CLOSE\n")
        proto_layout.addRow("Kapatma Komutu:", self.close_cmd_input)

        self.status_cmd_input = QLineEdit("STATUS?\n")
        proto_layout.addRow("Durum Sorgu:", self.status_cmd_input)

        self.open_resp_input = QLineEdit("OK")
        proto_layout.addRow("Acma Yaniti:", self.open_resp_input)

        self.close_resp_input = QLineEdit("OK")
        proto_layout.addRow("Kapatma Yaniti:", self.close_resp_input)

        # HTTP özel
        self.http_open_input = QLineEdit()
        self.http_open_input.setPlaceholderText("http://192.0.2.50/barrier/open")
        proto_layout.addRow("HTTP Acma URL:", self.http_open_input)

        self.http_close_input = QLineEdit()
        self.http_close_input.setPlaceholderText("http://192.0.2.50/barrier/close")
        proto_layout.addRow("HTTP Kapatma URL:", self.http_close_input)

        self.http_status_input = QLineEdit()
        self.http_status_input.setPlaceholderText("http://192.0.2.50/barrier/status")
        proto_layout.addRow("HTTP Durum URL:", self.http_status_input)

        # GPIO özel
        self.gpio_input = QSpinBox()
        self.gpio_input.setRange(1, 40)
        self.gpio_input.setValue(18)
        proto_layout.addRow("GPIO Pin:", self.gpio_input)

        layout.addWidget(proto_group)

        # Butonlar
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Ayarları Kaydet")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._save_settings)
        btn_row.addWidget(save_btn)

        test_btn = QPushButton("Baglanti Testi")
        test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(test_btn)

        btn_row.addStretch()

        open_btn = QPushButton("Bariyeri Ac")
        open_btn.setStyleSheet("background-color: #2ea043; color: white; font-weight: bold; padding: 8px 16px;")
        open_btn.clicked.connect(self._open_barrier)
        btn_row.addWidget(open_btn)

        self.close_btn = QPushButton("Bariyeri Kapat")
        self.close_btn.setStyleSheet("QPushButton:enabled {background-color: #da3633; color: white; font-weight: bold; padding: 8px 16px;}")
        self.close_btn.clicked.connect(self._close_barrier)
        btn_row.addWidget(self.close_btn)

        layout.addLayout(btn_row)
        self.close_help = QLabel()
        self.close_help.setWordWrap(True)
        self.close_help.setStyleSheet("color:#9a6700; font-size:11px")
        layout.addWidget(self.close_help)

        # Durum güncelleme timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(2000)

        # Olay geçmişi
        layout.addWidget(QLabel("Bariyer Olay Gecmisi:"))
        self.event_table = QTableWidget(0, 6)
        self.event_table.setHorizontalHeaderLabels(
            ["Zaman", "Kapi", "Islem", "Onceki Durum", "Kullanici", "Not"])
        self.event_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.event_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.event_table, 1)

        self._on_gate_change(self.gate_combo.currentText())

    def _save_arma_profile(self):
        trigger_hex = "".join(self.arma_trigger_hex.text().split()).upper()
        try:
            if trigger_hex:
                bytes.fromhex(trigger_hex)
        except ValueError:
            QMessageBox.warning(
                self, "Geçersiz Komut",
                "Metcom açma komutu yalnızca 0-9/A-F karakterlerinden oluşan, "
                "çift uzunlukta bir HEX değer olmalıdır.")
            return
        if self.arma_trigger_verified.isChecked() and not trigger_hex:
            QMessageBox.warning(
                self, "Komut Eksik",
                "'Sahada doğrulandı' seçiliyken açma komutu boş bırakılamaz.")
            return
        values = {
            "arma_barrier_type": "Ethernet",
            "arma_barrier_ip": self.arma_barrier_ip.text().strip(),
            "arma_barrier_output": str(self.arma_output.value()),
            "arma_barrier_shortcut": "F4",
            "arma_barrier_tcp_port": str(self.arma_tcp_port.value()),
            "arma_barrier_heartbeat_hex": "00",
            "arma_barrier_heartbeat_sec": "5",
            "arma_barrier_trigger_hex": trigger_hex,
            "arma_barrier_trigger_verified":
                "1" if self.arma_trigger_verified.isChecked() else "0",
            "arma_metcom_status_url":
                f"http://{self.arma_barrier_ip.text().strip()}/index.xml",
            "arma_loop_device": self.arma_loop_device.currentText(),
            "arma_loop_contact": self.arma_contact.currentText(),
            "arma_loop_wait_sec": str(self.arma_wait.value()),
            "arma_led_type": "Tip2",
            "arma_led_ip": self.arma_led_ip.text().strip(),
            "arma_led_port": "6101",
            "arma_led_enabled": "1" if self.arma_led_enabled.isChecked() else "0",
            "arma_led_size": "64x32",
            "arma_led_panel": "Tek Renk",
            "arma_led_duration_sec": "5",
            "arma_led_border": "0",
            "arma_led_show_speed": "1" if self.arma_led_speed.isChecked() else "0",
            "arma_led_idle_text": self.arma_led_text.text().replace(" / ", "\n").strip(),
        }
        for key, value in values.items():
            db.set_setting(key, value)
        barrier = db.get_device_by_gate("CIKIS-1", "BARRIER")
        barrier_fields = {
            "name": "METCOM IO - CIKIS BARIYERI",
            "ip": values["arma_barrier_ip"],
            "port": values["arma_barrier_tcp_port"],
            "direction": "EXIT",
            "online": 1,
            "protocol": "METCOM_IO",
            "http_status_url": values["arma_metcom_status_url"],
            "note": f"Metcom I/O; Bariyer{values['arma_barrier_output']}, Loop1",
        }
        if barrier:
            db.update_device(barrier["id"], **barrier_fields)
        else:
            db.add_device(kind="BARRIER", gate="CIKIS-1", **barrier_fields)
        self.service.reload_gate("CIKIS-1")
        # Refresh protocol fields too: leaving MOCK visible here could overwrite
        # the real profile when the user subsequently runs Connection Test.
        self._load_settings(self.gate_combo.currentText())
        self._update_close_controls()
        parent = self.parent()
        if parent and hasattr(parent, "service"):
            parent.service.exit_display.reload_physical_settings()
        db.audit(self.username, "ARMA_SAHA_PROFILI_DEGISTI", target="CIKIS-1",
                 new_value=f"IO {values['arma_barrier_ip']}/OUT{values['arma_barrier_output']} | "
                           f"LED {values['arma_led_ip']}")
        QMessageBox.information(
            self, "Kaydedildi",
            "Bariyer, loop ve LED tabela saha profili kaydedildi.\n\n"
            "Not: Ekrandaki 'Port 3' TCP portu değil, röle çıkış kanalıdır. "
            "TCP bağlantısı ve heartbeat otomatik yönetilir. Açma paketi yalnızca "
            "doğrulanmış olarak işaretlenmişse gönderilir.")

    def _on_gate_change(self, gate: str):
        self._load_settings(gate)
        self._update_close_controls()
        self._refresh_status()
        self._load_events(gate)

    def _update_close_controls(self):
        supported = self.service.is_close_supported(self.gate_combo.currentText())
        self.close_btn.setEnabled(supported)
        self.close_btn.setText("Bariyeri Kapat" if supported else "Uzaktan Kapatma Tanımlı Değil")
        self.close_btn.setToolTip("" if supported else METCOM_CLOSE_UNAVAILABLE)
        self.close_help.setText("" if supported else METCOM_CLOSE_UNAVAILABLE)
        self.close_help.setVisible(not supported)

    def _on_proto_change(self, proto: str):
        """Protokol seçimine göre alanları göster/gizle."""
        is_serial = proto == "SERIAL"
        is_tcp = proto == "TCP"
        is_metcom = proto == "METCOM_IO"
        is_http = proto == "HTTP"
        is_gpio = proto == "GPIO"

        # IP/Port adı: SERIAL ve TCP için
        self._set_row_visible(self.ip_input, is_serial or is_tcp or is_metcom)
        # TCP port
        self._set_row_visible(self.port_input, is_tcp or is_metcom)
        # Baud: sadece SERIAL
        self._set_row_visible(self.baud_input, is_serial)
        # Komutlar: SERIAL ve TCP için
        show_cmds = is_serial or is_tcp
        self._set_row_visible(self.open_cmd_input, show_cmds)
        self._set_row_visible(self.close_cmd_input, show_cmds)
        self._set_row_visible(self.status_cmd_input, show_cmds)
        self._set_row_visible(self.open_resp_input, show_cmds)
        self._set_row_visible(self.close_resp_input, show_cmds)
        # HTTP URL'leri: sadece HTTP
        self._set_row_visible(self.http_open_input, is_http)
        self._set_row_visible(self.http_close_input, is_http)
        self._set_row_visible(self.http_status_input, is_http)
        # GPIO pin: sadece GPIO
        self._set_row_visible(self.gpio_input, is_gpio)

    def _set_row_visible(self, field, visible: bool):
        field.setVisible(visible)
        # QFormLayout etiketleri ayri widget'lardir; sadece alani gizlemek bos
        # satir birakir. labelForField ile etiketi de birlikte yonet.
        form = field.parentWidget().layout()
        if isinstance(form, QFormLayout):
            label = form.labelForField(field)
            if label:
                label.setVisible(visible)

    def _load_settings(self, gate: str):
        device = db.get_device_by_gate(gate, "BARRIER")
        if not device:
            self.proto_combo.setCurrentText("MOCK")
            self._on_proto_change("MOCK")
            return
        proto = device.get("protocol") or "MOCK"
        self.proto_combo.setCurrentText(proto)
        self._on_proto_change(proto)
        self.ip_input.setText(device.get("ip") or "")
        self.port_input.setValue(int(device.get("port") or 5000))
        self.baud_input.setValue(int(device.get("baud_rate") or 9600))
        self.open_cmd_input.setText(device.get("open_cmd") or "OPEN\n")
        self.close_cmd_input.setText(device.get("close_cmd") or "CLOSE\n")
        self.status_cmd_input.setText(device.get("status_cmd") or "STATUS?\n")
        self.open_resp_input.setText(device.get("open_response") or "OK")
        self.close_resp_input.setText(device.get("close_response") or "OK")
        self.http_open_input.setText(device.get("http_open_url") or "")
        self.http_close_input.setText(device.get("http_close_url") or "")
        self.http_status_input.setText(device.get("http_status_url") or "")
        self.gpio_input.setValue(int(device.get("gpio_pin") or 18))

    def _save_settings(self):
        gate = self.gate_combo.currentText()
        proto = self.proto_combo.currentText()
        device = db.get_device_by_gate(gate, "BARRIER")
        if not device:
            # Yeni cihaz kaydı oluştur
            did = db.add_device(
                name=f"BARIYER-{gate}", kind="BARRIER", gate=gate,
                ip=self.ip_input.text().strip(),
                port=str(self.port_input.value()),
                direction="",
                note=f"Kapi {gate} bariyer kontrolcusu",
                protocol=proto,
                baud_rate=self.baud_input.value(),
                open_cmd=self.open_cmd_input.text(),
                close_cmd=self.close_cmd_input.text(),
                status_cmd=self.status_cmd_input.text(),
                open_response=self.open_resp_input.text(),
                close_response=self.close_resp_input.text(),
                http_open_url=self.http_open_input.text(),
                http_close_url=self.http_close_input.text(),
                http_status_url=self.http_status_input.text(),
                gpio_pin=self.gpio_input.value(),
            )
            db.audit(self.username, "BARIYER_CIHAZ_EKLENDI", target=f"device#{did}",
                     new_value=f"{proto}:{gate}")
        else:
            db.update_device(device["id"],
                             protocol=proto,
                             ip=self.ip_input.text().strip(),
                             port=str(self.port_input.value()),
                             baud_rate=self.baud_input.value(),
                             open_cmd=self.open_cmd_input.text(),
                             close_cmd=self.close_cmd_input.text(),
                             status_cmd=self.status_cmd_input.text(),
                             open_response=self.open_resp_input.text(),
                             close_response=self.close_resp_input.text(),
                             http_open_url=self.http_open_input.text(),
                             http_close_url=self.http_close_input.text(),
                             http_status_url=self.http_status_input.text(),
                             gpio_pin=self.gpio_input.value())
            db.audit(self.username, "BARIYER_AYAR_DEGISTI", target=gate,
                     new_value=proto)

        # Controller'ı yeniden yükle
        self.service.reload_gate(gate)
        self._update_close_controls()
        QMessageBox.information(self, "Kaydedildi",
                                f"{gate} bariyer ayarlari kaydedildi.\n"
                                f"Protokol: {proto}\n"
                                f"Baglanti testi icin 'Baglanti Testi' butonunu kullanin.")

    def _test_connection(self):
        gate = self.gate_combo.currentText()
        # Önce ayarları kaydet ki yeni ayarlarla test yapılsın
        self._save_settings()
        self.status_lbl.setText("Durum: BAGLANTI TEST EDILIYOR...")
        self._run_async(lambda: self.service.test_connection(gate),
                        lambda result, error: self._test_finished(gate, result, error))

    def _test_finished(self, gate, result, error):
        if error:
            ok, msg = False, str(error)
        else:
            ok, msg = result
        if ok:
            if not self.service.is_open_configured(gate):
                QMessageBox.warning(
                    self, "Baglanti Var, Acma Komutu Eksik",
                    msg + "\n\nIP baglantisi tek basina bariyeri acmaz. "
                    "Calisan uygulamanin bariyer acma komutu veya ureticinin "
                    "protokol bilgisi gereklidir.")
            else:
                QMessageBox.information(
                    self, "Baglanti OK", msg + "\n\nBu test fiziksel bariyeri acmaz.")
        else:
            QMessageBox.warning(self, "Baglanti Basarisiz", msg)
        db.audit(self.username, "BARIYER_BAGLANTI_TESTI", target=gate,
                 new_value="OK" if ok else "HATA", note=msg)

    def _open_barrier(self):
        gate = self.gate_combo.currentText()
        self.status_lbl.setText("Durum: ACMA KOMUTU GONDERILIYOR...")
        self._run_async(
            lambda: self.service.open(gate, username=self.username, reason="Panel manuel acma"),
            lambda ok, error: self._open_finished(gate, ok, error))

    def _open_finished(self, gate, ok, error):
        self._load_events(gate)
        self._refresh_status()
        if error:
            QMessageBox.warning(self, "Hata", str(error))
            return
        if ok:
            db.audit(self.username, "BARIYER_PANEL_ACMA", target=gate)
        else:
            QMessageBox.warning(
                self, "Bariyer Acilamadi",
                self.service.last_error(gate) or f"{gate} bariyeri acilamadi.")

    def _close_barrier(self):
        gate = self.gate_combo.currentText()
        if not self.service.is_close_supported(gate):
            QMessageBox.information(self, "Kapatma Komutu Tanımlı Değil", METCOM_CLOSE_UNAVAILABLE)
            return
        self.status_lbl.setText("Durum: KAPATMA KOMUTU GONDERILIYOR...")
        self._run_async(
            lambda: self.service.close(gate, username=self.username),
            lambda ok, error: self._close_finished(gate, ok, error))

    def _close_finished(self, gate, ok, error):
        self._load_events(gate)
        self._refresh_status()
        if error:
            QMessageBox.warning(self, "Hata", str(error))
            return
        if not ok:
            QMessageBox.warning(
                self, "Bariyer Kapatilamadi",
                self.service.last_error(gate)
                or f"{gate} bariyeri kapatilamadi. Emniyet sensoru aktif veya cihaz baglantisi yok.")

    def _refresh_status(self):
        if self._status_busy:
            return
        gate = self.gate_combo.currentText()
        self._status_busy = True
        self._run_async(lambda: self.service.status(gate),
                        lambda state, error: self._status_finished(gate, state, error))

    def _status_finished(self, gate, state, error):
        self._status_busy = False
        if gate != self.gate_combo.currentText():
            return
        if error:
            state = BAGLANTI_YOK
        color = "#2ea043" if state == ACIK else "#da3633" if state in (ARIZALI, BAGLANTI_YOK) else "#8b949e"
        self.status_lbl.setText(f"Durum: {state}")
        self.status_lbl.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {color};")

    def _run_async(self, operation, callback):
        task = _BarrierTask(operation, self)
        self._tasks.add(task)
        task.completed.connect(callback)
        task.finished.connect(lambda t=task: self._task_finished(t))
        task.start()

    def _task_finished(self, task):
        self._tasks.discard(task)
        task.deleteLater()
        if self._close_requested and not self._tasks:
            QTimer.singleShot(0, self.close)

    def _load_events(self, gate: str):
        events = db.list_barrier_events(gate, 50)
        self.event_table.setRowCount(0)
        for e in events:
            row = self.event_table.rowCount()
            self.event_table.insertRow(row)
            vals = [e.get("ts", "-"), e.get("gate", "-"), e.get("action", "-"),
                    e.get("state_before", "-"), e.get("username", "-"), e.get("note", "-")]
            for c, v in enumerate(vals):
                self.event_table.setItem(row, c, QTableWidgetItem(str(v)))

    def closeEvent(self, event):
        self._timer.stop()
        if self._tasks:
            self._close_requested = True
            self.status_lbl.setText("Durum: DEVAM EDEN ISLEM TAMAMLANIYOR...")
            event.ignore()
            return
        super().closeEvent(event)
