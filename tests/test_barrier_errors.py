"""Acma hatalari kullaniciya ulasir; testlerde fiziksel baglanti kurulmaz."""
from unittest.mock import MagicMock

import pytest

from app.barrier import (
    ARIZALI, BAGLANTI_YOK, KAPALI,
    BarrierService, MetcomIoController, MockBarrierController,
)


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr("app.barrier.BARRIER_AUTO_CLOSE_SEC", 0)
    controller = MagicMock()
    controller.is_connected.return_value = True
    controller.get_status.return_value = KAPALI
    controller.open_barrier.return_value = True
    return BarrierService(controller)


def assert_failed(service, isolated_db, text):
    assert text in service.last_error("CIKIS-1")
    events = isolated_db.list_barrier_events("CIKIS-1")
    assert len(events) == 1
    assert events[0]["action"] == "ACMA_HATASI"
    assert text in events[0]["note"]


def test_invalid_controller_settings_are_reported(service, isolated_db, monkeypatch):
    def invalid(_gate):
        raise ValueError("TCP portu gecersiz")

    monkeypatch.setattr(service, "_get_controller_for_gate", invalid)
    assert service.open("CIKIS-1") is False
    assert_failed(service, isolated_db, "TCP portu gecersiz")


@pytest.mark.parametrize("failure_at", ["connect", "status", "open"])
def test_network_error_is_preserved_without_retrying_status(
        service, isolated_db, failure_at):
    controller = service.controller
    if failure_at == "connect":
        controller.is_connected.return_value = False
        controller.connect.side_effect = ConnectionError("Metcom baglanti kesildi")
    elif failure_at == "status":
        controller.get_status.side_effect = ConnectionError("Metcom baglanti kesildi")
    else:
        controller.open_barrier.side_effect = ConnectionError("Metcom baglanti kesildi")
    states = []
    service.state_changed.connect(lambda gate, state: states.append((gate, state)))

    assert service.open("CIKIS-1") is False

    assert_failed(service, isolated_db, "Metcom baglanti kesildi")
    assert states == [("CIKIS-1", BAGLANTI_YOK)]
    assert controller.get_status.call_count == (0 if failure_at == "connect" else 1)
    if failure_at != "open":
        controller.open_barrier.assert_not_called()


def test_false_driver_result_is_not_logged_as_open(service, isolated_db):
    service.controller.open_barrier.return_value = False
    states = []
    service.state_changed.connect(lambda gate, state: states.append(state))

    assert service.open("CIKIS-1") is False

    assert_failed(service, isolated_db, "acma komutunu kabul etmedi")
    assert states == [ARIZALI]


def test_success_and_reload_clear_only_relevant_error(service):
    service.controller.open_barrier.side_effect = RuntimeError("acma hatasi")
    assert service.open("CIKIS-1") is False
    assert service.open("GIRIS-1") is False
    service.controller.open_barrier.side_effect = None

    assert service.open("CIKIS-1") is True
    assert service.last_error("CIKIS-1") == ""
    assert service.last_error("GIRIS-1") == "acma hatasi"
    service.reload_gate("GIRIS-1")
    assert service.last_error("GIRIS-1") == ""

    service.controller.open_barrier.side_effect = RuntimeError("acma hatasi")
    assert service.open("CIKIS-1") is False
    service.reload_all()
    assert service.last_error("CIKIS-1") == ""


def test_manual_open_status_error_is_reported(service, isolated_db):
    service.controller.get_status.side_effect = ConnectionError("durum okunamadi")

    assert service.manual_open("CIKIS-1", "admin", "Sistem arizasi") is False

    assert_failed(service, isolated_db, "durum okunamadi")
    service.controller.open_barrier.assert_not_called()


def test_metcom_missing_trigger_reports_error_without_sending_command(
        monkeypatch, isolated_db):
    controller = MetcomIoController(heartbeat_interval=0)
    controller._http_connected = True
    controller._sock = MagicMock()
    monkeypatch.setattr(controller, "get_status", lambda gate: KAPALI)
    service = BarrierService(controller)

    assert service.is_open_configured("CIKIS-1") is False
    assert service.open("CIKIS-1") is False

    assert_failed(service, isolated_db, "acma komutu dogrulanmadi")
    controller._sock.sendall.assert_not_called()


@pytest.mark.parametrize("trigger, verified, expected", [
    ("", False, False), ("03", False, False), ("03", True, True),
])
def test_readiness_checks_configuration_without_hardware_io(trigger, verified, expected):
    controller = MetcomIoController(trigger_hex=trigger, trigger_verified=verified)
    controller.connect = MagicMock(side_effect=AssertionError("No hardware I/O"))
    controller.get_status = MagicMock(side_effect=AssertionError("No hardware I/O"))
    service = BarrierService(controller)

    assert service.is_open_configured("CIKIS-1") is expected
    controller.connect.assert_not_called()
    controller.get_status.assert_not_called()


def test_other_controllers_keep_existing_readiness_behavior():
    assert BarrierService(MockBarrierController()).is_open_configured("CIKIS-1") is True
