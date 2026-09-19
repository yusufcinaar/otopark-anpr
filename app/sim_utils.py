"""Gercek kamera/donanim yokken test icin sentetik plaka goruntusu ureten yardimcilar.

SIMULATION_MODE=True oldugunda UI'daki "Arac Simule Et" butonlari bu modulu kullanir.
Gercek RTSP kameralar baglaninca bu modul artik kullanilmaz; app/camera.py gercek
kare akisini app/anpr/pipeline.py'a dogrudan verir.
"""
import random

import cv2
import numpy as np

_LETTERS = "ABCDEFGHIJKLMNOPRSTUVYZ"
_IL_KODLARI = [34, 6, 35, 16, 1, 42, 41, 7, 61, 55]


def random_plate() -> str:
    il = random.choice(_IL_KODLARI)
    harf = "".join(random.choice(_LETTERS) for _ in range(random.choice([2, 3])))
    rakam = random.randint(1, 999) if len(harf) == 3 else random.randint(1, 9999)
    return f"{il:02d}{harf}{rakam}"


def render_plate_image(plate_text: str, width: int = 520, height: int = 130) -> np.ndarray:
    """Beyaz zeminli, siyah cerceveli basit bir TR plakasi gorseli olusturur (BGR, numpy)."""
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.rectangle(img, (4, 4), (width - 5, height - 5), (0, 0, 0), 6)

    display = plate_text
    if len(plate_text) >= 5:
        # kaba bicimleme: il kodu / harf blogu / rakam blogu araciligiyla bosluk ekle
        import re
        m = re.match(r"^(\d{2})([A-Z]{1,3})(\d+)$", plate_text)
        if m:
            display = f"{m.group(1)} {m.group(2)} {m.group(3)}"

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 1.8
    thickness = 5
    (tw, th), _ = cv2.getTextSize(display, font, scale, thickness)
    x = max(10, (width - tw) // 2)
    y = (height + th) // 2
    cv2.putText(img, display, (x, y), font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    return img


def render_frame_with_plate(plate_text: str, scene_size=(640, 480)) -> np.ndarray:
    """Plakayi gri bir 'arac govdesi' sahnesinin ortasina yerlestirir; boylece
    detector.py'daki kontur tabanli plaka bolgesi bulma mantigi da test edilir."""
    w, h = scene_size
    scene = np.full((h, w, 3), (90, 90, 90), dtype=np.uint8)
    plate_img = render_plate_image(plate_text, width=int(w * 0.55), height=int(h * 0.18))
    ph, pw = plate_img.shape[:2]
    x0 = (w - pw) // 2
    y0 = int(h * 0.62)
    scene[y0:y0 + ph, x0:x0 + pw] = plate_img
    return scene
