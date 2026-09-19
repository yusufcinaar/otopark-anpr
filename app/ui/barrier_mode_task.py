"""Serbest gecis kart komutunu ana arayuzu bekletmeden uygular."""
from PySide6.QtCore import QThread, Signal


class FreePassModeTask(QThread):
    completed = Signal(bool, str)

    def __init__(self, service, enabled: bool, username: str, parent=None):
        super().__init__(parent)
        self.service = service
        self.enabled = enabled
        self.username = username

    def run(self):
        try:
            applied = self.service.set_free_pass_mode(
                self.enabled, username=self.username)
            detail = "" if applied else self.service.last_error("CIKIS-1")
            self.completed.emit(bool(applied), detail or "")
        except Exception as exc:
            self.completed.emit(False, str(exc))
