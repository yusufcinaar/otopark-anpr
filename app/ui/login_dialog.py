from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QPushButton, QLabel, QMessageBox
)

from app import db


class LoginDialog(QDialog):
    """Uygulama acilisinda kullanici girisi. Varsayilan: admin / admin."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Giris Yap - Otopark Yonetim Sistemi")
        self.setFixedWidth(360)
        self.current_user = None

        layout = QVBoxLayout(self)
        title = QLabel("PLAKA TANIMA SISTEMI")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        form = QFormLayout()
        self.username_input = QLineEdit()
        self.username_input.setText("admin")
        form.addRow("Kullanici Adi:", self.username_input)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Sifre:", self.password_input)
        layout.addLayout(form)

        hint = QLabel("Ilk kurulum hesabi admin'dir. Ilk giristen sonra Tanimlar > Kullanici Tanimlari ekranindan sifreyi degistirin.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #8b949e;")
        layout.addWidget(hint)

        self.login_btn = QPushButton("Giris Yap")
        self.login_btn.setObjectName("primaryBtn")
        self.login_btn.clicked.connect(self.try_login)
        layout.addWidget(self.login_btn)

        self.password_input.returnPressed.connect(self.try_login)

    def try_login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        user = db.verify_user(username, password)
        if not user:
            QMessageBox.warning(self, "Giris Basarisiz", "Kullanici adi veya sifre hatali.")
            return
        self.current_user = user
        if username == "admin" and password == "admin":
            QMessageBox.warning(
                self, "Varsayilan Sifre",
                "Guvenlik icin admin sifresini Kullanici Tanimlari ekranindan hemen degistirin.",
            )
        self.accept()
