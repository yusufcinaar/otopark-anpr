"""
Otopark Plaka Tanima Sistemi - Yapilandirma

Gercek donanima (RTSP kameralar, bariyer, dijital ekran) baglanildiginda
sadece bu dosyadaki degerler degistirilir; is mantigi (app/services, app/anpr)
degismeden calisir.
"""
from dataclasses import dataclass, field
import os
import shutil

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Program guncellenirken/silinip yeniden acilirken operasyon verileri kaybolmasin.
# Varsayilan konum program klasorunun disindaki Windows uygulama veri alanidir.
_app_data_root = (
    os.getenv("LOCALAPPDATA")
    or os.getenv("APPDATA")
    or os.path.join(os.path.expanduser("~"), ".otopark-anpr")
)
DATA_DIR = os.path.abspath(
    os.getenv("OTOPARK_DATA_DIR", os.path.join(_app_data_root, "OtoparkANPRPublic", "data"))
)
LEGACY_DATA_DIR = os.path.join(BASE_DIR, "data")


def _migrate_legacy_data():
    """Eski paket-ici veriyi kalici alana ilk calistirmada kayipsiz tasi."""
    legacy_db = os.path.join(LEGACY_DATA_DIR, "otopark.db")
    target_db = os.path.join(DATA_DIR, "otopark.db")
    if os.path.normcase(LEGACY_DATA_DIR) == os.path.normcase(DATA_DIR):
        return
    if os.path.isfile(legacy_db) and not os.path.exists(target_db):
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copytree(LEGACY_DATA_DIR, DATA_DIR, dirs_exist_ok=True)


_migrate_legacy_data()

# Kalici .env varsa onu kullan; eski kurulumlarla uyumluluk icin paket-ici .env de okunur.
load_dotenv(os.path.join(DATA_DIR, ".env"))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)

DB_PATH = os.path.join(DATA_DIR, "otopark.db")
CAPTURES_DIR = os.path.join(DATA_DIR, "captures")
SIM_PLATES_DIR = os.path.join(DATA_DIR, "sim_plates")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

os.makedirs(CAPTURES_DIR, exist_ok=True)
os.makedirs(SIM_PLATES_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "backups"), exist_ok=True)

# ------------------------------------------------------------- SMTP / .env ----
# Hassas bilgiler kaynak koduna yazilmaz; .env dosyasindan okunur (.env git'e eklenmez).
SMTP = {
    "host": os.getenv("SMTP_HOST", ""),
    "port": int(os.getenv("SMTP_PORT", "587") or 587),
    "auth_mode": os.getenv("SMTP_AUTH_MODE", "password").strip().lower() or "password",
    "username": os.getenv("SMTP_USERNAME", ""),
    "password": os.getenv("SMTP_PASSWORD", ""),
    "from_email": os.getenv("SMTP_FROM_EMAIL", ""),
    "from_name": os.getenv("SMTP_FROM_NAME", "Otopark Yonetim Sistemi"),
    "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
    "use_ssl": os.getenv("SMTP_USE_SSL", "false").lower() == "true",
    "timeout": int(os.getenv("SMTP_TIMEOUT", "30") or 30),
}
DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "09:00")

# Kamera webhook sunucusu (yerel ag): gercek LPR kameralari POST /api/camera-event
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "127.0.0.1")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8090") or 8090)

SPEED_LIMIT_KMH = float(os.getenv("SPEED_LIMIT_KMH", "0") or 0)

# Gercek kameralar bagli degilse True yapilir; bariyer/ekran de simule edilir.
# Gercek sisteme gecerken: False yapip CameraConfig.rtsp_url alanlarini doldurun.
# .env dosyasindan SIMULATION_MODE=false ile kontrol edilebilir.
SIMULATION_MODE = os.getenv("SIMULATION_MODE", "true").lower() == "true"


@dataclass
class CameraConfig:
    name: str
    role: str  # "entry" | "exit"
    lane: str  # "Giris-1", "Giris-2", "Cikis-1"
    rtsp_url: str = ""  # ornek: rtsp://kullanici:sifre@192.0.2.10:554/stream1
    process_every_n_frames: int = 15  # RTSP modunda her N karede bir ANPR calistir
    detection_cooldown_sec: float = 8.0  # ayni plaka icin tekrar tetiklenmeme suresi
    speed_scale: float = 1.0  # saha kalibrasyon katsayisi (RTSP goruntu tabanli)


CAMERAS = {
    "entry1": CameraConfig(name="entry1", role="entry", lane="Giris-1", rtsp_url=""),
    "entry2": CameraConfig(name="entry2", role="entry", lane="Giris-2", rtsp_url=""),
    "exit1": CameraConfig(name="exit1", role="exit", lane="Cikis-1", rtsp_url=""),
}


@dataclass
class Tariff:
    """Saatlik ucret tarifesi: her baslayan saat tam ucretlendirilir.

    free_minutes / daily_max varsayilan olarak kapali (0); kasiyer isterse
    Ucret Tarifesi Ayarlari ekranindan tekrar acabilir.
    """
    free_minutes: int = 0           # ilk X dakika ucretsiz (0 = yok, tamamen saatlik)
    hourly_rate: float = 20.0       # TL / saat (baslayan saat tam ucretlendirilir)
    daily_max: float = 0.0          # gunluk ucret tavani (0 = tavan yok)
    currency: str = "TL"


DEFAULT_TARIFF = Tariff()

# Bariyer, acildiktan sonra kac saniye icinde otomatik kapanir (simulasyon/gercek ayni)
BARRIER_AUTO_CLOSE_SEC = 7.0

# Turkce plaka olceginde yazi tanima icin izin verilen karakterler
OCR_ALLOWLIST = "ABCDEFGHIJKLMNOPRSTUVYZ0123456789"
