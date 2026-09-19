"""Bariyer kontrol sistemi: ureticiden bagimsiz adapter yapisi.

Gercek donanim protokolu bilinmedigi icin MockBarrierController ile simule edilir.
Gercek donanima gecerken BarrierController'dan tureyen yeni bir sinif yazilir
(seri port / TCP / role karti); ust katmanlar degismez.

Desteklenen protokoller:
  - MOCK: simülasyon (donanım yok)
  - SERIAL: RS-232/RS-485 seri port (pyserial gerekir)
  - TCP: TCP soket (IP:Port ile komut gönderme)
  - HTTP: HTTP GET/POST ile bariyer kontrolü
  - GPIO: Raspberry Pi GPIO pini (RPi.GPIO gerekir)
"""
import socket
import threading
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, Signal, QTimer, Slot

from app import db
from app.config import BARRIER_AUTO_CLOSE_SEC
from app.metcom_profile import (
    CAPTURED_TRIGGER_HEX, CAPTURED_HOLD_HEX, CAPTURED_RELEASE_HEX,
    captured_profile_matches,
)

# Bariyer durumlari
KAPALI = "KAPALI"
ACILIYOR = "ACILIYOR"
ACIK = "ACIK"
KAPANIYOR = "KAPANIYOR"
ARIZALI = "ARIZALI"
BAGLANTI_YOK = "BAGLANTI_YOK"
EMNIYET_AKTIF = "EMNIYET_AKTIF"
METCOM_CLOSE_UNAVAILABLE = (
    "Bu Metcom tesis profili icin dogrulanmis uzaktan kapatma komutu yok. "
    "Serbest Gecis'i kapatmak surekli acik tutma rolesini birakir ve "
    "bariyeri kendi otomatik kapanma duzenine dondurur. Bu islem ayri bir "
    "zorla kapatma komutu degildir."
)
METCOM_HOLD_UNCERTAIN = (
    "Son serbest gecis komutunun sonucu dogrulanamadi. Otomatik tekrar "
    "gonderim durduruldu; baglantiyi kontrol edip Serbest Gecis islemini yeniden secin."
)

MANUAL_OPEN_REASONS = [
    "Sistem arizasi", "Bariyer arizasi", "Kamera arizasi",
    "Acil durum", "Yonetim onayi", "Yanlis plaka okumasi", "Diger",
]


class BarrierController:
    """Spesifikasyondaki arayuz. Gercek donanim adapteri bu sinifi uygular."""

    supports_close_command = True

    def open_barrier(self, gate_id: str):
        raise NotImplementedError

    def close_barrier(self, gate_id: str):
        raise NotImplementedError

    def set_hold_open(self, gate_id: str, enabled: bool):
        raise NotImplementedError(
            "Bu bariyer surucusunde dogrulanmis surekli acik tutma/normal moda donus komutu yok.")

    def ensure_hold_open(self, gate_id: str):
        return self.set_hold_open(gate_id, True)

    def get_status(self, gate_id: str):
        raise NotImplementedError

    def connect(self):
        """Baglanti kur (seri port ac, TCP baglan, vb.)."""
        return True

    def disconnect(self):
        """Baglantiyi kapat."""
        pass

    def is_connected(self) -> bool:
        return True


class MockBarrierController(BarrierController):
    """Gercek donanim olmadan test icin bariyer simulatoru.

    Emniyet sensoru (fotoseL/loop) arac algilarken bariyer KAPATILMAZ.
    """

    def __init__(self):
        self._states: dict[str, str] = {}
        self._sensor_active: dict[str, bool] = {}
        self._online: dict[str, bool] = {}
        self._hold_open: dict[str, bool] = {}
        self._lock = threading.Lock()

    def _state(self, gate_id: str) -> str:
        return self._states.get(gate_id, KAPALI)

    def set_online(self, gate_id: str, online: bool):
        with self._lock:
            self._online[gate_id] = online
            if not online:
                self._states[gate_id] = BAGLANTI_YOK
            elif self._state(gate_id) == BAGLANTI_YOK:
                self._states[gate_id] = KAPALI

    def set_sensor(self, gate_id: str, active: bool):
        with self._lock:
            self._sensor_active[gate_id] = active

    def sensor_active(self, gate_id: str) -> bool:
        return self._sensor_active.get(gate_id, False)

    def open_barrier(self, gate_id: str):
        with self._lock:
            if not self._online.get(gate_id, True):
                raise ConnectionError(f"Bariyer baglantisi yok: {gate_id}")
            self._states[gate_id] = ACIK
        return True

    def close_barrier(self, gate_id: str):
        with self._lock:
            if not self._online.get(gate_id, True):
                raise ConnectionError(f"Bariyer baglantisi yok: {gate_id}")
            if self._hold_open.get(gate_id, False):
                return False
            if self._sensor_active.get(gate_id, False):
                # emniyet: arac sensor uzerindeyken kapatma
                self._states[gate_id] = EMNIYET_AKTIF
                return False
            self._states[gate_id] = KAPALI
        return True

    def set_hold_open(self, gate_id: str, enabled: bool):
        with self._lock:
            if not self._online.get(gate_id, True):
                raise ConnectionError(f"Bariyer baglantisi yok: {gate_id}")
            self._hold_open[gate_id] = bool(enabled)
            if enabled:
                self._states[gate_id] = ACIK
            # Releasing a latch does not establish that the arm has closed.
        return True

    def get_status(self, gate_id: str) -> str:
        return self._state(gate_id)


class SerialBarrierController(BarrierController):
    """Seri port (RS-232/RS-485) ile bariyer kontrolu.

    pyserial gerekir: pip install pyserial
    Bariyere komut gonderir (ornek: "OPEN\\n"), yanit bekler.
    """

    def __init__(self, port: str = "COM1", baud_rate: int = 9600,
                 open_cmd: str = "OPEN\n", close_cmd: str = "CLOSE\n",
                 status_cmd: str = "STATUS?\n",
                 open_response: str = "OK", close_response: str = "OK",
                 timeout: float = 2.0):
        self.port = port
        self.baud_rate = baud_rate
        self.open_cmd = open_cmd
        self.close_cmd = close_cmd
        self.status_cmd = status_cmd
        self.open_response = open_response
        self.close_response = close_response
        self.timeout = timeout
        self._serial = None
        self._lock = threading.Lock()
        self._states: dict[str, str] = {}

    def connect(self):
        try:
            import serial
        except ImportError:
            raise RuntimeError("pyserial kurulu degil: pip install pyserial")
        try:
            self._serial = serial.Serial(self.port, self.baud_rate, timeout=self.timeout)
            return True
        except Exception as exc:
            self._serial = None
            raise ConnectionError(f"Seri port acilamadi {self.port}: {exc}")

    def disconnect(self):
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def _send(self, cmd: str) -> str:
        with self._lock:
            if not self.is_connected():
                raise ConnectionError("Seri port bagli degil")
            self._serial.write(cmd.encode("utf-8"))
            resp = self._serial.readline().decode("utf-8", errors="ignore").strip()
            return resp

    def open_barrier(self, gate_id: str):
        resp = self._send(self.open_cmd)
        if self.open_response and self.open_response not in resp:
            raise RuntimeError(f"Bariyer acma yaniti beklenmeyen: {resp}")
        self._states[gate_id] = ACIK
        return True

    def close_barrier(self, gate_id: str):
        resp = self._send(self.close_cmd)
        if self.close_response and self.close_response not in resp:
            raise RuntimeError(f"Bariyer kapatma yaniti beklenmeyen: {resp}")
        self._states[gate_id] = KAPALI
        return True

    def get_status(self, gate_id: str) -> str:
        if self.status_cmd:
            try:
                resp = self._send(self.status_cmd)
                if "OPEN" in resp.upper():
                    self._states[gate_id] = ACIK
                elif "CLOSE" in resp.upper():
                    self._states[gate_id] = KAPALI
            except Exception:
                pass
        return self._states.get(gate_id, KAPALI)


class TcpBarrierController(BarrierController):
    """TCP soket ile bariyer kontrolu (IP:Port).

    Bariyerin TCP server oldugu durumlar icin. Komut gonderir, yanit bekler.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5000,
                 open_cmd: str = "OPEN\n", close_cmd: str = "CLOSE\n",
                 status_cmd: str = "STATUS?\n",
                 open_response: str = "OK", close_response: str = "OK",
                 timeout: float = 3.0):
        self.host = host
        self.port = port
        self.open_cmd = open_cmd
        self.close_cmd = close_cmd
        self.status_cmd = status_cmd
        self.open_response = open_response
        self.close_response = close_response
        self.timeout = timeout
        self._sock = None
        self._lock = threading.Lock()
        self._states: dict[str, str] = {}

    def connect(self):
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            return True
        except Exception as exc:
            self._sock = None
            raise ConnectionError(f"TCP baglanti kurulamadi {self.host}:{self.port}: {exc}")

    def disconnect(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def is_connected(self) -> bool:
        return self._sock is not None

    def _send(self, cmd: str) -> str:
        with self._lock:
            if not self.is_connected():
                raise ConnectionError("TCP bagli degil")
            self._sock.sendall(cmd.encode("utf-8"))
            data = self._sock.recv(1024)
            return data.decode("utf-8", errors="ignore").strip()

    def open_barrier(self, gate_id: str):
        resp = self._send(self.open_cmd)
        if self.open_response and self.open_response not in resp:
            raise RuntimeError(f"Bariyer acma yaniti beklenmeyen: {resp}")
        self._states[gate_id] = ACIK
        return True

    def close_barrier(self, gate_id: str):
        resp = self._send(self.close_cmd)
        if self.close_response and self.close_response not in resp:
            raise RuntimeError(f"Bariyer kapatma yaniti beklenmeyen: {resp}")
        self._states[gate_id] = KAPALI
        return True

    def get_status(self, gate_id: str) -> str:
        if self.status_cmd:
            try:
                resp = self._send(self.status_cmd)
                if "OPEN" in resp.upper():
                    self._states[gate_id] = ACIK
                elif "CLOSE" in resp.upper():
                    self._states[gate_id] = KAPALI
            except Exception:
                pass
        return self._states.get(gate_id, KAPALI)


class HttpBarrierController(BarrierController):
    """HTTP GET/POST ile bariyer kontrolu.

    Bariyerin HTTP API sunucusu oldugu durumlar icin.
    Ornek: http://192.0.2.50/barrier/open?gate=CIKIS-1
    """

    def __init__(self, open_url: str = "", close_url: str = "", status_url: str = "",
                 timeout: float = 5.0):
        self.open_url = open_url
        self.close_url = close_url
        self.status_url = status_url
        self.timeout = timeout
        self._states: dict[str, str] = {}
        self._lock = threading.Lock()

    def connect(self):
        return True  # HTTP baglantisi her cagrıda kurulur

    def disconnect(self):
        pass

    def is_connected(self) -> bool:
        return bool(self.open_url)

    def _http_get(self, url: str) -> str:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore").strip()
        except urllib.error.URLError as exc:
            raise ConnectionError(f"HTTP istek hatasi {url}: {exc}")

    def open_barrier(self, gate_id: str):
        if not self.open_url:
            raise ConnectionError("HTTP open URL tanimli degil")
        resp = self._http_get(self.open_url)
        if "ok" not in resp.lower() and "success" not in resp.lower():
            raise RuntimeError(f"Bariyer acma HTTP yaniti: {resp}")
        self._states[gate_id] = ACIK
        return True

    def close_barrier(self, gate_id: str):
        if not self.close_url:
            raise ConnectionError("HTTP close URL tanimli degil")
        resp = self._http_get(self.close_url)
        if "ok" not in resp.lower() and "success" not in resp.lower():
            raise RuntimeError(f"Bariyer kapatma HTTP yaniti: {resp}")
        self._states[gate_id] = KAPALI
        return True

    def get_status(self, gate_id: str) -> str:
        if self.status_url:
            try:
                resp = self._http_get(self.status_url)
                if "open" in resp.lower():
                    self._states[gate_id] = ACIK
                elif "close" in resp.lower():
                    self._states[gate_id] = KAPALI
            except Exception:
                pass
        return self._states.get(gate_id, KAPALI)


class MetcomIoController(BarrierController):
    """Metcom I/O kartinin durum/loop ve kalici TCP baglanti surucusu.

    Kart POST /index.xml yanitinda dyn0..dyn3 bariyer cikislarini,
    dyn4..dyn7 loop girislerini dondurur. Arma ag kaydinda kartin 8080/TCP
    baglantisini acik tuttugu ve bes saniyede bir 00 heartbeat gonderdigi
    dogrulanmistir. 18.09.2026 saha kaydindaki 02 00 03 02 03 tetigi
    yalnizca kaydin alindigi IP/port/OUT3 profili icin kullanilir.
    Diger komutlar ayri bir HEX ayari ve dogrulama gerektirir.
    """

    supports_close_command = False

    def __init__(self, host: str = "", output_channel: int = 3,
                 loop_channel: int = 1, tcp_port: int = 8080,
                 heartbeat_hex: str = "00", heartbeat_interval: float = 5.0,
                 trigger_hex: str = "", trigger_verified: bool = False,
                 timeout: float = 3.0, hold_open_requested: bool = False):
        self.host = host
        self.output_channel = max(1, min(4, int(output_channel)))
        self.loop_channel = max(1, min(4, int(loop_channel)))
        self.tcp_port = int(tcp_port)
        self.heartbeat = self._decode_hex(heartbeat_hex, "heartbeat")
        self.heartbeat_interval = max(0.0, float(heartbeat_interval))
        self.trigger = self._decode_hex(trigger_hex, "acma komutu") if trigger_hex.strip() else b""
        self.trigger_verified = bool(trigger_verified)
        self.timeout = timeout
        self._http_connected = False
        self._sock = None
        self._tcp_lock = threading.RLock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._last_values: dict[str, str] = {}
        self._hold_requested = bool(hold_open_requested)
        self._socket_generation = 0
        self._hold_applied_generation = None
        self._hold_restore_blocked = False

    @staticmethod
    def _decode_hex(value: str, field_name: str) -> bytes:
        compact = "".join((value or "").split())
        if len(compact) % 2:
            raise ValueError(f"Metcom {field_name} HEX degeri cift sayida karakter olmali.")
        try:
            return bytes.fromhex(compact)
        except ValueError as exc:
            raise ValueError(f"Metcom {field_name} yalnizca 0-9/A-F HEX karakterleri icerebilir.") from exc

    @property
    def status_url(self):
        return f"http://{self.host}/index.xml"

    def _read_values(self) -> dict[str, str]:
        payload = urllib.parse.urlencode({"IGNOREDTAG": "1"}).encode("ascii")
        request = urllib.request.Request(self.status_url, data=payload, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                root = ET.fromstring(response.read())
        except (urllib.error.URLError, ET.ParseError, OSError) as exc:
            self._http_connected = False
            raise ConnectionError(f"Metcom I/O durum okunamadi ({self.host}): {exc}")
        self._last_values = {child.tag: (child.text or "").strip() for child in root}
        self._http_connected = True
        return self._last_values

    def connect(self):
        self._read_values()
        self._connect_tcp()
        if self.heartbeat:
            self._send_tcp(self.heartbeat, reconnect=False)
        self._start_heartbeat()
        return True

    def disconnect(self):
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=min(1.0, self.timeout))
        self._heartbeat_thread = None
        with self._tcp_lock:
            self._close_socket_locked()
        self._http_connected = False

    def is_connected(self) -> bool:
        return self._http_connected and self._sock is not None

    @property
    def trigger_ready(self) -> bool:
        if not self.trigger or not self.trigger_verified:
            return False
        if self.trigger.hex().upper() == CAPTURED_TRIGGER_HEX:
            return captured_profile_matches(self.host, self.tcp_port, self.output_channel)
        return True

    @property
    def hold_ready(self) -> bool:
        return (
            self.trigger_verified
            and self.trigger.hex().upper() == CAPTURED_TRIGGER_HEX
            and captured_profile_matches(self.host, self.tcp_port, self.output_channel)
        )

    def _check_hold_profile(self):
        if not self.hold_ready:
            raise RuntimeError(
                "Surekli acik tutma ve normal moda donus komutlari yalnizca "
                "dogrulanmis METCOM_PROFILE_HOST:8080 / OUT3 acma profili icin gecerlidir.")

    def _connect_tcp(self, restore_hold: bool = True):
        with self._tcp_lock:
            if self._sock is not None:
                return
            if restore_hold and self._hold_requested and not self._hold_restore_blocked:
                self._check_hold_profile()
            try:
                self._sock = socket.create_connection(
                    (self.host, self.tcp_port), timeout=self.timeout)
            except OSError as exc:
                self._sock = None
                raise ConnectionError(
                    f"Metcom kontrol baglantisi kurulamadi ({self.host}:{self.tcp_port}): {exc}"
                ) from exc
            self._socket_generation += 1
            if restore_hold and self._hold_requested and not self._hold_restore_blocked:
                try:
                    self._send_tcp(bytes.fromhex(CAPTURED_HOLD_HEX), reconnect=False)
                except ConnectionError as exc:
                    self._hold_restore_blocked = True
                    raise ConnectionError(f"{METCOM_HOLD_UNCERTAIN} Ayrinti: {exc}") from exc
                self._hold_applied_generation = self._socket_generation

    def _close_socket_locked(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._hold_applied_generation = None

    def _send_tcp(self, payload: bytes, reconnect: bool = True):
        if not payload:
            return
        with self._tcp_lock:
            if self._sock is None:
                self._connect_tcp()
            try:
                self._sock.sendall(payload)
                return
            except OSError as first_error:
                self._close_socket_locked()
                if not reconnect:
                    raise ConnectionError(
                        f"Metcom TCP veri gonderilemedi ({self.host}:{self.tcp_port}): {first_error}"
                    ) from first_error
            # Kopmus kalici baglantiyi bir kez yenileyip ayni paketi tekrar gonder.
            self._connect_tcp()
            try:
                self._sock.sendall(payload)
            except OSError as exc:
                self._close_socket_locked()
                raise ConnectionError(
                    f"Metcom TCP veri gonderilemedi ({self.host}:{self.tcp_port}): {exc}"
                ) from exc

    def _start_heartbeat(self):
        if not self.heartbeat or self.heartbeat_interval <= 0:
            return
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="metcom-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.wait(self.heartbeat_interval):
            try:
                self._send_tcp(self.heartbeat)
            except ConnectionError:
                # Sonraki dongude yeniden baglanmayi dener. Kamera/OCR akisini
                # bariyer ag kesintisi nedeniyle durdurma.
                continue

    @staticmethod
    def _is_open(value: str) -> bool:
        return value.casefold() in {"acik", "açık", "open", "on", "1"}

    def get_status(self, gate_id: str) -> str:
        values = self._read_values()
        value = values.get(f"dyn{self.output_channel - 1}", "")
        return ACIK if self._is_open(value) else KAPALI

    def get_loop_status(self) -> bool:
        values = self._read_values()
        value = values.get(f"dyn{3 + self.loop_channel}", "")
        return self._is_open(value)

    def open_barrier(self, gate_id: str):
        with self._tcp_lock:
            if self._hold_restore_blocked:
                raise RuntimeError(METCOM_HOLD_UNCERTAIN)
            if self._hold_requested:
                return self.ensure_hold_open(gate_id)
            return self._open_pulse(gate_id)

    def _open_pulse(self, gate_id: str):
        if not self.trigger_ready:
            raise RuntimeError(
                f"Metcom I/O OUT{self.output_channel} acma komutu dogrulanmadi. "
                "Bariyer ve LED Ayarlari ekranindaki IP, TCP portu, role cikisi "
                "ve komut dogrulamasini kontrol edin. Kayittan alinan komut "
                "yalnizca METCOM_PROFILE_HOST:8080 / OUT3 icin gecerlidir."
            )
        # A send error can occur after some/all bytes reached the card.
        # Do not automatically replay an actuation whose outcome is uncertain.
        self._send_tcp(self.trigger, reconnect=False)
        return True

    def ensure_hold_open(self, gate_id: str):
        """Traffic/F4 may maintain a confirmed latch, never retry uncertain actuation."""
        with self._tcp_lock:
            if self._hold_restore_blocked:
                raise RuntimeError(METCOM_HOLD_UNCERTAIN)
            return self.set_hold_open(gate_id, True)

    def set_hold_open(self, gate_id: str, enabled: bool):
        """Apply the captured OUT3 latch/release, without inventing physical close."""
        with self._tcp_lock:
            self._check_hold_profile()
            enabled = bool(enabled)
            if (enabled and self._hold_requested and not self._hold_restore_blocked
                    and self._sock is not None
                    and self._hold_applied_generation == self._socket_generation):
                return True
            try:
                # Mode-off on a fresh connection must send RELEASE directly,
                # without restoring the old HOLD first.
                self._connect_tcp(restore_hold=False)
                payload = CAPTURED_HOLD_HEX if enabled else CAPTURED_RELEASE_HEX
                self._send_tcp(bytes.fromhex(payload), reconnect=False)
            except Exception as exc:
                # Preserve the previous desired mode but do not let heartbeat
                # or subsequent number-plate reads replay an uncertain command.
                self._hold_restore_blocked = True
                raise ConnectionError(f"{METCOM_HOLD_UNCERTAIN} Ayrinti: {exc}") from exc
            self._hold_requested = enabled
            self._hold_restore_blocked = False
            self._hold_applied_generation = self._socket_generation if enabled else None
            self._start_heartbeat()
            return True

    def suspend_hold_restore(self):
        with self._tcp_lock:
            self._hold_restore_blocked = True

    def close_barrier(self, gate_id: str):
        # Only opening was captured and physically confirmed. Relay idle is
        # not evidence that the arm closed, and must not count as success.
        raise NotImplementedError(METCOM_CLOSE_UNAVAILABLE)


class GpioBarrierController(BarrierController):
    """Raspberry Pi GPIO pini ile bariyer kontrolu (role karti).

    RPi.GPIO gerekir: pip install RPi.GPIO
    Pin HIGH = bariyer ac, Pin LOW = bariyer kapat.
    """

    def __init__(self, pin: int = 18):
        self.pin = pin
        self._gpio = None
        self._states: dict[str, str] = {}

    def connect(self):
        try:
            import RPi.GPIO as GPIO
        except ImportError:
            raise RuntimeError("RPi.GPIO kurulu degil (Raspberry Pi gerekir)")
        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.OUT)
        return True

    def disconnect(self):
        if self._gpio:
            self._gpio.cleanup(self.pin)
            self._gpio = None

    def is_connected(self) -> bool:
        return self._gpio is not None

    def open_barrier(self, gate_id: str):
        if not self.is_connected():
            raise ConnectionError("GPIO bagli degil")
        self._gpio.output(self.pin, self._gpio.HIGH)
        self._states[gate_id] = ACIK
        return True

    def close_barrier(self, gate_id: str):
        if not self.is_connected():
            raise ConnectionError("GPIO bagli degil")
        self._gpio.output(self.pin, self._gpio.LOW)
        self._states[gate_id] = KAPALI
        return True

    def get_status(self, gate_id: str) -> str:
        if self.is_connected():
            state = self._gpio.input(self.pin)
            self._states[gate_id] = ACIK if state == self._gpio.HIGH else KAPALI
        return self._states.get(gate_id, KAPALI)


def create_barrier_controller_from_device(device: dict) -> BarrierController:
    """DB'deki cihaz kaydindan uygun bariyer controller olusturur."""
    protocol = (device.get("protocol") or "MOCK").upper()

    if protocol == "SERIAL":
        return SerialBarrierController(
            port=device.get("ip") or "COM1",
            baud_rate=device.get("baud_rate") or 9600,
            open_cmd=device.get("open_cmd") or "OPEN\n",
            close_cmd=device.get("close_cmd") or "CLOSE\n",
            status_cmd=device.get("status_cmd") or "STATUS?\n",
            open_response=device.get("open_response") or "OK",
            close_response=device.get("close_response") or "OK",
        )
    if protocol == "TCP":
        return TcpBarrierController(
            host=device.get("ip") or "127.0.0.1",
            port=int(device.get("port") or 5000),
            open_cmd=device.get("open_cmd") or "OPEN\n",
            close_cmd=device.get("close_cmd") or "CLOSE\n",
            status_cmd=device.get("status_cmd") or "STATUS?\n",
            open_response=device.get("open_response") or "OK",
            close_response=device.get("close_response") or "OK",
        )
    if protocol == "HTTP":
        return HttpBarrierController(
            open_url=device.get("http_open_url") or "",
            close_url=device.get("http_close_url") or "",
            status_url=device.get("http_status_url") or "",
        )
    if protocol == "METCOM_IO":
        return MetcomIoController(
            host=device.get("ip") or "",
            output_channel=int(db.get_setting("arma_barrier_output", "3") or 3),
            loop_channel=1,
            tcp_port=int(device.get("port") or db.get_setting("arma_barrier_tcp_port", "8080") or 8080),
            heartbeat_hex=db.get_setting("arma_barrier_heartbeat_hex", "00") or "00",
            heartbeat_interval=float(db.get_setting("arma_barrier_heartbeat_sec", "5") or 5),
            trigger_hex=db.get_setting("arma_barrier_trigger_hex", "") or "",
            trigger_verified=db.get_setting("arma_barrier_trigger_verified", "0") == "1",
            hold_open_requested=db.get_setting("free_pass_mode", "0") == "1",
        )
    if protocol == "GPIO":
        return GpioBarrierController(pin=int(device.get("gpio_pin") or 18))
    # MOCK veya bilinmeyen
    return MockBarrierController()


class BarrierService(QObject):
    """Qt sinyalleriyle UI'ya durum bildiren, loglayan bariyer servisi.

    Kapı bazında controller destekler. Her kapı için DB'den cihaz kaydı okunur,
    uygun controller (Serial/TCP/HTTP/GPIO/Mock) oluşturulur.
    """
    state_changed = Signal(str, str)  # (gate_id, durum)
    passage_finished = Signal(str, object)  # (gate_id, session_id)
    _auto_close_requested = Signal(str, object, str)

    def __init__(self, controller: BarrierController | None = None):
        super().__init__()
        self.controller = controller or MockBarrierController()
        # Kapı bazlı controller'lar (gerçek donanım)
        self._gate_controllers: dict[str, BarrierController] = {}
        self._auto_close_timers: dict[str, QTimer] = {}
        self._last_errors: dict[str, str] = {}
        self._mode_lock = threading.RLock()
        self._auto_close_requested.connect(self._schedule_auto_close)

    def last_error(self, gate_id: str) -> str:
        """Son acma hatasini arayuz icin dondurur; cihaza istek gondermez."""
        return self._last_errors.get(gate_id, "")

    def is_open_configured(self, gate_id: str) -> bool:
        """Acma komutu yapilandirilmis mi? Fiziksel baglanti/acma testi yapmaz."""
        try:
            ctrl = self._get_controller_for_gate(gate_id)
        except Exception:
            return False
        return not isinstance(ctrl, MetcomIoController) or ctrl.trigger_ready

    def is_close_supported(self, gate_id: str) -> bool:
        """Kapatma komutu var mi? Cihaza baglanmaz veya durum sorgulamaz."""
        try:
            return bool(self._get_controller_for_gate(gate_id).supports_close_command)
        except Exception:
            return False

    def _get_controller_for_gate(self, gate_id: str) -> BarrierController:
        """Kapı için DB'den cihaz kaydı okur, uygun controller oluşturur."""
        with self._mode_lock:
            if gate_id in self._gate_controllers:
                return self._gate_controllers[gate_id]
            device = db.get_device_by_gate(gate_id, "BARRIER")
            if device and (device.get("protocol") or "MOCK").upper() != "MOCK":
                ctrl = create_barrier_controller_from_device(device)
                self._gate_controllers[gate_id] = ctrl
                return ctrl
            # Cihaz tanımlı değilse veya MOCK ise genel controller kullan
            return self.controller

    def test_connection(self, gate_id: str) -> tuple[bool, str]:
        """Kapı bariyerine bağlantı testi yapar. (success, message) döner."""
        with self._mode_lock:
            return self._test_connection_locked(gate_id)

    def _test_connection_locked(self, gate_id: str) -> tuple[bool, str]:
        try:
            ctrl = self._get_controller_for_gate(gate_id)
            if not ctrl.is_connected():
                ctrl.connect()
            connected = ctrl.is_connected()
            if connected:
                if isinstance(ctrl, MetcomIoController) and not ctrl.trigger_ready:
                    return True, (
                        f"{gate_id} Metcom durum + TCP heartbeat baglantisi basarili. "
                        "Fiziksel acma komutu henuz dogrulanmadigi icin role tetigi kapali."
                    )
                return True, f"{gate_id} bariyerine baglanti basarili ({type(ctrl).__name__})"
            return False, f"{gate_id} bariyerine baglanti kurulamadi"
        except Exception as exc:
            return False, f"{gate_id} baglanti hatasi: {exc}"

    def open(self, gate_id: str, username: str = "", plate: str = "",
             session_id: int | None = None, reason: str = "", note: str = "") -> bool:
        # Keep the free-pass setting and its hardware command atomic relative
        # to F4/vehicle callbacks that run on separate workers.
        with self._mode_lock:
            return self._open_locked(gate_id, username, plate, session_id, reason, note)

    def _open_locked(self, gate_id: str, username: str, plate: str,
                     session_id: int | None, reason: str, note: str) -> bool:
        before = BAGLANTI_YOK
        failure_state = ARIZALI
        try:
            ctrl = self._get_controller_for_gate(gate_id)
            failure_state = BAGLANTI_YOK
            if not ctrl.is_connected():
                ctrl.connect()
            before = ctrl.get_status(gate_id)
            failure_state = ARIZALI
            free_pass = gate_id == "CIKIS-1" and db.get_setting("free_pass_mode", "0") == "1"
            accepted = ctrl.ensure_hold_open(gate_id) if free_pass else ctrl.open_barrier(gate_id)
            if accepted is False:
                raise RuntimeError("Bariyer surucusu acma komutunu kabul etmedi.")
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            self._last_errors[gate_id] = error
            db.log_barrier_event(gate_id, "ACMA_HATASI", before, username,
                                 plate, session_id, reason, error)
            if isinstance(exc, ConnectionError):
                failure_state = BAGLANTI_YOK
            # Hata sonrasinda tekrar durum okumak asil hatayi maskeleyebilir.
            self.state_changed.emit(gate_id, failure_state)
            return False
        self._last_errors.pop(gate_id, None)
        action = "SERBEST_GECIS_ACIK_TUTULUYOR" if free_pass else "ACILDI"
        db.log_barrier_event(gate_id, action, before, username, plate, session_id, reason, note)
        self.state_changed.emit(gate_id, ACIK)
        free_pass = db.get_setting("free_pass_mode", "0") == "1" or "serbest gecis" in reason.lower()
        if not free_pass and BARRIER_AUTO_CLOSE_SEC > 0 and ctrl.supports_close_command:
            # Manual panel/F4 run network work outside the GUI thread. Timers
            # must be created in this service QObject's owning Qt thread.
            self._auto_close_requested.emit(gate_id, session_id, username)
        return True

    def set_free_pass_mode(self, enabled: bool, username: str = "") -> bool:
        """Persist the mode only after its hardware command was accepted."""
        gate_id = "CIKIS-1"
        with self._mode_lock:
            enabled = bool(enabled)
            previous = db.get_setting("free_pass_mode", "0")
            ctrl = None
            applied = False
            try:
                ctrl = self._get_controller_for_gate(gate_id)
                # Metcom set_hold_open opens its TCP connection directly. A
                # generic connect here could restore HOLD immediately before
                # the requested RELEASE after an application restart.
                if not isinstance(ctrl, MetcomIoController) and not ctrl.is_connected():
                    ctrl.connect()
                if ctrl.set_hold_open(gate_id, enabled) is False:
                    raise RuntimeError("Bariyer serbest gecis komutunu kabul etmedi.")
                applied = True
                db.set_setting("free_pass_mode", "1" if enabled else "0")
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                if applied:
                    if isinstance(ctrl, MetcomIoController):
                        ctrl.suspend_hold_restore()
                    error = (
                        "Komut gonderildi ancak serbest gecis ayari kaydedilemedi; "
                        f"donanim ve kayitli mod uyumu dogrulanamadi. {error}")
                self._last_errors[gate_id] = error
                db.log_barrier_event(gate_id, "SERBEST_GECIS_HATASI", "", username, note=error)
                return False
            self._last_errors.pop(gate_id, None)
            action = "SERBEST_GECIS_ACIK_TUT" if enabled else "SERBEST_GECIS_NORMAL_MOD"
            note = ("Surekli acik tutma komutu gonderildi." if enabled else
                    "Surekli acik tutma birakildi; bariyer kendi otomatik kapanma duzenine doner. "
                    "Fiziksel kapali durumu dogrulanmadi.")
            db.log_barrier_event(gate_id, action, "", username, note=note)
            db.audit(username, action, target=gate_id, old_value=previous,
                     new_value="1" if enabled else "0", note=note)
            if enabled:
                self.state_changed.emit(gate_id, ACIK)
            return True

    def close(self, gate_id: str, username: str = "") -> bool:
        timer = self._auto_close_timers.pop(gate_id, None)
        if timer:
            timer.stop()
            timer.deleteLater()
        ctrl = self._get_controller_for_gate(gate_id)
        if not ctrl.supports_close_command:
            self._last_errors[gate_id] = METCOM_CLOSE_UNAVAILABLE
            db.log_barrier_event(gate_id, "KAPATMA_DESTEKLENMIYOR", "", username,
                                 note=METCOM_CLOSE_UNAVAILABLE)
            return False
        if not ctrl.is_connected():
            try:
                ctrl.connect()
            except Exception as exc:
                db.log_barrier_event(gate_id, "KAPATMA_HATASI", BAGLANTI_YOK,
                                     username, note=str(exc))
                self.state_changed.emit(gate_id, BAGLANTI_YOK)
                return False
        before = ctrl.get_status(gate_id)
        try:
            ok = ctrl.close_barrier(gate_id)
        except ConnectionError as exc:
            db.log_barrier_event(gate_id, "KAPATMA_HATASI", before, username, note=str(exc))
            self.state_changed.emit(gate_id, ctrl.get_status(gate_id))
            return False
        except Exception as exc:
            db.log_barrier_event(gate_id, "KAPATMA_HATASI", before, username, note=str(exc))
            self.state_changed.emit(gate_id, ARIZALI)
            return False
        state = ctrl.get_status(gate_id)
        action = "KAPANDI" if ok else "EMNIYET_ENGELLEDI"
        db.log_barrier_event(gate_id, action, before, username)
        self.state_changed.emit(gate_id, state)
        return ok

    @Slot(str, object, str)
    def _schedule_auto_close(self, gate_id: str, session_id, username: str):
        old = self._auto_close_timers.pop(gate_id, None)
        if old:
            old.stop()
            old.deleteLater()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(
            lambda g=gate_id, sid=session_id, user=username: self._auto_close(g, sid, user))
        self._auto_close_timers[gate_id] = timer
        timer.start(max(1, int(BARRIER_AUTO_CLOSE_SEC * 1000)))

    def _auto_close(self, gate_id: str, session_id, username: str):
        self._auto_close_timers.pop(gate_id, None)
        # Serbest gecis sonradan acildiysa bariyeri acik tut.
        if db.get_setting("free_pass_mode", "0") == "1":
            return
        if not self.is_close_supported(gate_id):
            return
        if self.close(gate_id, username=username):
            self.passage_finished.emit(gate_id, session_id)

    def manual_open(self, gate_id: str, username: str, reason: str, note: str = "",
                    plate: str = "", session_id: int | None = None) -> bool:
        """Manuel acma: yalnizca yetkili kullanicilar (UI + servis katmani kontrol eder).
        'Diger' secilirse aciklama zorunludur."""
        if reason == "Diger" and not note.strip():
            raise ValueError("'Diger' secildiginde aciklama zorunludur.")
        try:
            before = self._get_controller_for_gate(gate_id).get_status(gate_id)
        except Exception:
            # Asil acma yolu baglanti/yapilandirma hatasini kaydedip UI'ya verir.
            before = BAGLANTI_YOK
        ok = self.open(gate_id, username, plate, session_id, reason, note)
        if ok:
            db.audit(username, "MANUEL_BARIYER_ACMA", target=gate_id,
                     old_value=before, new_value=ACIK, note=f"{reason}: {note} (plaka: {plate})")
        return ok

    def status(self, gate_id: str) -> str:
        with self._mode_lock:
            ctrl = self._get_controller_for_gate(gate_id)
            if not ctrl.is_connected():
                try:
                    ctrl.connect()
                except Exception:
                    return BAGLANTI_YOK
            return ctrl.get_status(gate_id)

    def set_sensor(self, gate_id: str, active: bool):
        ctrl = self._get_controller_for_gate(gate_id)
        if isinstance(ctrl, MockBarrierController):
            ctrl.set_sensor(gate_id, active)
            self.state_changed.emit(gate_id, ctrl.get_status(gate_id))

    def set_online(self, gate_id: str, online: bool):
        ctrl = self._get_controller_for_gate(gate_id)
        if isinstance(ctrl, MockBarrierController):
            ctrl.set_online(gate_id, online)
            self.state_changed.emit(gate_id, ctrl.get_status(gate_id))

    def reload_gate(self, gate_id: str):
        """Kapı controller'ini yeniden yukler (cihaz ayarlari degistirildiginde)."""
        with self._mode_lock:
            self._last_errors.pop(gate_id, None)
            if gate_id in self._gate_controllers:
                try:
                    self._gate_controllers[gate_id].disconnect()
                except Exception:
                    pass
                del self._gate_controllers[gate_id]

    def reload_all(self):
        """Tum kapı controller'larini yeniden yukler."""
        with self._mode_lock:
            for gate_id in list(self._gate_controllers.keys()):
                self.reload_gate(gate_id)
            self._last_errors.clear()
