"""Шаг signals: посекундные ряды → signals.json."""
from __future__ import annotations

import numpy as np

from ..jobs import Job
from ..models import ChatMessage, Clip, VodMeta, from_dict, load_json, load_jsonl, load_list, save_json
from .audio import loudness_series
from .chat import chat_series
from .clips import clip_series


def run(job: Job, cfg: dict) -> None:
    meta = from_dict(VodMeta, load_json(job.p("meta.json")))
    n = int(meta.duration) + 1
    series: dict[str, np.ndarray] = {}
    if job.p("chat.jsonl").exists():
        series.update(chat_series(load_jsonl(job.p("chat.jsonl"), ChatMessage), meta.duration))
    if job.p("clips.json").exists():
        series["clips"] = clip_series(load_list(job.p("clips.json"), Clip), meta.duration)
    if job.p("audio.wav").exists():
        series["loudness"] = loudness_series(job.p("audio.wav"))
    # громкость в дБ: дополняем тишиной (-100), а не 0 дБ — иначе конец VOD выглядит самым громким местом
    fill = {"loudness": -100.0}
    fixed = {k: np.pad(v, (0, max(0, n - len(v))), constant_values=fill.get(k, 0.0))[:n] for k, v in series.items()}
    save_json(job.p("signals.json"), {"step": 1.0, "series": {k: np.round(v, 3).tolist() for k, v in fixed.items()}})
    job.log("сигналы: " + ", ".join(f"{k}(max {v.max():.1f})" for k, v in fixed.items()))
