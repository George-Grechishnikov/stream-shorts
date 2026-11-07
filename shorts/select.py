"""Шаг select: из кандидатов → лучшие моменты с точными границами и названием (SPEC §5)."""
from __future__ import annotations

import json
import os
import re

from .jobs import Job
from .models import (
    Candidate,
    ChatMessage,
    Clip,
    Moment,
    VodMeta,
    Word,
    from_dict,
    load_json,
    load_jsonl,
    load_list,
    save_json,
)
from .signals.chat import top_messages
from .transcribe import words_between

PROMPT = """Ты монтажёр вертикальных роликов (Shorts/TikTok) из стримов. Ниже кусок стрима: расшифровка речи стримера
с таймкодами (секунды от начала стрима), реакции чата и клипы зрителей вокруг пика активности.

Стрим: «{title}» ({channel})
Пик активности чата/клипов: {peak:.0f} c. Окно расшифровки: {a:.0f}–{b:.0f} c.
Клипы зрителей рядом: {clips}
Частые сообщения чата: {chat}

Расшифровка (старт_с: текст):
{text}

Реши, получится ли из этого самостоятельный цепляющий ролик, и выбери точные границы.
Правила: длина {min_len}–{max_len} с; начинать с фразы/действия, которое сразу цепляет (без долгого вступления);
заканчивать на развязке/реакции, не посреди фразы; ролик должен быть понятен без контекста стрима.
Если момент — просто шум, болтовня или ожидание, keep=false.
Ответь ТОЛЬКО JSON: {{"keep": true, "start": 0.0, "end": 0.0, "title": "название ролика до 60 символов, по-русски, кликабельно, без кавычек",
"hook": "короткая плашка в начале, 2-5 слов", "score": 0-10, "reason": "одной фразой, что тут смешного/интересного"}}"""


def fmt_words(words: list[Word], gap: float = 0.6) -> str:
    lines, cur, t0, last = [], [], None, -9.0
    for w in words:
        if cur and (w.start - last > gap or len(cur) >= 14):
            lines.append(f"{t0:.1f}: {' '.join(cur)}")
            cur = []
        if not cur:
            t0 = w.start
        cur.append(w.text)
        last = w.end
    if cur:
        lines.append(f"{t0:.1f}: {' '.join(cur)}")
    return "\n".join(lines)


def call_llm(cfg: dict, prompt: str) -> dict | None:
    llm = cfg["llm"]
    key = os.environ.get(llm.get("api_key_env") or "", "")
    if llm["provider"] == "anthropic":
        import anthropic

        r = anthropic.Anthropic(api_key=key or None).messages.create(
            model=llm["model"] or "claude-sonnet-4-5", max_tokens=600, messages=[{"role": "user", "content": prompt}])
        txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    elif llm["provider"] == "openai":
        from openai import OpenAI

        r = OpenAI(api_key=key or None).chat.completions.create(
            model=llm["model"] or "gpt-4.1-mini", messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"})
        txt = r.choices[0].message.content or ""
    else:
        return None
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0)) if m else None


# ------------------------------------------------------------ границы по словам
def snap(words: list[Word], start: float, end: float, min_len: float, max_len: float) -> tuple[float, float]:
    """Подвинуть границы на паузы между словами (не резать слово), уложить в длину."""
    if not words:
        return start, min(end, start + max_len)
    gaps = [(words[i].end, words[i + 1].start) for i in range(len(words) - 1)]
    starts = [words[0].start] + [b for a, b in gaps if b - a >= 0.25]
    ends = [a for a, b in gaps if b - a >= 0.25] + [words[-1].end]
    s = min(starts, key=lambda x: abs(x - start))
    e = min(ends, key=lambda x: abs(x - end) + (100 if x - s < min_len else 0) + (100 if x - s > max_len else 0))
    if e - s < min_len:  # после s мало речи и подходящей паузы нет — тянем до min_len, а не отдаём огрызок
        e = max(e, s + min_len)
    s, e = max(0.0, s - 0.15), e + 0.35
    if e - s > max_len:
        e = s + max_len
    return round(s, 2), round(e, 2)


def heuristic(c: Candidate, words: list[Word], clips: list[Clip], sc: dict) -> dict:
    near = [k for k in clips if abs(k.vod_offset + k.duration / 2 - c.peak) < 60]
    if near:
        best = max(near, key=lambda k: k.views)
        a, b, title = best.vod_offset - 3, best.vod_offset + best.duration + 2, best.title
    else:
        a, b, title = c.peak - 25, c.peak + 10, ""
    a, b = snap(words, a, b, sc["min_len"], sc["max_len"])
    return {"keep": True, "start": a, "end": b, "title": title, "hook": "", "score": min(10, c.score), "reason": "эвристика"}


def run(job: Job, cfg: dict) -> list[Moment]:
    sc = cfg["select"]
    meta = from_dict(VodMeta, load_json(job.p("meta.json")))
    cands = load_list(job.p("candidates.json"), Candidate)
    transcript = load_json(job.p("transcript.json")) if job.p("transcript.json").exists() else {}
    chat = load_jsonl(job.p("chat.jsonl"), ChatMessage) if job.p("chat.jsonl").exists() else []
    clips = load_list(job.p("clips.json"), Clip) if job.p("clips.json").exists() else []
    moments: list[Moment] = []
    for i, c in enumerate(cands):
        a, b = c.start - 30, c.end + 30
        words = words_between(transcript, a, b)
        near = sorted((k for k in clips if a <= k.vod_offset <= b), key=lambda k: -k.views)[:5]
        res = None
        if cfg["llm"]["provider"] != "none":
            prompt = PROMPT.format(title=meta.title, channel=meta.channel, peak=c.peak, a=a, b=b,
                                   clips="; ".join(f"«{k.title}» {k.views} просм. @{k.vod_offset:.0f}c" for k in near) or "нет",
                                   chat=" | ".join(top_messages(chat, c.peak - 20, c.peak + 25)) or "нет",
                                   text=fmt_words(words) or "(речи нет)", min_len=sc["min_len"], max_len=sc["max_len"])
            try:
                res = call_llm(cfg, prompt)
            except Exception as e:  # noqa: BLE001
                job.log(f"!! LLM: {e}")
        if res is None:
            res = heuristic(c, words, clips, sc)
        if not res.get("keep"):
            job.log(f"  {c.peak:.0f}: отброшен — {res.get('reason', '')}")
            continue
        s, e = snap(words, float(res["start"]), float(res["end"]), sc["min_len"], sc["max_len"])
        mw = [w for w in words if s <= w.start and w.end <= e]
        title = (res.get("title") or (near[0].title if near else "") or meta.title)[:80]
        moments.append(Moment(id=f"m{i:02d}_{int(s)}", start=s, end=e, title=title, hook=res.get("hook", ""),
                              score=float(res.get("score", c.score)), reason=res.get("reason", ""), words=mw))
        job.log(f"  {s:.0f}–{e:.0f} ({e - s:.0f} c) [{moments[-1].score}] {title}")
    moments.sort(key=lambda m: -m.score)
    moments = dedupe(moments)[: int(sc["max_moments"])]
    save_json(job.p("moments.json"), moments)
    return moments


def dedupe(ms: list[Moment]) -> list[Moment]:
    out: list[Moment] = []
    for m in ms:
        if all(min(m.end, o.end) - max(m.start, o.start) < 0.3 * (m.end - m.start) for o in out):
            out.append(m)
    return out
