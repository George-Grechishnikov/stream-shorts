from shorts import select
from shorts.edit.subtitles import build_ass, group_words
from shorts.models import Candidate, Clip, Moment, Word, load_list, save_json
from shorts.transcribe import merge_windows, words_between
from tests.conftest import write_meta


def speech(start: float, end: float, step: float = 0.5, pause_every: int = 6) -> list[Word]:
    """Слова по 0.4 с, каждые pause_every слов — пауза 0.6 с."""
    out, t, i = [], start, 0
    while t + 0.4 <= end:
        out.append(Word(start=round(t, 2), end=round(t + 0.4, 2), text=f"w{i}", prob=0.9))
        i += 1
        t += step + (0.6 if i % pause_every == 0 else 0)
    return out


SC = {"min_len": 15, "max_len": 59, "max_moments": 12}


def test_snap_lands_on_pauses_and_fits_length():
    words = speech(0, 120)
    s, e = select.snap(words, 30.3, 60.1, 15, 59)
    starts = {w.start for w in words}
    assert round(s + 0.15, 2) in starts         # начало — на начале слова
    assert 15 <= e - s <= 59


def test_snap_never_shorter_than_min_len_when_speech_ends():
    """Регрессия: после начала почти нет речи → раньше выходили ролики по 2–7 с."""
    words = speech(100, 104)                     # 4 с речи, дальше тишина
    s, e = select.snap(words, 100, 135, 15, 59)
    assert e - s >= 15


def test_snap_caps_max_len():
    words = speech(0, 300)
    s, e = select.snap(words, 0, 200, 15, 59)
    assert e - s <= 59


def test_snap_without_words():
    assert select.snap([], 10, 100, 15, 59) == (10, 69)


def test_heuristic_prefers_most_viewed_clip():
    c = Candidate(start=955, end=1020, peak=1000, score=12, reasons={})
    clips = [Clip(id="a", title="мало", views=5, vod_offset=990, duration=20, created_at="", url=""),
             Clip(id="b", title="Лучший клип", views=900, vod_offset=1005, duration=25, created_at="", url="")]
    r = select.heuristic(c, speech(900, 1100), clips, SC)
    assert r["title"] == "Лучший клип" and r["keep"] and r["score"] == 10
    assert r["start"] <= 1003 and r["end"] >= 1025


def test_dedupe_drops_overlapping():
    a = Moment(id="a", start=0, end=30, title="", score=9)
    b = Moment(id="b", start=10, end=40, title="", score=8)
    c = Moment(id="c", start=100, end=130, title="", score=7)
    assert [m.id for m in select.dedupe([a, b, c])] == ["a", "c"]


def test_run_heuristic_end_to_end(job, cfg):
    write_meta(job, 3600)
    cands = [Candidate(start=p - 45, end=p + 20, peak=p, score=s, reasons={}) for p, s in
             [(600, 14), (1500, 11), (2400, 8)]]
    save_json(job.p("candidates.json"), cands)
    # окно 1500 — почти без речи (регрессия на min_len)
    tr = {"500-700": speech(500, 700), "1400-1600": speech(1474, 1478), "2300-2500": speech(2300, 2500)}
    save_json(job.p("transcript.json"), tr)
    ms = select.run(job, cfg)
    assert ms == load_list(job.p("moments.json"), Moment)
    assert [m.score for m in ms] == sorted((m.score for m in ms), reverse=True)
    assert len(ms) == 3
    for m in ms:
        assert 15 <= m.end - m.start <= 59
        assert m.title == "Стрим"                # без LLM и клипов — название стрима
        assert all(m.start <= w.start and w.end <= m.end for w in m.words)


def test_words_between_dedups_overlapping_windows():
    w = speech(10, 20)
    tr = {"0-15": [x for x in w if x.end <= 15], "12-30": [x for x in w if x.start >= 12]}
    got = words_between(tr, 10, 20)
    assert [x.start for x in got] == sorted({x.start for x in w})


def test_merge_windows_joins_overlaps():
    cs = [Candidate(start=100, end=160, peak=0, score=0, reasons={}),
          Candidate(start=150, end=200, peak=0, score=0, reasons={}),
          Candidate(start=500, end=560, peak=0, score=0, reasons={})]
    assert merge_windows(cs, 30, 3600) == [(70, 230), (470, 590)]


def test_subtitles_groups_and_no_overlap():
    words = speech(100, 110)
    assert all(1 <= len(g) <= 3 for g in group_words(words))
    ass = build_ass(words, 100, 10, hook="Хук")
    lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: 0,")]
    assert lines and "Hook" in ass

    def sec(t):
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    spans = [(sec(ln.split(",")[1]), sec(ln.split(",")[2])) for ln in lines]
    assert all(a < b for a, b in spans)
    assert all(spans[i][1] <= spans[i + 1][0] + 1e-6 for i in range(len(spans) - 1))  # строки не накладываются
