"""Pytest ortak ayarlari: her test icin izole gecici veritabani ve QCoreApplication."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Prevent imports from loading any installed application's persistent settings.
_test_data = tempfile.TemporaryDirectory(prefix='anpr-test-session-')
os.environ['OTOPARK_DATA_DIR'] = _test_data.name

# Proje kokunu Python yoluna ekle
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# PySide6 QApplication gereksinimini minimal QCoreApplication ile gider
from PySide6.QtCore import QCoreApplication
_app = QCoreApplication.instance() or QCoreApplication(sys.argv)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Her test icin taze gecici SQLite ve data dizinleri."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "test.db"

    monkeypatch.setattr("app.config.DB_PATH", str(db_path))
    monkeypatch.setattr("app.config.CAPTURES_DIR", str(data_dir / "captures"))
    monkeypatch.setattr("app.config.SIM_PLATES_DIR", str(data_dir / "sim_plates"))
    monkeypatch.setattr("app.config.REPORTS_DIR", str(data_dir / "reports"))
    (data_dir / "captures").mkdir(exist_ok=True)
    (data_dir / "sim_plates").mkdir(exist_ok=True)
    (data_dir / "reports").mkdir(exist_ok=True)

    # db modulu import sirasinda DB_PATH'i kullanir; yeniden import etmek yerine
    # modul icerisindeki _connect'in DB_PATH'i dinamik okumasini sagla
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "_connect", _make_connect(str(db_path)))

    from app import db
    db.init_db()
    yield db


def _make_connect(db_path: str):
    """Her cagrida guncel DB_PATH'e baglanan context manager uretir."""
    import sqlite3
    import threading
    from contextlib import contextmanager
    lock = threading.Lock()

    @contextmanager
    def _connect():
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with lock:
                yield conn
                conn.commit()
        finally:
            conn.close()

    return _connect


@pytest.fixture
def svc(isolated_db):
    """ParkingService ornegi (admin kullanici ile)."""
    from app.services.parking_service import ParkingService
    s = ParkingService()
    s.current_user = {"username": "admin", "role": "sistem_yoneticisi"}
    return s


@pytest.fixture
def admin():
    return {"username": "admin", "role": "sistem_yoneticisi"}


@pytest.fixture
def cashier():
    return {"username": "kasiyer1", "role": "kasiyer"}


@pytest.fixture
def security():
    return {"username": "guv1", "role": "guvenlik"}


@pytest.fixture
def accountant():
    return {"username": "muh1", "role": "muhasebe"}


@pytest.fixture
def manager():
    return {"username": "yon1", "role": "yonetici"}


@pytest.fixture
def open_shift(isolated_db, admin):
    """Acik bir vardiya acar ve shift_id dondurur."""
    return isolated_db.open_shift("admin", "KASA-1", "500.00")


def make_event(direction="ENTRY", plate="34ABC123", confidence=96.0,
               gate="GIRIS-1", camera="LPR-GIRIS-01", event_time=None):
    """Test icin kamera olayi uretir."""
    from app.utils import new_uuid, now_iso
    return {
        "event_id": new_uuid(),
        "camera_id": camera,
        "gate_id": gate,
        "direction": direction,
        "raw_plate": plate,
        "confidence": confidence,
        "event_time": event_time or now_iso(),
        "vehicle_type": "CAR",
        "vehicle_color": "WHITE",
    }
