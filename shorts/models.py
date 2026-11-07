"""Общий контракт данных между модулями (см. docs/SPEC.md §3). Все времена — секунды от начала VOD."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar


@dataclass
class VodMeta:
    id: str
    url: str
    channel: str = ""
    title: str = ""
    duration: float = 0.0
    created_at: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0


@dataclass
class ChatMessage:
    t: float
    user: str
    text: str
    emotes: list[str] = field(default_factory=list)


@dataclass
class Clip:
    id: str
    title: str
    views: int
    vod_offset: float
    duration: float
    created_at: str = ""
    url: str = ""


@dataclass
class Candidate:
    start: float
    end: float
    peak: float
    score: float
    reasons: dict[str, float] = field(default_factory=dict)


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Moment:
    id: str
    start: float
    end: float
    title: str
    hook: str = ""
    score: float = 0.0
    reason: str = ""
    words: list[Word] = field(default_factory=list)


@dataclass
class RenderResult:
    moment_id: str
    path: str
    duration: float
    layout: str
    thumbnail: str = ""


T = TypeVar("T")


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj


def from_dict(cls: type[T], d: dict) -> T:
    names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    kw = {k: v for k, v in d.items() if k in names}
    if cls is Moment and "words" in kw:
        kw["words"] = [Word(**w) if isinstance(w, dict) else w for w in kw["words"]]
    return cls(**kw)  # type: ignore[call-arg]


def save_json(path: Path | str, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(to_jsonable(obj), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_list(path: Path, cls: type[T]) -> list[T]:
    return [from_dict(cls, d) for d in load_json(path)]


def save_jsonl(path: Path | str, items: list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(to_jsonable(it), ensure_ascii=False) + "\n")


def load_jsonl(path: Path, cls: type[T]) -> list[T]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(from_dict(cls, json.loads(line)))
    return out
