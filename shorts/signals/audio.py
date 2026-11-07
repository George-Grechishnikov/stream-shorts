"""Громкость по секундам из audio.wav (mono 16 кГц)."""
from __future__ import annotations

import wave

import numpy as np


def loudness_series(wav_path, chunk_sec: int = 600) -> np.ndarray:
    out = []
    with wave.open(str(wav_path)) as w:
        sr = w.getframerate()
        while True:
            raw = w.readframes(sr * chunk_sec)
            if not raw:
                break
            x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
            k = len(x) // sr
            if k == 0:
                break
            x = x[: k * sr].reshape(k, sr)
            rms = np.sqrt(np.mean(x * x, axis=1) + 1e-10)
            out.append(20 * np.log10(rms))
    return np.concatenate(out) if out else np.zeros(0)
