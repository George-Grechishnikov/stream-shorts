from __future__ import annotations

import math

import numpy as np

from ..models import Clip


def clip_series(clips: list[Clip], duration: float) -> np.ndarray:
    n = int(duration) + 1
    x = np.zeros(n)
    for c in clips:
        a = int(max(0, c.vod_offset))
        b = int(min(n, c.vod_offset + max(c.duration, 5)))
        x[a:b] += math.log1p(max(c.views, 0)) + 1.0
    return x
