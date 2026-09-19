"""Free passage keeps the physical OUT3 latch during ANPR events and F4."""
from unittest.mock import MagicMock, call

from app.barrier import MetcomIoController
from app.metcom_profile import CAPTURED_HOLD_HEX, CAPTURED_RELEASE_HEX, CAPTURED_TRIGGER_HEX
from conftest import make_event


def test_car_passage_and_f4_cannot_replace_hold_with_short_pulse(svc, isolated_db):
    controller = MetcomIoController(
        host="192.0.2.7", trigger_hex=CAPTURED_TRIGGER_HEX, trigger_verified=True, heartbeat_interval=0)
    sock = MagicMock()
    controller._sock = sock
    controller._http_connected = True
    controller._read_values = MagicMock(return_value={"dyn2": "Acik"})
    svc.barriers.controller = controller
    svc.exit_display.show_idle = MagicMock()
    svc.exit_display.show_paid = MagicMock()

    assert svc.barriers.set_free_pass_mode(True, username="admin")
    assert isolated_db.get_setting("free_pass_mode") == "1"
    controller._read_values.reset_mock()
    for plate in ("34AB123", "34AB124"):
        entry = svc.ingest_camera_event(make_event(plate=plate))
        result = svc.ingest_camera_event(make_event(
            direction="EXIT", gate="CIKIS-1", camera="LPR-CIKIS-01", plate=plate))
        assert result["status"] == "FREE_PASS_EXIT"
        assert result["barrier_command_sent"] is False
        assert isolated_db.get_session(entry["session_id"])["status"] == "TAMAMLANDI"
    controller._read_values.assert_not_called()
    assert svc.barriers.manual_open("CIKIS-1", "admin", reason="F4 klavye kisayolu")
    assert sock.sendall.call_args_list == [call(bytes.fromhex(CAPTURED_HOLD_HEX))]

    assert svc.barriers.set_free_pass_mode(False, username="admin")
    assert isolated_db.get_setting("free_pass_mode") == "0"
    assert svc.barriers.manual_open("CIKIS-1", "admin", reason="F4 klavye kisayolu")
    assert sock.sendall.call_args_list == [
        call(bytes.fromhex(CAPTURED_HOLD_HEX)),
        call(bytes.fromhex(CAPTURED_RELEASE_HEX)),
        call(bytes.fromhex(CAPTURED_TRIGGER_HEX)),
    ]

import pytest
@pytest.fixture(autouse=True)
def synthetic_metcom_profile(monkeypatch):
    monkeypatch.setattr("app.metcom_profile.CAPTURED_HOST", "192.0.2.7")
