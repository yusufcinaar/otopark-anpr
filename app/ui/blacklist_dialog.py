from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLineEdit, QMessageBox
)

from app import db
from app.services.plate_utils import format_plate, normalize_plate


class BlacklistDialog(QDialog):
    """Kara Liste Tanimlari: girisi/cikisi izlenmesi gereken/alarm verilecek plakalar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kara Liste Tanimlari")
        self.resize(520, 400)

        layout = QVBoxLayout(self)

        form = QHBoxLayout()
        self.plate_input = QLineEdit()
        self.plate_input.setPlaceholderText("Plaka (34 GGG 01)")
        form.addWidget(self.plate_input)
        self.reason_input = QLineEdit()
        self.reason_input.setPlaceholderText("Sebep (ornek: odenmemis borc, guvenlik uyarisi)")
        form.addWidget(self.reason_input, 1)
        self.add_btn = QPushButton("Ekle / Guncelle")
        self.add_btn.setObjectName("primaryBtn")
        self.add_btn.clicked.connect(self.add_entry)
        form.addWidget(self.add_btn)
        layout.addLayout(form)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "Plaka", "Sebep"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.remove_btn = QPushButton("Secili Kaydi Sil")
        self.remove_btn.clicked.connect(self.remove_selected)
        btn_row.addWidget(self.remove_btn)
        layout.addLayout(btn_row)

        self.reload()

    def add_entry(self):
        plate = normalize_plate(self.plate_input.text())
        if not plate:
            QMessageBox.warning(self, "Hata", "Gecerli bir plaka giriniz.")
            return
        reason = self.reason_input.text().strip()
        if not reason:
            QMessageBox.warning(self, "Hata", "Kara liste sebebi zorunludur.")
            return
        db.add_blacklist(plate, reason)
        username = getattr(self.parent(), "current_user", {}).get("username", "sistem")
        db.audit(username, "KARA_LISTE_EKLENDI_GUNCELLENDI", target=plate, note=reason)
        self.plate_input.clear()
        self.reason_input.clear()
        self.reload()

    def remove_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        entry_id = int(self.table.item(row, 0).text())
        plate = self.table.item(row, 1).text()
        if QMessageBox.question(
                self, "Kara Listeden Sil", f"{plate} plakasini kara listeden silmek istiyor musunuz?") \
                != QMessageBox.StandardButton.Yes:
            return
        db.remove_blacklist(entry_id)
        username = getattr(self.parent(), "current_user", {}).get("username", "sistem")
        db.audit(username, "KARA_LISTE_SILINDI", target=normalize_plate(plate))
        self.reload()

    def reload(self):
        entries = db.list_blacklist()
        self.table.setRowCount(0)
        for e in entries:
            r = self.table.rowCount()
            self.table.insertRow(r)
            values = [str(e["id"]), format_plate(e["plate"]), e.get("reason") or "-"]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(v))
