"""SMTP ve gunluk rapor ayarlari: alici listesi, gonderim saati, test e-postasi.

SMTP sunucu ve hesap bilgileri bu ekrandan yonetilir. Parola Windows DPAPI ile
sifrelenerek kalici veritabaninda saklanir.
"""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QLabel,
    QPushButton, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QCheckBox
)

from app import db
from app.reports import mailer
from app.security import protect_secret
from app.services import auth


class _SmtpTestWorker(QThread):
    """SMTP ağ işlemini arayüz iş parçacığından ayırır."""

    completed = Signal(str, object)

    def __init__(self, recipient: str, parent=None):
        super().__init__(parent)
        self.recipient = recipient

    def run(self):
        try:
            mailer.send_test_email(self.recipient)
            self.completed.emit(self.recipient, None)
        except Exception as exc:
            self.completed.emit(self.recipient, exc)


class SmtpDialog(QDialog):
    ACCESS_PERMISSIONS = (
        "manage_smtp", "manage_schedule", "manage_email_recipients", "send_test_email",
    )

    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.username = username
        self.current_user = getattr(parent, "current_user", None)
        self._test_worker = None
        self.setWindowTitle("SMTP ve Gunluk Rapor Ayarlari")
        self.resize(660, 700)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Google Workspace için önerilen yöntem SMTP Relay'dir. Relay modunda uygulama "
            "şifresi girilmez; tesisin sabit internet çıkış IP'si Google Admin Console'da "
            "bir kez yetkilendirilir. Gmail uygulama şifresi yöntemi de ayrıca kullanılabilir.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(info)

        form = QFormLayout()
        current_host = db.get_setting("smtp_host", "")
        saved_provider = db.get_setting("smtp_provider", "custom") or "custom"
        if saved_provider == "custom":
            if current_host.lower() in {"smtp.gmail.com", "smtp.googlemail.com"}:
                saved_provider = "gmail"
            elif current_host.lower() == "smtp-relay.gmail.com":
                saved_provider = "google_relay"
            elif current_host.lower() == "smtp.office365.com":
                saved_provider = "microsoft"
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Google Workspace SMTP Relay (önerilen)", "google_relay")
        self.provider_combo.addItem("Gmail / Workspace uygulama şifresi", "gmail")
        self.provider_combo.addItem("Microsoft 365", "microsoft")
        self.provider_combo.addItem("Özel SMTP sunucusu", "custom")
        provider_index = self.provider_combo.findData(saved_provider)
        self.provider_combo.setCurrentIndex(max(0, provider_index))
        form.addRow("Sağlayıcı:", self.provider_combo)

        self.auth_combo = QComboBox()
        self.auth_combo.addItem("IP yetkili relay (kullanıcı/şifre yok)", "relay")
        self.auth_combo.addItem("Uygulama şifresi ile giriş", "password")
        saved_auth = db.get_setting(
            "smtp_auth_mode",
            "relay" if saved_provider == "google_relay" else "password")
        auth_index = self.auth_combo.findData(saved_auth)
        self.auth_combo.setCurrentIndex(max(0, auth_index))
        form.addRow("Kimlik doğrulama:", self.auth_combo)

        self.host_input = QLineEdit(db.get_setting("smtp_host", ""))
        self.host_input.setPlaceholderText("smtp.gmail.com veya smtp.office365.com")
        form.addRow("SMTP sunucusu:", self.host_input)
        self.port_input = QLineEdit(db.get_setting("smtp_port", "587"))
        form.addRow("SMTP portu:", self.port_input)
        self.username_input = QLineEdit(db.get_setting("smtp_username", ""))
        self.username_input.setPlaceholderText("mail@firma.com")
        form.addRow("Google ana hesabı / kullanıcı:", self.username_input)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Kayıtlı şifreyi korumak için boş bırakın")
        form.addRow("SMTP uygulama şifresi:", self.password_input)
        self.from_email_input = QLineEdit(db.get_setting("smtp_from_email", ""))
        self.from_email_input.setPlaceholderText("mail@firma.com")
        form.addRow("Gönderen e-posta:", self.from_email_input)
        self.from_name_input = QLineEdit(db.get_setting("smtp_from_name", "Otopark Yönetim Sistemi"))
        form.addRow("Gönderen adı:", self.from_name_input)
        self.tls_check = QCheckBox("STARTTLS kullan (genellikle port 587)")
        self.tls_check.setChecked(db.get_setting("smtp_use_tls", "1") == "1")
        form.addRow("", self.tls_check)
        self.ssl_check = QCheckBox("Doğrudan SSL kullan (genellikle port 465)")
        self.ssl_check.setChecked(db.get_setting("smtp_use_ssl", "0") == "1")
        form.addRow("", self.ssl_check)
        self.mode_help = QLabel()
        self.mode_help.setWordWrap(True)
        self.mode_help.setStyleSheet("color:#52657a; font-size:10px")
        form.addRow("", self.mode_help)
        self.time_input = QLineEdit(db.get_setting("report_time", "09:00"))
        form.addRow("Gunluk gonderim saati (HH:MM):", self.time_input)
        period = QLabel("Rapor dönemi: önceki gün 09:00 — bugün 08:59:59")
        period.setStyleSheet("color:#52657a")
        form.addRow("", period)
        self.active_check = QCheckBox("Gunluk rapor gonderimi aktif")
        self.active_check.setChecked(db.get_setting("report_active", "1") == "1")
        form.addRow("", self.active_check)
        layout.addLayout(form)

        layout.addWidget(QLabel("Alici Listesi:"))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "E-posta", "Tur"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        add_row = QHBoxLayout()
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("ornek@firma.com")
        add_row.addWidget(self.email_input, 1)
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(["TO", "CC"])
        add_row.addWidget(self.kind_combo)
        add_btn = QPushButton("Ekle")
        add_btn.clicked.connect(self._add_recipient)
        add_row.addWidget(add_btn)
        remove_btn = QPushButton("Secileni Sil")
        remove_btn.clicked.connect(self._remove_selected)
        add_row.addWidget(remove_btn)
        layout.addLayout(add_row)

        btn_row = QHBoxLayout()
        self.test_btn = QPushButton("Test E-postasi Gonder")
        self.test_btn.clicked.connect(self._send_test)
        btn_row.addWidget(self.test_btn)
        btn_row.addStretch()
        self.save_btn = QPushButton("Kaydet")
        self.save_btn.setObjectName("primaryBtn")
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

        self.provider_combo.currentIndexChanged.connect(self._apply_provider_preset)
        self.auth_combo.currentIndexChanged.connect(self._update_auth_mode)
        self.tls_check.toggled.connect(
            lambda checked: self.ssl_check.setChecked(False) if checked else None)
        self.ssl_check.toggled.connect(
            lambda checked: self.tls_check.setChecked(False) if checked else None)
        self._update_auth_mode()
        self._network_widgets = (
            self.provider_combo, self.auth_combo, self.host_input, self.port_input,
            self.username_input, self.password_input, self.from_email_input,
            self.from_name_input, self.tls_check, self.ssl_check,
        )
        if not self._can("manage_smtp"):
            for widget in self._network_widgets:
                widget.setEnabled(False)
            info.setText(
                "SMTP bağlantı ve hesap ayarlarını yalnızca yetkili yönetici değiştirebilir. "
                "Rapor saati, alıcı listesi ve test e-postası kayıtlı bağlantı ayarlarını kullanır.")
        self.time_input.setEnabled(self._can("manage_schedule"))
        self.active_check.setEnabled(self._can("manage_schedule"))
        for widget in (self.email_input, self.kind_combo, add_btn, remove_btn):
            widget.setEnabled(self._can("manage_email_recipients"))
        self.test_btn.setEnabled(self._can("send_test_email"))
        self.save_btn.setEnabled(self._can("manage_smtp") or self._can("manage_schedule"))
        self._reload()

    def _can(self, permission):
        return auth.has_perm(self.current_user, permission)

    def _require(self, permission):
        try:
            auth.require(self.current_user, permission)
        except auth.PermissionDenied as exc:
            QMessageBox.warning(self, "Yetki", str(exc))
            return False
        return True

    def _apply_provider_preset(self):
        if not self._can("manage_smtp"):
            return
        provider = self.provider_combo.currentData()
        if provider == "google_relay":
            self.host_input.setText("smtp-relay.gmail.com")
            self.port_input.setText("587")
            self.auth_combo.setCurrentIndex(self.auth_combo.findData("relay"))
            self.tls_check.setChecked(True)
            self.ssl_check.setChecked(False)
        elif provider == "gmail":
            self.host_input.setText("smtp.gmail.com")
            self.port_input.setText("587")
            self.auth_combo.setCurrentIndex(self.auth_combo.findData("password"))
            self.tls_check.setChecked(True)
            self.ssl_check.setChecked(False)
        elif provider == "microsoft":
            self.host_input.setText("smtp.office365.com")
            self.port_input.setText("587")
            self.auth_combo.setCurrentIndex(self.auth_combo.findData("password"))
            self.tls_check.setChecked(True)
            self.ssl_check.setChecked(False)
        self._update_auth_mode()

    def _update_auth_mode(self):
        relay = self.auth_combo.currentData() == "relay"
        self.username_input.setEnabled(not relay and self._can("manage_smtp"))
        self.password_input.setEnabled(not relay and self._can("manage_smtp"))
        if relay:
            self.mode_help.setText(
                "Relay için Google yöneticisi tesisin sabit çıkış IP adresini "
                "smtp-relay.gmail.com hizmetinde yetkilendirmelidir. Kullanıcı adı ve şifre gönderilmez.")
        else:
            self.mode_help.setText(
                "Uygulama şifresi hangi ana Google hesabında üretildiyse kullanıcı adı o hesap olmalıdır. "
                "Gönderen adresi ayrı bir onaylı takma ad olabilir.")

    def _reload(self):
        self.table.setRowCount(0)
        for r in db.list_email_recipients(active_only=False):
            row = self.table.rowCount()
            self.table.insertRow(row)
            for c, v in enumerate([str(r["id"]), r["email"], r["kind"]]):
                self.table.setItem(row, c, QTableWidgetItem(v))

    def _add_recipient(self):
        if not self._require("manage_email_recipients"):
            return
        email = self.email_input.text().strip()
        if "@" not in email:
            QMessageBox.warning(self, "Hata", "Gecerli bir e-posta adresi giriniz.")
            return
        db.add_email_recipient(email, self.kind_combo.currentText())
        db.audit(self.username, "RAPOR_ALICISI_EKLENDI", target=email)
        self.email_input.clear()
        self._reload()

    def _remove_selected(self):
        if not self._require("manage_email_recipients"):
            return
        row = self.table.currentRow()
        if row < 0:
            return
        rid = int(self.table.item(row, 0).text())
        email = self.table.item(row, 1).text()
        db.remove_email_recipient(rid)
        db.audit(self.username, "RAPOR_ALICISI_SILINDI", target=email)
        self._reload()

    def _send_test(self):
        if not self._require("send_test_email"):
            return
        if self._test_worker is not None:
            return
        recipients = [r["email"] for r in db.list_email_recipients() if r["kind"] == "TO"]
        target = recipients[0] if recipients else self.email_input.text().strip()
        if not target:
            QMessageBox.warning(self, "Hata", "Once bir alici ekleyin veya e-posta girin.")
            return
        can_save = self._can("manage_smtp") or self._can("manage_schedule")
        if can_save and not self._save_settings(show_message=False):
            return
        self.test_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.test_btn.setText("Baglanti test ediliyor...")
        self._test_worker = _SmtpTestWorker(target, self)
        self._test_worker.completed.connect(self._test_finished)
        self._test_worker.finished.connect(self._test_worker_finished)
        self._test_worker.start()

    def _test_finished(self, target: str, error):
        if error is None:
            QMessageBox.information(self, "Basarili", f"Test e-postasi gonderildi: {target}")
            db.audit(self.username, "TEST_EPOSTASI_GONDERILDI", target=target)
            return
        db.audit(self.username, "TEST_EPOSTASI_HATA", target=target,
                 note=f"{type(error).__name__}: {error}")
        QMessageBox.critical(
            self, "Basarisiz", f"Gonderim hatasi:\n{type(error).__name__}: {error}")

    def _test_worker_finished(self):
        worker = self._test_worker
        self._test_worker = None
        self.test_btn.setEnabled(self._can("send_test_email"))
        self.save_btn.setEnabled(self._can("manage_smtp") or self._can("manage_schedule"))
        self.test_btn.setText("Test E-postasi Gonder")
        if worker:
            worker.deleteLater()

    def _save_settings(self, show_message=True):
        can_smtp = self._can("manage_smtp")
        can_schedule = self._can("manage_schedule")
        if not (can_smtp or can_schedule):
            QMessageBox.warning(self, "Yetki", "SMTP veya rapor saati değiştirme yetkiniz yok.")
            return False
        time_val = self.time_input.text().strip()
        if can_schedule:
            try:
                hh, mm = time_val.split(":")
                assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
            except (ValueError, AssertionError):
                QMessageBox.warning(self, "Hata", "Saat formati HH:MM olmalidir.")
                return False
        if can_smtp and not self._save_smtp_settings():
            return False
        if can_schedule:
            old_time = db.get_setting("report_time")
            db.set_setting("report_time", time_val)
            db.set_setting("report_active", "1" if self.active_check.isChecked() else "0")
            db.audit(self.username, "RAPOR_ZAMANI_DEGISTI", old_value=old_time, new_value=time_val)
        if show_message:
            QMessageBox.information(self, "Kaydedildi",
                                    "Yetkiniz dahilindeki ayarlar kaydedildi. Yeni gonderim saati "
                                    "pencere kapaninca hemen etkinlesir.")
        return True

    def _save_smtp_settings(self):
        if not self._require("manage_smtp"):
            return False
        try:
            port = int(self.port_input.text().strip())
            assert 1 <= port <= 65535
        except (ValueError, AssertionError):
            QMessageBox.warning(self, "Hata", "SMTP portu 1-65535 arasında olmalıdır.")
            return False
        if not self.host_input.text().strip() or "@" not in self.from_email_input.text():
            QMessageBox.warning(self, "Hata", "SMTP sunucusu ve gönderen e-posta zorunludur.")
            return False
        if self.tls_check.isChecked() and self.ssl_check.isChecked():
            QMessageBox.warning(self, "Hata", "TLS ve SSL aynı anda seçilemez.")
            return False
        auth_mode = self.auth_combo.currentData()
        if auth_mode == "password":
            if "@" not in self.username_input.text():
                QMessageBox.warning(
                    self, "Hata", "Uygulama şifresi modunda tam kullanıcı e-posta adresi zorunludur.")
                return False
            if not self.password_input.text() and not db.get_setting("smtp_password_enc", ""):
                QMessageBox.warning(
                    self, "Hata", "SMTP uygulama şifresi zorunludur.")
                return False
        db.set_setting("smtp_provider", str(self.provider_combo.currentData()))
        db.set_setting("smtp_auth_mode", str(auth_mode))
        db.set_setting("smtp_host", self.host_input.text().strip())
        db.set_setting("smtp_port", str(port))
        db.set_setting("smtp_username", self.username_input.text().strip())
        if self.password_input.text():
            password = self.password_input.text()
            if self.host_input.text().strip().lower() in {"smtp.gmail.com", "smtp.googlemail.com"}:
                password = "".join(password.split())
            db.set_setting("smtp_password_enc", protect_secret(password))
        db.set_setting("smtp_from_email", self.from_email_input.text().strip())
        db.set_setting("smtp_from_name", self.from_name_input.text().strip())
        db.set_setting("smtp_use_tls", "1" if self.tls_check.isChecked() else "0")
        db.set_setting("smtp_use_ssl", "1" if self.ssl_check.isChecked() else "0")
        db.audit(self.username, "SMTP_AYAR_DEGISTI", target=self.host_input.text().strip())
        return True

    def _save(self):
        if not self._save_settings(show_message=True):
            return
        self.accept()

    def done(self, result: int):
        if self._test_worker and self._test_worker.isRunning():
            QMessageBox.information(
                self, "SMTP Testi Suruyor",
                "SMTP baglanti testi tamamlanmadan bu pencere kapatilamaz.")
            return
        super().done(result)
