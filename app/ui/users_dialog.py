from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLineEdit, QComboBox, QMessageBox, QInputDialog, QLabel
)

from app import db
from app.services import auth


class UsersDialog(QDialog):
    """Kullanici Tanimlari: uygulamaya giris yapabilecek kasiyer/yonetici hesaplari."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_user = getattr(parent, "current_user", None)
        self.setWindowTitle("Kullanici Tanimlari")
        self.resize(520, 400)

        layout = QVBoxLayout(self)

        form = QHBoxLayout()
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Kullanici adi")
        form.addWidget(self.username_input)
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Sifre")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addWidget(self.password_input)
        self.role_combo = QComboBox()
        self.role_combo.addItems([
            role for role in auth.ROLES if auth.can_manage_role(self.current_user, role)])
        form.addWidget(self.role_combo)
        self.add_btn = QPushButton("Kullanici Ekle")
        self.add_btn.setObjectName("primaryBtn")
        self.add_btn.clicked.connect(self.add_user)
        form.addWidget(self.add_btn)
        layout.addLayout(form)
        self.add_btn.setEnabled(self.role_combo.count() > 0)
        if not auth.has_perm(self.current_user, "manage_devices"):
            note = QLabel("Yonetici hesaplarini yalnizca yoneticiler degistirebilir.")
            note.setWordWrap(True)
            layout.addWidget(note)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "Kullanici Adi", "Rol", "Olusturulma"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.password_btn = QPushButton("Secili Kullanicinin Sifresini Degistir")
        self.password_btn.clicked.connect(self.change_password)
        btn_row.addWidget(self.password_btn)
        btn_row.addStretch()
        self.remove_btn = QPushButton("Secili Kullaniciyi Sil")
        self.remove_btn.clicked.connect(self.remove_selected)
        btn_row.addWidget(self.remove_btn)
        layout.addLayout(btn_row)

        self.reload()

    def add_user(self):
        if not self._allowed(self.role_combo.currentText()):
            return
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()
        if not username or not password:
            QMessageBox.warning(self, "Hata", "Kullanici adi ve sifre giriniz.")
            return
        if len(password) < 8:
            QMessageBox.warning(self, "Hata", "Sifre en az 8 karakter olmalidir.")
            return
        try:
            db.create_user(username, password, self.role_combo.currentText())
        except Exception:
            QMessageBox.warning(self, "Hata", "Bu kullanici adi zaten mevcut.")
            return
        actor = self.current_user["username"]
        db.audit(actor, "KULLANICI_EKLENDI", target=username,
                 new_value=self.role_combo.currentText())
        self.username_input.clear()
        self.password_input.clear()
        self.reload()

    def remove_selected(self):
        user = self._selected_user()
        if not user or not self._allowed(user["role"]):
            return
        if self.table.rowCount() <= 1:
            QMessageBox.warning(self, "Hata", "En az bir kullanici tanimli olmalidir.")
            return
        user_id = user["id"]
        username = user["username"]
        current = self.current_user["username"]
        if username == current:
            QMessageBox.warning(self, "Hata", "Giris yaptiginiz kullaniciyi silemezsiniz.")
            return
        if QMessageBox.question(
                self, "Kullaniciyi Sil", f"'{username}' kullanicisini silmek istiyor musunuz?") \
                != QMessageBox.StandardButton.Yes:
            return
        db.remove_user(user_id)
        db.audit(current or "sistem", "KULLANICI_SILINDI", target=username)
        self.reload()

    def change_password(self):
        user = self._selected_user()
        if not user:
            QMessageBox.warning(self, "Hata", "Once bir kullanici secin.")
            return
        if not self._allowed(user["role"]):
            return
        user_id = user["id"]
        username = user["username"]
        password, ok = QInputDialog.getText(
            self, "Sifre Degistir", f"{username} icin yeni sifre:",
            QLineEdit.EchoMode.Password)
        if not ok:
            return
        if len(password) < 8:
            QMessageBox.warning(self, "Hata", "Sifre en az 8 karakter olmalidir.")
            return
        db.update_user_password(user_id, password)
        actor = self.current_user["username"]
        db.audit(actor, "KULLANICI_SIFRESI_DEGISTI", target=username)
        QMessageBox.information(self, "Kaydedildi", "Sifre degistirildi.")

    def _allowed(self, role):
        try:
            auth.require_manage_role(self.current_user, role)
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki Yok", str(exc))
            return False
        return True

    def _selected_user(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is None:
            return None
        user_id = int(item.text())
        return next((user for user in db.list_users() if user["id"] == user_id), None)

    def _update_actions(self):
        user = self._selected_user()
        allowed = bool(user and auth.can_manage_role(self.current_user, user["role"]))
        self.password_btn.setEnabled(allowed)
        self.remove_btn.setEnabled(
            allowed and user["username"] != self.current_user["username"]
            and self.table.rowCount() > 1)

    def reload(self):
        users = db.list_users()
        self.table.setRowCount(0)
        for u in users:
            r = self.table.rowCount()
            self.table.insertRow(r)
            values = [str(u["id"]), u["username"], u["role"], u["created_at"]]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self._update_actions()
