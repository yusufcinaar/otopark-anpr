"""Observed free-pass exits keep occupancy current without hardware I/O."""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from app.db import InvalidTransition
from app.utils import money_from_db, now
from tests.conftest import make_event


def prepare_session(svc, isolated_db, admin, state):
    svc.barriers.open = MagicMock(return_value=state != "ODENDI")
    entry = svc.ingest_camera_event(make_event(
        plate="34SG123", event_time=(now() - timedelta(minutes=90)).isoformat()))
    session_id = entry["session_id"]
    if state != "ICERIDE":
        svc.ingest_camera_event(make_event(direction="EXIT", gate="CIKIS-1", plate="34SG123"))
    if state in {"ODENDI", "CIKIS_IZNI"}:
        svc.record_payment(session_id, "KREDI_KARTI", user=admin)
    assert isolated_db.get_session(session_id)["status"] == state
    return session_id


@pytest.mark.parametrize("state", ["ICERIDE", "ODEME_BEKLIYOR", "ODENDI", "CIKIS_IZNI"])
def test_free_pass_exit_completes_every_active_state_without_barrier_io(
        svc, isolated_db, admin, state):
    session_id = prepare_session(svc, isolated_db, admin, state)
    before = isolated_db.get_session(session_id)
    payments = isolated_db.list_payments(100)
    updates = []
    svc.session_updated.connect(lambda: updates.append(True))
    svc.barriers.open = MagicMock(side_effect=AssertionError("Per-car barrier I/O is forbidden"))
    isolated_db.set_setting("free_pass_mode", "1")
    event = make_event(direction="EXIT", gate="CIKIS-1", camera="LPR-CIKIS-01", plate="34SG123")
    event.update(vehicle_image="exit.jpg", speed_kmh=12.5)

    result = svc.ingest_camera_event(event)

    assert result["status"] == "FREE_PASS_EXIT"
    assert result["barrier_command_sent"] is False
    after = isolated_db.get_session(session_id)
    assert after["status"] == "TAMAMLANDI"
    assert after["exit_lane"] == "CIKIS-1"
    assert after["exit_camera"] == "LPR-CIKIS-01"
    assert after["exit_image"] == "exit.jpg"
    assert after["exit_speed_kmh"] == 12.5
    assert isolated_db.get_open_session_by_plate_v2("34SG123") is None
    assert isolated_db.list_inside_sessions() == []
    assert isolated_db.list_payments(100) == payments
    assert updates == [True]
    svc.barriers.open.assert_not_called()
    if state in {"ODENDI", "CIKIS_IZNI"}:
        for key in ("fee", "fee_dec", "extra_fee_dec", "paid", "paid_at", "exit_allowed_until"):
            assert after[key] == before[key], key
    else:
        assert money_from_db(after["fee_dec"]) == 0
        assert after["extra_fee_dec"] is None
        assert after["fee"] == 0
    assert any(row["action"] == "SERBEST_GECIS_CIKISI" and
               row["target"] == f"session#{session_id}" for row in isolated_db.list_audit_logs())


def test_pending_overtime_exit_preserves_prior_payment(svc, isolated_db, admin):
    session_id = prepare_session(svc, isolated_db, admin, "CIKIS_IZNI")
    isolated_db.transition_session(session_id, "ODEME_BEKLIYOR", {
        "fee_dec": "0.00", "extra_fee_dec": "25.00"})
    before = isolated_db.get_session(session_id)
    payments = isolated_db.list_payments(100)
    isolated_db.set_setting("free_pass_mode", "1")
    svc.barriers.open = MagicMock(side_effect=AssertionError("No barrier command expected"))

    result = svc.ingest_camera_event(make_event(direction="EXIT", gate="CIKIS-1", plate="34SG123"))

    assert result["status"] == "FREE_PASS_EXIT"
    after = isolated_db.get_session(session_id)
    for key in ("fee", "fee_dec", "extra_fee_dec", "paid", "paid_at", "exit_allowed_until"):
        assert after[key] == before[key], key
    assert isolated_db.list_payments(100) == payments


def test_free_pass_inventory_does_not_depend_on_tariff(svc, isolated_db, admin):
    session_id = prepare_session(svc, isolated_db, admin, "ICERIDE")
    isolated_db.update_session_fields(session_id, {
        "tariff_snapshot": '{"type":"hourly","hourly_price":"invalid-price"}'})
    isolated_db.set_setting("free_pass_mode", "1")
    assert svc.ingest_camera_event(make_event(
        direction="EXIT", gate="CIKIS-1", plate="34SG123"))["status"] == "FREE_PASS_EXIT"


def test_delayed_free_pass_event_keeps_camera_exit_time(svc, isolated_db, admin):
    session_id = prepare_session(svc, isolated_db, admin, "ICERIDE")
    isolated_db.set_setting("free_pass_mode", "1")
    observed = now() - timedelta(minutes=5)
    result = svc.ingest_camera_event(make_event(
        direction="EXIT", gate="CIKIS-1", plate="34SG123", event_time=observed.isoformat()))
    assert result["status"] == "FREE_PASS_EXIT"
    session = isolated_db.get_session(session_id)
    assert session["exit_time"] == observed.isoformat()
    assert session["duration_minutes"] == 85


@pytest.mark.parametrize("event_time", ["bad-time", "2099-01-01T00:00:00+03:00", "2000-01-01T00:00:00+03:00"])
def test_invalid_camera_timestamp_cannot_block_free_pass_exit(svc, isolated_db, admin, event_time):
    session_id = prepare_session(svc, isolated_db, admin, "ICERIDE")
    isolated_db.set_setting("free_pass_mode", "1")
    result = svc.ingest_camera_event(make_event(
        direction="EXIT", gate="CIKIS-1", plate="34SG123", event_time=event_time))
    assert result["status"] == "FREE_PASS_EXIT"
    session = isolated_db.get_session(session_id)
    assert session["exit_time"] != event_time
    assert session["duration_minutes"] == 90


def test_completed_exit_is_idempotent_and_preserves_original_record(svc, isolated_db, admin):
    session_id = prepare_session(svc, isolated_db, admin, "ICERIDE")
    isolated_db.set_setting("free_pass_mode", "1")
    event = make_event(direction="EXIT", gate="CIKIS-1", plate="34SG123")
    assert svc.ingest_camera_event(event)["status"] == "FREE_PASS_EXIT"
    before = isolated_db.get_session(session_id)
    audits = isolated_db.list_audit_logs()

    assert svc.ingest_camera_event(event)["status"] == "DUPLICATE_EVENT"
    assert isolated_db.complete_free_pass_exit(session_id, {"exit_image": "wrong.jpg"}) is False
    assert isolated_db.get_session(session_id) == before
    assert isolated_db.list_audit_logs() == audits


@pytest.mark.parametrize("state", ["ODEME_BEKLIYOR", "ODENDI"])
def test_normal_payment_transition_guards_remain_strict(svc, isolated_db, admin, state):
    session_id = prepare_session(svc, isolated_db, admin, state)
    with pytest.raises(InvalidTransition):
        isolated_db.transition_session(session_id, "TAMAMLANDI")
    with pytest.raises(InvalidTransition, match="modu acik degil"):
        isolated_db.complete_free_pass_exit(session_id, {})
    assert isolated_db.get_session(session_id)["status"] == state


@pytest.mark.parametrize("state", ["IPTAL", "MANUEL_INCELEME", "SISTEM_HATASI", "GIRIS_BEKLIYOR"])
def test_free_pass_cannot_complete_unrelated_inactive_states(svc, isolated_db, admin, state):
    session_id = prepare_session(svc, isolated_db, admin, "ICERIDE")
    isolated_db.update_session_fields(session_id, {"status": state})
    isolated_db.set_setting("free_pass_mode", "1")
    with pytest.raises(InvalidTransition, match="gecersiz durum"):
        isolated_db.complete_free_pass_exit(session_id, {})
    assert isolated_db.get_session(session_id)["status"] == state


def test_unmatched_plate_cannot_remove_an_inside_vehicle(svc, isolated_db, admin):
    session_id = prepare_session(svc, isolated_db, admin, "ICERIDE")
    isolated_db.set_setting("free_pass_mode", "1")
    isolated_db.set_setting("unattended_mode", "1")
    result = svc.ingest_camera_event(make_event(direction="EXIT", gate="CIKIS-1", plate="34SG128"))
    assert result["status"] == "UNMATCHED_EXIT_RECORDED"
    assert isolated_db.get_session(session_id)["status"] == "ICERIDE"


def test_free_pass_has_priority_over_automatic_pulse_exit(svc, isolated_db, admin):
    prepare_session(svc, isolated_db, admin, "ICERIDE")
    isolated_db.set_setting("free_pass_mode", "1")
    isolated_db.set_setting("automatic_free_exit", "1")
    svc.barriers.open = MagicMock(side_effect=AssertionError("No pulse expected during hold"))
    result = svc.ingest_camera_event(make_event(direction="EXIT", gate="CIKIS-1", plate="34SG123"))
    assert result["status"] == "FREE_PASS_EXIT"
    svc.barriers.open.assert_not_called()
