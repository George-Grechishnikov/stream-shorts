"""Загрузка: метаданные и аудио VOD (yt-dlp), чат (TwitchDownloaderCLI), клипы (Helix API).

Видео целиком не качаем: 6-часовой VOD в 1080p60 — это 20+ ГБ. Для анализа хватает аудио,
а куски видео под выбранные моменты докачиваются на шаге render (fetch_section).
Локальный файл вместо ссылки тоже поддерживается.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import requests

from ..jobs import Job
from ..models import ChatMessage, Clip, VodMeta, save_json, save_jsonl


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


# ---------------------------------------------------------------- meta + audio
def fetch_meta(url: str) -> tuple[VodMeta, dict]:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as y:
        info = y.extract_info(url, download=False)
    ts = info.get("timestamp")
    meta = VodMeta(
        id=str(info.get("id", "")).lstrip("v"),
        url=url,
        channel=info.get("uploader_id") or info.get("uploader") or "",
        title=info.get("title") or "",
        duration=float(info.get("duration") or 0),
        created_at=dt.datetime.fromtimestamp(ts, dt.UTC).isoformat() if ts else "",
        width=int(info.get("width") or 0),
        height=int(info.get("height") or 0),
        fps=float(info.get("fps") or 0),
    )
    return meta, info


def download_audio(url: str, out_wav: Path, ffmpeg: str = "ffmpeg") -> None:
    """Самая лёгкая аудиодорожка → mono 16 кГц WAV (для whisper и громкости)."""
    import yt_dlp

    tmp = out_wav.with_suffix(".src")
    opts = {"quiet": True, "format": "audio_only/bestaudio/worst", "outtmpl": str(tmp) + ".%(ext)s",
            "concurrent_fragment_downloads": 8, "noprogress": True}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])
    src = next(tmp.parent.glob(tmp.name + ".*"))
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(out_wav)], check=True)
    src.unlink(missing_ok=True)


def fetch_section(url: str, start: float, end: float, out_mp4: Path, max_height: int = 1080) -> Path:
    """Докачать кусок видео [start, end] (HLS-сегменты) — для рендера одного момента."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    opts = {"quiet": True, "noprogress": True,
            "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
            "download_ranges": download_range_func(None, [(max(0.0, start), end)]),
            "force_keyframes_at_cuts": False, "outtmpl": str(out_mp4.with_suffix("")) + ".%(ext)s",
            "merge_output_format": "mp4", "concurrent_fragment_downloads": 8}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])
    got = next(out_mp4.parent.glob(out_mp4.stem + ".*"))
    if got != out_mp4:
        got.replace(out_mp4)
    return out_mp4


def probe_local(path: Path, ffprobe: str = "ffprobe") -> VodMeta:
    r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), {})
    num, _, den = (v.get("r_frame_rate") or "0/1").partition("/")
    return VodMeta(id=path.stem, url=str(path.resolve()), title=path.stem, duration=float(j["format"]["duration"]),
                   width=int(v.get("width", 0)), height=int(v.get("height", 0)), fps=float(num) / float(den or 1))


# ---------------------------------------------------------------- chat
def download_chat(vod_id: str, out_json: Path, tool: str) -> bool:
    exe = shutil.which(tool) or (tool if Path(tool).exists() else None)
    if not exe:
        return False
    subprocess.run([exe, "chatdownload", "--id", vod_id, "-o", str(out_json), "--compression", "None",
                    "-E", "--collision", "Overwrite"], check=True)
    return True


def parse_twitchdownloader_chat(path: Path) -> list[ChatMessage]:
    j = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for c in j.get("comments", []):
        msg = c.get("message", {})
        frags = msg.get("fragments") or []
        emotes = [f["text"] for f in frags if f.get("emoticon")]
        out.append(ChatMessage(t=float(c.get("content_offset_seconds", 0)),
                               user=(c.get("commenter") or {}).get("display_name", ""),
                               text=msg.get("body", ""), emotes=emotes))
    out.sort(key=lambda m: m.t)
    return out


# ---------------------------------------------------------------- clips (Helix)
class Helix:
    def __init__(self, client_id: str, client_secret: str):
        r = requests.post("https://id.twitch.tv/oauth2/token", timeout=30, data={
            "client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
        r.raise_for_status()
        self.h = {"Client-Id": client_id, "Authorization": "Bearer " + r.json()["access_token"]}

    def get(self, path: str, **params) -> dict:
        r = requests.get("https://api.twitch.tv/helix/" + path, headers=self.h, params=params, timeout=30)
        r.raise_for_status()
        return r.json()


def fetch_clips(helix: Helix, meta: VodMeta) -> list[Clip]:
    """Клипы, сделанные из этого VOD (video_id совпадает), с vod_offset."""
    users = helix.get("users", login=meta.channel)["data"]
    if not users:
        return []
    bid = users[0]["id"]
    start = dt.datetime.fromisoformat(meta.created_at) if meta.created_at else None
    params = {"broadcaster_id": bid, "first": 100}
    if start:
        params["started_at"] = start.isoformat().replace("+00:00", "Z")
        params["ended_at"] = (start + dt.timedelta(seconds=meta.duration + 7 * 86400)).isoformat().replace("+00:00", "Z")
    out, cursor = [], None
    for _ in range(50):
        if cursor:
            params["after"] = cursor
        j = helix.get("clips", **params)
        for c in j["data"]:
            if c.get("video_id") == meta.id and c.get("vod_offset") is not None:
                out.append(Clip(id=c["id"], title=c["title"], views=int(c["view_count"]), vod_offset=float(c["vod_offset"]),
                                duration=float(c["duration"]), created_at=c["created_at"], url=c["url"]))
        cursor = j.get("pagination", {}).get("cursor")
        if not cursor:
            break
    out.sort(key=lambda c: -c.views)
    return out


# ---------------------------------------------------------------- step
def run(job: Job, source: str, cfg: dict) -> None:
    ff = cfg["tools"]["ffmpeg"]
    if is_url(source):
        meta, _ = fetch_meta(source)
        job.log(f"VOD {meta.id}: «{meta.title}» {meta.channel}, {meta.duration/3600:.1f} ч")
        save_json(job.p("meta.json"), meta)
        if not job.p("audio.wav").exists():
            job.log("качаю аудио…")
            download_audio(source, job.p("audio.wav"), ff)
        # чат
        if not job.p("chat.jsonl").exists():
            raw = job.p("chat_raw.json")
            try:
                if download_chat(meta.id, raw, cfg["tools"]["twitch_downloader"]):
                    msgs = parse_twitchdownloader_chat(raw)
                    save_jsonl(job.p("chat.jsonl"), msgs)
                    job.log(f"чат: {len(msgs)} сообщений")
                else:
                    job.log("!! TwitchDownloaderCLI не найден — чат пропущен")
            except Exception as e:  # noqa: BLE001
                job.log(f"!! чат не скачан: {e}")
        # клипы
        if not job.p("clips.json").exists():
            tw = cfg["twitch"]
            if tw.get("client_id") and tw.get("client_secret"):
                try:
                    clips = fetch_clips(Helix(tw["client_id"], tw["client_secret"]), meta)
                    save_json(job.p("clips.json"), clips)
                    job.log(f"клипы: {len(clips)}")
                except Exception as e:  # noqa: BLE001
                    job.log(f"!! клипы не получены: {e}")
            else:
                job.log("!! нет twitch.client_id/secret — клипы пропущены")
    else:
        path = Path(source)
        meta = probe_local(path, cfg["tools"]["ffprobe"])
        save_json(job.p("meta.json"), meta)
        if not job.p("audio.wav").exists():
            subprocess.run([ff, "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                            str(job.p("audio.wav"))], check=True)
        # локальный чат/клипы можно положить рядом: <file>.chat.json (формат TwitchDownloader)
        side = path.with_suffix(".chat.json")
        if side.exists() and not job.p("chat.jsonl").exists():
            save_jsonl(job.p("chat.jsonl"), parse_twitchdownloader_chat(side))
