"""Uygulamanin kendi pencerelerinde cikis bariyeri klavye kisayolu."""
from PySide6.QtCore import QEvent, QObject, QThread, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from app.services import auth


class _OpenBarrierTask(QThread):
    completed = Signal(bool, str)

    def __init__(self, service, username, reason, parent):
        super().__init__(parent)
        self.service, self.username, self.reason = service, username, reason

    def run(self):
        try:
            opened = self.service.manual_open(
                "CIKIS-1", self.username, reason=self.reason)
            error = "" if opened else self.service.last_error("CIKIS-1")
            self.completed.emit(bool(opened), error or "")
        except Exception as exc:
            self.completed.emit(False, str(exc))


class ExitBarrierShortcut(QObject):
    """Fiziksel komutu UI disinda gonderir; bekleyen istegi tekrarlamaz."""
    idle = Signal()

    def __init__(self, owner, service, get_user, append_log, key="F4"):
        super().__init__(owner)
        self.owner, self.service = owner, service
        self.get_user, self.append_log = get_user, append_log
        sequence = QKeySequence(key)
        self.sequence = sequence if sequence.count() == 1 else QKeySequence("F4")
        self.reason = f"{self.sequence.toString()} klavye kisayolu"
        self._worker = None
        self._enabled = True
        self._shortcuts = {}
        self._bind(owner)
        QApplication.instance().installEventFilter(self)

    @property
    def busy(self):
        return self._worker is not None

    def disable(self):
        self._enabled = False
        for shortcut in self._shortcuts.values():
            shortcut.setEnabled(False)

    def _owns(self, widget):
        while isinstance(widget, QWidget):
            if widget is self.owner:
                return True
            widget = widget.parentWidget()
        return False

    def _bind(self, window):
        if window in self._shortcuts:
            return
        shortcut = QShortcut(self.sequence, window)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.setAutoRepeat(False)
        shortcut.setEnabled(self._enabled)
        shortcut.activated.connect(self.activate)
        self._shortcuts[window] = shortcut
        window.destroyed.connect(lambda: self._shortcuts.pop(window, None))

    def eventFilter(self, watched, event):
        # Ana pencerenin kisayolu modal diyalog acikken Qt tarafindan devre
        # disi kalir. Yalnizca bize ait diyaloglara ayni kisayolu bagla.
        if (event.type() == QEvent.Type.Show and isinstance(watched, QWidget)
                and watched.isWindow() and self._owns(watched)):
            self._bind(watched)
        return False

    def activate(self):
        if (not self._enabled or self.busy
                or not self._owns(QApplication.activeWindow())):
            return
        try:
            user = self.get_user()
            auth.require(user, "manual_barrier_open")
        except auth.PermissionDenied as exc:
            QMessageBox.warning(QApplication.activeWindow(), "Yetki", str(exc))
            return
        username = user["username"]
        worker = _OpenBarrierTask(self.service, username, self.reason, self)
        self._worker = worker
        self._request_username = username
        worker.completed.connect(self._completed)
        worker.finished.connect(self._finished)
        worker.start()

    def _completed(self, opened, error):
        if opened:
            self.append_log(
                f"[CIKIS-1] Bariyer acma komutu gonderildi: "
                f"{self._request_username} ({self.reason})")
            return
        detail = error or "Bariyer acma komutu basarisiz. Ayarlari ve ag baglantisini kontrol edin."
        self.append_log(f"[CIKIS-1] {self.reason}: {detail}")
        # Kapatma bekleniyorsa uygulamayi yeni bir modal mesajla tutma.
        if self._enabled:
            QMessageBox.critical(
                QApplication.activeWindow() or self.owner, "Bariyer Acilamadi", detail)

    def _finished(self):
        worker, self._worker = self._worker, None
        worker.deleteLater()
        self.idle.emit()
