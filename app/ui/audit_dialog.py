"""Audit (islem/guvenlik) loglari goruntuleyici. Loglar silinemez."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit
)

from app import db
from app.services.plate_utils import format_plate


class AuditLogDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Islem ve Guvenlik Loglari (Audit)")
        self.resize(900, 560)

        layout = QVBoxLayout(self)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filtrele (kullanici, islem, plaka...)")
        self.filter_input.textChanged.connect(self._reload)
        layout.addWidget(self.filter_input)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Zaman", "Kullanici", "Islem", "Hedef", "Eski Deger", "Yeni Deger", "Aciklama"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self._reload()

    def _reload(self):
        text = self.filter_input.text().strip().lower()
        self.table.setRowCount(0)
        for log in db.list_audit_logs(500):
            values = [log["ts"], log.get("username") or "-", log["action"],
                      format_plate(log.get("target")) or "-", format_plate(log.get("old_value")) or "-",
                      format_plate(log.get("new_value")) or "-", log.get("note") or "-"]
            # Hem gorunen bosluklu plaka hem de eski kayit bicimi aranabilsin.
            raw_text = " ".join(str(v or "") for v in log.values()).lower()
            display_text = " ".join(str(v) for v in values).lower()
            if text and text not in raw_text and text not in display_text:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for c, v in enumerate(values):
                self.table.setItem(row, c, QTableWidgetItem(str(v)))
