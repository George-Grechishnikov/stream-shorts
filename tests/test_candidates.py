import numpy as np

from shorts import candidates
from shorts.models import Candidate, load_json, load_list, save_json


def chat_with_bursts(n: int, bursts: dict[int, float], seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.poisson(2, n).astype(float)
    for t, k in bursts.items():
        x[t:t + 8] += k
    return x


def test_pick_peaks_respects_gap_and_top_n():
    s = np.zeros(1000)
    s[[100, 130, 400, 700]] = [5, 6, 4, 3]
    assert candidates.pick_peaks(s, top_n=10, min_gap=90) == [130, 400, 700]
    assert candidates.pick_peaks(s, top_n=2, min_gap=90) == [130, 400]


def test_pick_peaks_ignores_nonpositive():
    assert candidates.pick_peaks(np.zeros(100), 5, 10) == []


def test_score_series_shifts_chat_back_by_delay():
    n = 3600
    chat = chat_with_bursts(n, {1206: 40})        # реакция чата через ~6 с после события в 1200
    score, parts = candidates.score_series({"chat_rate": chat}, chat_delay=6)
    assert set(parts) == {"chat_rate"}
    assert abs(int(score.argmax()) - 1203) <= 4  # центр всплеска 1206..1213, сдвинутый на 6 с назад


def test_score_series_skips_empty_and_unknown():
    score, parts = candidates.score_series({"chat_rate": np.zeros(100), "foo": np.ones(100)})
    assert parts == {} and len(score) == 0


def test_run_writes_candidates_and_score(job, cfg):
    n = 3600
    chat = chat_with_bursts(n, {600: 30, 2000: 60})
    save_json(job.p("signals.json"), {"step": 1.0, "series": {"chat_rate": chat.tolist(), "chat_users": chat.tolist(),
                                                              "emote_hype": (chat / 3).tolist()}})
    out = candidates.run(job, cfg)
    assert out == load_list(job.p("candidates.json"), Candidate)
    peaks = sorted(c.peak for c in out[:2])
    assert abs(peaks[0] - 597) <= 10 and abs(peaks[1] - 1997) <= 10
    assert out[0].score >= out[1].score  # оба всплеска упираются в потолок z=6 — порядок между ними не важен
    assert all(x.score < out[1].score for x in out[2:])
    c = out[0]
    assert c.end - c.start == cfg["candidates"]["before"] + cfg["candidates"]["after"]
    assert "chat_rate" in c.reasons
    assert len(load_json(job.p("signals.json"))["series"]["score"]) == n
