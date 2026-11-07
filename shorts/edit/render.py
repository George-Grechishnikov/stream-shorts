"""Шаг render: момент → вертикальный ролик 1080×1920 (раскладка + субтитры + плашка + громкость)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from ..ingest.twitch import fetch_section, is_url
from ..jobs import Job
from ..models import Moment, RenderResult, VodMeta, from_dict, load_json, load_list, save_json
from .subtitles import build_ass

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "assets" / "fonts"
PROFILES = ROOT / "profiles"
PAD = 1.0  # запас при докачке куска


def load_profile(channel: str) -> dict:
    p = PROFILES / f"{channel.lower()}.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_profile(channel: str, prof: dict) -> None:
    PROFILES.mkdir(exist_ok=True)
    (PROFILES / f"{channel.lower()}.yaml").write_text(yaml.safe_dump(prof, allow_unicode=True), encoding="utf-8")


def layout_filter(layout: str, W: int, H: int, cam: list[float] | None, cam_h: float = 0.36) -> tuple[str, int]:
    """filter_complex от [0:v] до [base]; возвращает (фильтр, MarginV субтитров от низа)."""
    if layout == "cam_top" and cam:
        x, y, w, h = cam
        ch = int(H * cam_h) // 2 * 2
        gh = H - ch
        f = (f"[0:v]split=2[a][b];"
             f"[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{ch}:force_original_aspect_ratio=increase,crop={W}:{ch}[cam];"
             f"[b]scale=-2:{gh},crop={W}:{gh}[game];"
             f"[cam][game]vstack=inputs=2,setsar=1[base]")
        return f, int(gh * 0.38)
    if layout == "full_crop":
        return f"[0:v]scale=-2:{H},crop={W}:{H},setsar=1[base]", int(H * 0.22)
    # blur_bg: исходник по ширине в центре, сзади размытая копия
    f = (f"[0:v]split=2[a][b];"
         f"[a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},boxblur=8:2,scale={W}:{H},eq=brightness=-0.08[bg];"
         f"[b]scale={W}:-2[fg];[bg][fg]overlay=0:(H-h)/2,setsar=1[base]")
    return f, int(H * 0.25)


def encoder_args(enc: str) -> list[str]:
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]


def has_nvenc(ffmpeg: str) -> bool:
    try:
        r = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1", "-c:v", "h264_nvenc",
                            "-f", "null", "-"], capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def render_moment(job: Job, m: Moment, meta: VodMeta, cfg: dict, layout: str | None = None) -> RenderResult:
    rc = cfg["render"]
    ff = cfg["tools"]["ffmpeg"]
    W, H, fps = int(rc["width"]), int(rc["height"]), int(rc["fps"])
    prof = load_profile(meta.channel or "default")
    layout = layout or prof.get("layout") or rc["layout"]
    rd = job.p("renders")
    dur = round(m.end - m.start, 2)

    # источник
    if is_url(meta.url):
        src = rd / f"{m.id}_src.mp4"
        if not src.exists():
            job.log(f"  качаю кусок {m.start:.0f}–{m.end:.0f}")
            fetch_section(meta.url, m.start - PAD, m.end + PAD, src)
        ss, inp = PAD if m.start >= PAD else m.start, src
    else:
        ss, inp = m.start, Path(meta.url)

    # камера
    cam = prof.get("cam")
    if layout == "cam_top" and not cam:
        from .facecam import detect_cam

        found = detect_cam(inp)
        if found:
            cam = list(found)
            prof.update({"cam": cam, "layout": "cam_top", "cam_note": "найдено автоматически — проверь"})
            save_profile(meta.channel or "default", prof)
            job.log(f"  камера найдена: {cam}")
        else:
            layout = "blur_bg"

    vf, sub_mv = layout_filter(layout, W, H, cam, float(prof.get("cam_h", 0.36)))
    # Субтитры вшиваются в видео; .ass не кладём рядом с <id>.mp4 — плееры (VLC, MPC) подхватывают
    # одноимённый файл сами и показывают субтитры второй раз поверх вшитых.
    ass_dir = job.p("ass")
    ass_dir.mkdir(exist_ok=True)
    ass = ass_dir / f"{m.id}.ass"
    ass.write_text(build_ass(m.words, m.start, dur, m.hook, W, H, sub_y=sub_mv), encoding="utf-8")
    fonts_rel = os.path.relpath(FONTS, rd).replace("\\", "/")
    ass_rel = os.path.relpath(ass, rd).replace("\\", "/")
    vf += f";[base]subtitles={ass_rel}:fontsdir={fonts_rel}[v]"
    out = rd / f"{m.id}.mp4"
    enc = rc["encoder"] if rc["encoder"] != "h264_nvenc" or has_nvenc(ff) else "libx264"
    cmd = [ff, "-v", "error", "-y", "-ss", f"{ss:.2f}", "-t", f"{dur:.2f}", "-i", str(Path(inp).resolve()),
           "-filter_complex", vf, "-map", "[v]", "-map", "0:a?", "-r", str(fps),
           *encoder_args(enc), "-pix_fmt", "yuv420p",
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
           "-movflags", "+faststart", out.name]
    subprocess.run(cmd, check=True, cwd=rd)
    thumb = rd / f"{m.id}.jpg"
    subprocess.run([ff, "-v", "error", "-y", "-ss", f"{min(1.5, dur / 2):.2f}", "-i", out.name, "-frames:v", "1",
                    "-vf", "scale=360:-2", thumb.name], check=True, cwd=rd)
    res = RenderResult(moment_id=m.id, path=str(out), duration=dur, layout=layout, thumbnail=str(thumb))
    save_json(rd / f"{m.id}.json", res)
    if not cfg.get("keep_source", True) and is_url(meta.url):
        (rd / f"{m.id}_src.mp4").unlink(missing_ok=True)
    return res


def run(job: Job, cfg: dict, only: list[str] | None = None) -> list[RenderResult]:
    meta = from_dict(VodMeta, load_json(job.p("meta.json")))
    out = []
    for m in load_list(job.p("moments.json"), Moment):
        if only and m.id not in only:
            continue
        job.log(f"рендер {m.id}: {m.title}")
        out.append(render_moment(job, m, meta, cfg))
    return out
