"""Serbest gecis UI istekleri: gercek Qt thread'leri, sahte kart ve veritabani."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


_PROBE = r'''
import sys, threading, time
from types import SimpleNamespace
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QMessageBox
from app import db
from app.ui.main_window import MainWindow
from app.ui.barrier_shortcut import ExitBarrierShortcut

case = sys.argv[1]
app = QApplication([])
main_thread = threading.get_ident()
initial = '1' if case.startswith(('disable', 'startup')) else '0'
settings = {'free_pass_mode': initial}
calls, writes, audits, logs, messages, cleanup = [], [], [], [], [], []
release, f4_release = threading.Event(), threading.Event()
if case not in ('busy', 'close_free', 'close_both'):
    release.set()
failing = case in ('enable_failure', 'disable_failure', 'startup_failure')
db.get_setting = lambda key, default='': settings.get(key, default)
def set_setting(key, value):
    writes.append((key, value, threading.get_ident()))
    settings[key] = value
db.set_setting = set_setting
db.audit = lambda *args, **kwargs: audits.append(args)
QMessageBox.question = lambda *args: (QMessageBox.StandardButton.No if case == 'reject'
                                    else QMessageBox.StandardButton.Yes)
QMessageBox.warning = lambda parent, title, detail: messages.append((title, detail))
QMessageBox.critical = lambda parent, title, detail: messages.append((title, detail))

class Barrier:
    def set_free_pass_mode(self, enabled, username=''):
        calls.append((enabled, username, threading.get_ident()))
        expected = '1' if case in ('hold_then_release_failure', 'cashier_roundtrip') and len(calls) == 2 else initial
        assert settings['free_pass_mode'] == expected, 'UI committed mode before card success'
        assert release.wait(5), 'Card worker was not released'
        if case == 'enable_exception':
            raise RuntimeError('Baglanti koptu')
        if failing or (case == 'hold_then_release_failure' and not enabled):
            return False
        db.set_setting('free_pass_mode', '1' if enabled else '0')
        return True
    def last_error(self, gate):
        assert gate == 'CIKIS-1'
        return 'Kart komutu kabul edilmedi'
    def manual_open(self, gate, username, *, reason):
        assert gate == 'CIKIS-1'
        calls.append(('F4', username, threading.get_ident()))
        assert f4_release.wait(5)
        return True
    def open(self, *args, **kwargs):
        raise AssertionError('Free pass must not send a normal open pulse')
    def close(self, *args, **kwargs):
        raise AssertionError('Releasing hold must not send a manual close')

class Harness(MainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        role = 'muhasebe' if case == 'permission' else ('kasiyer' if case == 'cashier_roundtrip' else 'guvenlik')
        self.current_user = {'username': 'operator', 'role': role}
        self._free_pass_worker = None
        self._free_pass_error = ''
        self._free_pass_applied = True if case.startswith('disable') else None
        self._close_after_barrier_shortcut = False
        self.free_pass_btn = QPushButton(self)
        self.free_pass_btn.setCheckable(True)
        self.free_pass_btn.clicked.connect(self.toggle_free_pass)
        self.append_log = logs.append
        self.service = SimpleNamespace(barriers=Barrier(), exit_display=SimpleNamespace(close=lambda: None))
        self._webhook_retry_timer = SimpleNamespace(stop=lambda: None)
        self._stop_camera_workers = lambda: None
        self.scheduler = None
        self.webhook = SimpleNamespace(stop=lambda: cleanup.append('closed'))
        self.barrier_shortcut = ExitBarrierShortcut(
            self, self.service.barriers, lambda: self.current_user, logs.append, 'F4')
        self.barrier_shortcut.idle.connect(self._barrier_shortcut_idle)
        self._sync_free_pass_button()

window = Harness()
window.show()
window.activateWindow()
app.processEvents()
def settle(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        QTest.qWait(5)
    assert predicate(), 'Qt operation timed out: ' + case

if case.startswith('startup'):
    assert window.free_pass_btn.text() != 'SERBEST GECIS ACIK'
    window._apply_persisted_free_pass()
elif case == 'permission':
    assert not window.free_pass_btn.isEnabled()
    window.toggle_free_pass(True)  # Kontrol programatik cagrida da zorunlu.
else:
    window.free_pass_btn.click()

if case in ('permission', 'reject'):
    assert not calls and not writes and not audits
    assert not window.free_pass_btn.isChecked()
    assert bool(messages) == (case == 'permission')
else:
    settle(lambda: len(calls) == 1)
    assert calls[0][:2] == (not case.startswith('disable'), 'operator')
    assert calls[0][2] != main_thread, 'Card I/O ran on UI thread'
    if case in ('busy', 'close_free', 'close_both'):
        assert window._free_pass_worker is not None
        assert not window.free_pass_btn.isEnabled()
        assert not writes and not audits
        window.toggle_free_pass(True)
        window.toggle_free_pass(False)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        settle(lambda: bool(ticks))
        assert len(calls) == 1, 'Pending mode change accepted a duplicate'
        if case == 'close_both':
            window.barrier_shortcut.activate()
            settle(lambda: len(calls) == 2)
        if case.startswith('close'):
            window.close()
            app.processEvents()
            assert window.isVisible() and not cleanup
            window.toggle_free_pass(True)
            assert len(calls) == (2 if case == 'close_both' else 1)
        release.set()
    settle(lambda: window._free_pass_worker is None)
    if case == 'close_both':
        assert window.isVisible() and not cleanup, 'Shutdown raced the pending F4 worker'
        f4_release.set()
        settle(lambda: not window.barrier_shortcut.busy)
    if case.startswith('close'):
        settle(lambda: not window.isVisible())
        assert cleanup == ['closed']
    if failing or case == 'enable_exception':
        assert settings['free_pass_mode'] == initial
        assert not writes and not audits
        assert window.free_pass_btn.isChecked() == (initial == '1')
        assert 'uygulanamadi' in logs[0]
        assert messages
        assert not any('komutu gonderildi' in line for line in logs)
        if case in ('startup_failure', 'disable_failure'):
            assert window.free_pass_btn.text() == 'Serbest gecis - uygulanamadi'
            assert not window.free_pass_btn.styleSheet()
            assert window._free_pass_applied is None
    else:
        desired = '0' if case.startswith('disable') else '1'
        assert settings['free_pass_mode'] == desired
        assert len(writes) == 1 and writes[0][2] != main_thread
        assert bool(audits) == (not case.startswith('startup'))
        assert not messages
        assert window.free_pass_btn.isChecked() == (desired == '1')
        if desired == '0':
            assert 'normal gecis moduna donuldu' in logs[0]
            assert 'bariyer kapatildi' not in logs[0].lower()
        else:
            assert window.free_pass_btn.text() == 'SERBEST GECIS ACIK'

if case == 'cashier_roundtrip':
    assert window._free_pass_applied is True
    assert window.free_pass_btn.isEnabled(), 'Cashier cannot return to normal passage'
    window.free_pass_btn.click()
    settle(lambda: len(calls) == 2 and window._free_pass_worker is None)
    assert calls[1][:2] == (False, 'operator')
    assert calls[1][2] != main_thread
    assert settings['free_pass_mode'] == '0'
    assert [(key, value) for key, value, thread_id in writes] == [
        ('free_pass_mode', '1'), ('free_pass_mode', '0')]
    assert len(audits) == 2, 'Both cashier mode changes must be audited'
    assert not window.free_pass_btn.isChecked()
    assert window.free_pass_btn.isEnabled()
    assert window._free_pass_applied is False
    assert 'normal gecis moduna donuldu' in logs[-1]
    assert not messages

if case == 'hold_then_release_failure':
    assert window._free_pass_applied is True
    assert window.free_pass_btn.styleSheet(), 'Successful HOLD must first be confirmed'
    window.free_pass_btn.click()
    settle(lambda: len(calls) == 2 and window._free_pass_worker is None)
    assert calls[1][:2] == (False, 'operator')
    assert settings['free_pass_mode'] == '1' and len(writes) == 1
    assert len(audits) == 1, 'Failed RELEASE was audited as success'
    assert window.free_pass_btn.isChecked(), 'Saved preference changed after failed RELEASE'
    assert window._free_pass_applied is None
    assert window.free_pass_btn.text() == 'Serbest gecis - uygulanamadi'
    assert not window.free_pass_btn.styleSheet()
    assert window.free_pass_btn.objectName() != 'successBtn'
    assert messages and 'uygulanamadi' in logs[-1]

window.close()
app.processEvents()
print('OK', case)
'''


@pytest.mark.parametrize("case", [
    "enable_success", "disable_success", "enable_failure", "disable_failure",
    "enable_exception", "startup_success", "startup_failure", "busy", "permission",
    "reject", "close_free", "close_both", "hold_then_release_failure",
    "cashier_roundtrip",
])
def test_free_pass_ui(case, tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, case],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", OTOPARK_DATA_DIR=str(tmp_path)),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"OK {case}" in result.stdout
