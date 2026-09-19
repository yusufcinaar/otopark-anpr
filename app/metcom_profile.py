"""Sahada acilan CIKIS-1 bariyerinin yakalanmis Metcom komut profili."""

# Bu baytlar baska bir cikis kanali icin genellenmez.
CAPTURED_TRIGGER_HEX = "0200030203"
CAPTURED_HOLD_HEX = "0200030103"
CAPTURED_RELEASE_HEX = "0200030003"
import os
from app import config  # Load .env before reading the opt-in hardware profile.

CAPTURED_HOST = os.getenv("METCOM_PROFILE_HOST", "").strip()
CAPTURED_PORT = 8080
CAPTURED_OUTPUT = 3


def captured_profile_matches(host, port, output) -> bool:
    """Yakalanmis komutun cihaz/port/kanal kapsamiyla tam eslesme."""
    try:
        return (
            bool(CAPTURED_HOST) and str(host or "").strip() == CAPTURED_HOST
            and int(str(port).strip()) == CAPTURED_PORT
            and int(str(output).strip()) == CAPTURED_OUTPUT
        )
    except (TypeError, ValueError):
        return False
