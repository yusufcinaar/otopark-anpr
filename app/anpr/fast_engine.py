"""ONNX tabanli, plaka icin ozel egitilmis hizli ANPR motoru."""
import os
import threading

# ONNX/OpenMP'nin tum cekirdekleri ayni anda doldurup arayuzu kilitlemesini
# engeller. Kullanici ortamda farkli deger verdiyse ona dokunulmaz.
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
os.environ.setdefault("OMP_DYNAMIC", "TRUE")

_engine = None
_init_lock = threading.Lock()
_predict_lock = threading.Lock()


def get_engine():
    global _engine
    if _engine is None:
        with _init_lock:
            if _engine is None:
                from fast_alpr import ALPR
                _engine = ALPR(
                    # 640 model giris kameralarindaki kucuk/uzak plakalari
                    # 384 modele gore daha guvenilir yakalar.
                    detector_model="yolo-v9-t-640-license-plate-end2end",
                    detector_conf_thresh=0.12,
                    ocr_model="cct-s-v2-global-model",
                    ocr_device="cpu",
                )
    return _engine


def predict(frame):
    engine = get_engine()
    with _predict_lock:
        return engine.predict(frame)
