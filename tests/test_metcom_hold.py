"""Captured OUT3 latch/release; all network access is replaced with fake sockets."""
import threading
from unittest.mock import MagicMock, call, patch

import pytest

from app.barrier import (
    ACIK, KAPALI, BarrierService, MetcomIoController, MockBarrierController,
    TcpBarrierController, create_barrier_controller_from_device,
)
from app.metcom_profile import (
    CAPTURED_TRIGGER_HEX, CAPTURED_HOLD_HEX, CAPTURED_RELEASE_HEX,
)


HOLD = bytes.fromhex(CAPTURED_HOLD_HEX)
RELEASE = bytes.fromhex(CAPTURED_RELEASE_HEX)
PULSE = bytes.fromhex(CAPTURED_TRIGGER_HEX)


def controller(**changes):
    options = dict(host="192.0.2.7", trigger_hex=CAPTURED_TRIGGER_HEX, trigger_verified=True,
                   heartbeat_interval=0)
    options.update(changes)
    ctrl = MetcomIoController(**options)
    ctrl._sock = MagicMock()
    ctrl._http_connected = True
    ctrl.get_status = MagicMock(return_value=KAPALI)
    return ctrl


def test_normal_open_remains_captured_pulse():
    ctrl = controller()
    assert ctrl.open_barrier("CIKIS-1")
    assert ctrl._sock.sendall.call_args_list == [call(PULSE)]


def test_hold_once_vehicle_and_f4_do_not_replace_latch_with_pulse(isolated_db):
    ctrl = controller()
    service = BarrierService(ctrl)
    assert service.set_free_pass_mode(True, "admin")
    assert service.open("CIKIS-1", plate="34AAA01", reason="Serbest gecis")
    assert service.manual_open("CIKIS-1", "admin", "Yonetim onayi")
    assert service.set_free_pass_mode(True, "admin")  # startup reconciliation is idempotent
    assert isolated_db.get_setting("free_pass_mode") == "1"
    assert ctrl._sock.sendall.call_args_list == [call(HOLD)]
    assert ctrl._hold_requested


def test_release_returns_normal_mode_without_claiming_physical_close(isolated_db):
    ctrl = controller()
    service = BarrierService(ctrl)
    states = []
    service.state_changed.connect(lambda *args: states.append(args))
    assert service.set_free_pass_mode(True)
    assert service.set_free_pass_mode(False)
    assert isolated_db.get_setting("free_pass_mode") == "0"
    assert states == [("CIKIS-1", ACIK)]
    assert service.open("CIKIS-1")
    assert ctrl._sock.sendall.call_args_list == [call(HOLD), call(RELEASE), call(PULSE)]
    events = isolated_db.list_barrier_events("CIKIS-1")
    assert "SERBEST_GECIS_NORMAL_MOD" in [row["action"] for row in events]
    assert "KAPANDI" not in [row["action"] for row in events]


@pytest.mark.parametrize("reload_all", [False, True])
@pytest.mark.parametrize("probe", ["status", "test_connection"])
def test_reload_cannot_restore_old_hold_during_release_commit(
        monkeypatch, isolated_db, reload_all, probe):
    ctrl = controller()
    old_socket = ctrl._sock
    service = BarrierService()
    service._gate_controllers["CIKIS-1"] = ctrl
    assert service.set_free_pass_mode(True)
    original_set_setting = isolated_db.set_setting
    release_sent = threading.Event()
    allow_commit = threading.Event()
    reload_started = threading.Event()
    reload_done = threading.Event()
    seeded_modes, results, errors = [], [], []
    new_socket = MagicMock()

    def pause_after_release(key, value):
        if key == "free_pass_mode" and value == "0":
            release_sent.set()
            assert allow_commit.wait(5)
        return original_set_setting(key, value)

    def create_replacement(device):
        held = isolated_db.get_setting("free_pass_mode") == "1"
        seeded_modes.append(held)
        replacement = controller(hold_open_requested=held)
        replacement._sock = None
        replacement._read_values = MagicMock(return_value={})
        return replacement

    def change_mode():
        try:
            results.append(service.set_free_pass_mode(False))
        except BaseException as exc:
            errors.append(exc)

    def reload_and_probe():
        reload_started.set()
        try:
            if reload_all:
                service.reload_all()
            else:
                service.reload_gate("CIKIS-1")
            getattr(service, probe)("CIKIS-1")
        except BaseException as exc:
            errors.append(exc)
        finally:
            reload_done.set()

    monkeypatch.setattr("app.barrier.db.set_setting", pause_after_release)
    monkeypatch.setattr("app.barrier.db.get_device_by_gate", lambda *args: {"protocol": "METCOM"})
    monkeypatch.setattr("app.barrier.create_barrier_controller_from_device", create_replacement)
    monkeypatch.setattr("app.barrier.socket.create_connection", lambda *args, **kwargs: new_socket)
    mode_worker = threading.Thread(target=change_mode)
    reload_worker = threading.Thread(target=reload_and_probe)
    mode_worker.start()
    try:
        assert release_sent.wait(5)
        reload_worker.start()
        assert reload_started.wait(5)
        assert not reload_done.wait(0.05), "Reload bypassed pending mode commit"
        assert not seeded_modes
    finally:
        allow_commit.set()
        mode_worker.join(5)
        if reload_worker.ident:
            reload_worker.join(5)
    assert not mode_worker.is_alive() and not reload_worker.is_alive()
    assert not errors
    assert results == [True]
    assert isolated_db.get_setting("free_pass_mode") == "0"
    assert seeded_modes == [False]
    assert old_socket.sendall.call_args_list == [call(HOLD), call(RELEASE)]
    assert new_socket.sendall.call_args_list == [call(b"\x00")]


@pytest.mark.parametrize("changes", [
    {"host": "192.0.2.8"}, {"tcp_port": 8081}, {"output_channel": 1},
    {"output_channel": 2}, {"output_channel": 4}, {"trigger_verified": False},
    {"trigger_hex": "0200010203"},
])
def test_hold_and_release_are_scoped_to_verified_captured_profile(changes):
    ctrl = controller(**changes)
    for enabled in (True, False):
        with pytest.raises(RuntimeError, match="OUT3"):
            ctrl.set_hold_open("CIKIS-1", enabled)
    ctrl._sock.sendall.assert_not_called()


def test_manual_close_is_still_unsupported_after_hold(isolated_db):
    ctrl = controller()
    service = BarrierService(ctrl)
    assert service.set_free_pass_mode(True)
    assert not service.is_close_supported("CIKIS-1")
    assert not service.close("CIKIS-1")
    assert ctrl._sock.sendall.call_args_list == [call(HOLD)]


def test_reconnect_restores_confirmed_hold_once_without_pulse():
    ctrl = controller()
    old_socket = ctrl._sock
    ctrl.set_hold_open("CIKIS-1", True)
    old_socket.sendall.side_effect = OSError("lost TCP")
    next_socket = MagicMock()
    with patch("app.barrier.socket.create_connection", return_value=next_socket) as connect:
        ctrl._send_tcp(b"\x00")
        ctrl._send_tcp(b"\x00")
        assert ctrl.open_barrier("CIKIS-1")
    connect.assert_called_once()
    assert next_socket.sendall.call_args_list == [call(HOLD), call(b"\x00"), call(b"\x00")]


def test_factory_seeds_saved_hold_and_applies_on_first_connection(isolated_db):
    isolated_db.ensure_standard_site_drivers()
    device = isolated_db.get_device_by_gate("CIKIS-1", "BARRIER")
    isolated_db.update_device(device["id"], protocol="METCOM_IO", ip="192.0.2.7", port="8080")
    isolated_db.set_setting("arma_barrier_trigger_hex", CAPTURED_TRIGGER_HEX)
    isolated_db.set_setting("arma_barrier_trigger_verified", "1")
    isolated_db.set_setting("arma_barrier_heartbeat_sec", "0")
    isolated_db.set_setting("free_pass_mode", "1")
    ctrl = create_barrier_controller_from_device(
        isolated_db.get_device_by_gate("CIKIS-1", "BARRIER"))
    assert ctrl._hold_requested
    sock = MagicMock()
    with patch("app.barrier.socket.create_connection", return_value=sock):
        ctrl._send_tcp(b"\x00")
        ctrl._send_tcp(b"\x00")
    assert sock.sendall.call_args_list == [call(HOLD), call(b"\x00"), call(b"\x00")]


def test_release_after_restart_does_not_first_reapply_hold():
    ctrl = controller(hold_open_requested=True)
    ctrl._sock = None
    sock = MagicMock()
    with patch("app.barrier.socket.create_connection", return_value=sock):
        assert ctrl.set_hold_open("CIKIS-1", False)
    assert sock.sendall.call_args_list == [call(RELEASE)]


@pytest.mark.parametrize("enabled", [True, False])
def test_uncertain_mode_change_preserves_db_and_intent_without_automatic_replay(
        enabled, isolated_db):
    ctrl = controller()
    service = BarrierService(ctrl)
    if not enabled:
        assert service.set_free_pass_mode(True)
    previous = isolated_db.get_setting("free_pass_mode", "0")
    previous_intent = ctrl._hold_requested
    old_socket = ctrl._sock
    old_socket.sendall.reset_mock()
    old_socket.sendall.side_effect = OSError("lost after write")
    states = []
    service.state_changed.connect(lambda *args: states.append(args))
    with patch("app.barrier.socket.create_connection") as reconnect:
        assert not service.set_free_pass_mode(enabled)
    reconnect.assert_not_called()
    assert isolated_db.get_setting("free_pass_mode", "0") == previous
    assert ctrl._hold_requested == previous_intent
    assert states == []
    assert "dogrulanamadi" in service.last_error("CIKIS-1")
    old_socket.sendall.assert_called_once_with(HOLD if enabled else RELEASE)
    next_socket = MagicMock()
    with patch("app.barrier.socket.create_connection", return_value=next_socket):
        ctrl._send_tcp(b"\x00")
        with pytest.raises(RuntimeError, match="dogrulanamadi"):
            ctrl.ensure_hold_open("CIKIS-1")
        with pytest.raises(RuntimeError, match="dogrulanamadi"):
            ctrl.open_barrier("CIKIS-1")
        assert next_socket.sendall.call_args_list == [call(b"\x00")]
        # An explicit operator mode choice may resolve the uncertainty.
        assert service.set_free_pass_mode(enabled)
    assert next_socket.sendall.call_args_list == [call(b"\x00"), call(HOLD if enabled else RELEASE)]


def test_uncertain_reconnect_restore_is_not_repeated_by_next_heartbeat():
    ctrl = controller(hold_open_requested=True)
    ctrl._sock = None
    bad_socket = MagicMock()
    bad_socket.sendall.side_effect = OSError("lost after write")
    next_socket = MagicMock()
    with patch("app.barrier.socket.create_connection", side_effect=[bad_socket, next_socket]):
        with pytest.raises(ConnectionError, match="dogrulanamadi"):
            ctrl._send_tcp(b"\x00")
        ctrl._send_tcp(b"\x00")
    bad_socket.sendall.assert_called_once_with(HOLD)
    next_socket.sendall.assert_called_once_with(b"\x00")


def test_db_write_failure_does_not_claim_mode_success(monkeypatch, isolated_db):
    ctrl = controller()
    service = BarrierService(ctrl)
    monkeypatch.setattr("app.barrier.db.set_setting", MagicMock(side_effect=OSError("DB unavailable")))
    states = []
    service.state_changed.connect(lambda *args: states.append(args))
    assert not service.set_free_pass_mode(True)
    assert "Komut gonderildi" in service.last_error("CIKIS-1")
    assert isolated_db.get_setting("free_pass_mode", "0") == "0"
    assert ctrl._hold_restore_blocked
    assert states == []
    ctrl._sock.sendall.assert_called_once_with(HOLD)


def test_unknown_driver_cannot_enable_free_pass(isolated_db):
    ctrl = TcpBarrierController()
    ctrl._sock = MagicMock()
    service = BarrierService(ctrl)
    assert not service.set_free_pass_mode(True)
    assert isolated_db.get_setting("free_pass_mode", "0") == "0"
    ctrl._sock.sendall.assert_not_called()


def test_mock_latch_survives_passage_close_and_releases_without_false_close(isolated_db):
    ctrl = MockBarrierController()
    service = BarrierService(ctrl)
    assert service.set_free_pass_mode(True)
    assert ctrl.get_status("CIKIS-1") == ACIK
    assert not ctrl.close_barrier("CIKIS-1")
    assert ctrl.get_status("CIKIS-1") == ACIK
    assert service.set_free_pass_mode(False)
    assert ctrl.get_status("CIKIS-1") == ACIK
    assert ctrl.close_barrier("CIKIS-1")


def test_release_and_db_commit_serialize_against_concurrent_f4(monkeypatch, isolated_db):
    ctrl = controller()
    service = BarrierService(ctrl)
    assert service.set_free_pass_mode(True)
    original_set_setting = isolated_db.set_setting
    release_sent = threading.Event()
    allow_commit = threading.Event()
    open_started = threading.Event()
    results = []

    def pause_after_release(key, value):
        if key == "free_pass_mode" and value == "0":
            release_sent.set()
            assert allow_commit.wait(5)
        return original_set_setting(key, value)

    def open_f4():
        open_started.set()
        results.append(service.open("CIKIS-1"))

    monkeypatch.setattr("app.barrier.db.set_setting", pause_after_release)
    mode_worker = threading.Thread(target=lambda: results.append(service.set_free_pass_mode(False)))
    open_worker = threading.Thread(target=open_f4)
    mode_worker.start()
    try:
        assert release_sent.wait(5)
        open_worker.start()
        assert open_started.wait(5)
        assert ctrl._sock.sendall.call_args_list == [call(HOLD), call(RELEASE)]
    finally:
        allow_commit.set()
        mode_worker.join(5)
        if open_worker.ident:
            open_worker.join(5)
    assert results == [True, True]
    assert ctrl._sock.sendall.call_args_list == [call(HOLD), call(RELEASE), call(PULSE)]
    assert isolated_db.get_setting("free_pass_mode") == "0"


# Explicit synthetic hardware profile: no deployment is bundled with the app.
import pytest
@pytest.fixture(autouse=True)
def synthetic_metcom_profile(monkeypatch):
    monkeypatch.setattr("app.metcom_profile.CAPTURED_HOST", "192.0.2.7")
