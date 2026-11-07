"""Загрузка config.yaml поверх значений по умолчанию."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "work_dir": "work",
    "keep_source": True,
    "twitch": {"client_id": "", "client_secret": ""},
    "tools": {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "twitch_downloader": "TwitchDownloaderCLI"},
    "whisper": {"model": "large-v3", "device": "cuda", "compute_type": "int8_float16", "language": "ru"},
    "llm": {"provider": "none", "model": "", "api_key_env": "ANTHROPIC_API_KEY"},
    "candidates": {"top_n": 30, "min_gap": 90, "chat_delay": 6, "before": 45, "after": 20},
    "select": {"max_moments": 12, "min_len": 15, "max_len": 59},
    "render": {"encoder": "h264_nvenc", "layout": "cam_top", "fps": 30, "width": 1080, "height": 1920},
}


def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else Path("config.yaml")
    user = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _merge(DEFAULTS, user or {})
