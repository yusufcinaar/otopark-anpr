"""Gercek Qt tus olaylari; kameralar/ag/donanima baglanmadan alt surecte."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


_PROBE = r'''
import sys, threading, time
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QDialog, QLineEdit, QVBoxLayout, QMessageBox
from app.ui.barrier_shortcut import ExitBarrierShortcut

case = sys.argv[1]
app = QApplication([])
cleanup = []
if case == 'close':
    from types import SimpleNamespace
    from app.ui.main_window import MainWindow
    class CloseHarness(MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self._close_after_barrier_shortcut = False
            self._webhook_retry_timer = SimpleNamespace(stop=lambda: None)
            self._stop_camera_workers = lambda: None
            self.service = SimpleNamespace(exit_display=SimpleNamespace(close=lambda: None))
            self.scheduler = None
            self.webhook = SimpleNamespace(stop=lambda: cleanup.append('closed'))
    window = CloseHarness()
else:
    window = QMainWindow()
window.show()
window.activateWindow()
app.processEvents()
logs, messages, calls = [], [], []
release = threading.Event()
if case not in ('busy', 'close'):
    release.set()
main_thread = threading.get_ident()
class Service:
    def manual_open(self, gate, username, *, reason):
        calls.append((gate, username, reason, threading.get_ident()))
        assert release.wait(5), 'Worker never released'
        if case == 'exception':
            raise RuntimeError('Baglanti koptu')
        return case != 'failure'
    def last_error(self, gate):
        return 'Karttan yanit alinamadi'

QMessageBox.warning = lambda parent, title, text: messages.append(('warning', text))
QMessageBox.critical = lambda parent, title, text: messages.append(('critical', text))
role = 'muhasebe' if case == 'permission' else ('kasiyer' if case.startswith('cashier') else 'guvenlik')
user = {'username': 'operator', 'role': role}
shortcut = ExitBarrierShortcut(window, Service(), lambda: user, logs.append, 'F4')
if case == 'close':
    window.barrier_shortcut = shortcut
    shortcut.idle.connect(window._barrier_shortcut_idle)
assert not shortcut._shortcuts[window].autoRepeat()
assert shortcut._shortcuts[window].key().toString() == 'F4'
def settle(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        QTest.qWait(5)
    assert predicate(), f'Qt operation timed out ({case})'
def press(target=window):
    QTest.keyClick(target, Qt.Key_F4)
    app.processEvents()

target = window
dialog = None
if case in ('modal', 'cashier_modal'):
    dialog = QDialog(window)
    dialog.setModal(True)
    target = QLineEdit(dialog)
    QVBoxLayout(dialog).addWidget(target)
    dialog.show()
    dialog.activateWindow()
    target.setFocus()
    app.processEvents()
    assert dialog in shortcut._shortcuts
elif case == 'unrelated':
    unrelated = QDialog()
    unrelated.show()
    unrelated.activateWindow()
    app.processEvents()
    press(unrelated)
    assert not calls
    assert unrelated not in shortcut._shortcuts
    unrelated.close()
    window.activateWindow()
    app.processEvents()
elif case == 'autorepeat':
    repeat = QKeyEvent(QEvent.KeyPress, Qt.Key_F4, Qt.NoModifier, '', True, 1)
    QApplication.sendEvent(window, repeat)
    app.processEvents()
    assert not calls

press(target)
if case == 'permission':
    assert not calls and not shortcut.busy
    assert messages and messages[0][0] == 'warning'
else:
    settle(lambda: len(calls) == 1)
    if case == 'busy':
        assert shortcut.busy
        press()
        press()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        settle(lambda: bool(ticks))
        assert len(calls) == 1, 'Repeated press sent overlapping opens'
        release.set()
    if case == 'close':
        window.close()
        app.processEvents()
        assert window.isVisible(), 'Window destroyed a running QThread'
        assert not cleanup, 'Services closed while a barrier operation was pending'
        press()
        assert len(calls) == 1
        release.set()
        settle(lambda: not window.isVisible())
        assert cleanup == ['closed']
    settle(lambda: not shortcut.busy)
    assert calls[0][:3] == ('CIKIS-1', 'operator', 'F4 klavye kisayolu')
    assert calls[0][3] != main_thread, 'Barrier I/O blocked the UI thread'
    if case in ('failure', 'exception'):
        expected = 'Karttan yanit alinamadi' if case == 'failure' else 'Baglanti koptu'
        assert messages == [('critical', expected)]
        assert expected in logs[0]
    else:
        assert not messages
        assert 'komutu gonderildi' in logs[0] and 'F4 klavye kisayolu' in logs[0]
    if case == 'autorepeat':
        for _ in range(4):
            QApplication.sendEvent(window, QKeyEvent(QEvent.KeyPress, Qt.Key_F4, Qt.NoModifier, '', True, 1))
        app.processEvents()
        assert len(calls) == 1
    if case == 'busy':
        press()
        settle(lambda: len(calls) == 2 and not shortcut.busy)

shortcut.disable()
press(target)
assert not shortcut.busy
if dialog:
    dialog.close()
window.close()
app.processEvents()
print('OK', case)
'''


@pytest.mark.parametrize("case", [
    "main", "modal", "unrelated", "autorepeat", "busy", "permission", "failure", "exception", "close",
    "cashier_main", "cashier_modal",
])
def test_exit_barrier_shortcut(case, tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", OTOPARK_DATA_DIR=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, case], env=env,
        cwd=Path(__file__).resolve().parents[1], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"OK {case}" in result.stdout
