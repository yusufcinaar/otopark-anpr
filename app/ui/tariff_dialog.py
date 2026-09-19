"""Tarife yonetimi: blok tarife duzenleme, surumler, ornek ucret hesaplama.

Kaydedilen her degisiklik YENI SURUM olarak saklanir; gecmis park kayitlari
giriste alinan snapshot ile hesaplandigi icin etkilenmez.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDoubleSpinBox
)

from app import db
from app.services.tariff_engine import calculate_fee_from_rules
from app.utils import money


class TariffDialog(QDialog):
    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.username = username
        self.setWindowTitle("Tarife Yonetimi")
        self.resize(640, 620)

        layout = QVBoxLayout(self)

        tariff = db.get_active_tariff()
        rules = tariff["rules"] if tariff else {"free_minutes": 15, "blocks": [], "extra_day_price": "500"}
        self.tariff_name = tariff["name"] if tariff else "Standart Tarife"
        version = tariff["version"] if tariff else 0

        layout.addWidget(QLabel(f"Aktif tarife: {self.tariff_name} (surum {version})"))

        form = QFormLayout()
        self.free_input = QDoubleSpinBox()
        self.free_input.setRange(0, 24)
        self.free_input.setDecimals(2)
        self.free_input.setSingleStep(0.25)
        self.free_input.setSuffix(" saat")
        self.free_input.setValue(int(rules.get("free_minutes", 0)) / 60)
        form.addRow("Ucretsiz sure:", self.free_input)
        self.extra_day_input = QLineEdit(str(rules.get("extra_day_price", "500")))
        form.addRow("Her ek 24 saat ucreti (TL):", self.extra_day_input)
        layout.addLayout(form)

        layout.addWidget(QLabel("Sure bloklari (saat siniri -> toplam ucret):"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Sure siniri (saat)", "Ucret (TL)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        for b in rules.get("blocks", []):
            hours = int(b["upto_minutes"]) / 60
            self._add_block_row(f"{hours:g}", str(b["price"]))

        block_btns = QHBoxLayout()
        add_btn = QPushButton("Blok Ekle")
        add_btn.clicked.connect(lambda: self._add_block_row("", ""))
        block_btns.addWidget(add_btn)
        del_btn = QPushButton("Secili Blogu Sil")
        del_btn.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        block_btns.addWidget(del_btn)
        block_btns.addStretch()
        layout.addLayout(block_btns)

        calc_row = QHBoxLayout()
        calc_row.addWidget(QLabel("Ornek hesap - sure (saat):"))
        self.calc_input = QLineEdit("1,5")
        self.calc_input.setFixedWidth(80)
        calc_row.addWidget(self.calc_input)
        calc_btn = QPushButton("Hesapla")
        calc_btn.clicked.connect(self._example_calc)
        calc_row.addWidget(calc_btn)
        self.calc_result = QLabel("-")
        calc_row.addWidget(self.calc_result)
        calc_row.addStretch()
        layout.addLayout(calc_row)

        save_btn = QPushButton("Yeni Surum Olarak Kaydet")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)

    def _add_block_row(self, hours: str, price: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(hours))
        self.table.setItem(row, 1, QTableWidgetItem(price))

    def _collect_rules(self) -> dict | None:
        blocks = []
        for r in range(self.table.rowCount()):
            m_item, p_item = self.table.item(r, 0), self.table.item(r, 1)
            if not m_item or not p_item or not m_item.text().strip():
                continue
            try:
                hours = float(m_item.text().strip().replace(",", "."))
                minutes = round(hours * 60)
                if minutes <= 0:
                    raise ValueError
                price = str(money(p_item.text().strip().replace(",", ".")))
            except (ValueError, ArithmeticError):
                QMessageBox.warning(self, "Hata", f"Satir {r+1}: gecersiz deger.")
                return None
            blocks.append({"upto_minutes": minutes, "price": price})
        if not blocks:
            QMessageBox.warning(self, "Hata", "En az bir blok tanimlayin.")
            return None
        blocks.sort(key=lambda b: b["upto_minutes"])
        try:
            extra = str(money(self.extra_day_input.text().replace(",", ".")))
        except ArithmeticError:
            QMessageBox.warning(self, "Hata", "Ek gun ucreti gecersiz.")
            return None
        return {
            "type": "blocks",
            "free_minutes": round(self.free_input.value() * 60),
            "blocks": blocks,
            "extra_day_price": extra,
            "currency": "TL",
        }

    def _example_calc(self):
        rules = self._collect_rules()
        if not rules:
            return
        try:
            hours = float(self.calc_input.text().replace(",", "."))
            minutes = round(hours * 60)
        except ValueError:
            return
        fee = calculate_fee_from_rules(rules, minutes)
        self.calc_result.setText(f"= {fee} TL")

    def _save(self):
        rules = self._collect_rules()
        if not rules:
            return
        old = db.get_active_tariff()
        db.save_new_tariff_version(self.tariff_name, rules)
        db.audit(self.username, "TARIFE_DEGISTI", target=self.tariff_name,
                 old_value=str(old["version"]) if old else "-",
                 new_value=f"yeni surum (blok sayisi: {len(rules['blocks'])})")
        QMessageBox.information(self, "Kaydedildi",
                                "Tarife yeni surum olarak kaydedildi. Gecmis kayitlar etkilenmez.")
        self.accept()
