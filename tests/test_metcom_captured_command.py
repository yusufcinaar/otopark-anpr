"""Regressions for the command captured from the working Arma installation."""
from unittest.mock import MagicMock, call, patch

import pytest

from app.barrier import MetcomIoController, create_barrier_controller_from_device
from app.metcom_profile import CAPTURED_TRIGGER_HEX


def test_site_profile_sends_captured_bytes_after_heartbeat(isolated_db):
    isolated_db.ensure_standard_site_drivers()
    device = isolated_db.get_device_by_gate("CIKIS-1", "BARRIER")
    isolated_db.update_device(device["id"], protocol="METCOM_IO", ip="192.0.2.7", port="8080")
    isolated_db.set_setting("arma_barrier_trigger_hex", CAPTURED_TRIGGER_HEX)
    isolated_db.set_setting("arma_barrier_trigger_verified", "1")
    isolated_db.set_setting("arma_barrier_heartbeat_sec", "0")
    controller = create_barrier_controller_from_device(
        isolated_db.get_device_by_gate("CIKIS-1", "BARRIER"))
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"<PICTCPIP><dyn2>Kapali</dyn2></PICTCPIP>"
    sock = MagicMock()
    with patch("app.barrier.urllib.request.urlopen", return_value=response), \
         patch("app.barrier.socket.create_connection", return_value=sock) as connect:
        assert controller.connect() is True
        # A connection test must never send the opening command.
        assert sock.sendall.call_args_list == [call(b"\x00")]
        assert controller.trigger_ready is True
        assert controller.open_barrier("CIKIS-1") is True
        assert sock.sendall.call_args_list == [call(b"\x00"), call(b"\x02\x00\x03\x02\x03")]
        with pytest.raises(NotImplementedError, match="kapatma komutu yok"):
            controller.close_barrier("CIKIS-1")
        assert sock.sendall.call_count == 2  # No invented close command.
        controller.disconnect()
    connect.assert_called_once_with(("192.0.2.7", 8080), timeout=3.0)


@pytest.mark.parametrize("changes", [
    {"host": "192.0.2.8"}, {"tcp_port": 8081},
    {"output_channel": 1}, {"output_channel": 4}, {"trigger_verified": False},
])
def test_captured_command_cannot_be_used_with_other_profile(changes):
    options = dict(host="192.0.2.7", trigger_hex=CAPTURED_TRIGGER_HEX, trigger_verified=True, heartbeat_interval=0)
    options.update(changes)
    controller = MetcomIoController(**options)
    controller._sock = MagicMock()
    assert controller.trigger_ready is False
    with pytest.raises(RuntimeError, match="dogrulanmadi"):
        controller.open_barrier("CIKIS-1")
    controller._sock.sendall.assert_not_called()


def test_open_is_not_replayed_when_send_outcome_is_uncertain():
    controller = MetcomIoController(
        host="192.0.2.7", trigger_hex=CAPTURED_TRIGGER_HEX, trigger_verified=True, heartbeat_interval=0)
    sock = MagicMock()
    sock.sendall.side_effect = OSError("connection lost after write")
    controller._sock = sock
    with patch("app.barrier.socket.create_connection") as reconnect:
        with pytest.raises(ConnectionError, match="veri gonderilemedi"):
            controller.open_barrier("CIKIS-1")
    sock.sendall.assert_called_once_with(bytes.fromhex(CAPTURED_TRIGGER_HEX))
    sock.close.assert_called_once()
    reconnect.assert_not_called()
    assert controller._sock is None


# Explicit synthetic hardware profile: no deployment is bundled with the app.
import pytest
@pytest.fixture(autouse=True)
def synthetic_metcom_profile(monkeypatch):
    monkeypatch.setattr("app.metcom_profile.CAPTURED_HOST", "192.0.2.7")
