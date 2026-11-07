"""CLI:
  python -m shorts run <twitch-vod-url | файл>        весь конвейер
  python -m shorts step <job_id> <шаг> [--force]      один шаг (ingest|signals|candidates|transcribe|select|render)
  python -m shorts render <job_id> [moment_id ...]    перерендер выбранных моментов
"""
from __future__ import annotations

import argparse

from . import pipeline
from .config import load_config
from .edit import render
from .jobs import STEPS, open_job


def main() -> None:
    ap = argparse.ArgumentParser(prog="shorts")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("source")
    r.add_argument("--job")
    r.add_argument("--force", action="store_true")
    s = sub.add_parser("step")
    s.add_argument("job")
    s.add_argument("step", choices=STEPS)
    s.add_argument("--force", action="store_true")
    rr = sub.add_parser("render")
    rr.add_argument("job")
    rr.add_argument("moments", nargs="*")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.cmd == "run":
        job = pipeline.run(a.job, cfg, source=a.source, force=a.force)
        print("готово:", job.dir / "renders")
    elif a.cmd == "step":
        pipeline.run(a.job, cfg, steps=[a.step], force=a.force)
    elif a.cmd == "render":
        render.run(open_job(cfg["work_dir"], a.job), cfg, only=a.moments or None)


if __name__ == "__main__":
    main()
