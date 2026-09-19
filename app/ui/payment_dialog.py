"""Kasiyer manuel odeme ekrani: yontem secimi, nakit/para ustu hesabi."""
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QLineEdit, QLabel,
    QPushButton, QMessageBox, QFrame
)

from app.utils import money, money_from_db
from app.services.tariff_engine import format_duration
from app.services.plate_utils import format_plate
from app.ui.image_viewer import ImagePreviewLabel

PAYMENT_METHODS = [
    ("NAKIT", "Nakit"),
    ("KREDI_KARTI", "Kredi Karti (yalnizca raporlama)"),
    ("BANKA_KARTI", "Banka Karti (yalnizca raporlama)"),
    ("HAVALE_EFT", "Havale / EFT"),
    ("ABONE_HESABI", "Abone Hesabi"),
]


class PaymentDialog(QDialog):
    def __init__(self, session: dict, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Cikis / Odeme Ekrani")
        self.resize(1080, 680)
        self.result_data = None
        self._seconds_left = 30
        self._closing = False
        self._image_labels = []
        self._open_image_labels = set()

        amount = money_from_db(session.get("fee_dec")) + money_from_db(session.get("extra_fee_dec"))
        self.amount = amount

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        plate_lbl = QLabel(format_plate(session["plate"]))
        plate_lbl.setStyleSheet("font-size: 32px; font-weight: 800; font-family: Consolas; color: #dc2626;")
        title_row.addWidget(plate_lbl)
        title_row.addStretch()
        self.timeout_lbl = QLabel()
        self.timeout_lbl.setStyleSheet("font-size: 13px; color: #64748b;")
        title_row.addWidget(self.timeout_lbl)
        layout.addLayout(title_row)

        amount_lbl = QLabel(f"Odenecek Tutar: {amount} TL")
        amount_lbl.setStyleSheet("font-size: 28px; font-weight: 800; color: #dc2626;")
        layout.addWidget(amount_lbl)

        detail_lbl = QLabel(
            f"Giris: {session.get('entry_time') or '-'}     "
            f"Park suresi: {format_duration(session.get('duration_minutes') or 0)}"
        )
        detail_lbl.setStyleSheet("font-size: 15px; font-weight: 600; color: #334155;")
        layout.addWidget(detail_lbl)

        images = QHBoxLayout()
        images.setSpacing(12)
        plate = format_plate(session.get("plate")) or "-"
        images.addWidget(self._image_card(
            "CIKIS GORSELI", session.get("exit_image"),
            f"{plate} | Çıkış görseli | {session.get('exit_time') or '-'}"))
        images.addWidget(self._image_card(
            "GIRIS GORSELI", session.get("entry_image"),
            f"{plate} | Giriş görseli | {session.get('entry_time') or '-'}"))
        layout.addLayout(images, 1)

        form = QFormLayout()
        self.method_combo = QComboBox()
        for code, label in PAYMENT_METHODS:
            self.method_combo.addItem(label, code)
        form.addRow("Odeme Yontemi:", self.method_combo)

        self.cash_input = QLineEdit()
        self.cash_input.setPlaceholderText("Alinan nakit (TL)")
        self.cash_input.setText(str(amount))
        form.addRow("Alinan Nakit:", self.cash_input)

        self.change_lbl = QLabel("-")
        form.addRow("Para Ustu:", self.change_lbl)

        self.note_input = QLineEdit()
        form.addRow("Aciklama:", self.note_input)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        cancel_btn = QPushButton("Islem Iptal")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        buttons.addStretch()
        self.confirm_btn = QPushButton("TAMAM - Odemeyi Kaydet")
        self.confirm_btn.setObjectName("successBtn")
        self.confirm_btn.setMinimumHeight(44)
        buttons.addWidget(self.confirm_btn)
        layout.addLayout(buttons)

        self.method_combo.currentIndexChanged.connect(self._method_changed)
        self.cash_input.textChanged.connect(self._update_change)
        self.confirm_btn.clicked.connect(self._confirm)
        self._method_changed()
        self._tick_timeout()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_timeout)
        self._timer.start(1000)

    def _image_card(self, title: str, path: str | None, viewer_title: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet("QFrame { background: #0b1220; border-radius: 7px; }")
        box = QVBoxLayout(card)
        heading = QLabel(title)
        heading.setStyleSheet("color: white; font-size: 13px; font-weight: 700;")
        box.addWidget(heading)
        image = ImagePreviewLabel("Gorsel bulunamadi", title=viewer_title)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setMinimumSize(420, 260)
        image.setStyleSheet("color: #94a3b8;")
        image.set_image_path(path or "", title=viewer_title)
        image.image_opened.connect(lambda: self._image_opened(image))
        image.image_closed.connect(lambda: self._image_closed(image))
        self._image_labels.append(image)
        box.addWidget(image, 1)
        return card

    def _image_opened(self, image):
        if self._closing:
            return
        self._open_image_labels.add(image)
        if hasattr(self, "_timer"):
            self._timer.stop()
        self.timeout_lbl.setText("Görsel açık; otomatik kapanma duraklatıldı")

    def _image_closed(self, image):
        self._open_image_labels.discard(image)
        if self._closing or self._open_image_labels:
            return
        self.timeout_lbl.setText(f"{self._seconds_left} saniye sonra otomatik kapanir")
        if hasattr(self, "_timer"):
            self._timer.start(1000)

    def _tick_timeout(self):
        # A queued timeout must not dismiss the payment screen during inspection.
        if self._closing or self._open_image_labels:
            return
        self.timeout_lbl.setText(f"{self._seconds_left} saniye sonra otomatik kapanir")
        if self._seconds_left <= 0:
            if hasattr(self, "_timer"):
                self._timer.stop()
            self.reject()
            return
        self._seconds_left -= 1

    def done(self, result):
        self._closing = True
        if hasattr(self, "_timer"):
            self._timer.stop()
        for image in self._image_labels:
            image.close_viewer()
        self._open_image_labels.clear()
        super().done(result)

    def _method_changed(self):
        is_cash = self.method_combo.currentData() == "NAKIT"
        self.cash_input.setEnabled(is_cash)
        self.change_lbl.setText("-" if not is_cash else self.change_lbl.text())

    def _update_change(self):
        try:
            received = money(self.cash_input.text().replace(",", "."))
            change = received - self.amount
            if change < 0:
                self.change_lbl.setText(f"EKSIK: {abs(change)} TL")
                self.change_lbl.setStyleSheet("color: #ef4444; font-weight: 600;")
            else:
                self.change_lbl.setText(f"{change} TL")
                self.change_lbl.setStyleSheet("color: #22c55e; font-weight: 600;")
        except (InvalidOperation, ValueError):
            self.change_lbl.setText("-")

    def _confirm(self):
        if hasattr(self, "_timer"):
            self._timer.stop()
        method = self.method_combo.currentData()
        cash_received = None
        if method == "NAKIT":
            try:
                cash_received = money(self.cash_input.text().replace(",", "."))
            except (InvalidOperation, ValueError):
                QMessageBox.warning(self, "Hata", "Gecerli bir nakit tutar giriniz.")
                return
            if cash_received < self.amount:
                QMessageBox.warning(self, "Eksik Odeme", "Eksik odeme kabul edilmez.")
                return
        self.result_data = {
            "method": method,
            "cash_received": cash_received,
            "note": self.note_input.text().strip(),
        }
        self.accept()
