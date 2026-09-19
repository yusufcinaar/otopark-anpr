"""Kurulum/guncelleme oncesi guvenli SQLite yedegi."""
from datetime import datetime
import glob
import os
import sqlite3

from app.config import DATA_DIR, DB_PATH


def create_backup(keep: int = 20) -> str | None:
    if not os.path.isfile(DB_PATH):
        return None
    backup_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(backup_dir, f"otopark_{datetime.now():%Y%m%d_%H%M%S}.db")
    source = sqlite3.connect(DB_PATH, timeout=30)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    backups = sorted(glob.glob(os.path.join(backup_dir, "otopark_*.db")), reverse=True)
    for old in backups[max(1, keep):]:
        try:
            os.remove(old)
        except OSError:
            pass
    return target


if __name__ == "__main__":
    result = create_backup()
    print(result or "Yedeklenecek mevcut veritabani yok; ilk kurulum.")
