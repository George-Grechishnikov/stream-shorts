"""Шаг candidates: сумма нормированных сигналов → пики → окна-кандидаты (SPEC §4)."""
from __future__ import annotations

import numpy as np

from .jobs import Job
from .models import Candidate, load_json, save_json
from .signals.series import robust_z, shift, smooth

WEIGHTS = {"clips": 3.0, "emote_hype": 2.0, "chat_rate": 1.0, "chat_users": 0.7, "loudness": 0.7, "laughter": 1.0}
CHAT_KEYS = ("chat_rate", "chat_users", "emote_hype")


def score_series(series: dict[str, np.ndarray], chat_delay: int = 6) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    parts: dict[str, np.ndarray] = {}
    for k, x in series.items():
        if k not in WEIGHTS or len(x) == 0 or not np.any(x):
            continue
        x = np.asarray(x, float)
        if k in CHAT_KEYS:
            x = shift(smooth(x, 5), chat_delay)
        elif k == "loudness":
            x = smooth(x, 3)
        elif k == "clips":
            parts[k] = WEIGHTS[k] * np.clip(x / max(1e-6, np.percentile(x[x > 0], 90) if np.any(x > 0) else 1), 0, 2) * 3
            continue
        parts[k] = WEIGHTS[k] * robust_z(x)
    n = max((len(v) for v in parts.values()), default=0)
    total = np.zeros(n)
    for v in parts.values():
        total[: len(v)] += v
    return smooth(total, 7), parts


def pick_peaks(score: np.ndarray, top_n: int, min_gap: int) -> list[int]:
    order = np.argsort(-score)
    taken: list[int] = []
    for i in order:
        if score[i] <= 0:
            break
        if all(abs(int(i) - t) >= min_gap for t in taken):
            taken.append(int(i))
            if len(taken) >= top_n:
                break
    return sorted(taken)


def run(job: Job, cfg: dict) -> list[Candidate]:
    c = cfg["candidates"]
    sig = load_json(job.p("signals.json"))["series"]
    series = {k: np.asarray(v, float) for k, v in sig.items()}
    score, parts = score_series(series, int(c["chat_delay"]))
    n = len(score)
    cands = []
    for p in pick_peaks(score, int(c["top_n"]), int(c["min_gap"])):
        a, b = max(0, p - int(c["before"])), min(n - 1, p + int(c["after"]))
        reasons = {k: round(float(v[max(0, p - 10): p + 10].max()), 2) for k, v in parts.items()}
        cands.append(Candidate(start=float(a), end=float(b), peak=float(p), score=round(float(score[p]), 2), reasons=reasons))
    cands.sort(key=lambda x: -x.score)
    save_json(job.p("candidates.json"), cands)
    sig["score"] = np.round(score, 3).tolist()
    save_json(job.p("signals.json"), {"step": 1.0, "series": sig})
    job.log(f"кандидатов: {len(cands)}; лучшие: " + ", ".join(f"{fmt(x.peak)}({x.score})" for x in cands[:8]))
    return cands


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
