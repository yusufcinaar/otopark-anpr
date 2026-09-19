"""Kamera erisim katmani.

- RtspCameraWorker: SIMULATION_MODE=False oldugunda gercek IP kameradan (RTSP)
  surekli goruntu okur, periyodik olarak ANPR calistirir. Gercek donanima
  gecerken sadece app/config.py'daki rtsp_url doldurulur, bu sinif degismez.
- RecognitionWorker: tek bir karede (simulasyon goruntusu ya da RTSP karesi)
  ANPR calistirip sonucu Qt sinyali ile UI thread'ine dondurur (UI'yi kitlememek icin).
"""
import time
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from app.anpr.pipeline import recognize_plate, PlateResult
from app.config import CameraConfig

# Uc kamera thread'inin her birinde OpenCV'nin yeniden tum CPU cekirdeklerini
# acmasini onler; FFmpeg video cozme bundan etkilenmez.
cv2.setNumThreads(1)

_OCR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="anpr-ocr")

# Hizli arac plaka alaninda yalnizca birkac kare kalabiliyor. Boyle bir karede
# OCR guveni yuksekse ikinci, birebir ayni metni beklemek gercek okumayi
# kaciriyordu (ozellikle GIRIS-2). Dusuk guvenli okumalar yine iki-kare
# dogrulamasindan geciyor.
_FAST_PASS_CONFIDENCE = 0.80
_BEST_CAPTURE_WINDOW_SEC = 0.75
_DISPLAY_INTERVAL_SEC = 0.25       # 4 FPS: operator izlemesi icin yeterli
_OCR_ACTIVE_INTERVAL_SEC = 0.55    # hareket varken plaka tarama araligi
_OCR_IDLE_INTERVAL_SEC = 3.0       # duran arac da tamamen kacmasin
_MOTION_HOLD_SEC = 2.0
_NIGHT_BRIGHTEN_LUT = np.array(
    [min(255, round(((value / 255.0) ** 0.68) * 255.0)) for value in range(256)],
    dtype=np.uint8,
)


def enhance_night_frame(frame: np.ndarray) -> np.ndarray:
    """Karanlik RTSP goruntusunu kamera ayarini bozmadan ekranda aydinlat."""
    return cv2.LUT(frame, _NIGHT_BRIGHTEN_LUT)


def make_preview(frame: np.ndarray, max_width: int = 960) -> np.ndarray:
    """UI'ye tam cozumurluk yerine hafif, aydinlatilmis onizleme gonder."""
    height, width = frame.shape[:2]
    if width > max_width:
        scale = max_width / width
        frame = cv2.resize(frame, (max_width, max(1, int(height * scale))),
                           interpolation=cv2.INTER_AREA)
    return enhance_night_frame(frame)


class RecognitionWorker(QThread):
    """Bir kare uzerinde FastALPR/ONNX tanimayi arka planda calistirir."""
    finished_with_result = Signal(object, dict)  # (PlateResult|None, metadata)

    def __init__(self, frame: np.ndarray, metadata: dict, parent=None):
        super().__init__(parent)
        self._frame = frame
        self._metadata = metadata

    def run(self):
        try:
            result = recognize_plate(self._frame)
        except Exception as exc:  # ANPR hatasi UI'yi dusurmesin
            result = None
            self._metadata["error"] = str(exc)
        self.finished_with_result.emit(result, self._metadata)


class OnvifDiscoveryWorker(QThread):
    """Kayitli kamera icin ONVIF medya profilini ve RTSP adresini bulur."""
    stream_found = Signal(int, str, str)  # device_id, rtsp_uri, profile_token
    discovery_error = Signal(int, str, str)  # device_id, gate, mesaj

    def __init__(self, device: dict, password: str, parent=None):
        super().__init__(parent)
        self.device = device
        self.password = password

    def run(self):
        try:
            from onvif import ONVIFCamera
            camera = ONVIFCamera(
                self.device.get("ip"), int(self.device.get("onvif_port") or 80),
                self.device.get("onvif_username") or "", self.password)
            media = camera.create_media_service()
            profiles = media.GetProfiles()
            if not profiles:
                raise RuntimeError("ONVIF medya profili bulunamadi")
            profile = profiles[0]
            setup = {"StreamSetup": {"Stream": "RTP-Unicast",
                                      "Transport": {"Protocol": "RTSP"}},
                     "ProfileToken": profile.token}
            uri = str(media.GetStreamUri(setup).Uri)
            self.stream_found.emit(int(self.device["id"]), uri, str(profile.token))
        except Exception as exc:
            self.discovery_error.emit(
                int(self.device["id"]), self.device.get("gate") or "KAMERA", str(exc))


class RtspCameraWorker(QThread):
    """Gercek IP kameradan (RTSP) surekli goruntu okuyan thread.

    SIMULATION_MODE=True oldugu surece uygulama bu sinifi kullanmaz;
    gercek kameralar baglaninca app/config.py -> CAMERAS[...].rtsp_url doldurulup
    bu worker baslatilir.
    """
    frame_ready = Signal(np.ndarray, str)              # (kare, lane)
    plate_recognized = Signal(object, str, object)      # (PlateResult, lane, okunan tam kare)
    connection_error = Signal(str, str)                 # (lane, mesaj)

    def __init__(self, config: CameraConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._running = False
        self._last_plate = None
        self._last_plate_time = 0.0
        self._ocr_future = None
        self._last_display_time = 0.0
        self._last_ocr_submit_time = 0.0
        self._plate_votes = []
        self._pending_candidate = None
        self._candidate_lock = threading.RLock()
        self._motion_previous = None
        self._speed_samples = []
        self._motion_probe = None
        self._last_motion_check = 0.0
        self._motion_active_until = 0.0

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        frame_count = 0
        while self._running:
            cap = cv2.VideoCapture(self.config.rtsp_url)
            # IP kamera gecikmesini ve bellek birikmesini azalt. Desteklemeyen
            # OpenCV/FFmpeg surumleri bu ayarlari sessizce yok sayabilir.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                self.connection_error.emit(self.config.lane, "Kameraya baglanilamadi; 5 saniye sonra yeniden denenecek")
                cap.release()
                for _ in range(50):
                    if not self._running:
                        return
                    time.sleep(0.1)
                continue
            failed_reads = 0
            while self._running:
                # Publish an already confirmed plate on its own deadline.
                # Another successful OCR result is not required for a fast car
                # that has already left the image.
                self._emit_pending_if_due(time.time())
                ok, frame = cap.read()
                if not ok:
                    failed_reads += 1
                    if failed_reads >= 3:
                        self.connection_error.emit(self.config.lane, "Goruntu kesildi; kamera yeniden baglaniyor")
                        break
                    time.sleep(0.2)
                    continue
                failed_reads = 0
                frame_count += 1
                now = time.monotonic()
                # Dusuk cozumurluklu hareket kontrolu, bos sahnede pahali OCR'nin
                # surekli calismasini engeller. Her uc saniyede bir emniyet
                # taramasi yine yapilir.
                if now - self._last_motion_check >= 0.20:
                    self._last_motion_check = now
                    probe = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
                    probe = cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY)
                    if self._motion_probe is not None:
                        activity = float(cv2.absdiff(probe, self._motion_probe).mean())
                        if activity >= 1.25:
                            self._motion_active_until = now + _MOTION_HOLD_SEC
                    self._motion_probe = probe
                # Qt tarafinda tam kamera FPS'iyle QPixmap uretmek CPU'yu tuketir.
                # Canli izleme 4 FPS ve en fazla 960 px genislikte tutulur.
                if now - self._last_display_time >= _DISPLAY_INTERVAL_SEC:
                    self._last_display_time = now
                    self.frame_ready.emit(make_preview(frame), self.config.lane)

                # Kamera FPS'inden bagimsiz, yaklasik saniyede bir ANPR denemesi.
                # Her kamera en fazla bir bekleyen OCR isi tutar.
                # Exit traffic can pass without stopping while the barrier is
                # held open. A weak motion signal must not leave a three-second
                # blind interval on that camera.
                motion_active = (self.config.role == "exit"
                                 or now <= self._motion_active_until
                                 or self._pending_candidate is not None)
                ocr_interval = _OCR_ACTIVE_INTERVAL_SEC if motion_active else _OCR_IDLE_INTERVAL_SEC
                if (now - self._last_ocr_submit_time >= ocr_interval
                        and (self._ocr_future is None or self._ocr_future.done())):
                    self._last_ocr_submit_time = now
                    ocr_frame = enhance_night_frame(frame)
                    self._ocr_future = _OCR_EXECUTOR.submit(recognize_plate, ocr_frame)
                    self._ocr_future.add_done_callback(
                        lambda future, captured=ocr_frame: self._ocr_completed(future, captured))
            cap.release()

    def _ocr_completed(self, future, captured_frame):
        # OCR callbacks and the camera's deadline check run on different threads.
        with self._candidate_lock:
            self._process_ocr_result(future, captured_frame)

    def _process_ocr_result(self, future, captured_frame):
        if not self._running:
            return
        current = time.time()
        self._emit_pending_if_due(current)
        try:
            result: PlateResult = future.result()
        except Exception as exc:
            self.connection_error.emit(self.config.lane, f"ANPR hatasi: {exc}")
            return
        if not result or not result.is_valid_format:
            return
        self._update_speed_estimate(result, current)
        # Yuksek guvenli ve formati gecerli bir okumayi hemen isle. Hizli arac
        # ikinci OCR karesine kalmadan gorus alanindan cikabilir. Daha dusuk
        # guvenli sonuclar gece yansimasina karsi iki-kare onayi ister.
        self._plate_votes.append((current, result.plate, result))
        self._plate_votes = [v for v in self._plate_votes if current - v[0] <= 3.0]
        if result.confidence < _FAST_PASS_CONFIDENCE:
            counts = Counter(v[1] for v in self._plate_votes)
            plate, vote_count = counts.most_common(1)[0]
            if vote_count < 2:
                return
            result = max(
                (v[2] for v in self._plate_votes if v[1] == plate),
                key=lambda item: item.confidence,
            )
        self._plate_votes.clear()
        self._queue_best_capture(result, captured_frame, current)
        self._emit_pending_if_due(current)

    @staticmethod
    def _capture_score(result: PlateResult):
        """Yaklasan araclarda plakasi daha buyuk/net olan kareyi tercih et."""
        crop = result.crop
        area = int(crop.shape[0] * crop.shape[1]) if crop is not None and crop.size else 0
        sharpness = 0.0
        if crop is not None and crop.size:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return area, sharpness, float(result.confidence)

    def _update_speed_estimate(self, result: PlateResult, current: float):
        """Ardisik plaka konumlarindan RTSP tabanli yaklasik arac hizi uretir.

        ONVIF standart akisi hiz alani tasimaz. Bu nedenle plakanin kareler
        arasindaki hareketi, fiziksel TR plaka genisligiyle olceklendiririz.
        ``speed_scale`` sahada radar/Arma degeriyle ince ayar icindir.
        """
        if not result.bbox:
            return
        x1, y1, x2, y2 = result.bbox
        width = max(1.0, float(x2 - x1))
        center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        previous = self._motion_previous
        self._motion_previous = (current, center, width)
        if previous is None:
            return
        prev_time, prev_center, prev_width = previous
        elapsed = current - prev_time
        if not 0.12 <= elapsed <= 1.5:
            self._speed_samples.clear()
            return
        dx = center[0] - prev_center[0]
        dy = center[1] - prev_center[1]
        pixel_motion = (dx * dx + dy * dy) ** 0.5 + abs(width - prev_width) * 1.5
        normalized_motion = pixel_motion / max(1.0, (width + prev_width) / 2.0)
        speed = normalized_motion * 0.52 / elapsed * 3.6 * self.config.speed_scale
        if 1.0 <= speed <= 120.0:
            self._speed_samples.append(speed)
            self._speed_samples = self._speed_samples[-5:]
            result.speed_kmh = round(float(np.median(self._speed_samples)), 1)

    def _queue_best_capture(self, result: PlateResult, captured_frame, current: float):
        with self._candidate_lock:
            self._queue_best_capture_locked(result, captured_frame, current)

    def _queue_best_capture_locked(self, result: PlateResult, captured_frame, current: float):
        score = self._capture_score(result)
        pending = self._pending_candidate
        if pending is not None and result.plate != pending["result"].plate:
            # The next confirmed car must not be discarded while waiting for
            # a better photograph of the previous one.
            self._emit_pending_if_due(current, force=True)
            pending = None
        if pending is None:
            self._pending_candidate = {
                "result": result, "frame": captured_frame.copy(), "started": current,
                "score": score,
            }
        elif result.plate == pending["result"].plate and score > pending["score"]:
            if result.speed_kmh is None:
                result.speed_kmh = pending["result"].speed_kmh
            pending.update(result=result, frame=captured_frame.copy(), score=score)
        elif result.plate == pending["result"].plate and result.speed_kmh is not None:
            pending["result"].speed_kmh = result.speed_kmh

    def _emit_pending_if_due(self, current: float, *, force: bool = False):
        with self._candidate_lock:
            self._emit_pending_locked(current, force=force)

    def _emit_pending_locked(self, current: float, *, force: bool = False):
        pending = self._pending_candidate
        if pending is None or (not force and current - pending["started"] < _BEST_CAPTURE_WINDOW_SEC):
            return
        self._pending_candidate = None
        result = pending["result"]
        same_plate_recently = (
            result.plate == self._last_plate
            and (current - self._last_plate_time) < self.config.detection_cooldown_sec)
        if not same_plate_recently:
            self._last_plate = result.plate
            self._last_plate_time = current
            self.plate_recognized.emit(result, self.config.lane, pending["frame"])
