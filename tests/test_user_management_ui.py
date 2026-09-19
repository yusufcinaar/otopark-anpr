"""Kullanici yonetimi ag yetkilerini dolayli olarak kazandirmamalidir."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


_PROBE = r'''
import sys
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QWidget, QMessageBox, QInputDialog
from app import db
from app.ui.users_dialog import UsersDialog

app = QApplication([])
db.init_db()
db.create_user('operator', 'Operator-123', 'kasiyer')
parent = QWidget()
case = sys.argv[1]
parent.current_user = {
    'username': 'admin' if case == 'admin' else 'operator',
    'role': 'sistem_yoneticisi' if case == 'admin' else (
        'muhasebe' if case == 'denied' else 'kasiyer'),
}
warnings = []
QMessageBox.warning = lambda *args: warnings.append(args[2])
QMessageBox.information = lambda *args: None
QMessageBox.question = lambda *args: QMessageBox.StandardButton.Yes
QInputDialog.getText = lambda *args: ('Changed-123', True)
dialog = UsersDialog(parent)

def select(username):
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 1).text() == username:
            dialog.table.selectRow(row)
            return
    raise AssertionError('Missing user: ' + username)

def add(username, role):
    dialog.username_input.setText(username)
    dialog.password_input.setText('Temporary-123')
    dialog.role_combo.setCurrentText(role)
    dialog.add_user()

if case == 'cashier':
    assert {dialog.role_combo.itemText(i) for i in range(dialog.role_combo.count())} == {
        'kasiyer', 'guvenlik', 'muhasebe'}
    for role in ('kasiyer', 'guvenlik', 'muhasebe'):
        add('new_' + role, role)
        assert db.verify_user('new_' + role, 'Temporary-123')['role'] == role
    select('new_kasiyer')
    assert dialog.password_btn.isEnabled() and dialog.remove_btn.isEnabled()
    dialog.change_password()
    assert db.verify_user('new_kasiyer', 'Changed-123')
    assert db.verify_user('new_kasiyer', 'Temporary-123') is None
    dialog.remove_selected()
    assert not any(u['username'] == 'new_kasiyer' for u in db.list_users())
    select('operator')
    assert not dialog.remove_btn.isEnabled()
    dialog.remove_selected()
    assert any(u['username'] == 'operator' for u in db.list_users())
    select('admin')
    assert not dialog.password_btn.isEnabled() and not dialog.remove_btn.isEnabled()
    # Handler checks remain effective even when called directly or the table is stale.
    dialog.table.item(dialog.table.currentRow(), 2).setText('kasiyer')
    with patch.object(db, 'remove_user') as remove, \
         patch.object(db, 'update_user_password') as update:
        dialog.change_password()
        dialog.remove_selected()
        remove.assert_not_called()
        update.assert_not_called()
    for role in ('yonetici', 'sistem_yoneticisi'):
        dialog.role_combo.addItem(role)
        add('forbidden_' + role, role)
        assert not any(u['username'] == 'forbidden_' + role for u in db.list_users())
    assert db.verify_user('admin', 'admin')
    with db._connect() as conn:
        actions = {r['action'] for r in conn.execute(
            "SELECT action FROM audit_logs WHERE username='operator'")}
    assert {'KULLANICI_EKLENDI', 'KULLANICI_SIFRESI_DEGISTI', 'KULLANICI_SILINDI'} <= actions
elif case == 'admin':
    assert dialog.role_combo.count() == 5
    add('second_admin', 'sistem_yoneticisi')
    select('second_admin')
    assert dialog.password_btn.isEnabled() and dialog.remove_btn.isEnabled()
    dialog.change_password()
    assert db.verify_user('second_admin', 'Changed-123')
    dialog.remove_selected()
    assert not any(u['username'] == 'second_admin' for u in db.list_users())
elif case == 'denied':
    assert dialog.role_combo.count() == 0 and not dialog.add_btn.isEnabled()
    dialog.role_combo.addItem('kasiyer')
    add('forbidden', 'kasiyer')
    assert not any(u['username'] == 'forbidden' for u in db.list_users())
    select('operator')
    dialog.change_password()
    dialog.remove_selected()
    assert db.verify_user('operator', 'Operator-123')
    assert warnings

dialog.close()
parent.close()
print('PASS: ' + case)
'''


@pytest.mark.parametrize("case", ["cashier", "admin", "denied"])
def test_user_management_boundaries(case, tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               OTOPARK_DATA_DIR=str(tmp_path / "ui_data"))
    Path(env['OTOPARK_DATA_DIR']).mkdir()
    (Path(env['OTOPARK_DATA_DIR']) / 'otopark.db').touch()
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, case],
        cwd=Path(__file__).resolve().parents[1], env=env,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: " + case in result.stdout
