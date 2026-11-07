"""Общие фикстуры: временная папка задачи и конфиг по умолчанию (без GPU и сети)."""
from __future__ import annotations

import wave

import numpy as np
import pytest

from shorts.config import DEFAULTS, _merge
from shorts.jobs import open_job
from shorts.models import ChatMessage, VodMeta, save_json, save_jsonl


@pytest.fixture
def cfg(tmp_path):
    return _merge(DEFAULTS, {"work_dir": str(tmp_path / "work")})


@pytest.fixture
def job(cfg):
    return open_job(cfg["work_dir"], "t1")


def write_meta(job, duration: float) -> None:
    save_json(job.p("meta.json"), VodMeta(id="1", url="https://www.twitch.tv/videos/1", channel="ch", title="Стрим",
                                          duration=duration, created_at="", width=1920, height=1080, fps=30))


def write_wav(path, seconds: int, loud: dict[int, float] | None = None, sr: int = 16000) -> None:
    """Тихий шум ~-40 дБ, на секундах из loud — громкий синус с заданной амплитудой."""
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.01, seconds * sr)
    for s, amp in (loud or {}).items():
        t = np.arange(sr) / sr
        x[s * sr:(s + 1) * sr] = amp * np.sin(2 * np.pi * 440 * t)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def write_chat(job, msgs: list[ChatMessage]) -> None:
    save_jsonl(job.p("chat.jsonl"), msgs)
