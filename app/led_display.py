"""Huidu/HD tek renk 64x32 LED tabela (saha kaydindan dogrulanan port 6101).

Protokol HT/HR cerceveleri kullanir. Gorsel 64x32/1-bit, satir bazli ve MSB-first
olarak 256 bayt tasinir. Cerceve sonundaki iki bayt, govdenin 16-bit toplamidir.
"""
import os
import queue
import secrets
import socket
import threading

from PIL import Image, ImageDraw, ImageFont


_SETUP_BODY = bytes.fromhex(
    "4854001b004d1800000000000000000000000000000000000029532f7941"
    "000100010000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000"
)
_BITMAP_PREFIX = bytes.fromhex(
    "4854001b01571900000000000000000000000000000000000029540579410000"
    "484100190000000000400020000100000000000000000000000000001d001739"
    "000000000000001ec90300010000001700000100"
)
_COMMIT_BODY = bytes.fromhex(
    "4854001b00211a0000000000000000000000000000000000002955037941"
)


def _frame(body: bytes) -> bytes:
    return body + (sum(body) & 0xFFFF).to_bytes(2, "big") + b"\xaa"


def _font(size: int):
    for path in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_mono_bitmap(lines: list[str], width: int = 64, height: int = 32) -> bytes:
    lines = [str(x).strip().upper() for x in lines if str(x).strip()][:3] or ["-"]
    image = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(image)
    line_height = max(8, height // len(lines))
    for row, text in enumerate(lines):
        size = min(14, line_height + 2)
        while size > 6:
            font = _font(size)
            box = draw.textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= width:
                break
            size -= 1
        box = draw.textbbox((0, 0), text, font=font)
        x = max(0, (width - (box[2] - box[0])) // 2)
        y = row * line_height + max(0, (line_height - (box[3] - box[1])) // 2) - box[1]
        draw.text((x, y), text, font=font, fill=1)

    packed = bytearray()
    for y in range(height):
        for block in range(width // 8):
            value = 0
            for bit in range(8):
                if image.getpixel((block * 8 + bit, y)):
                    value |= 1 << (7 - bit)
            packed.append(value)
    return bytes(packed)


class HuiduMonoLed:
    def __init__(self, host="", port=6101, timeout=3.0):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self._counter = secrets.randbelow(240)
        self._lock = threading.Lock()

    def _transaction_frames(self, bitmap: bytes):
        if len(bitmap) != 256:
            raise ValueError("LED bitmap 64x32 icin 256 bayt olmalidir")
        token = secrets.token_bytes(2)
        frames = []
        for base, subcommand, content in (
            (_SETUP_BODY, 0x2F, None),
            (_BITMAP_PREFIX, 0x05, bitmap),
            (_COMMIT_BODY, 0x03, None),
        ):
            body = bytearray(base)
            body[26] = self._counter & 0xFF
            body[27] = subcommand
            body[28:30] = token
            self._counter = (self._counter + 1) & 0xFF
            if content is not None:
                body.extend(content)
            frames.append(_frame(bytes(body)))
        return frames

    def show_lines(self, lines: list[str]):
        frames = self._transaction_frames(render_mono_bitmap(lines))
        with self._lock, socket.create_connection((self.host, self.port), self.timeout) as sock:
            sock.settimeout(self.timeout)
            for packet in frames:
                sock.sendall(packet)
                response = sock.recv(128)
                if not response.startswith(b"HR"):
                    raise RuntimeError("LED tabela beklenmeyen cevap verdi")


class PhysicalLedQueue:
    """UI/OCR akisini bekletmeyen, tek elemanli fiziksel tabela kuyrugu.

    Tabela cevrimdisiyken eski mesajlarin sinirsiz birikmesi ve uygulamanin
    kapanista executor thread'ini beklemesi engellenir; her zaman en guncel
    mesaj gonderilir.
    """
    def __init__(self, enabled, host, port=6101):
        self.enabled = bool(enabled)
        self.device = HuiduMonoLed(host, port)
        self._queue = queue.Queue(maxsize=1)
        self._closed = False
        self._worker = None
        if self.enabled:
            self._worker = threading.Thread(
                target=self._run, name="led-tabela", daemon=True)
            self._worker.start()

    def submit(self, lines: list[str]):
        if not self.enabled or self._closed:
            return
        payload = list(lines)
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                pass

    def _run(self):
        while True:
            lines = self._queue.get()
            try:
                if lines is None:
                    return
                self._safe_send(lines)
            finally:
                self._queue.task_done()

    def _safe_send(self, lines):
        try:
            self.device.show_lines(lines)
        except Exception:
            # Kamera/OCR ve odeme akisinin tabela hatasindan etkilenmesine izin verme.
            pass

    def close(self):
        self._closed = True
        if not self._worker:
            return
        try:
            while True:
                self._queue.get_nowait()
                self._queue.task_done()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
