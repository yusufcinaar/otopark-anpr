"""Uygulama icinde plaka/araç arama ve görsel kayıt inceleme ekrani."""
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app import db
from app.services.tariff_engine import format_duration
from app.services.plate_utils import format_plate
from app.ui.image_viewer import ImagePreviewLabel
from app.utils import money_from_db


class ReportsDialog(QDialog):
    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.username = username
        self._results = []
        self.setWindowTitle("Raporlar - Arac ve Plaka Arama")
        self.resize(1180, 760)

        root = QVBoxLayout(self)
        title = QLabel("ARAC VE PLAKA KAYITLARI")
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        root.addWidget(title)

        filters = QHBoxLayout()
        self.plate_input = QLineEdit()
        self.plate_input.setPlaceholderText("Plaka yazin (orn. 34 ABC 123)")
        self.plate_input.setClearButtonEnabled(True)
        self.plate_input.returnPressed.connect(self._search)
        filters.addWidget(self.plate_input, 2)
        self.status_combo = QComboBox()
        self.status_combo.addItem("Tum kayitlar", "")
        for label, value in (("Iceride", "ICERIDE"), ("Tamamlandi", "TAMAMLANDI"),
                             ("Odeme bekliyor", "ODEME_BEKLIYOR"),
                             ("Manuel inceleme", "MANUEL_INCELEME")):
            self.status_combo.addItem(label, value)
        filters.addWidget(self.status_combo)
        self.all_dates = QCheckBox("Tum tarihler")
        self.all_dates.setChecked(True)
        self.all_dates.toggled.connect(self._toggle_dates)
        filters.addWidget(self.all_dates)
        self.start_date = QDateEdit(QDate.currentDate().addMonths(-1))
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("dd.MM.yyyy")
        self.end_date = QDateEdit(QDate.currentDate())
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("dd.MM.yyyy")
        filters.addWidget(self.start_date)
        filters.addWidget(self.end_date)
        search_btn = QPushButton("Ara")
        search_btn.setObjectName("primaryBtn")
        search_btn.clicked.connect(self._search)
        filters.addWidget(search_btn)
        root.addLayout(filters)
        self._toggle_dates(True)

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Plaka", "Giris", "Cikis", "Giris Kapisi", "Cikis Kapisi", "Hiz", "Sure", "Durum"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.cellClicked.connect(self._show_detail)
        split.addWidget(self.table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.detail_text = QLabel("Detay ve fotograflari gormek icin bir kayit secin.")
        self.detail_text.setWordWrap(True)
        self.detail_text.setStyleSheet("font-weight: 600; padding: 4px;")
        detail_layout.addWidget(self.detail_text)
        images = QGridLayout()
        images.addWidget(QLabel("GIRIS GORSELI"), 0, 0)
        images.addWidget(QLabel("CIKIS GORSELI"), 0, 1)
        self.entry_image = self._image_label()
        self.exit_image = self._image_label()
        images.addWidget(self.entry_image, 1, 0)
        images.addWidget(self.exit_image, 1, 1)
        detail_layout.addLayout(images, 1)
        split.addWidget(detail)
        split.setSizes([330, 350])
        root.addWidget(split, 1)
        self.result_label = QLabel()
        self.result_label.setStyleSheet("color: #64748b;")
        root.addWidget(self.result_label)
        self._search()

    @staticmethod
    def _image_label():
        label = ImagePreviewLabel("Gorsel yok")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumHeight(220)
        label.setStyleSheet(
            "background: #e8eef6; border: 1px solid #cbd5e1; border-radius: 8px; color: #64748b;")
        return label

    def _toggle_dates(self, checked):
        self.start_date.setEnabled(not checked)
        self.end_date.setEnabled(not checked)

    def _search(self):
        plate = "".join(self.plate_input.text().upper().split())
        start = "" if self.all_dates.isChecked() else self.start_date.date().toString("yyyy-MM-dd")
        end = "" if self.all_dates.isChecked() else self.end_date.date().toString("yyyy-MM-dd")
        self._results = db.search_sessions(
            plate=plate, start_date=start, end_date=end,
            status=self.status_combo.currentData() or "", limit=1000)
        self.table.setRowCount(0)
        for session in self._results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            speed = f"{session.get('entry_speed_kmh') or '-'} / {session.get('exit_speed_kmh') or '-'} km/sa"
            values = [format_plate(session.get("plate")) or "-", session.get("entry_time") or "-",
                      session.get("exit_time") or "-", session.get("entry_lane") or "-",
                      session.get("exit_lane") or "-", speed,
                      format_duration(session.get("duration_minutes") or 0),
                      session.get("status") or "-"]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.result_label.setText(f"{len(self._results)} kayit bulundu")
        self.detail_text.setText("Detay ve fotograflari gormek icin bir kayit secin.")
        self._set_image(self.entry_image, "")
        self._set_image(self.exit_image, "")
        if plate:
            db.audit(self.username, "PLAKA_ARAMASI", target=plate,
                     note=f"{len(self._results)} sonuc")

    def _show_detail(self, row, _column):
        if row < 0 or row >= len(self._results):
            return
        session = self._results[row]
        fee_value = session.get("fee_dec") if session.get("fee_dec") is not None else session.get("fee") or 0
        fee = money_from_db(fee_value)
        self.detail_text.setText(
            f"{format_plate(session.get('plate')) or '-'}   |   Durum: {session.get('status', '-')}   |   "
            f"Giris: {session.get('entry_time', '-')} ({session.get('entry_lane') or '-'})   |   "
            f"Cikis: {session.get('exit_time') or '-'} ({session.get('exit_lane') or '-'})   |   "
            f"Sure: {format_duration(session.get('duration_minutes') or 0)}   |   Ucret: {fee} TL")
        plate = format_plate(session.get("plate")) or "-"
        self._set_image(
            self.entry_image, session.get("entry_image") or "",
            f"{plate} | Giriş görseli | {session.get('entry_time') or '-'}")
        self._set_image(
            self.exit_image, session.get("exit_image") or "",
            f"{plate} | Çıkış görseli | {session.get('exit_time') or '-'}")

    @staticmethod
    def _set_image(label: ImagePreviewLabel, path: str, title: str = "Görsel"):
        label.set_image_path(path, title=title)

    def done(self, result):
        self.entry_image.close_viewer()
        self.exit_image.close_viewer()
        super().done(result)
