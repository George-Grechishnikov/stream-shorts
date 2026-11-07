"""Поиск области вебкамеры: лицо, стабильное на нескольких кадрах → прямоугольник камеры (доли кадра).

Результат — догадка: его стоит проверить и поправить в profiles/<channel>.yaml.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def detect_cam(video: str | Path, samples: int = 12) -> tuple[float, float, float, float] | None:
    import cv2

    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    W, H = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    try:
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except Exception:  # noqa: BLE001
        return None
    if casc.empty():
        return None
    boxes = []
    for i in np.linspace(0, n - 1, samples).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        f = casc.detectMultiScale(g, 1.1, 6, minSize=(int(H * 0.05), int(H * 0.05)))
        if len(f):
            boxes.append(max(f, key=lambda b: b[2] * b[3]))
    if len(boxes) < max(3, samples // 3):
        return None
    x, y, w, h = np.median(np.array(boxes), axis=0)
    # камера обычно ~3× лица по ширине, 16:9 или 4:3
    cw, ch = w * 3.2, w * 3.2 * 0.75
    cx, cy = x + w / 2, y + h / 2 + h * 0.3
    x0, y0 = max(0.0, cx - cw / 2), max(0.0, cy - ch / 2)
    x1, y1 = min(W, x0 + cw), min(H, y0 + ch)
    return (round(x0 / W, 4), round(y0 / H, 4), round((x1 - x0) / W, 4), round((y1 - y0) / H, 4))
