"""Cashier operational settings stay usable without SMTP/device write access."""
import os
import subprocess
import sys
from pathlib import Path


_PROBE = r'''
import socket
import time
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from app import db
from app.ui import main_window
from app.ui.smtp_dialog import SmtpDialog
from app.reports import mailer

app = QApplication([])
db.init_db()
messages = []
QMessageBox.warning = lambda parent, title, text: messages.append((title, text))
QMessageBox.information = lambda *args: None
QMessageBox.critical = lambda *args: (_ for _ in ()).throw(AssertionError(args))
socket.socket.connect = lambda *args: (_ for _ in ()).throw(AssertionError('Real network forbidden'))

class Harness(main_window.MainWindow):
    def __init__(self, role):
        QMainWindow.__init__(self)
        self.current_user = {'username': 'test-user', 'role': role}

cashier = Harness('kasiyer')
cashier._build_menu()
actions = {}
for menu_action in cashier.menuBar().actions():
    for action in menu_action.menu().actions():
        actions[action.text()] = action
for title in ('Manuel Bariyer Aç', 'Abone Tanımları', 'Tarife Yönetimi', 'Kara Liste',
              'Kullanıcı Tanımları', 'Hız Limiti Ayarı', 'Hız Ölçüm Kalibrasyonu',
              'Araç / Plaka Ara ve Raporlar', 'Otomatik Rapor ve E-posta Ayarları',
              'İşlem Logları'):
    assert actions[title].isEnabled(), title
for title in ('Kamera Tanımları', 'Bariyer ve LED Ayarları'):
    assert not actions[title].isEnabled(), title
with patch.object(main_window, 'CameraDialog') as cameras, \
     patch.object(main_window, 'BarrierControlDialog') as barriers:
    cashier.open_devices()
    cashier.open_barrier_control()
    cameras.assert_not_called()
    barriers.assert_not_called()
with patch.object(main_window, 'SubscribersDialog') as subscribers, \
     patch.object(main_window, 'TariffDialog') as tariffs, \
     patch.object(main_window, 'BlacklistDialog') as blacklist, \
     patch.object(main_window, 'UsersDialog') as users:
    cashier.open_subscribers()
    cashier.open_tariffs()
    cashier.open_blacklist()
    cashier.open_users()
    for dialog in (subscribers, tariffs, blacklist, users):
        dialog.return_value.exec.assert_called_once()
# Verify the permitted actions persist real operational records with this actor.
subscriber_form = main_window.SubscribersDialog(cashier)
subscriber_form.plate_input.setText('34 abc 123')
subscriber_form.name_input.setText('Kasiyer Test Abonesi')
subscriber_form.unlimited_btn.click()
subscriber_form.add_btn.click()
assert db.get_active_subscriber('34ABC123')['name'] == 'Kasiyer Test Abonesi'
old_tariff = db.get_active_tariff()
tariff_form = main_window.TariffDialog('test-user', cashier)
tariff_form.extra_day_input.setText('750')
tariff_form._save()
new_tariff = db.get_active_tariff()
assert new_tariff['version'] == old_tariff['version'] + 1
assert new_tariff['rules']['extra_day_price'] == '750.00'
assert any(t['id'] == old_tariff['id'] for t in db.list_tariffs())
assert {'ABONE_EKLENDI_GUNCELLENDI', 'TARIFE_DEGISTI'} <= {
    row['action'] for row in db.list_audit_logs() if row['username'] == 'test-user'}
with patch.object(main_window.QInputDialog, 'getDouble', return_value=(25.0, True)):
    cashier.configure_speed_limit()
assert db.get_setting('speed_limit_kmh') == '25.0'
with patch.object(main_window.QInputDialog, 'getDouble', return_value=(2.0, True)), \
     patch.object(cashier, '_start_configured_cameras') as restart:
    cashier.configure_speed_calibration()
    restart.assert_called_once()
assert db.get_setting('speed_estimation_scale') == '2.0'

settings = {
    'smtp_provider': 'google_relay', 'smtp_auth_mode': 'relay',
    'smtp_host': 'smtp-relay.gmail.com', 'smtp_port': '587',
    'smtp_username': 'configured@example.test', 'smtp_password_enc': 'PRESERVED',
    'smtp_from_email': 'sender@example.test', 'smtp_from_name': 'Hotel',
    'smtp_use_tls': '1', 'smtp_use_ssl': '0',
}
for key, value in settings.items():
    db.set_setting(key, value)
dialog = SmtpDialog('test-user', cashier)
assert all(not widget.isEnabled() for widget in dialog._network_widgets)
assert dialog.time_input.isEnabled() and dialog.active_check.isEnabled()
assert dialog.email_input.isEnabled() and dialog.test_btn.isEnabled()
dialog.host_input.setText('malicious.invalid')
dialog.port_input.setText('bad-port')
dialog.password_input.setText('changed')
dialog.time_input.setText('10:45')
dialog.active_check.setChecked(False)
assert dialog._save_settings(show_message=False)
assert db.get_setting('report_time') == '10:45'
assert db.get_setting('report_active') == '0'
assert not dialog._save_smtp_settings()
assert all(db.get_setting(key) == value for key, value in settings.items())
dialog.email_input.setText('recipient@example.test')
dialog._add_recipient()
assert [row['email'] for row in db.list_email_recipients()] == ['recipient@example.test']
sent = []
mailer.send_test_email = lambda target: sent.append((target, db.get_setting('smtp_host')))
dialog._send_test()
end = time.monotonic() + 4
while dialog._test_worker is not None and time.monotonic() < end:
    app.processEvents()
    QTest.qWait(5)
assert dialog._test_worker is None, 'Mock email worker did not finish'
assert sent == [('recipient@example.test', settings['smtp_host'])]
assert all(db.get_setting(key) == value for key, value in settings.items())
dialog.table.selectRow(0)
dialog._remove_selected()
assert not db.list_email_recipients()

# Operational report scheduling must not depend on valid network credentials.
db.set_setting('smtp_host', '')
db.set_setting('smtp_from_email', '')
dialog.time_input.setText('11:15')
assert dialog._save_settings(show_message=False)
assert db.get_setting('report_time') == '11:15'

security = Harness('guvenlik')
blocked = SmtpDialog('security', security)
blocked.email_input.setText('blocked@example.test')
blocked._add_recipient()
blocked.time_input.setText('12:30')
assert not blocked._save_settings(show_message=False)
blocked._send_test()
assert not db.list_email_recipients()
assert db.get_setting('report_time') == '11:15'
assert len(sent) == 1
with patch.object(main_window, 'SubscribersDialog') as subscribers, \
     patch.object(main_window, 'BlacklistDialog') as blacklist, \
     patch.object(main_window, 'UsersDialog') as users, \
     patch.object(main_window.QInputDialog, 'getDouble') as prompt:
    security.open_subscribers()
    security.open_blacklist()
    security.open_users()
    security.configure_speed_limit()
    security.configure_speed_calibration()
    for operation in (subscribers, blacklist, users, prompt):
        operation.assert_not_called()

manager = Harness('yonetici')
admin_dialog = SmtpDialog('manager', manager)
assert admin_dialog.host_input.isEnabled() and admin_dialog.time_input.isEnabled()
admin_dialog.host_input.setText('smtp-relay.gmail.com')
admin_dialog.port_input.setText('587')
admin_dialog.from_email_input.setText('new-sender@example.test')
admin_dialog.time_input.setText('09:30')
assert admin_dialog._save_settings(show_message=False)
assert db.get_setting('smtp_from_email') == 'new-sender@example.test'
assert db.get_setting('report_time') == '09:30'
print('Cashier operational controls and network settings boundary verified')
'''


def test_cashier_settings_keep_network_configuration_protected(tmp_path):
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env['OTOPARK_DATA_DIR'] = str(tmp_path / 'ui-data')
    Path(env['OTOPARK_DATA_DIR']).mkdir()
    (Path(env['OTOPARK_DATA_DIR']) / 'otopark.db').touch()
    result = subprocess.run(
        [sys.executable, '-c', _PROBE],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
