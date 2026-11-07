"""Субтитры ASS: по 1–3 слова, текущее слово подсвечено; плашка-хук в начале."""
from __future__ import annotations

from ..models import Word

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,{ol},3,2,60,60,{mv},1
Style: Hook,{font},{hs},&H00101010,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,18,0,8,80,80,{hv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
HL = r"{\c&H00E6FF&}"   # жёлтый (BGR)
NORM = r"{\c&HFFFFFF&}"


def ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(words: list[Word], max_words: int = 3, max_chars: int = 18, gap: float = 0.5) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        txt = sum(len(x.text) + 1 for x in cur) + len(w.text)
        end_sentence = bool(cur) and cur[-1].text[-1:] in ".?!…"
        if cur and (len(cur) >= max_words or txt > max_chars or w.start - cur[-1].end > gap or end_sentence):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[Word], t0: float, dur: float, hook: str = "", w: int = 1080, h: int = 1920,
              font: str = "Inter Black", sub_y: int = 560, upper: bool = True) -> str:
    """words — с абсолютными временами VOD; t0 — начало ролика."""
    out = [HEADER.format(w=w, h=h, font=font, fs=86, ol=7, mv=sub_y, hs=64, hv=150)]
    groups = group_words(words)
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0].start if gi + 1 < len(groups) else 1e9
        g_end = min(g[-1].end + 0.25, nxt)
        for i, wd in enumerate(g):
            a = wd.start - t0
            b = (g[i + 1].start if i + 1 < len(g) else g_end) - t0
            if b <= 0 or a >= dur:
                continue
            parts = []
            for j, x in enumerate(g):
                t = esc(x.text.upper() if upper else x.text)
                parts.append((HL + t + NORM) if j == i else t)
            pop = r"{\fscx108\fscy108\t(0,80,\fscx100\fscy100)}" if i == 0 else ""
            out.append(f"Dialogue: 0,{ts(a)},{ts(min(b, dur))},Sub,,0,0,0,,{pop}{' '.join(parts)}")
    if hook:
        out.append(f"Dialogue: 1,{ts(0.05)},{ts(min(2.6, dur))},Hook,,0,0,0,,{{\\fad(120,200)}}{esc(hook)}")
    return "\n".join(out) + "\n"
