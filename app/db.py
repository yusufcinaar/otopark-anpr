"""SQLite tabanli basit veri erisim katmani.

Her cagri kendi kisa omurlu baglantisini acar; masaustu uygulamasi olceginde
bu, thread guvenligi icin yeterlidir (kamera thread'leri + UI thread).
"""
import hashlib
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from app import config
from app.config import DB_PATH, SPEED_LIMIT_KMH
from app.metcom_profile import (
    CAPTURED_HOST, CAPTURED_PORT, CAPTURED_TRIGGER_HEX, captured_profile_matches,
)
from app.security import protect_secret
from app.utils import now_iso, new_uuid, money_to_db, money_from_db

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_lane TEXT,
    entry_image TEXT,
    exit_time TEXT,
    exit_lane TEXT,
    exit_image TEXT,
    duration_minutes INTEGER,
    fee REAL,
    status TEXT NOT NULL DEFAULT 'OTOPARKTA',
    paid INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_plate ON sessions(plate);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate TEXT UNIQUE NOT NULL,
    name TEXT,
    phone TEXT,
    valid_until TEXT,      -- ISO tarih; NULL = suresiz abonelik
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate TEXT UNIQUE NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'kasiyer',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS camera_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    camera_id TEXT,
    gate_id TEXT,
    direction TEXT,              -- ENTRY | EXIT
    raw_plate TEXT,
    normalized_plate TEXT,
    confidence REAL,
    event_time TEXT,
    plate_image TEXT,
    vehicle_image TEXT,
    vehicle_type TEXT,
    vehicle_color TEXT,
    speed_kmh REAL,
    processed INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_camera_events_plate ON camera_events(normalized_plate);

CREATE TABLE IF NOT EXISTS tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    rules_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_uuid TEXT UNIQUE NOT NULL,
    session_id INTEGER NOT NULL,
    plate TEXT NOT NULL,
    calculated_amount TEXT NOT NULL,   -- Decimal metin (float kullanilmaz)
    collected_amount TEXT NOT NULL,
    method TEXT NOT NULL,              -- NAKIT | KREDI_KARTI | BANKA_KARTI | HAVALE_EFT | ABONE_HESABI
    cash_received TEXT,
    change_given TEXT,
    cashier TEXT,
    register TEXT,
    shift_id INTEGER,
    paid_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'TAMAM',  -- TAMAM | IPTAL | IADE
    note TEXT,
    cancelled_by TEXT,
    cancelled_at TEXT,
    cancel_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_session ON payments(session_id);
CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments(paid_at);

CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cashier TEXT NOT NULL,
    register TEXT NOT NULL DEFAULT 'KASA-1',
    opened_at TEXT NOT NULL,
    opening_cash TEXT NOT NULL DEFAULT '0.00',
    closed_at TEXT,
    counted_cash TEXT,
    expected_cash TEXT,
    difference TEXT,
    difference_note TEXT,
    status TEXT NOT NULL DEFAULT 'ACIK'   -- ACIK | KAPALI
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    ts TEXT NOT NULL,
    ip TEXT,
    action TEXT NOT NULL,
    target TEXT,
    old_value TEXT,
    new_value TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS email_recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'TO',  -- TO | CC
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS report_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'GUNLUK',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    recipients TEXT,
    files TEXT,
    status TEXT NOT NULL DEFAULT 'HAZIRLANIYOR',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    next_retry TEXT
);
CREATE INDEX IF NOT EXISTS idx_report_logs_date ON report_logs(report_date);

CREATE TABLE IF NOT EXISTS barrier_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate TEXT,
    action TEXT,
    state_before TEXT,
    username TEXT,
    plate TEXT,
    session_id INTEGER,
    reason TEXT,
    note TEXT,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,             -- CAMERA | BARRIER | SENSOR | GATE
    gate TEXT,                      -- iliskili kapi (GIRIS-1, CIKIS-1, ...)
    ip TEXT,
    port TEXT,
    rtsp_url TEXT,
    direction TEXT,                 -- ENTRY | EXIT | (bos)
    online INTEGER NOT NULL DEFAULT 1,
    last_seen TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_devices_kind ON devices(kind);
CREATE INDEX IF NOT EXISTS idx_devices_gate ON devices(gate);
"""

# sessions tablosuna guvenli (veri kaybetmeyen) sekilde eklenen yeni kolonlar
_SESSION_MIGRATION_COLUMNS = [
    ("session_uuid", "TEXT"),
    ("raw_plate", "TEXT"),
    ("confidence", "REAL"),
    ("entry_camera", "TEXT"),
    ("exit_camera", "TEXT"),
    ("vehicle_type", "TEXT"),
    ("vehicle_color", "TEXT"),
    ("is_subscriber", "INTEGER DEFAULT 0"),
    ("tariff_name", "TEXT"),
    ("tariff_version", "INTEGER"),
    ("tariff_snapshot", "TEXT"),
    ("fee_dec", "TEXT"),
    ("extra_fee_dec", "TEXT"),
    ("paid_at", "TEXT"),
    ("exit_allowed_until", "TEXT"),
    ("created_by", "TEXT"),
    ("entry_speed_kmh", "REAL"),
    ("exit_speed_kmh", "REAL"),
]

_CAMERA_EVENT_MIGRATION_COLUMNS = [
    ("speed_kmh", "REAL"),
]

_SUBSCRIBER_MIGRATION_COLUMNS = [
    ("email", "TEXT"),
    ("start_date", "TEXT"),
    ("fee_dec", "TEXT"),
    ("payment_status", "TEXT DEFAULT 'ODENDI'"),
    ("allowed_gates", "TEXT"),
    ("allowed_days", "TEXT"),
    ("allowed_hours", "TEXT"),
    ("active", "INTEGER DEFAULT 1"),
]

# devices tablosuna bariyer/dijital ekran protokol ayarlari
_DEVICE_MIGRATION_COLUMNS = [
    ("protocol", "TEXT"),        # SERIAL | TCP | HTTP | GPIO | MOCK
    ("baud_rate", "INTEGER"),
    ("open_cmd", "TEXT"),        # bariyer acma komutu (ornek: "OPEN\n")
    ("close_cmd", "TEXT"),       # bariyer kapatma komutu (ornek: "CLOSE\n")
    ("status_cmd", "TEXT"),      # durum sorgu komutu (ornek: "STATUS?\n")
    ("open_response", "TEXT"),   # acma basarili yaniti (ornek: "OK")
    ("close_response", "TEXT"),  # kapatma basarili yaniti
    ("http_open_url", "TEXT"),   # HTTP modunda acma URL'i
    ("http_close_url", "TEXT"),  # HTTP modunda kapatma URL'i
    ("http_status_url", "TEXT"),  # HTTP modunda durum URL
    ("gpio_pin", "INTEGER"),     # GPIO modunda pin numarasi
    ("poll_interval", "INTEGER DEFAULT 5"),  # durum sorgulama araligi (sn)
    ("onvif_port", "INTEGER DEFAULT 80"),
    ("onvif_username", "TEXT"),
    ("onvif_password_enc", "TEXT"),
    ("onvif_profile_token", "TEXT"),
    ("rtsp_port", "INTEGER DEFAULT 554"),
]

# Park oturumu durumlari ve izin verilen gecisler
SESSION_STATUSES = [
    "GIRIS_BEKLIYOR", "ICERIDE", "ODEME_BEKLIYOR", "ODENDI",
    "CIKIS_IZNI", "TAMAMLANDI", "IPTAL", "MANUEL_INCELEME", "SISTEM_HATASI",
]

VALID_TRANSITIONS = {
    "GIRIS_BEKLIYOR": {"ICERIDE", "IPTAL", "SISTEM_HATASI"},
    "ICERIDE": {"ODEME_BEKLIYOR", "MANUEL_INCELEME", "IPTAL", "SISTEM_HATASI", "TAMAMLANDI"},
    # TAMAMLANDI'ya dogrudan gecis yalnizca abone/serbest gecis (odeme gerektirmeyen) icindir
    "ODEME_BEKLIYOR": {"ODENDI", "MANUEL_INCELEME", "IPTAL", "SISTEM_HATASI", "ICERIDE"},
    "ODENDI": {"CIKIS_IZNI", "ODEME_BEKLIYOR", "SISTEM_HATASI"},
    "CIKIS_IZNI": {"TAMAMLANDI", "ODEME_BEKLIYOR", "SISTEM_HATASI"},
    "TAMAMLANDI": set(),           # tekrar acmak yonetici yetkisi ile ozel fonksiyondan yapilir
    "IPTAL": set(),
    "MANUEL_INCELEME": {"ICERIDE", "ODEME_BEKLIYOR", "TAMAMLANDI", "IPTAL"},
    "SISTEM_HATASI": {"ICERIDE", "ODEME_BEKLIYOR", "MANUEL_INCELEME", "IPTAL"},
}


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        with _lock:
            yield conn
            conn.commit()
    finally:
        conn.close()


def _migrate_add_columns(conn, table: str, columns):
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _migrate_statuses(conn):
    """Eski durum adlarini yeni durum modeline tasir (veri kaybi olmadan)."""
    conn.execute("UPDATE sessions SET status='ICERIDE' WHERE status='OTOPARKTA'")
    conn.execute("UPDATE sessions SET status='ODEME_BEKLIYOR' WHERE status='ODEME_BEKLENIYOR'")


def init_db():
    with _connect() as conn:
        # Kamera thread'leri okuma/yazma yaparken arayuz sorgularinin birbirini
        # kilitlemesini azaltir. WAL mevcut veriyi silmeden etkinlesir.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        _migrate_add_columns(conn, "sessions", _SESSION_MIGRATION_COLUMNS)
        _migrate_add_columns(conn, "camera_events", _CAMERA_EVENT_MIGRATION_COLUMNS)
        _migrate_add_columns(conn, "subscribers", _SUBSCRIBER_MIGRATION_COLUMNS)
        _migrate_add_columns(conn, "devices", _DEVICE_MIGRATION_COLUMNS)
        _migrate_statuses(conn)
        report_v2 = conn.execute(
            "SELECT value FROM system_settings WHERE key='report_window_0900_v2'"
        ).fetchone()
        if not report_v2:
            conn.execute(
                "INSERT INTO system_settings(key,value) VALUES('report_time','09:00') "
                "ON CONFLICT(key) DO UPDATE SET value='09:00'"
            )
            conn.execute(
                "INSERT INTO system_settings(key,value) VALUES('report_window_0900_v2','1')"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_entry_time ON sessions(entry_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_exit_time ON sessions(exit_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status_entry ON sessions(status, entry_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camera_events_event_time ON camera_events(event_time)")
        seeded = conn.execute(
            "SELECT value FROM system_settings WHERE key='standard_cameras_seeded_v1'"
        ).fetchone()
        if not seeded:
            camera_password = protect_secret("")
            standards = [
                ("LPR-GIRIS-01", "GIRIS-1", "", "ENTRY"),
                ("LPR-GIRIS-02", "GIRIS-2", "", "ENTRY"),
                # Arma'daki CIKIS 2, tesisteki tek fiziksel cikistir.
                ("LPR-CIKIS-01", "CIKIS-1", "", "EXIT"),
            ]
            for name, gate, ip, direction in standards:
                exists = conn.execute(
                    "SELECT 1 FROM devices WHERE kind='CAMERA' AND gate=?", (gate,)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """INSERT INTO devices
                       (name, kind, gate, ip, port, rtsp_url, direction, online,
                        note, created_at, protocol, onvif_port, onvif_username,
                        onvif_password_enc, rtsp_port)
                       VALUES (?, 'CAMERA', ?, ?, '80', '', ?, 0, ?, ?,
                               'ONVIF', 80, '', ?, 554)""",
                    (name, gate, ip, direction,
                     "Standart tesis kamerasi; ONVIF akisi otomatik bulunur",
                     _now(), camera_password),
                )
            conn.execute(
                "INSERT INTO system_settings(key,value) VALUES('standard_cameras_seeded_v1','1')"
            )
    _seed_default_admin()
    _seed_default_tariff()
    _seed_default_settings()
    ensure_captured_metcom_command()
    ensure_saved_metcom_profile()


def ensure_captured_metcom_command() -> bool:
    """Public distribution never imports a deployment-specific command."""
    return False


def ensure_saved_metcom_profile() -> bool:
    """Public distribution never restores a deployment-specific device."""
    return False


def ensure_standard_site_drivers():
    """Tesisteki Metcom bariyer ve LED suruculerini bir kez otomatik tanimlar.

    Isaret kaydi sayesinde kullanicinin daha sonra arayuzden yaptigi degisiklikler
    uygulama her acildiginda ezilmez.
    """
    marker = "standard_metcom_led_drivers_v1"
    if get_setting(marker, "0") == "1":
        ensure_captured_metcom_command()
        ensure_saved_metcom_profile()
        return

    barrier = get_device_by_gate("CIKIS-1", "BARRIER")
    barrier_fields = {
        "name": "METCOM IO - CIKIS BARIYERI",
        "ip": "",
        "port": "8080",
        "direction": "EXIT",
        "online": 0,
        "protocol": "MOCK",
        "http_status_url": "",
        "note": "Metcom I/O; Bariyer3 (OUT3), Loop1; durum/loop surucusu",
    }
    if barrier:
        update_device(barrier["id"], **barrier_fields)
    else:
        add_device(
            kind="BARRIER", gate="CIKIS-1",
            poll_interval=5, **barrier_fields,
        )

    site_settings = {
        "arma_barrier_ip": "",
        "arma_barrier_output": "3",
        "arma_barrier_tcp_port": "8080",
        "arma_metcom_status_url": "",
        "arma_loop_device": "USB 1",
        "arma_loop_contact": "NO",
        "arma_loop_wait_sec": "7",
        "arma_led_type": "Tip2",
        "arma_led_ip": "",
        "arma_led_port": "6101",
        "arma_led_enabled": "0",
        "arma_led_size": "64x32",
        "arma_led_panel": "Tek Renk",
        "arma_led_idle_text": "DEMO\nOTOPARK",
    }
    for key, value in site_settings.items():
        set_setting(key, value)
    set_setting(marker, "1")
    ensure_captured_metcom_command()
    ensure_saved_metcom_profile()


def _now():
    return now_iso()  # Europe/Istanbul, saniye hassasiyetinde


def create_entry(plate: str, lane: str, image_path: str = "") -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO sessions (plate, entry_time, entry_lane, entry_image,
                                      status, paid, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'OTOPARKTA', 0, ?, ?)""",
            (plate, now, lane, image_path, now, now),
        )
        return cur.lastrowid


def get_open_session_by_plate(plate: str):
    """Ayni plaka icin en son acik (henuz cikis yapmamis) kaydi getirir."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM sessions WHERE plate = ? AND status = 'OTOPARKTA'
               ORDER BY entry_time DESC LIMIT 1""",
            (plate,),
        ).fetchone()
        return dict(row) if row else None


def get_session(session_id: int):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None


def set_exit_pending_payment(session_id: int, lane: str, image_path: str,
                              duration_minutes: int, fee: float):
    now = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE sessions SET exit_time=?, exit_lane=?, exit_image=?,
               duration_minutes=?, fee=?, status='ODEME_BEKLENIYOR', updated_at=?
               WHERE id=?""",
            (now, lane, image_path, duration_minutes, fee, now, session_id),
        )


def mark_paid_and_closed(session_id: int):
    now = _now()
    with _connect() as conn:
        conn.execute(
            """UPDATE sessions SET paid=1, status='TAMAMLANDI', updated_at=?
               WHERE id=?""",
            (now, session_id),
        )


def create_unknown_exit(plate: str, lane: str, image_path: str) -> int:
    """Girisi olmayan/eslesmeyen bir plaka cikista okunursa manuel inceleme icin kayit acar."""
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO sessions (plate, entry_time, entry_lane, exit_time, exit_lane,
                                      exit_image, status, paid, created_by, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?, 'MANUEL_INCELEME', 0, 'UNKNOWN_EXIT', ?, ?)""",
            (plate, now, now, lane, image_path, now, now),
        )
        return cur.lastrowid


def list_recent_sessions(limit: int = 100):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def today_revenue() -> float:
    today = datetime.now().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(fee), 0) AS total FROM sessions
               WHERE paid = 1 AND exit_time LIKE ?""",
            (f"{today}%",),
        ).fetchone()
        return float(row["total"])


# ------------------------------------------------------------- ABONELER ----
def add_subscriber(plate: str, name: str, phone: str, valid_until: str | None, **extra):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO subscribers (plate, name, phone, valid_until, email, allowed_gates,
                   allowed_days, allowed_hours, active, payment_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(plate) DO UPDATE SET name=excluded.name, phone=excluded.phone,
               valid_until=excluded.valid_until, email=excluded.email,
               allowed_gates=excluded.allowed_gates, allowed_days=excluded.allowed_days,
               allowed_hours=excluded.allowed_hours, active=excluded.active,
               payment_status=excluded.payment_status""",
            (plate, name, phone, valid_until, extra.get("email", ""),
             extra.get("allowed_gates", ""), extra.get("allowed_days", ""),
             extra.get("allowed_hours", ""), 1 if extra.get("active", True) else 0,
             extra.get("payment_status", "ODENDI"), _now()),
        )


def remove_subscriber(subscriber_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM subscribers WHERE id = ?", (subscriber_id,))


def list_subscribers():
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM subscribers ORDER BY plate").fetchall()
        return [dict(r) for r in rows]


def get_active_subscriber(plate: str):
    today = datetime.now().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM subscribers WHERE plate = ?
               AND (valid_until IS NULL OR valid_until = '' OR valid_until >= ?)""",
            (plate, today),
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------- KARA LISTE ----
def add_blacklist(plate: str, reason: str):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO blacklist (plate, reason, created_at) VALUES (?, ?, ?)
               ON CONFLICT(plate) DO UPDATE SET reason=excluded.reason""",
            (plate, reason, _now()),
        )


def remove_blacklist(entry_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM blacklist WHERE id = ?", (entry_id,))


def list_blacklist():
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM blacklist ORDER BY plate").fetchall()
        return [dict(r) for r in rows]


def is_blacklisted(plate: str):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM blacklist WHERE plate = ?", (plate,)).fetchone()
        return dict(row) if row else None


# -------------------------------------------------------- KULLANICILAR ----
_PASSWORD_ITERATIONS = 310_000


def _legacy_hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: str, iterations: int = _PASSWORD_ITERATIONS) -> str:
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${derived.hex()}"


def _password_matches(password: str, stored: str, salt: str) -> bool:
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _algorithm, iterations, expected = stored.split("$", 2)
            actual = _hash_password(password, salt, int(iterations)).split("$", 2)[2]
            return secrets.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False
    return secrets.compare_digest(_legacy_hash_password(password, salt), stored)


def create_user(username: str, password: str, role: str = "kasiyer"):
    salt = secrets.token_hex(16)
    with _connect() as conn:
        conn.execute(
            """INSERT INTO users (username, password_hash, salt, role, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (username, _hash_password(password, salt), salt, role, _now()),
        )


def remove_user(user_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def update_user_password(user_id: int, password: str):
    salt = secrets.token_hex(16)
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash=?, salt=? WHERE id=?",
            (_hash_password(password, salt), salt, user_id),
        )


def list_users():
    with _connect() as conn:
        rows = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
        return [dict(r) for r in rows]


def verify_user(username: str, password: str):
    """Basariliysa {'username':..., 'role':...} dondurur, degilse None."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        stored = row["password_hash"] or ""
        valid = _password_matches(password, stored, row["salt"])
        if valid and not stored.startswith("pbkdf2_sha256$"):
            # Eski SHA-256 kaydini basarili giriste veri kaybetmeden guclendir.
            new_salt = secrets.token_hex(16)
            conn.execute(
                "UPDATE users SET password_hash=?, salt=? WHERE id=?",
                (_hash_password(password, new_salt), new_salt, row["id"]),
            )
        if valid:
            return {"username": row["username"], "role": row["role"]}
        return None


def _seed_default_admin():
    """Temiz kurulumda admin/admin olusturur; eski varsayilani bir kez tasir.

    Degistirilmis sifreler, kullanici rolleri ve diger hesaplar korunur.
    Gecis isareti, daha sonra bilerek secilen bir sifrenin tekrar ezilmesini onler.
    """
    marker = "default_admin_password_20260919_v1"
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count == 0:
            salt = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO users(username,password_hash,salt,role,created_at) "
                "VALUES(?,?,?,?,?)",
                ("admin", _hash_password("admin", salt), salt,
                 "sistem_yoneticisi", _now()),
            )
        migrated = conn.execute(
            "SELECT value FROM system_settings WHERE key=?", (marker,),
        ).fetchone()
        if migrated and migrated["value"] == "1":
            return
        row = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
        if count and row and _password_matches("admin123", row["password_hash"] or "", row["salt"]):
            salt = secrets.token_hex(16)
            conn.execute(
                "UPDATE users SET password_hash=?, salt=? WHERE id=?",
                (_hash_password("admin", salt), salt, row["id"]),
            )
        conn.execute(
            "INSERT INTO system_settings(key,value) VALUES(?,'1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'", (marker,),
        )


# ------------------------------------------------------ SISTEM AYARLARI ----
_DEFAULT_SETTINGS = {
    "location_name": "DEMO OTOPARK",
    "confidence_auto": "90",        # bu oranin ustunde otomatik islem
    "confidence_operator": "70",    # bu araliktaki okumalar operator onayina duser
    "exit_grace_minutes": "15",     # odeme sonrasi cikis suresi
    "report_time": "09:00",
    "report_active": "0",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_provider": "custom",
    "smtp_auth_mode": "password",
    "smtp_username": "",
    "smtp_password_enc": "",
    "smtp_from_email": "",
    "smtp_from_name": "Otopark Yonetim Sistemi",
    "smtp_use_tls": "1",
    "smtp_use_ssl": "0",
    "image_retention_days": "90",
    "speed_limit_kmh": str(SPEED_LIMIT_KMH),  # 0 = alarm kapali; kameranin olctugu km/sa
    "speed_estimation_scale": "3.0",  # sabit otopark kamera acisi icin baslangic katsayisi
    "free_pass_mode": "0",          # 1 = bariyerler acik, cikista ucret yok
    "unattended_mode": "0",          # ana uygulama acilisinda 1: operator onayi beklenmez
    "unattended_min_confidence": "50",
    "automatic_free_exit": "0",      # operator olmayan kurulumda odemesiz otomatik cikis
    # Arma PTS ekranindan alinmis saha donanim profili. Degerler arayuzden
    # degistirilebilir; uretici haberlesme komutu dogrulanmadan role tetiklenmez.
    "arma_barrier_type": "Ethernet",
    "arma_barrier_ip": "",
    "arma_barrier_output": "3",
    "arma_barrier_shortcut": "F4",
    "arma_barrier_tcp_port": "8080",
    "arma_barrier_heartbeat_hex": "00",
    "arma_barrier_heartbeat_sec": "5",
    # Yakalanmis acma komutu yalnizca eslesen saha cihazina tek seferlik
    # ensure_captured_metcom_command gecisiyle uygulanir.
    "arma_barrier_trigger_hex": "",
    "arma_barrier_trigger_verified": "0",
    "arma_metcom_status_url": "",
    "arma_loop_device": "USB 1",
    "arma_loop_port": "",
    "arma_loop_contact": "NO",
    "arma_loop_wait_sec": "7",
    "arma_alarm_device": "USB 1",
    "arma_alarm_port": "",
    "arma_led_type": "Tip2",
    "arma_led_ip": "",
    "arma_led_port": "6101",
    "arma_led_enabled": "0",
    "arma_led_size": "64x32",
    "arma_led_panel": "Tek Renk",
    "arma_led_duration_sec": "5",
    "arma_led_border": "0",
    "arma_led_show_speed": "0",
    "arma_led_idle_text": "DEMO\nOTOPARK",
}


def _seed_default_settings():
    with _connect() as conn:
        for k, v in _DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)", (k, v))


def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# -------------------------------------------------------------- TARIFELER ----
DEFAULT_TARIFF_RULES = {
    "type": "blocks",
    "free_minutes": 15,
    "blocks": [
        {"upto_minutes": 60, "price": "80"},
        {"upto_minutes": 120, "price": "120"},
        {"upto_minutes": 240, "price": "180"},
        {"upto_minutes": 480, "price": "250"},
        {"upto_minutes": 720, "price": "350"},
        {"upto_minutes": 1440, "price": "500"},
    ],
    "extra_day_price": "500",
    "currency": "TL",
}


def _seed_default_tariff():
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM tariffs").fetchone()["c"]
        if count == 0:
            conn.execute(
                "INSERT INTO tariffs (name, version, rules_json, active, created_at) VALUES (?, 1, ?, 1, ?)",
                ("Standart Tarife", json.dumps(DEFAULT_TARIFF_RULES), _now()),
            )


def get_active_tariff():
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tariffs WHERE active = 1 ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        t = dict(row)
        t["rules"] = json.loads(t["rules_json"])
        return t


def list_tariffs():
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM tariffs ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def save_new_tariff_version(name: str, rules: dict) -> int:
    """Tarife degisikligi yeni surum olarak kaydedilir; eski surumler korunur
    (gecmis kayitlar giriste alinan snapshot ile hesaplandigi icin etkilenmez)."""
    with _connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(version),0) AS v FROM tariffs WHERE name = ?", (name,)).fetchone()
        version = row["v"] + 1
        conn.execute("UPDATE tariffs SET active = 0")
        cur = conn.execute(
            "INSERT INTO tariffs (name, version, rules_json, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (name, version, json.dumps(rules), _now()),
        )
        return cur.lastrowid


# --------------------------------------------------- KAMERA OLAYLARI ----
def record_camera_event(event: dict) -> bool:
    """Kamera olayini kaydeder. event_id daha once islendiyse False dondurur (idempotency)."""
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO camera_events (event_id, camera_id, gate_id, direction, raw_plate,
                       normalized_plate, confidence, event_time, plate_image, vehicle_image,
                       vehicle_type, vehicle_color, speed_kmh, processed, result, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)""",
                (event["event_id"], event.get("camera_id"), event.get("gate_id"),
                 event.get("direction"), event.get("raw_plate"), event.get("normalized_plate"),
                 event.get("confidence"), event.get("event_time"), event.get("plate_image"),
                 event.get("vehicle_image"), event.get("vehicle_type"), event.get("vehicle_color"),
                 event.get("speed_kmh"),
                 _now()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def mark_camera_event_processed(event_id: str, result: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE camera_events SET processed = 1, result = ? WHERE event_id = ?",
            (result, event_id),
        )


def list_camera_events(limit: int = 200):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM camera_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------- OTURUM DURUM GECISI ----
class InvalidTransition(Exception):
    pass


def transition_session(session_id: int, new_status: str, extra_fields: dict | None = None):
    """Durum gecisini VALID_TRANSITIONS'a gore dogrulayarak uygular."""
    with _connect() as conn:
        row = conn.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise InvalidTransition(f"Oturum bulunamadi: {session_id}")
        current = row["status"]
        if new_status not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransition(f"Gecersiz durum gecisi: {current} -> {new_status}")
        fields = {"status": new_status, "updated_at": _now()}
        if extra_fields:
            fields.update(extra_fields)
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), session_id))


def complete_free_pass_exit(session_id: int, exit_fields: dict) -> bool:
    """Kamerada gorulen serbest gecisi ucretli cikis kurallarindan ayri tamamlar.

    Onceki tahsilatlar korunur. Yalnizca henuz odemesi olmayan kaydin bekleyen
    ucreti kaldirilir; durum ve ucret degisikligi ayni islemde audit'e yazilir.
    """
    allowed_fields = {
        "exit_time", "exit_lane", "exit_image", "exit_camera",
        "exit_speed_kmh", "duration_minutes",
    }
    if set(exit_fields) - allowed_fields:
        raise ValueError("Serbest gecis icin gecersiz cikis alanlari")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise InvalidTransition(f"Oturum bulunamadi: {session_id}")
        if row["status"] == "TAMAMLANDI":
            return False
        mode = conn.execute(
            "SELECT value FROM system_settings WHERE key='free_pass_mode'").fetchone()
        if not mode or mode["value"] != "1":
            raise InvalidTransition("Serbest gecis modu acik degil")
        if row["status"] not in {"ICERIDE", "ODEME_BEKLIYOR", "ODENDI", "CIKIS_IZNI"}:
            raise InvalidTransition(f"Serbest gecis icin gecersiz durum: {row['status']}")

        collected = conn.execute(
            "SELECT 1 FROM payments WHERE session_id=? AND status='TAMAM' LIMIT 1",
            (session_id,),
        ).fetchone()
        preserve_payment = bool(collected or row["paid"] or
                                row["status"] in {"ODENDI", "CIKIS_IZNI"})
        timestamp = _now()
        fields = {**exit_fields, "status": "TAMAMLANDI", "updated_at": timestamp}
        if not preserve_payment:
            fields.update(fee=0.0, fee_dec=money_to_db(0), extra_fee_dec=None, paid=1)
        sets = ", ".join(f"{key}=?" for key in fields)
        conn.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), session_id))
        old = {key: row[key] for key in ("status", "fee_dec", "extra_fee_dec", "paid")}
        note = ("Kamerada serbest gecis cikisi goruldu; onceki odeme bilgileri korundu."
                if preserve_payment else
                "Kamerada serbest gecis cikisi goruldu; bekleyen ucret kaldirildi, tahsilat yapilmadi.")
        conn.execute(
            """INSERT INTO audit_logs (username, ts, action, target, old_value, new_value, note)
               VALUES ('KAMERA', ?, 'SERBEST_GECIS_CIKISI', ?, ?, 'TAMAMLANDI', ?)""",
            (timestamp, f"session#{session_id}", json.dumps(old, ensure_ascii=False), note),
        )
        return True


def update_session_fields(session_id: int, fields: dict):
    fields = dict(fields)
    fields["updated_at"] = _now()
    with _connect() as conn:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), session_id))


def create_entry_full(**kw) -> int:
    """Genisletilmis giris kaydi: tarife snapshot, kamera, guven orani vb. ile."""
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO sessions (plate, raw_plate, entry_time, entry_lane, entry_image,
                   entry_camera, confidence, vehicle_type, vehicle_color, is_subscriber,
                   tariff_name, tariff_version, tariff_snapshot, session_uuid, created_by, entry_speed_kmh,
                   status, paid, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ICERIDE', 0, ?, ?)""",
            (kw["plate"], kw.get("raw_plate", ""), kw.get("entry_time") or now,
             kw.get("lane", ""), kw.get("image_path", ""), kw.get("camera", ""),
             kw.get("confidence"), kw.get("vehicle_type"), kw.get("vehicle_color"),
             1 if kw.get("is_subscriber") else 0, kw.get("tariff_name"),
             kw.get("tariff_version"), kw.get("tariff_snapshot"),
             new_uuid(), kw.get("created_by", "SISTEM"), kw.get("speed_kmh"), now, now),
        )
        return cur.lastrowid


def list_inside_sessions():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE status = 'ICERIDE' ORDER BY entry_time DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_open_session_by_plate_v2(plate: str):
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM sessions WHERE plate = ? AND status IN ('ICERIDE','ODEME_BEKLIYOR','ODENDI','CIKIS_IZNI')
               ORDER BY entry_time DESC LIMIT 1""",
            (plate,),
        ).fetchone()
        return dict(row) if row else None


def sessions_between(start_iso: str, end_iso: str):
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM sessions WHERE (entry_time BETWEEN ? AND ?)
               OR (exit_time BETWEEN ? AND ?) ORDER BY id""",
            (start_iso, end_iso, start_iso, end_iso),
        ).fetchall()
        return [dict(r) for r in rows]


# ----------------------------------------------------------- ODEMELER ----
def create_payment(**kw) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO payments (payment_uuid, session_id, plate, calculated_amount,
                   collected_amount, method, cash_received, change_given, cashier, register,
                   shift_id, paid_at, status, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TAMAM', ?)""",
            (new_uuid(), kw["session_id"], kw["plate"], kw["calculated_amount"],
             kw["collected_amount"], kw["method"], kw.get("cash_received"),
             kw.get("change_given"), kw.get("cashier"), kw.get("register", "KASA-1"),
             kw.get("shift_id"), _now(), kw.get("note", "")),
        )
        return cur.lastrowid


def cancel_payment(payment_id: int, cancelled_by: str, reason: str):
    """Odeme silinmez; iptal kaydiyla kapatilir."""
    with _connect() as conn:
        conn.execute(
            "UPDATE payments SET status='IPTAL', cancelled_by=?, cancelled_at=?, cancel_reason=? WHERE id=?",
            (cancelled_by, _now(), reason, payment_id),
        )


def list_payments(limit: int = 300):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def payments_between(start_iso: str, end_iso: str):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE paid_at BETWEEN ? AND ? ORDER BY id", (start_iso, end_iso)
        ).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------ VARDIYALAR ----
def open_shift(cashier: str, register: str, opening_cash: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO shifts (cashier, register, opened_at, opening_cash, status) VALUES (?, ?, ?, ?, 'ACIK')",
            (cashier, register, _now(), opening_cash),
        )
        return cur.lastrowid


def get_open_shift(cashier: str):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM shifts WHERE cashier = ? AND status = 'ACIK' ORDER BY id DESC LIMIT 1",
            (cashier,),
        ).fetchone()
        return dict(row) if row else None


def close_shift(shift_id: int, counted_cash: str, expected_cash: str, difference: str, note: str):
    with _connect() as conn:
        conn.execute(
            """UPDATE shifts SET closed_at=?, counted_cash=?, expected_cash=?, difference=?,
               difference_note=?, status='KAPALI' WHERE id=? AND status='ACIK'""",
            (_now(), counted_cash, expected_cash, difference, note, shift_id),
        )


def shift_payment_totals(shift_id: int) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT method, status, COUNT(*) AS cnt, GROUP_CONCAT(collected_amount) AS amounts
               FROM payments WHERE shift_id = ? GROUP BY method, status""",
            (shift_id,),
        ).fetchall()
    totals = {}
    for r in rows:
        amounts = [money_from_db(a) for a in (r["amounts"] or "").split(",") if a]
        key = f"{r['method']}_{r['status']}"
        totals[key] = str(sum(amounts)) if amounts else "0.00"
    return totals


def list_shifts(limit: int = 100):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------ AUDIT LOG ----
def audit(username: str, action: str, target: str = "", old_value: str = "",
          new_value: str = "", note: str = "", ip: str = ""):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO audit_logs (username, ts, ip, action, target, old_value, new_value, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, _now(), ip, action, target, old_value, new_value, note),
        )


def list_audit_logs(limit: int = 500):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------- BARIYER OLAYLARI ----
def log_barrier_event(gate: str, action: str, state_before: str, username: str = "",
                       plate: str = "", session_id: int | None = None,
                       reason: str = "", note: str = ""):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO barrier_events (gate, action, state_before, username, plate,
                   session_id, reason, note, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (gate, action, state_before, username, plate, session_id, reason, note, _now()),
        )


def barrier_events_between(start_iso: str, end_iso: str):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM barrier_events WHERE ts BETWEEN ? AND ? ORDER BY id", (start_iso, end_iso)
        ).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------- E-POSTA / RAPORLAR ----
def list_email_recipients(active_only: bool = True):
    with _connect() as conn:
        q = "SELECT * FROM email_recipients"
        if active_only:
            q += " WHERE active = 1"
        rows = conn.execute(q + " ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def add_email_recipient(email: str, kind: str = "TO"):
    with _connect() as conn:
        conn.execute("INSERT INTO email_recipients (email, kind, active) VALUES (?, ?, 1)", (email, kind))


def remove_email_recipient(recipient_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM email_recipients WHERE id = ?", (recipient_id,))


def create_report_log(report_date: str, kind: str = "GUNLUK") -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO report_logs (report_date, kind, created_at, status) VALUES (?, ?, ?, 'HAZIRLANIYOR')",
            (report_date, kind, _now()),
        )
        return cur.lastrowid


def update_report_log(report_id: int, **fields):
    with _connect() as conn:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE report_logs SET {sets} WHERE id=?", (*fields.values(), report_id))


def get_report_log_for_date(report_date: str, kind: str = "GUNLUK"):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM report_logs WHERE report_date = ? AND kind = ? ORDER BY id DESC LIMIT 1",
            (report_date, kind),
        ).fetchone()
        return dict(row) if row else None


def list_report_logs(limit: int = 100):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM report_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ----------------------------------------------------------- CIHAZ YONETIMI ----
def list_devices(kind: str | None = None):
    with _connect() as conn:
        if kind:
            rows = conn.execute("SELECT * FROM devices WHERE kind=? ORDER BY id", (kind,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM devices ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def add_device(name: str, kind: str, gate: str = "", ip: str = "", port: str = "",
               rtsp_url: str = "", direction: str = "", note: str = "",
               protocol: str = "MOCK", baud_rate: int = 9600,
               open_cmd: str = "", close_cmd: str = "", status_cmd: str = "",
               open_response: str = "", close_response: str = "",
               http_open_url: str = "", http_close_url: str = "", http_status_url: str = "",
               gpio_pin: int = 0, poll_interval: int = 5,
               onvif_port: int = 80, onvif_username: str = "",
               onvif_password_enc: str = "", onvif_profile_token: str = "",
               rtsp_port: int = 554, online: int = 1) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO devices (name, kind, gate, ip, port, rtsp_url, direction, note, created_at,
               protocol, baud_rate, open_cmd, close_cmd, status_cmd,
               open_response, close_response, http_open_url, http_close_url, http_status_url,
               gpio_pin, poll_interval, onvif_port, onvif_username,
               onvif_password_enc, onvif_profile_token, rtsp_port, online)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, kind, gate, ip, port, rtsp_url, direction, note, _now(),
             protocol, baud_rate, open_cmd, close_cmd, status_cmd,
             open_response, close_response, http_open_url, http_close_url, http_status_url,
             gpio_pin, poll_interval, onvif_port, onvif_username,
             onvif_password_enc, onvif_profile_token, rtsp_port, 1 if online else 0),
        )
        return cur.lastrowid


def update_device(device_id: int, **fields):
    allowed = {"name", "kind", "gate", "ip", "port", "rtsp_url", "direction", "online",
               "last_seen", "note", "protocol", "baud_rate", "open_cmd", "close_cmd",
               "status_cmd", "open_response", "close_response", "http_open_url",
               "http_close_url", "http_status_url", "gpio_pin", "poll_interval"}
    allowed.update({"onvif_port", "onvif_username", "onvif_password_enc",
                    "onvif_profile_token", "rtsp_port"})
    sets = []
    vals = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    vals.append(device_id)
    with _connect() as conn:
        conn.execute(f"UPDATE devices SET {', '.join(sets)} WHERE id=?", vals)


def remove_device(device_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM devices WHERE id=?", (device_id,))


def set_device_online(device_id: int, online: bool):
    update_device(device_id, online=1 if online else 0, last_seen=_now())


def get_device_by_gate(gate: str, kind: str = "BARRIER"):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE gate=? AND kind=? ORDER BY id LIMIT 1",
            (gate, kind)).fetchone()
        return dict(row) if row else None


def list_barrier_events(gate: str = "", limit: int = 100):
    with _connect() as conn:
        if gate:
            rows = conn.execute(
                "SELECT * FROM barrier_events WHERE gate=? ORDER BY id DESC LIMIT ?",
                (gate, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM barrier_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------- ARAC ARAMA ----
def search_sessions(plate: str = "", start_date: str = "", end_date: str = "",
                    status: str = "", limit: int = 500):
    """Plaka, tarih araligi ve duruma gore park oturumu arar.
    plate: bos ise tum plakalar; start/end ISO tarih (YYYY-MM-DD) veya bos.
    status: bos ise tum durumlar.
    """
    query = "SELECT * FROM sessions WHERE 1=1"
    params = []
    if plate:
        query += " AND plate LIKE ?"
        params.append(f"%{plate.upper()}%")
    if start_date:
        query += " AND entry_time >= ?"
        params.append(f"{start_date}T00:00:00")
    if end_date:
        query += " AND entry_time <= ?"
        params.append(f"{end_date}T23:59:59")
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def session_events(session_id: int):
    """Bir park oturumuna ait kamera olaylari ve odeme kayitlari."""
    with _connect() as conn:
        cam = conn.execute(
            "SELECT * FROM camera_events WHERE gate_id IN "
            "(SELECT entry_lane FROM sessions WHERE id=? UNION SELECT exit_lane FROM sessions WHERE id=?) "
            "ORDER BY id", (session_id, session_id)
        ).fetchall()
        pays = conn.execute(
            "SELECT * FROM payments WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
        audits = conn.execute(
            "SELECT * FROM audit_logs WHERE target LIKE ? ORDER BY id", (f"session#{session_id}%",)
        ).fetchall()
    return {"camera_events": [dict(r) for r in cam],
            "payments": [dict(r) for r in pays],
            "audits": [dict(r) for r in audits]}
