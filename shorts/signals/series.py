"""Посекундные ряды и робастная нормировка."""
from __future__ import annotations

import numpy as np


def smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    k = np.ones(win) / win
    return np.convolve(x, k, mode="same")


def shift(x: np.ndarray, sec: int) -> np.ndarray:
    """Сдвиг влево на sec (реакция чата запаздывает → переносим её к причине)."""
    if sec <= 0:
        return x
    return np.concatenate([x[sec:], np.zeros(sec)])


def robust_z(x: np.ndarray, win: int = 600, cap: float = 6.0) -> np.ndarray:
    """(x - скользящая медиана) / (1.4826·MAD), окно win сек, обрезка в [0, cap]."""
    n = len(x)
    if n == 0:
        return x
    step = max(1, win // 4)
    centers = np.arange(0, n, step)
    med = np.empty(len(centers))
    mad = np.empty(len(centers))
    for i, c in enumerate(centers):
        seg = x[max(0, c - win // 2): min(n, c + win // 2)]
        m = np.median(seg)
        med[i] = m
        mad[i] = np.median(np.abs(seg - m))
    med_i = np.interp(np.arange(n), centers, med)
    mad_i = np.interp(np.arange(n), centers, mad)
    floor = max(1e-6, float(np.percentile(np.abs(x - med_i), 75)) * 0.5)
    z = (x - med_i) / (1.4826 * np.maximum(mad_i, floor))
    return np.clip(z, 0, cap)
