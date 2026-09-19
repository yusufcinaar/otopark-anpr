"""Kamera karesinden plaka metnine giden ONNX tabanli ANPR hatti."""
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.anpr.fast_engine import predict
from app.anpr.plate_format import parse_plate, format_display


@dataclass
class PlateResult:
    plate: str
    display: str
    confidence: float
    is_valid_format: bool
    crop: np.ndarray
    bbox: tuple[int, int, int, int] | None = None
    speed_kmh: float | None = None


def _mean_confidence(value) -> float:
    if isinstance(value, (list, tuple, np.ndarray)):
        return sum(float(x) for x in value) / max(len(value), 1)
    return float(value or 0.0)


def recognize_plate(frame: np.ndarray, min_confidence: float = 0.35) -> Optional[PlateResult]:
    best: Optional[PlateResult] = None
    for result in predict(frame):
        if not result.ocr or not result.ocr.text:
            continue
        confidence = _mean_confidence(result.ocr.confidence)
        if confidence < min_confidence:
            continue

        plate, is_valid = parse_plate(result.ocr.text)
        box = result.detection.bounding_box
        h, w = frame.shape[:2]
        x1, y1 = max(0, int(box.x1)), max(0, int(box.y1))
        x2, y2 = min(w, int(box.x2)), min(h, int(box.y2))
        region = frame[y1:y2, x1:x2].copy()
        candidate = PlateResult(
            plate=plate,
            display=format_display(plate) if is_valid else plate,
            confidence=confidence,
            is_valid_format=is_valid,
            crop=region,
            bbox=(x1, y1, x2, y2),
        )
        if best is None or (candidate.is_valid_format and not best.is_valid_format) \
                or (candidate.is_valid_format == best.is_valid_format
                    and candidate.confidence > best.confidence):
            best = candidate
    return best
