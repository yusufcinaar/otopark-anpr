"""Fast exit traffic reaches inventory without another usable OCR frame.

All camera frames, OCR futures, clocks and hardware are local test doubles.
"""
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from PySide6.QtCore import Qt

from app import camera
from app.anpr.pipeline import PlateResult
from app.config import CameraConfig
from conftest import make_event


def _forbidden(*args, **kwargs):
    raise AssertionError("Live camera or recognition model must not run in this test")


@pytest.fixture(autouse=True)
def no_live_recognition(monkeypatch):
    monkeypatch.setattr(camera.cv2, "VideoCapture", _forbidden)
    monkeypatch.setattr(camera, "recognize_plate", _forbidden)
    monkeypatch.setattr("app.anpr.fast_engine.get_engine", _forbidden)


class Clock:
    def __init__(self):
        self.current = 1000.0

    def read(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    # Replace only this module's clock, leaving database/event timestamps real.
    monkeypatch.setattr(camera, "time", SimpleNamespace(
        time=clock.read, monotonic=clock.read, sleep=clock.advance))
    return clock


def _worker(role="exit"):
    lane = "CIKIS-1" if role == "exit" else "GIRIS-1"
    worker = camera.RtspCameraWorker(CameraConfig(
        name=lane, role=role, lane=lane, rtsp_url="test://no-camera"))
    worker._running = True
    return worker


def _result(plate="34AB123", confidence=0.96, marker=10, valid=True):
    return PlateResult(
        plate=plate, display=plate, confidence=confidence,
        is_valid_format=valid, crop=np.full((8, 24, 3), marker, dtype=np.uint8))


def _finished(result=None, error=None):
    future = Future()
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)
    return future


def _collect(worker):
    emitted = []
    worker.plate_recognized.connect(
        lambda result, lane, frame: emitted.append((result, lane, frame)),
        Qt.ConnectionType.DirectConnection)
    return emitted


def test_consecutive_fast_cars_keep_both_plates_and_their_own_frames(clock):
    worker = _worker()
    emitted = _collect(worker)
    first = np.full((24, 64, 3), 11, dtype=np.uint8)
    second = np.full((24, 64, 3), 22, dtype=np.uint8)

    worker._ocr_completed(_finished(_result("34AB123")), first)
    assert emitted == []
    first[:] = 99  # The capture buffer may be reused after the callback.
    clock.advance(0.20)
    worker._ocr_completed(_finished(_result("34AB124")), second)
    assert [event[0].plate for event in emitted] == ["34AB123"]
    second[:] = 88
    clock.advance(0.80)
    worker._emit_pending_if_due(clock.read())

    assert [event[0].plate for event in emitted] == ["34AB123", "34AB124"]
    assert [event[1] for event in emitted] == ["CIKIS-1", "CIKIS-1"]
    assert np.all(emitted[0][2] == 11)
    assert np.all(emitted[1][2] == 22)
    worker._emit_pending_if_due(clock.read() + 10)
    assert len(emitted) == 2


@pytest.mark.parametrize("next_result", ["none", "invalid", "low_confidence", "error"])
def test_confirmed_plate_publishes_when_next_ocr_is_unusable(clock, next_result):
    worker = _worker()
    emitted = _collect(worker)
    errors = []
    worker.connection_error.connect(
        lambda lane, text: errors.append(text), Qt.ConnectionType.DirectConnection)
    frame = np.full((24, 64, 3), 17, dtype=np.uint8)
    worker._ocr_completed(_finished(_result()), frame)
    clock.advance(0.80)
    next_future = {
        "none": lambda: _finished(),
        "invalid": lambda: _finished(_result("UNREADABLE", valid=False)),
        "low_confidence": lambda: _finished(_result("34AB124", confidence=0.50)),
        "error": lambda: _finished(error=RuntimeError("synthetic OCR failure")),
    }[next_result]()
    worker._ocr_completed(next_future, np.zeros_like(frame))

    assert [event[0].plate for event in emitted] == ["34AB123"]
    assert np.array_equal(emitted[0][2], frame)
    assert worker._pending_candidate is None
    assert bool(errors) is (next_result == "error")


class Capture:
    """Deterministic camera loop: small motion never clears the motion threshold."""
    def __init__(self, worker, clock, count=10):
        self.worker, self.clock = worker, clock
        self.remaining = count
        self.released = False

    def set(self, *args):
        return True

    def isOpened(self):
        return True

    def read(self):
        if not self.remaining:
            self.worker.stop()
            return False, None
        self.remaining -= 1
        self.clock.advance(0.20)
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        frame[5:7, 5:7] = 8 if self.remaining % 2 else 0
        return True, frame

    def release(self):
        self.released = True


def test_camera_deadline_publishes_even_while_ocr_future_stays_unfinished(clock, monkeypatch):
    worker = _worker()
    emitted = _collect(worker)
    frame = np.full((24, 64, 3), 31, dtype=np.uint8)
    worker._ocr_completed(_finished(_result()), frame)
    unfinished = Future()
    worker._ocr_future = unfinished
    capture = Capture(worker, clock, count=6)
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda url: capture)
    monkeypatch.setattr(camera, "_OCR_EXECUTOR", SimpleNamespace(submit=_forbidden))

    worker.run()

    assert not unfinished.done()
    assert [event[0].plate for event in emitted] == ["34AB123"]
    assert np.array_equal(emitted[0][2], frame)
    assert capture.released


@pytest.mark.parametrize("role", ["exit", "entry"])
def test_exit_keeps_scanning_when_frame_motion_is_too_weak(clock, monkeypatch, role):
    worker = _worker(role)
    capture = Capture(worker, clock, count=11)
    submitted = []

    def submit(fn, frame):
        assert fn is _forbidden
        submitted.append(clock.read())
        return _finished()

    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda url: capture)
    monkeypatch.setattr(camera, "_OCR_EXECUTOR", SimpleNamespace(submit=submit))
    worker.run()

    assert capture.released
    assert worker._motion_active_until == 0.0
    if role == "exit":
        assert len(submitted) >= 3
        assert max(b - a for a, b in zip(submitted, submitted[1:])) < 0.75
    else:
        assert len(submitted) == 1  # Entry idle CPU optimization is retained.


def test_free_pass_camera_events_remove_both_fast_exits_from_inventory(clock, svc, isolated_db):
    # The real service/database consume published camera events. Barrier/display
    # are mocked so this exercises inventory with no physical side effects.
    isolated_db.set_setting("free_pass_mode", "1")
    svc.barriers.open = MagicMock(return_value=True)
    svc.exit_display.show_idle = MagicMock()
    svc.exit_display.show_paid = MagicMock()
    for plate in ("34AB123", "34AB124"):
        assert svc.ingest_camera_event(make_event(plate=plate))["status"] == "ENTRY_OK"
    assert len(isolated_db.list_inside_sessions()) == 2
    worker = _worker()
    processed = []

    def consume(result, lane, frame):
        event = make_event(
            direction="EXIT", gate=lane, camera="LPR-CIKIS-01",
            plate=result.plate, confidence=result.confidence * 100)
        processed.append(svc.ingest_camera_event(event))

    worker.plate_recognized.connect(consume, Qt.ConnectionType.DirectConnection)
    worker._ocr_completed(_finished(_result("34AB123")), np.zeros((24, 64, 3), dtype=np.uint8))
    clock.advance(0.20)
    worker._ocr_completed(_finished(_result("34AB124")), np.ones((24, 64, 3), dtype=np.uint8))
    clock.advance(0.80)
    worker._emit_pending_if_due(clock.read())

    assert [event["status"] for event in processed] == ["FREE_PASS_EXIT", "FREE_PASS_EXIT"]
    assert isolated_db.list_inside_sessions() == []
    svc.barriers.open.assert_not_called()
    assert all(event["barrier_command_sent"] is False for event in processed)
