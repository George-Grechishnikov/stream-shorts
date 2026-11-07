"""SQLite (work/shorts.db): задачи, моменты и очередь действий.

jobs    — задача = папка work/<id>/ (id совпадает с job_id конвейера);
moments — моменты задачи + решения пользователя (одобрен, название, id на YouTube);
tasks   — очередь для воркера: run (шаги конвейера) и rerender (перерендер моментов).

Статусы шагов конвейера живут в work/<id>/job.json (SPEC §3) — БД их не дублирует.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    source_url  TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | error | canceled
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    error       TEXT
);
CREATE TABLE IF NOT EXISTS moments (
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    moment_id   TEXT NOT NULL,
    start       REAL NOT NULL,
    "end"       REAL NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    hook        TEXT NOT NULL DEFAULT '',
    score       REAL NOT NULL DEFAULT 0,
    reason      TEXT NOT NULL DEFAULT '',
    approved    INTEGER,                          -- NULL: не решено, 1: одобрен, 0: отклонён
    render_path TEXT,
    youtube_id  TEXT,
    PRIMARY KEY (job_id, moment_id)
);
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                    -- run | rerender
    steps       TEXT NOT NULL DEFAULT '[]',       -- JSON: шаги для run ([] — все)
    moments     TEXT NOT NULL DEFAULT '[]',       -- JSON: id моментов для rerender
    force       INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | error | canceled
    cancel      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, id);
"""

ACTIVE = ("queued", "running")


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        con.executescript(SCHEMA)
        con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)  # autocommit; транзакции — явно
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")    # панель читает, пока воркер пишет
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def q(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        con = self._connect()
        try:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        finally:
            con.close()

    def x(self, sql: str, args: tuple = ()) -> int:
        con = self._connect()
        try:
            cur = con.execute(sql, args)
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount
        finally:
            con.close()

    # ------------------------------------------------------------------ jobs
    def upsert_job(self, job_id: str, source_url: str) -> None:
        t = now()
        self.x("INSERT INTO jobs(id, source_url, status, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?) "
               "ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url, updated_at=excluded.updated_at",
               (job_id, source_url, t, t))

    def get_job(self, job_id: str) -> dict | None:
        r = self.q("SELECT * FROM jobs WHERE id=?", (job_id,))
        return r[0] if r else None

    def list_jobs(self, limit: int = 100) -> list[dict]:
        return self.q("SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))

    def set_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def delete_job(self, job_id: str) -> None:
        self.x("DELETE FROM jobs WHERE id=?", (job_id,))

    # ------------------------------------------------------------------ tasks
    def enqueue(self, job_id: str, kind: str = "run", steps: list[str] | None = None,
                moments: list[str] | None = None, force: bool = False) -> int:
        tid = self.x("INSERT INTO tasks(job_id, kind, steps, moments, force, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (job_id, kind, json.dumps(steps or []), json.dumps(moments or []), int(force), now()))
        if not self.q("SELECT 1 FROM tasks WHERE job_id=? AND status='running'", (job_id,)):
            self.set_job(job_id, status="queued", error=None)
        return tid

    def claim_next(self) -> dict | None:
        """Атомарно взять самую старую задачу из очереди (queued → running)."""
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            r = con.execute("SELECT * FROM tasks WHERE status='queued' AND cancel=0 ORDER BY id LIMIT 1").fetchone()
            if r is None:
                con.execute("COMMIT")
                return None
            con.execute("UPDATE tasks SET status='running', started_at=? WHERE id=?", (now(), r["id"]))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return self.task(r["id"])

    def task(self, task_id: int) -> dict | None:
        r = self.q("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not r:
            return None
        t = r[0]
        t["steps"] = json.loads(t["steps"])
        t["moments"] = json.loads(t["moments"])
        return t

    def tasks(self, job_id: str | None = None, active_only: bool = False) -> list[dict]:
        sql, args = "SELECT * FROM tasks", []
        conds = []
        if job_id:
            conds.append("job_id=?")
            args.append(job_id)
        if active_only:
            conds.append("status IN ('queued', 'running')")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        rows = self.q(sql + " ORDER BY id", tuple(args))
        for t in rows:
            t["steps"] = json.loads(t["steps"])
            t["moments"] = json.loads(t["moments"])
        return rows

    def finish_task(self, task_id: int, status: str, error: str | None = None) -> None:
        self.x("UPDATE tasks SET status=?, error=?, finished_at=? WHERE id=?", (status, error, now(), task_id))

    def cancel_job(self, job_id: str) -> int:
        """Отменить всё по задаче: queued — сразу, running — флагом (воркер убьёт процесс шага)."""
        n = self.x("UPDATE tasks SET status='canceled', cancel=1, finished_at=? WHERE job_id=? AND status='queued'",
                   (now(), job_id))
        n += self.x("UPDATE tasks SET cancel=1 WHERE job_id=? AND status='running'", (job_id,))
        if not self.q("SELECT 1 FROM tasks WHERE job_id=? AND status='running'", (job_id,)):
            self.set_job(job_id, status="canceled")
        return n

    def cancel_requested(self, task_id: int) -> bool:
        r = self.q("SELECT cancel FROM tasks WHERE id=?", (task_id,))
        return bool(r and r[0]["cancel"])

    def requeue_stale(self) -> int:
        """После падения воркера: running → queued (шаги конвейера идемпотентны)."""
        return self.x("UPDATE tasks SET status='queued', started_at=NULL WHERE status='running'")

    # ------------------------------------------------------------------ moments
    def sync_moments(self, job_id: str, moments: list[dict], renders: dict[str, str]) -> None:
        """moments.json → БД. Решения пользователя (approved, title, youtube_id) сохраняются;
        моменты, которых больше нет в moments.json, удаляются."""
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            ids = [m["id"] for m in moments]
            con.execute(f"DELETE FROM moments WHERE job_id=? AND moment_id NOT IN ({','.join('?' * len(ids))})",
                        (job_id, *ids))
            for m in moments:
                con.execute(
                    'INSERT INTO moments(job_id, moment_id, start, "end", title, hook, score, reason, render_path) '
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, moment_id) DO UPDATE SET "
                    'start=excluded.start, "end"=excluded."end", hook=excluded.hook, score=excluded.score, '
                    "reason=excluded.reason, render_path=COALESCE(excluded.render_path, moments.render_path)",
                    (job_id, m["id"], m["start"], m["end"], m.get("title", ""), m.get("hook", ""),
                     m.get("score", 0), m.get("reason", ""), renders.get(m["id"])))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def moments(self, job_id: str) -> list[dict]:
        return self.q("SELECT * FROM moments WHERE job_id=? ORDER BY score DESC, moment_id", (job_id,))

    def moment(self, job_id: str, moment_id: str) -> dict | None:
        r = self.q("SELECT * FROM moments WHERE job_id=? AND moment_id=?", (job_id, moment_id))
        return r[0] if r else None

    def set_moment(self, job_id: str, moment_id: str, **fields: Any) -> int:
        allowed = {"title", "approved", "youtube_id", "render_path", "start", "end"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"нельзя менять: {bad}")
        cols = ", ".join(f'"{k}"=?' for k in fields)
        return self.x(f"UPDATE moments SET {cols} WHERE job_id=? AND moment_id=?", (*fields.values(), job_id, moment_id))
