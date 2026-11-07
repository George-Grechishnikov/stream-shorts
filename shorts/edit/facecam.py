"""Поиск области вебкамеры: лицо, стабильное на нескольких кадрах → прямоугольник камеры (доли кадра).

Детектор — YuNet (cv2.FaceDetectorYN, OpenCV ≥ 4.8). Модель лежит в assets/models
(MIT, opencv_zoo); если её нет, качается один раз с media.githubusercontent.com.

Результат — догадка: его стоит проверить и поправить в profiles/<channel>.yaml.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
MODEL_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
             "models/face_detection_yunet/face_detection_yunet_2023mar.onnx")

DET_W = 960            # кадр уменьшается до этой ширины перед детекцией
SCORE = 0.7            # порог уверенности YuNet
MIN_FACE = 0.04        # минимальная высота лица, доля высоты кадра
CLUSTER = 0.06         # лица ближе этого (доля кадра) считаются одним местом


def _model_path() -> Path | None:
    if MODEL.exists() and MODEL.stat().st_size > 100_000:
        return MODEL
    try:
        import requests

        r = requests.get(MODEL_URL, timeout=60)
        r.raise_for_status()
        if len(r.content) < 100_000:  # LFS-заглушка вместо модели
            return None
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        MODEL.write_bytes(r.content)
        return MODEL
    except Exception:  # noqa: BLE001
        return None


def _detector(w: int, h: int):
    import cv2

    if not hasattr(cv2, "FaceDetectorYN"):
        return None
    mp = _model_path()
    if mp is None:
        return None
    return cv2.FaceDetectorYN.create(str(mp), "", (w, h), SCORE, 0.3, 50)


def faces_in_frame(det, frame: np.ndarray, scale: float) -> list[tuple[float, float, float, float, float]]:
    """Лица на кадре в пикселях исходного кадра: (x, y, w, h, score)."""
    import cv2

    small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1 else frame
    det.setInputSize((small.shape[1], small.shape[0]))
    _, faces = det.detect(small)
    if faces is None:
        return []
    return [(f[0] / scale, f[1] / scale, f[2] / scale, f[3] / scale, float(f[-1])) for f in faces]


def _stable_face(boxes: list[tuple], W: float, H: float, need: int) -> np.ndarray | None:
    """Самая частая позиция лица по кадрам (лицо стримера стоит на месте, лица в игре/видео — нет)."""
    if not boxes:
        return None
    arr = np.array([b[:4] for b in boxes], dtype=float)
    cx = (arr[:, 0] + arr[:, 2] / 2) / W
    cy = (arr[:, 1] + arr[:, 3] / 2) / H
    best: np.ndarray | None = None
    for i in range(len(arr)):
        near = (np.abs(cx - cx[i]) < CLUSTER) & (np.abs(cy - cy[i]) < CLUSTER)
        if best is None or near.sum() > best.sum():
            best = near
    if best is None or best.sum() < need:
        return None
    return np.median(arr[best], axis=0)


def detect_cam(video: str | Path, samples: int = 16) -> tuple[float, float, float, float] | None:
    import cv2

    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    W, H = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    if not W or not H:
        return None
    scale = min(1.0, DET_W / W)
    det = _detector(int(W * scale), int(H * scale))
    if det is None:
        return None
    boxes = []
    for i in np.linspace(0, n - 1, samples).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if not ok:
            continue
        faces = [f for f in faces_in_frame(det, fr, scale) if f[3] >= H * MIN_FACE]
        boxes.extend(faces)
    cap.release()
    face = _stable_face(boxes, W, H, need=max(3, samples // 3))
    if face is None:
        return None
    x, y, w, h = face
    # камера обычно ~3× лица по ширине, 4:3; лицо чуть выше центра
    cw, ch = w * 3.2, w * 3.2 * 0.75
    cx, cy = x + w / 2, y + h / 2 + h * 0.3
    cw, ch = min(cw, W), min(ch, H)
    x0 = min(max(0.0, cx - cw / 2), W - cw)
    y0 = min(max(0.0, cy - ch / 2), H - ch)
    # float(): np.float64 в профиле yaml сохранился бы как !!python/object
    return tuple(round(float(v), 4) for v in (x0 / W, y0 / H, cw / W, ch / H))  # type: ignore[return-value]
