"""Windows GUI entry point: keep diagnostics without an open console."""
import os
import logging
from pathlib import Path
import sys
import traceback


def _log_path() -> Path:
    app_data = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
                or str(Path.home() / ".otopark-anpr"))
    data = Path(os.environ.get("OTOPARK_DATA_DIR")
                or Path(app_data) / "OtoparkANPRPublic" / "data")
    return data / "logs" / "uygulama.log"


def _show_startup_error(detail: str, log_path: Path):
    # Imports can fail before Qt is available; Windows still shows the error.
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        None,
        "Otopark ANPR baslatilamadi.\n\n"
        f"{detail}\n\nAyrintili kayit: {log_path}\n"
        "Gerekirse kur.bat ile kurulumu onarin.",
        "Otopark ANPR - Acilis Hatasi", 0x10,
    )


def main():
    log_path = _log_path()
    log = None
    handler = None
    logger = logging.getLogger("otopark")
    previous_level, previous_propagate = logger.level, logger.propagate
    previous_stdout, previous_stderr = sys.stdout, sys.stderr
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a", encoding="utf-8", buffering=1)
        # pythonw sets these streams to None. Libraries and Qt exception
        # reporting must have real streams before the application imports.
        sys.stdout = sys.stderr = log
        handler = logging.StreamHandler(log)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.info("PROGRAM BASLATILIYOR")
        from app.main import main as start_application
        return start_application()
    except Exception as exc:
        if log is not None:
            traceback.print_exc(file=log)
            log.flush()
        _show_startup_error(str(exc), log_path)
        return 1
    finally:
        if handler is not None:
            logger.info("PROGRAM KAPANIYOR")
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        sys.stdout, sys.stderr = previous_stdout, previous_stderr
        if log is not None:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
