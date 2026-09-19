"""Yerel ag kamera webhook sunucusu.

Gercek LPR kameralari plaka olaylarini HTTP POST ile gonderebilir:

  POST http://<sunucu>:8090/api/camera-event
  {
    "event_id": "unique-event-id",
    "camera_id": "LPR-GIRIS-01",
    "gate_id": "GIRIS-1",
    "direction": "ENTRY",            # ENTRY | EXIT
    "raw_plate": "34 ABC 123",
    "confidence": 96.4,
    "event_time": "2026-08-14T10:42:18+03:00",
    "vehicle_type": "CAR",
    "vehicle_color": "WHITE",
    "speed_kmh": 28,
    "vehicle_image_base64": "<tam arac karesi JPEG base64>",
    "plate_image_base64": "<plaka kirpimi JPEG base64>"
  }

``vehicle_image_base64`` kadrajda aracin tamaminin gorundugu ana kayittir;
``plate_image_base64`` yalnizca OCR/inceleme icin ayri tutulur.

Sunucu stdlib http.server ile calisir (ek framework gerekmez) ve olaylari
Qt ana thread'ine sinyalle iletir; boylece simulasyonla ayni is mantigi calisir.
Internet baglantisi gerekmez; yerel agda calisir.
"""
import errno
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

MAX_EVENT_BYTES = 16 * 1024 * 1024
ENTRY_GATES = {"GIRIS-1", "GIRIS-2"}
EXIT_GATES = {"CIKIS-1"}


class WebhookBridge(QObject):
    """HTTP thread'inden gelen olaylari Qt ana thread'ine tasir."""
    camera_event_received = Signal(dict)


class _Handler(BaseHTTPRequestHandler):
    bridge: WebhookBridge = None

    def log_message(self, fmt, *args):  # konsolu kirletme
        pass

    def _respond(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/camera-event":
            self._respond(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                self._respond(411, {"error": "Content-Length zorunludur"})
                return
            if length > MAX_EVENT_BYTES:
                self._respond(413, {"error": "kamera olayi cok buyuk"})
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, TypeError):
            self._respond(400, {"error": "gecersiz JSON"})
            return

        if not isinstance(data, dict):
            self._respond(400, {"error": "JSON nesnesi bekleniyor"})
            return

        required = ["event_id", "gate_id", "direction", "raw_plate"]
        missing = [k for k in required if not data.get(k)]
        if missing:
            self._respond(400, {"error": f"eksik alanlar: {missing}"})
            return

        direction = str(data["direction"]).strip().upper()
        gate = str(data["gate_id"]).strip().upper().replace(" ", "-")
        if direction not in ("ENTRY", "EXIT"):
            self._respond(400, {"error": "direction ENTRY veya EXIT olmalidir"})
            return
        valid_gates = ENTRY_GATES if direction == "ENTRY" else EXIT_GATES
        if gate not in valid_gates:
            self._respond(400, {"error": f"{direction} icin gecersiz/devre disi gate_id: {gate}"})
            return
        data["direction"] = direction
        data["gate_id"] = gate

        # Arma ve diger PTS yazilimlarinda alan adi speed/hiz olabilir.
        if "speed_kmh" not in data:
            data["speed_kmh"] = data.get("speed", data.get("hiz"))

        if self.bridge:
            self.bridge.camera_event_received.emit(data)
        self._respond(200, {"status": "accepted", "event_id": data["event_id"]})

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not found"})


class _ExclusiveHTTPServer(ThreadingHTTPServer):
    # Windows'ta SO_REUSEADDR iki uygulamanin ayni portu dinlemesine izin
    # verebilir. Kameranin olaylari yanlis uygulamaya gitmemeli.
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")
    allow_reuse_port = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class WebhookServer:
    def __init__(self, host: str, port: int):
        self.bridge = WebhookBridge()
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self._lifecycle_lock = threading.Lock()
        self.last_error = ""
        self.error_kind = ""

    def start(self):
        with self._lifecycle_lock:
            if self._server is not None and self._thread is not None and self._thread.is_alive():
                return True
            self._stop_locked()
            self.last_error = ""
            self.error_kind = ""
            handler = type("BoundHandler", (_Handler,), {"bridge": self.bridge})
            server = None
            try:
                # Ayri bind/activate, baslangic hatasinda acilmis soketi de kapatir.
                server = _ExclusiveHTTPServer((self.host, self.port), handler,
                                              bind_and_activate=False)
                server.server_bind()
                server.server_activate()
                thread = threading.Thread(target=server.serve_forever,
                                          kwargs={"poll_interval": 0.2},
                                          name="camera-webhook", daemon=True)
                thread.start()
            except (OSError, ValueError, OverflowError, RuntimeError) as exc:
                if server is not None:
                    server.server_close()
                self._record_start_error(exc)
                return False
            self._server = server
            self._thread = thread
            return True

    def _record_start_error(self, exc):
        codes = {getattr(exc, "errno", None), getattr(exc, "winerror", None)}
        if errno.EADDRINUSE in codes or 10048 in codes:
            self.error_kind = "port_in_use"
            detail = "Port baska bir uygulama veya ikinci bir Otopark penceresi tarafindan kullaniliyor."
        elif errno.EACCES in codes or errno.EPERM in codes or 10013 in codes:
            self.error_kind = "permission_denied"
            detail = "Windows porta erisimi reddetti; port ayrilmis veya baska bir dinleyici tarafindan korunuyor olabilir."
        elif errno.EADDRNOTAVAIL in codes or 10049 in codes:
            self.error_kind = "address_unavailable"
            detail = "Dinleme IP adresi bu bilgisayarda bulunamadi."
        elif isinstance(exc, (ValueError, OverflowError)):
            self.error_kind = "invalid_configuration"
            detail = "Webhook IP/port ayari gecersiz."
        else:
            self.error_kind = "startup_failed"
            detail = "Webhook dinleyicisi baslatilamadi."
        self.last_error = f"{self.host}:{self.port} - {detail} ({exc})"

    def stop(self):
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self):
        server, thread = self._server, self._thread
        if server is None:
            return
        try:
            # shutdown(), serve_forever calismiyorsa sonsuza kadar bekler.
            if thread is not None and thread.is_alive():
                server.shutdown()
                thread.join()
        finally:
            server.server_close()
            self._server = None
            self._thread = None
