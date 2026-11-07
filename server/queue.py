"""Воркер очереди: берёт задачи из БД по одной (GPU одна) и прогоняет шаги конвейера.

Каждый шаг — вызов `pipeline.run(job_id, steps=[step])` в дочернем процессе: так шаг можно
отменить посередине (скачивание и расшифровку изнутри функции не прервать). Статусы шагов
пишет сам конвейер в work/<id>/job.json; воркер ведёт статус задачи в БД.

  python -m server.queue [--config config.yaml]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from shorts.config import load_config
from shorts.jobs import STEPS, Job, job_id_for, open_job

from .db import DB

Target = Callable[..., None]


def db_path(cfg: dict) -> Path:
    return Path(cfg["work_dir"]) / "shorts.db"


def submit(db: DB, cfg: dict, source: str, steps: list[str] | None = None, force: bool = False) -> str:
    """Поставить VOD/файл в очередь. Возвращает job_id."""
    source = source.strip()
    if not source.startswith(("http://", "https://")):
        p = Path(source).expanduser()
        if not p.exists():
            raise FileNotFoundError(source)
        source = str(p.resolve())
    job_id = job_id_for(source)
    db.upsert_job(job_id, source)
    job = open_job(cfg["work_dir"], job_id)
    if not job.p("job.json").exists():
        job.set_step("ingest", "pending", source=source)
    db.enqueue(job_id, "run", steps=steps, force=force)
    return job_id


def import_existing(db: DB, cfg: dict) -> list[str]:
    """Задачи, запущенные из CLI (`python -m shorts run`), — в БД, чтобы их было видно в панели."""
    added = []
    for jf in sorted(Path(cfg["work_dir"]).glob("*/job.json")):
        job_id = jf.parent.name
        if db.get_job(job_id):
            continue
        st = json.loads(jf.read_text(encoding="utf-8"))
        steps = st.get("steps", {})
        if any(v == "error" for v in steps.values()):
            status = "error"
        elif steps.get("render") == "done":
            status = "done"
        else:
            status = "canceled"   # не доделана и не в очереди — можно перезапустить из панели
        db.upsert_job(job_id, st.get("source", ""))
        db.set_job(job_id, status=status)
        Worker(db, cfg)._sync(Job(job_id, jf.parent))
        added.append(job_id)
    return added


def pipeline_target(cfg: dict, job_id: str, kind: str, step: str, source: str, force: bool,
                    moments: list[str]) -> None:
    """Выполняется в дочернем процессе."""
    from shorts import pipeline
    from shorts.edit import render

    if kind == "rerender":
        render.run(open_job(cfg["work_dir"], job_id), cfg, only=moments or None)
    else:
        pipeline.run(job_id, cfg, source=source if step == "ingest" else None, steps=[step], force=force)


def _child(target: Target, args: tuple) -> None:
    try:
        target(*args)
    except BaseException:  # noqa: BLE001 — traceback уже в log.txt задачи
        import traceback

        traceback.print_exc()
        sys.exit(1)


def kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Worker:
    def __init__(self, db: DB, cfg: dict, target: Target = pipeline_target, in_process: bool = False,
                 poll: float = 0.5):
        self.db, self.cfg, self.target, self.in_process, self.poll = db, cfg, target, in_process, poll
        self._stop = False

    # ---------------------------------------------------------------- цикл
    def run_forever(self, idle_sleep: float = 1.0) -> None:
        n = self.db.requeue_stale()
        if n:
            print(f"[worker] вернул в очередь прерванных задач: {n}", flush=True)
        added = import_existing(self.db, self.cfg)
        if added:
            print(f"[worker] нашёл задачи из CLI: {', '.join(added)}", flush=True)
        print(f"[worker] жду задачи ({self.db.path})", flush=True)
        while not self._stop:
            if not self.run_once():
                time.sleep(idle_sleep)

    def stop(self) -> None:
        self._stop = True

    def run_once(self) -> bool:
        task = self.db.claim_next()
        if task is None:
            return False
        self.process(task)
        return True

    # ---------------------------------------------------------------- задача
    def process(self, task: dict) -> str:
        job_id = task["job_id"]
        job = open_job(self.cfg["work_dir"], job_id)
        info = self.db.get_job(job_id) or {}
        source = info.get("source_url") or job.status().get("source", "")
        steps = ["render"] if task["kind"] == "rerender" else (task["steps"] or STEPS)
        self.db.set_job(job_id, status="running", error=None)
        print(f"[worker] {job_id}: {task['kind']} {steps}", flush=True)

        status, error = "done", None
        for step in steps:
            if self.db.cancel_requested(task["id"]):
                status = "canceled"
                break
            status, error = self._exec(task, job, step, source)
            if status != "done":
                break

        self._sync(job)
        self.db.finish_task(task["id"], status, error)
        if not self.db.tasks(job_id, active_only=True):   # следующих задач по этой работе нет
            self.db.set_job(job_id, status=status, error=error)
        print(f"[worker] {job_id}: {status}" + (f" — {error}" if error else ""), flush=True)
        return status

    def _exec(self, task: dict, job: Job, step: str, source: str) -> tuple[str, str | None]:
        args = (self.cfg, job.id, task["kind"], step, source, bool(task["force"]), task["moments"])
        if self.in_process:
            try:
                self.target(*args)
                return "done", None
            except Exception as e:  # noqa: BLE001
                return "error", f"{step}: {e}"

        proc = mp.get_context("spawn").Process(target=_child, args=(self.target, args), daemon=False)
        proc.start()
        while proc.is_alive():
            proc.join(self.poll)
            if proc.is_alive() and self.db.cancel_requested(task["id"]):
                kill_tree(proc.pid)
                proc.join(10)
                job.set_step(step, "canceled")
                job.log(f"!! {step}: отменено")
                return "canceled", None
        if proc.exitcode == 0:
            return "done", None
        err = job.status().get("errors", {}).get(step) or f"процесс шага завершился с кодом {proc.exitcode}"
        return "error", f"{step}: {err}"

    def _sync(self, job: Job) -> None:
        """meta.json и moments.json → БД (название и канал задачи, моменты и пути рендеров)."""
        try:
            if job.p("meta.json").exists():
                meta = json.loads(job.p("meta.json").read_text(encoding="utf-8"))
                self.db.set_job(job.id, title=meta.get("title", ""), channel=meta.get("channel", ""))
            if job.p("moments.json").exists():
                moments = json.loads(job.p("moments.json").read_text(encoding="utf-8"))
                renders = {m["id"]: f"renders/{m['id']}.mp4" for m in moments
                           if job.p(f"renders/{m['id']}.mp4").exists() and job.p(f"renders/{m['id']}.json").exists()}
                self.db.sync_moments(job.id, moments, renders)
        except Exception as e:  # noqa: BLE001 — синхронизация не должна ронять воркер
            print(f"[worker] {job.id}: не синхронизировал моменты: {e}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(prog="server.queue", description="воркер очереди stream-shorts")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--add", metavar="URL_ИЛИ_ФАЙЛ", help="поставить в очередь и выйти")
    a = ap.parse_args()
    cfg = load_config(a.config)
    db = DB(db_path(cfg))
    if a.add:
        print(submit(db, cfg, a.add))
        return
    w = Worker(db, cfg)
    try:
        w.run_forever()
    except KeyboardInterrupt:
        print("[worker] остановлен", flush=True)


if __name__ == "__main__":
    main()
