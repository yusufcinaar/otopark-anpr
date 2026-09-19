"""Unknown Metcom close commands must never be reported as completed."""
from unittest.mock import MagicMock

import pytest

from app.barrier import BarrierService, MetcomIoController, MockBarrierController, KAPALI


def metcom_controller():
    controller = MetcomIoController(
        host="192.0.2.7", trigger_hex="0200030203", trigger_verified=True, heartbeat_interval=0)
    controller._sock = MagicMock()
    controller._http_connected = True
    controller.get_status = MagicMock(return_value=KAPALI)
    controller.connect = MagicMock(side_effect=AssertionError("Unexpected hardware access"))
    return controller


def test_close_without_known_command_is_not_success():
    controller = metcom_controller()
    with pytest.raises(NotImplementedError, match="kapatma komutu yok"):
        controller.close_barrier("CIKIS-1")
    controller._sock.sendall.assert_not_called()


def test_service_close_does_not_claim_closed_or_query_hardware(isolated_db):
    controller = metcom_controller()
    service = BarrierService(controller)
    states = []
    service.state_changed.connect(lambda *args: states.append(args))
    assert not service.is_close_supported("CIKIS-1")
    assert service.close("CIKIS-1") is False
    assert "kapatma komutu yok" in service.last_error("CIKIS-1")
    assert [e["action"] for e in isolated_db.list_barrier_events("CIKIS-1")] == ["KAPATMA_DESTEKLENMIYOR"]
    assert states == []
    controller._sock.sendall.assert_not_called()
    controller.get_status.assert_not_called()
    controller.connect.assert_not_called()


def test_metcom_open_does_not_schedule_fictitious_close(monkeypatch):
    monkeypatch.setattr("app.barrier.BARRIER_AUTO_CLOSE_SEC", 7)
    service = BarrierService(metcom_controller())
    assert service.open("CIKIS-1") is True
    assert service._auto_close_timers == {}


def test_pending_timer_cannot_complete_passage_for_metcom():
    controller = metcom_controller()
    service = BarrierService(controller)
    finished = []
    service.passage_finished.connect(lambda *args: finished.append(args))
    service._auto_close("CIKIS-1", 1, "admin")
    assert finished == []
    controller._sock.sendall.assert_not_called()
    controller.get_status.assert_not_called()


def test_actual_vehicle_passage_completes_without_unknown_close(svc, isolated_db):
    session_id = isolated_db.create_entry("34TEST123", "GIRIS-1")
    isolated_db.update_session_fields(session_id, {"status": "CIKIS_IZNI", "exit_lane": "CIKIS-1"})
    controller = metcom_controller()
    svc.barriers.controller = controller
    svc.exit_display.show_idle = MagicMock()
    assert svc.vehicle_passed(session_id)["status"] == "TAMAMLANDI"
    assert isolated_db.get_session(session_id)["status"] == "TAMAMLANDI"
    controller._sock.sendall.assert_not_called()
    assert isolated_db.list_barrier_events("CIKIS-1") == []


def test_simulation_keeps_supported_close(monkeypatch):
    monkeypatch.setattr("app.barrier.BARRIER_AUTO_CLOSE_SEC", 7)
    service = BarrierService(MockBarrierController())
    assert service.is_close_supported("CIKIS-1")
    assert service.open("CIKIS-1")
    assert "CIKIS-1" in service._auto_close_timers
    assert service.close("CIKIS-1")
    assert service._auto_close_timers == {}


def test_background_open_creates_timer_in_service_thread(monkeypatch):
    from PySide6.QtCore import QCoreApplication, QThread
    monkeypatch.setattr("app.barrier.BARRIER_AUTO_CLOSE_SEC", 7)
    service = BarrierService(MockBarrierController())

    class OpenThread(QThread):
        def run(self):
            self.result = service.open("CIKIS-1")

    worker = OpenThread()
    worker.start()
    assert worker.wait(5000)
    assert worker.result
    QCoreApplication.processEvents()
    timer = service._auto_close_timers["CIKIS-1"]
    assert timer.thread() == service.thread()
    assert service.close("CIKIS-1")


# Explicit synthetic hardware profile: no deployment is bundled with the app.
import pytest
@pytest.fixture(autouse=True)
def synthetic_metcom_profile(monkeypatch):
    monkeypatch.setattr("app.metcom_profile.CAPTURED_HOST", "192.0.2.7")
