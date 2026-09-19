"""Cikistaki fiziksel LED ucret ekraninin uygulama arayuzu."""

from PySide6.QtCore import QObject, Signal

from app import db
from app import config
from app.led_display import PhysicalLedQueue
from app.services.plate_utils import format_plate


class DigitalDisplay(QObject):
    """Cikistaki ucret gosterge ekranini temsil eder."""
    content_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._physical = PhysicalLedQueue(
            enabled=not config.SIMULATION_MODE and db.get_setting("arma_led_enabled", "0") == "1",
            host=db.get_setting("arma_led_ip", ""),
            port=int(db.get_setting("arma_led_port", "6101") or 6101),
        )

    def show_idle(self):
        self._emit({"mode": "idle", "text": "DEMO OTOPARK"})

    def show_processing(self):
        self.show_idle()

    def show_fee(self, plate_display: str, duration_str: str, fee: float, currency: str):
        self._emit({
            "mode": "fee",
            "plate": format_plate(plate_display),
            "duration": duration_str,
            "fee": fee,
            "currency": currency,
        })

    def show_paid(self, plate_display: str):
        self.show_idle()

    def show_error(self, message: str):
        self.show_idle()

    def _emit(self, payload: dict):
        self.content_changed.emit(payload)
        mode = payload.get("mode")
        if mode == "fee":
            lines = [payload.get("plate", ""), f"{payload.get('fee', 0):.0f} TL"]
        else:
            # Odeme disindaki tum durumlarda sabit otel adi gosterilir.
            lines = ["DEMO", "OTOPARK"]
        self._physical.submit(lines)

    def close(self):
        self._physical.close()

    def reload_physical_settings(self):
        self._physical.close()
        self._physical = PhysicalLedQueue(
            enabled=not config.SIMULATION_MODE and db.get_setting("arma_led_enabled", "0") == "1",
            host=db.get_setting("arma_led_ip", ""),
            port=int(db.get_setting("arma_led_port", "6101") or 6101),
        )
        self.show_idle()
