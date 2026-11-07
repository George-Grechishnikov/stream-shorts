"""Шаг transcribe: faster-whisper по окнам кандидатов (±pad) с таймингом слов → transcript.json."""
from __future__ import annotations

import wave

import numpy as np

from .jobs import Job
from .models import Candidate, Word, load_json, load_list, save_json

PAD = 30.0  # расшифровываем шире окна, чтобы select мог сдвинуть границы


def read_wav(path, start: float, end: float) -> np.ndarray:
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        w.setpos(int(max(0, start) * sr))
        raw = w.readframes(int((end - max(0, start)) * sr))
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def merge_windows(cands: list[Candidate], pad: float, dur: float) -> list[tuple[float, float]]:
    ws = sorted((max(0.0, c.start - pad), min(dur, c.end + pad)) for c in cands)
    out: list[list[float]] = []
    for a, b in ws:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def load_model(cfg: dict):
    from faster_whisper import WhisperModel

    w = cfg["whisper"]
    try:
        return WhisperModel(w["model"], device=w["device"], compute_type=w["compute_type"])
    except Exception:  # нет CUDA → CPU
        return WhisperModel(w["model"] if w["device"] == "cpu" else "small", device="cpu", compute_type="int8")


def transcribe_window(model, audio: np.ndarray, offset: float, language: str | None) -> list[Word]:
    segs, _ = model.transcribe(audio, language=language, word_timestamps=True, vad_filter=True,
                               vad_parameters={"min_silence_duration_ms": 300}, beam_size=5,
                               condition_on_previous_text=False)
    words = []
    for s in segs:
        for w in s.words or []:
            words.append(Word(start=round(offset + w.start, 2), end=round(offset + w.end, 2),
                              text=w.word.strip(), prob=round(float(w.probability), 3)))
    return words


def run(job: Job, cfg: dict) -> dict:
    cands = load_list(job.p("candidates.json"), Candidate)
    dur = load_json(job.p("meta.json"))["duration"]
    out = load_json(job.p("transcript.json")) if job.p("transcript.json").exists() else {}
    wins = [w for w in merge_windows(cands, PAD, dur) if f"{w[0]:.0f}-{w[1]:.0f}" not in out]
    if wins:
        model = load_model(cfg)
        for i, (a, b) in enumerate(wins, 1):
            words = transcribe_window(model, read_wav(job.p("audio.wav"), a, b), a, cfg["whisper"].get("language"))
            out[f"{a:.0f}-{b:.0f}"] = words
            save_json(job.p("transcript.json"), out)
            job.log(f"расшифровка {i}/{len(wins)}: {a:.0f}-{b:.0f} → {len(words)} слов")
    return out


def words_between(transcript: dict, start: float, end: float) -> list[Word]:
    ws: list[Word] = []
    for key, lst in transcript.items():
        a, b = (float(x) for x in key.split("-"))
        if b < start or a > end:
            continue
        for w in lst:
            w = Word(**w) if isinstance(w, dict) else w
            if start <= w.start and w.end <= end:
                ws.append(w)
    ws.sort(key=lambda w: w.start)
    dedup: list[Word] = []
    for w in ws:
        if not dedup or w.start >= dedup[-1].end - 0.01:
            dedup.append(w)
    return dedup
