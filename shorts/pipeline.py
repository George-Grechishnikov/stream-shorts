"""Весь конвейер по шагам. Каждый шаг пропускается, если его выход уже есть (force — перезапуск)."""
from __future__ import annotations

import traceback

from . import candidates, select, signals, transcribe
from .edit import render
from .ingest import twitch
from .jobs import STEPS, Job, job_id_for, open_job

OUTPUT = {"ingest": "meta.json", "signals": "signals.json", "candidates": "candidates.json",
          "transcribe": "transcript.json", "select": "moments.json", "render": None}


def run_step(job: Job, step: str, cfg: dict, source: str | None = None) -> None:
    if step == "ingest":
        twitch.run(job, source or job.status()["source"], cfg)
    elif step == "signals":
        signals.run(job, cfg)
    elif step == "candidates":
        candidates.run(job, cfg)
    elif step == "transcribe":
        transcribe.run(job, cfg)
    elif step == "select":
        select.run(job, cfg)
    elif step == "render":
        render.run(job, cfg)
    else:
        raise ValueError(step)


def run(job_id: str | None, cfg: dict, source: str | None = None, steps: list[str] | None = None,
        force: bool = False) -> Job:
    job = open_job(cfg["work_dir"], job_id or job_id_for(source or ""))
    if source:
        job.set_step("ingest", job.status().get("steps", {}).get("ingest", "pending"), source=source)
    for step in steps or STEPS:
        out = OUTPUT[step]
        if out and job.p(out).exists() and not force:
            job.set_step(step, "done")
            continue
        job.set_step(step, "running")
        job.log(f"== {step}")
        try:
            run_step(job, step, cfg, source)
            job.set_step(step, "done")
        except Exception as e:  # noqa: BLE001
            job.log(traceback.format_exc())
            job.set_step(step, "error", error=str(e))
            raise
    return job
