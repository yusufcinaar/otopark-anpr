"""ONVIF/RTSP kamera tanimlarini yoneten ekran.

Bariyer ve LED ayarlari bilerek bu ekrandan ayridir. Boylece bir kamera kaydi
duzenlenirken fiziksel bariyer surucusunun yanlislikla MOCK olarak ezilmesi
engellenir.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QMessageBox, QGroupBox, QCheckBox, QScrollArea, QAbstractItemView
)

from app import db
from app.camera import OnvifDiscoveryWorker
from app.security import protect_secret, unprotect_secret
from app.utils import now_iso


DIRECTIONS = ["", "ENTRY", "EXIT"]


class CameraDialog(QDialog):
    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.username = username
        self._editing_device_id = None
        self._test_worker = None
        self.setWindowTitle("Kamera Tanimlari")
        self.resize(1120, 760)
        self.setMinimumSize(900, 650)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Giris ve cikis kameralarini buradan yonetin.\n"
            "ONVIF kameralar icin IP, ONVIF portu, kullanici adi ve sifreyi doldurun.\n"
            "Bariyer ve LED ayarlari ayri 'Bariyer ve LED Ayarlari' ekranindadir."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #8b949e; font-size: 11px; padding: 4px;")
        layout.addWidget(info)

        # --- Cihaz ekleme formu ---
        add_group = QGroupBox("Yeni Cihaz Ekle")
        add_layout = QFormLayout(add_group)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("ornek: LPR-GIRIS-01")
        add_layout.addRow("Ad:", self.name_input)

        self.gate_input = QComboBox()
        self.gate_input.addItems(["GIRIS-1", "GIRIS-2", "CIKIS-1"])
        self.gate_input.currentTextChanged.connect(self._sync_direction)
        add_layout.addRow("Kapi:", self.gate_input)

        self.direction_combo = QComboBox()
        self.direction_combo.addItems(DIRECTIONS)
        add_layout.addRow("Yon:", self.direction_combo)
        self._sync_direction(self.gate_input.currentText())

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("192.0.2.10")
        add_layout.addRow("IP:", self.ip_input)

        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText("80")
        self.port_input.setText("80")
        add_layout.addRow("ONVIF Portu:", self.port_input)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Kamera kullanici adi")
        add_layout.addRow("ONVIF Kullanici:", self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Kamera sifresi")
        add_layout.addRow("ONVIF Sifre:", self.password_input)

        self.profile_input = QLineEdit()
        self.profile_input.setPlaceholderText("Baglanti testinde otomatik bulunur")
        add_layout.addRow("Profil Token:", self.profile_input)

        self.rtsp_input = QLineEdit()
        self.rtsp_input.setPlaceholderText("rtsp://kullanici:sifre@ip:554/stream")
        add_layout.addRow("RTSP URL:", self.rtsp_input)

        self.test_btn = QPushButton("ONVIF Baglantisini Test Et ve Akisi Bul")
        self.test_btn.clicked.connect(self._test_onvif)
        add_layout.addRow("", self.test_btn)

        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("aciklama (opsiyonel)")
        add_layout.addRow("Not:", self.note_input)

        add_btn = QPushButton("Ekle")
        add_btn.setObjectName("primaryBtn")
        add_btn.clicked.connect(self._add_device)
        add_layout.addRow("", add_btn)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(add_group)
        form_scroll.setMaximumHeight(330)
        layout.addWidget(form_scroll)

        # --- Cihaz listesi ---
        layout.addWidget(QLabel("Cihaz Listesi:"))
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Ad", "Tur", "Kapi", "Yon", "IP:ONVIF", "Kullanici", "Akis", "Durum", "Son Baglanti"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate([55, 155, 90, 95, 75, 145, 120, 75, 80, 170]):
            self.table.setColumnWidth(column, width)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.table.setMinimumHeight(260)
        self.table.doubleClicked.connect(self._load_selected)
        layout.addWidget(self.table, 1)

        # --- Butonlar ---
        btn_row = QHBoxLayout()
        online_btn = QPushButton("Secileni Online Yap")
        online_btn.clicked.connect(lambda: self._toggle_online(True))
        btn_row.addWidget(online_btn)

        offline_btn = QPushButton("Secileni Offline Yap")
        offline_btn.clicked.connect(lambda: self._toggle_online(False))
        btn_row.addWidget(offline_btn)

        edit_btn = QPushButton("Secileni Forma Yukle / Duzenle")
        edit_btn.clicked.connect(self._load_selected)
        btn_row.addWidget(edit_btn)

        del_btn = QPushButton("Secileni Sil")
        del_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(del_btn)

        btn_row.addStretch()
        close_btn = QPushButton("Kapat")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._reload()

    def _load_selected(self, *_):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Hata", "Once listeden bir cihaz secin.")
            return
        did = int(self.table.item(row, 0).text())
        device = next((d for d in db.list_devices("CAMERA") if int(d["id"]) == did), None)
        if not device:
            return
        self._editing_device_id = did
        self.name_input.setText(device.get("name") or "")
        self.gate_input.setCurrentText(device.get("gate") or "GIRIS-1")
        self.direction_combo.setCurrentText(device.get("direction") or "")
        self.ip_input.setText(device.get("ip") or "")
        self.port_input.setText(str(device.get("onvif_port") or device.get("port") or 80))
        self.username_input.setText(device.get("onvif_username") or "")
        self.password_input.clear()
        self.profile_input.setText(device.get("onvif_profile_token") or "")
        self.rtsp_input.setText(device.get("rtsp_url") or "")
        self.note_input.setText(device.get("note") or "")
        self.name_input.setFocus()

    def _sync_direction(self, gate: str):
        self.direction_combo.setCurrentText("EXIT" if gate.startswith("CIKIS") else "ENTRY")

    def _reload(self):
        self.table.setRowCount(0)
        for d in db.list_devices("CAMERA"):
            row = self.table.rowCount()
            self.table.insertRow(row)
            ip_port = f"{d.get('ip', '')}:{d.get('port', '')}" if d.get("ip") else "-"
            status = "Online" if d.get("online") else "Offline"
            stream = "Hazir" if d.get("rtsp_url") else "-"
            values = [str(d["id"]), d["name"], d["kind"], d.get("gate") or "-",
                      d.get("direction") or "-", ip_port, d.get("onvif_username") or "-", stream,
                      status, d.get("last_seen") or "-"]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c == 8:  # durum renklendirme
                    item.setForeground(Qt.GlobalColor.green if d.get("online") else Qt.GlobalColor.red)
                self.table.setItem(row, c, item)

    def _add_device(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Hata", "Cihaz adi zorunludur.")
            return
        kind = "CAMERA"
        gate = self.gate_input.currentText()
        direction = self.direction_combo.currentText()
        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip()
        rtsp = self.rtsp_input.text().strip()
        note = self.note_input.text().strip()

        existing = db.get_device_by_gate(gate, kind) if gate else None
        encrypted_password = (protect_secret(self.password_input.text())
                              if self.password_input.text() else
                              (existing or {}).get("onvif_password_enc", ""))
        fields = dict(
            name=name, kind=kind, gate=gate, ip=ip, port=port,
            rtsp_url=rtsp, direction=direction, note=note,
            protocol="ONVIF",
            onvif_port=int(port or 80), onvif_username=self.username_input.text().strip(),
            onvif_password_enc=encrypted_password,
            onvif_profile_token=self.profile_input.text().strip())
        if existing:
            did = existing["id"]
            db.update_device(did, **fields)
            db.audit(self.username, "CIHAZ_GUNCELLENDI", target=f"device#{did}",
                     new_value=f"{kind}:{name} ({gate})")
            QMessageBox.information(self, "Guncellendi", f"{gate} kamerasi guncellendi.")
        else:
            did = db.add_device(**fields)
            db.audit(self.username, "CIHAZ_EKLENDI", target=f"device#{did}",
                     new_value=f"{kind}:{name} ({gate})")
            QMessageBox.information(self, "Eklendi", f"Cihaz eklendi: {name} (#{did})")

        # formu temizle
        self.name_input.clear()
        self._editing_device_id = None
        self.ip_input.clear()
        self.port_input.clear()
        self.rtsp_input.clear()
        self.username_input.clear()
        self.password_input.clear()
        self.profile_input.clear()
        self.note_input.clear()
        self._reload()

    def _test_onvif(self):
        ip = self.ip_input.text().strip()
        username = self.username_input.text().strip()
        password = self.password_input.text()
        if not password and self._editing_device_id:
            existing = next(
                (d for d in db.list_devices("CAMERA") if int(d["id"]) == self._editing_device_id),
                None,
            )
            if existing:
                try:
                    password = unprotect_secret(existing.get("onvif_password_enc") or "")
                except Exception:
                    password = ""
        if not ip or not username or not password:
            QMessageBox.warning(self, "Eksik Bilgi", "IP, ONVIF kullanici adi ve sifre zorunludur.")
            return
        if self._test_worker and self._test_worker.isRunning():
            return
        device = {
            "id": -1,
            "gate": self.gate_input.currentText(),
            "ip": ip,
            "onvif_port": int(self.port_input.text() or 80),
            "onvif_username": username,
        }
        self.test_btn.setEnabled(False)
        self.test_btn.setText("ONVIF baglantisi deneniyor...")
        self._test_worker = OnvifDiscoveryWorker(device, password, self)
        self._test_worker.stream_found.connect(self._on_test_stream_found)
        self._test_worker.discovery_error.connect(self._on_test_error)
        self._test_worker.finished.connect(self._finish_test)
        self._test_worker.start()

    def _on_test_stream_found(self, _device_id: int, uri: str, profile: str):
        self.profile_input.setText(profile)
        self.rtsp_input.setText(uri)
        QMessageBox.information(
            self, "ONVIF Baglantisi Basarili",
            f"Profil: {profile}\nRTSP akisi bulundu ve forma yazildi.",
        )

    def _on_test_error(self, _device_id: int, _gate: str, message: str):
        QMessageBox.critical(self, "ONVIF Baglanti Hatasi", message)

    def _finish_test(self):
        worker = self._test_worker
        self._test_worker = None
        self.test_btn.setEnabled(True)
        self.test_btn.setText("ONVIF Baglantisini Test Et ve Akisi Bul")
        if worker:
            worker.deleteLater()

    def _toggle_online(self, online: bool):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Hata", "Once bir cihaz secin.")
            return
        did = int(self.table.item(row, 0).text())
        name = self.table.item(row, 1).text()
        db.set_device_online(did, online)
        db.audit(self.username, "CIHAZ_DURUM_DEGISTI", target=f"device#{did}",
                 new_value="ONLINE" if online else "OFFLINE", note=name)
        self._reload()

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Hata", "Once bir cihaz secin.")
            return
        did = int(self.table.item(row, 0).text())
        name = self.table.item(row, 1).text()
        reply = QMessageBox.question(self, "Sil", f"'{name}' cihazini silmek istediginize emin misiniz?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        db.remove_device(did)
        db.audit(self.username, "CIHAZ_SILINDI", target=f"device#{did}", note=name)
        self._reload()

    def closeEvent(self, event):
        if self._test_worker and self._test_worker.isRunning():
            QMessageBox.information(
                self, "Baglanti Testi Suruyor",
                "ONVIF baglanti testi tamamlanmadan bu pencere kapatilamaz.",
            )
            event.ignore()
            return
        super().closeEvent(event)

    def done(self, result: int):
        if self._test_worker and self._test_worker.isRunning():
            QMessageBox.information(
                self, "Baglanti Testi Suruyor",
                "ONVIF baglanti testi tamamlanmadan bu pencere kapatilamaz.",
            )
            return
        super().done(result)
