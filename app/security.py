"""Windows DPAPI ile yerel cihaz parolalarini koruma."""
import base64
import ctypes
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect_secret(value: str) -> str:
    if not value:
        return ""
    raw = value.encode("utf-8")
    if not hasattr(ctypes, "windll"):
        return "plain:" + base64.b64encode(raw).decode("ascii")
    source, keepalive = _blob(raw)
    output = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), "Otopark ANPR", None, None, None, 0,
            ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        protected = ctypes.string_at(output.pbData, output.cbData)
        return "dpapi:" + base64.b64encode(protected).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    prefix, encoded = value.split(":", 1) if ":" in value else ("plain", value)
    raw = base64.b64decode(encoded)
    if prefix == "plain" or not hasattr(ctypes, "windll"):
        return raw.decode("utf-8")
    source, keepalive = _blob(raw)
    output = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
