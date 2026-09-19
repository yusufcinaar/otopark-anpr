"""Simulasyon ekrani: gercek donanim olmadan tum sistemi test etmek icin.

Buradan gonderilen olaylar GERCEK backend servislerini
(ParkingService.ingest_camera_event) kullanir.
"""
from datetime import timedelta

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QLineEdit, QLabel,
    QPushButton, QSlider, QCheckBox, QMessageBox, QSpinBox
)
from PySide6.QtCore import Qt

from app.utils import now, new_uuid
from app.sim_utils import random_plate
from app.services.plate_utils import format_plate


class SimulationDialog(QDialog):
    """Sonuc: self.event olarak hazirlanan kamera olayi; MainWindow isler."""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Simulasyon Paneli")
        self.setFixedWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.gate_combo = QComboBox()
        self.gate_combo.addItems(["GIRIS-1", "GIRIS-2", "CIKIS-1"])
        form.addRow("Kapi:", self.gate_combo)

        self.plate_input = QLineEdit(format_plate(random_plate()))
        form.addRow("Plaka:", self.plate_input)

        conf_row = QHBoxLayout()
        self.conf_slider = QSlider(Qt.Orientation.Horizontal)
        self.conf_slider.setRange(30, 100)
        self.conf_slider.setValue(96)
        self.conf_lbl = QLabel("96%")
        self.conf_slider.valueChanged.connect(lambda v: self.conf_lbl.setText(f"{v}%"))
        conf_row.addWidget(self.conf_slider)
        conf_row.addWidget(self.conf_lbl)
        form.addRow("Kamera guven orani:", conf_row)

        self.minutes_ago = QSpinBox()
        self.minutes_ago.setRange(0, 100000)
        self.minutes_ago.setValue(0)
        form.addRow("Olay kac dk once oldu:", self.minutes_ago)

        layout.addLayout(form)

        send_row = QHBoxLayout()
        entry_btn = QPushButton("Giris Olayi Gonder")
        entry_btn.setObjectName("primaryBtn")
        entry_btn.clicked.connect(lambda: self._send("ENTRY"))
        send_row.addWidget(entry_btn)
        exit_btn = QPushButton("Cikis Olayi Gonder")
        exit_btn.setObjectName("primaryBtn")
        exit_btn.clicked.connect(lambda: self._send("EXIT"))
        send_row.addWidget(exit_btn)
        layout.addLayout(send_row)

        layout.addWidget(QLabel("Donanim Simulasyonu:"))
        hw_row = QHBoxLayout()
        open_btn = QPushButton("Bariyeri Ac")
        open_btn.clicked.connect(self._open_barrier)
        hw_row.addWidget(open_btn)
        close_btn = QPushButton("Bariyeri Kapat")
        close_btn.clicked.connect(self._close_barrier)
        hw_row.addWidget(close_btn)
        layout.addLayout(hw_row)

        toggle_row = QHBoxLayout()
        self.sensor_check = QCheckBox("Gecis sensoru arac algiliyor")
        self.sensor_check.toggled.connect(self._toggle_sensor)
        toggle_row.addWidget(self.sensor_check)
        self.online_check = QCheckBox("Bariyer/kamera cevrimici")
        self.online_check.setChecked(True)
        self.online_check.toggled.connect(self._toggle_online)
        toggle_row.addWidget(self.online_check)
        layout.addLayout(toggle_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        self.event_callback = None  # MainWindow atar

    def _gate(self):
        return self.gate_combo.currentText()

    def _send(self, direction: str):
        gate = self._gate()
        if direction == "ENTRY" and gate.startswith("CIKIS"):
            QMessageBox.warning(self, "Hata", "Giris olayi icin giris kapisi secin.")
            return
        if direction == "EXIT" and gate.startswith("GIRIS"):
            QMessageBox.warning(self, "Hata", "Cikis olayi icin cikis kapisi secin.")
            return
        event_time = (now() - timedelta(minutes=self.minutes_ago.value())).isoformat()
        event = {
            "event_id": new_uuid(),
            "camera_id": f"LPR-{gate}",
            "gate_id": gate,
            "direction": direction,
            "raw_plate": self.plate_input.text().strip(),
            "confidence": float(self.conf_slider.value()),
            "event_time": event_time,
            "vehicle_type": "CAR",
            "vehicle_color": "-",
        }
        if self.event_callback:
            self.event_callback(event)
            self.status_lbl.setText(f"Olay gonderildi: {direction} {format_plate(event['raw_plate'])} @ {gate}")

    def _open_barrier(self):
        ok = self.service.barriers.open(self._gate(), username="simulasyon", reason="Simulasyon testi")
        self.status_lbl.setText(f"Bariyer ac: {'OK' if ok else 'HATA'} | Durum: {self.service.barriers.status(self._gate())}")

    def _close_barrier(self):
        ok = self.service.barriers.close(self._gate(), username="simulasyon")
        self.status_lbl.setText(f"Bariyer kapat: {'OK' if ok else 'EMNIYET/HATA'} | Durum: {self.service.barriers.status(self._gate())}")

    def _toggle_sensor(self, active: bool):
        self.service.barriers.set_sensor(self._gate(), active)
        self.status_lbl.setText(f"Sensor: {'ARAC VAR' if active else 'BOS'}")

    def _toggle_online(self, online: bool):
        self.service.barriers.set_online(self._gate(), online)
        self.status_lbl.setText(f"Cihaz: {'CEVRIMICI' if online else 'CEVRIMDISI'}")
