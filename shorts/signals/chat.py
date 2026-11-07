"""Сигналы чата: скорость сообщений и «хайп» (смех/восторг)."""
from __future__ import annotations

import re

import numpy as np

from ..models import ChatMessage

HYPE_EMOTES = {
    "KEKW", "LUL", "LULW", "OMEGALUL", "LMAO", "KEKL", "ICANT", "pepeLaugh", "EZ", "Pog", "PogChamp", "POGGERS",
    "PogU", "Pogey", "WAYTOODANK", "monkaS", "monkaW", "Clap", "catJAM", "GIGACHAD", "Kappa", "D:", "PepeHands",
    "Sadge", "WutFace", "KEKWait", "Aware", "Stare", "HUH", "xdd", "xddd", "Starege", "peepoClap",
}
HYPE_RX = re.compile(
    r"(а?ха(ха)+|ахах|хах|кек|ору|орн|лол|ржу|жесть|ахуе|имба|пог|вау|нифига|ничего себе|"
    r"\bxd+\b|\blo+l\b|lmao|haha|w+o+w+|😂|🤣|💀|🔥|😱|!!!+|\?\?\?+)", re.I)


def is_hype(m: ChatMessage) -> bool:
    if any(e in HYPE_EMOTES for e in m.emotes):
        return True
    words = set(m.text.split())
    if words & HYPE_EMOTES:
        return True
    return bool(HYPE_RX.search(m.text)) or (len(m.text) >= 6 and m.text.isupper())


def chat_series(msgs: list[ChatMessage], duration: float) -> dict[str, np.ndarray]:
    n = int(duration) + 1
    rate = np.zeros(n)
    hype = np.zeros(n)
    users: list[set] = [set() for _ in range(n)]
    for m in msgs:
        i = int(m.t)
        if 0 <= i < n:
            rate[i] += 1
            users[i].add(m.user)
            if is_hype(m):
                hype[i] += 1
    uniq = np.array([len(u) for u in users], dtype=float)
    return {"chat_rate": rate, "chat_users": uniq, "emote_hype": hype}


def top_messages(msgs: list[ChatMessage], start: float, end: float, k: int = 12) -> list[str]:
    """Самые частые сообщения окна — контекст для LLM."""
    from collections import Counter

    c = Counter(m.text.strip()[:60] for m in msgs if start <= m.t <= end and m.text.strip())
    return [f"{t} ×{n}" if n > 1 else t for t, n in c.most_common(k)]
