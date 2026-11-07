"""Веб-панель: http://127.0.0.1:8000

  python -m server.app [--config config.yaml] [--port 8000]

Очередь выполняет отдельный процесс `python -m server.queue` (scripts/run.ps1 запускает оба).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from shorts.config import load_config
from shorts.jobs import STEPS
from shorts.models import Word, load_json, save_json
from shorts.transcribe import words_between

from .db import DB
from .queue import db_path, import_existing, submit

WEB = Path(__file__).parent / "web"
JOB_ID_RX = re.compile(r"^[\w\-]{1,80}$")


def create_app(cfg: dict | None = None, db: DB | None = None) -> FastAPI:
    cfg = cfg or load_config()
    work = Path(cfg["work_dir"]).resolve()
    db = db or DB(db_path(cfg))
    import_existing(db, cfg)

    app = FastAPI(title="stream-shorts", docs_url="/api/docs", redoc_url=None)
    app.state.cfg, app.state.db = cfg, db

    # ---------------------------------------------------------------- helpers
    def job_dir(job_id: str) -> Path:
        if not JOB_ID_RX.match(job_id):
            raise HTTPException(400, "плохой id задачи")
        d = work / job_id
        if not d.is_dir():
            raise HTTPException(404, "нет такой задачи")
        return d

    def job_row(job_id: str) -> dict:
        j = db.get_job(job_id)
        if j is None:
            job_dir(job_id)
            import_existing(db, cfg)
            j = db.get_job(job_id)
            if j is None:
                raise HTTPException(404, "нет такой задачи")
        return j

    def read_json(p: Path, default: Any) -> Any:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def job_view(j: dict) -> dict:
        d = work / j["id"]
        st = read_json(d / "job.json", {})
        steps = st.get("steps", {})
        tasks = db.tasks(j["id"], active_only=True)
        running = any(t["status"] == "running" for t in tasks)
        # «running» без живого воркера — шаг бросили (убили процесс, CLI прервали): показываем как отменённый
        shown = {s: ("canceled" if steps.get(s) == "running" and not running else steps.get(s, "pending")) for s in STEPS}
        return {**j, "steps": shown, "step_errors": st.get("errors", {}),
                "active": [{"id": t["id"], "kind": t["kind"], "status": t["status"]} for t in tasks]}

    # ---------------------------------------------------------------- страницы
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((WEB / "index.html").read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- задачи
    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        return [job_view(j) for j in db.list_jobs()]

    @app.post("/api/jobs", status_code=201)
    def create_job(payload: Annotated[dict, Body()]) -> dict:
        source = str(payload.get("source", "")).strip()
        if not source:
            raise HTTPException(400, "нужна ссылка на VOD или путь к файлу")
        try:
            job_id = submit(db, cfg, source)
        except FileNotFoundError:
            raise HTTPException(400, f"файл не найден: {source}") from None
        return job_view(db.get_job(job_id))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        j = job_row(job_id)
        d = job_dir(job_id)
        view = job_view(j)
        view["meta"] = read_json(d / "meta.json", {})
        view["moments"] = moments_view(job_id)
        return view

    @app.delete("/api/jobs/{job_id}")
    def forget_job(job_id: str) -> dict:
        """Убрать задачу из панели (файлы в work/ остаются)."""
        job_row(job_id)
        if db.tasks(job_id, active_only=True):
            raise HTTPException(409, "задача ещё выполняется — сначала отмени")
        db.delete_job(job_id)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> dict:
        job_row(job_id)
        return {"canceled": db.cancel_job(job_id)}

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str, payload: Annotated[dict | None, Body()] = None) -> dict:
        """Перезапустить с шага (по умолчанию — с первого незавершённого) до конца."""
        job_row(job_id)
        payload = payload or {}
        step = payload.get("step")
        if step is None:
            steps = read_json(job_dir(job_id) / "job.json", {}).get("steps", {})
            step = next((s for s in STEPS if steps.get(s) != "done"), "render")
        if step not in STEPS:
            raise HTTPException(400, f"нет шага {step}")
        rest = STEPS[STEPS.index(step):]
        tid = db.enqueue(job_id, "run", steps=rest, force=bool(payload.get("force", True)))
        return {"task": tid, "steps": rest}

    @app.get("/api/jobs/{job_id}/log", response_class=Response)
    def log(job_id: str, tail: int = 200) -> Response:
        p = job_dir(job_id) / "log.txt"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:] if p.exists() else []
        return Response("\n".join(lines), media_type="text/plain; charset=utf-8")

    @app.get("/api/jobs/{job_id}/signals")
    def signals(job_id: str, points: int = 1500) -> dict:
        d = job_dir(job_id)
        sig = read_json(d / "signals.json", {}).get("series", {})
        if not sig:
            return {"t": [], "score": [], "series": {}}
        n = max(len(v) for v in sig.values())
        bucket = max(1, -(-n // max(50, points)))

        def pool(v: list[float]) -> list[float]:  # максимум по корзине — пики не теряются
            return [round(max(v[i:i + bucket]), 3) for i in range(0, len(v), bucket)]

        score = sig.get("score")
        if score is None:
            import numpy as np

            from shorts.candidates import score_series

            sc, _ = score_series({k: np.asarray(v, float) for k, v in sig.items()})
            score = sc.tolist()
        return {"t": list(range(0, n, bucket)), "bucket": bucket, "score": pool(score),
                "series": {k: pool(v) for k, v in sig.items() if k != "score" and k != "loudness"},
                "candidates": [c.get("peak") for c in read_json(d / "candidates.json", [])]}

    # ---------------------------------------------------------------- моменты
    def moments_view(job_id: str) -> list[dict]:
        d = work / job_id
        file_ms = {m["id"]: m for m in read_json(d / "moments.json", [])}
        rows = db.moments(job_id)
        if not rows and file_ms:          # задача ещё не синхронизирована воркером
            from shorts.jobs import Job

            from .queue import Worker

            Worker(db, cfg)._sync(Job(job_id, d))
            rows = db.moments(job_id)
        busy = {m for t in db.tasks(job_id, active_only=True) if t["kind"] == "rerender" for m in t["moments"]}
        out = []
        for r in rows:
            fm = file_ms.get(r["moment_id"], {})
            rp = d / "renders" / f"{r['moment_id']}.mp4"
            out.append({
                "id": r["moment_id"], "start": r["start"], "end": r["end"], "duration": round(r["end"] - r["start"], 2),
                "title": r["title"], "hook": r["hook"], "score": r["score"], "reason": r["reason"],
                "approved": None if r["approved"] is None else bool(r["approved"]),
                "youtube_id": r["youtube_id"], "rendering": r["moment_id"] in busy,
                "video": f"/files/{job_id}/renders/{r['moment_id']}.mp4?v={int(rp.stat().st_mtime)}" if rp.exists() else None,
                "thumb": f"/files/{job_id}/renders/{r['moment_id']}.jpg" if (d / "renders" / f"{r['moment_id']}.jpg").exists() else None,
                "text": " ".join(w["text"] for w in fm.get("words", []))[:400],
            })
        return out

    def moment_or_404(job_id: str, mid: str) -> dict:
        m = db.moment(job_id, mid)
        if m is None:
            raise HTTPException(404, "нет такого момента")
        return m

    @app.patch("/api/jobs/{job_id}/moments/{mid}")
    def patch_moment(job_id: str, mid: str, payload: Annotated[dict, Body()]) -> dict:
        moment_or_404(job_id, mid)
        fields: dict[str, Any] = {}
        if "title" in payload:
            t = str(payload["title"]).strip()[:100]
            if not t:
                raise HTTPException(400, "пустое название")
            fields["title"] = t
        if "approved" in payload:
            a = payload["approved"]
            fields["approved"] = None if a is None else int(bool(a))
        if not fields:
            raise HTTPException(400, "нечего менять (title, approved)")
        db.set_moment(job_id, mid, **fields)
        return next(m for m in moments_view(job_id) if m["id"] == mid)

    @app.post("/api/jobs/{job_id}/moments/{mid}/rerender")
    def rerender(job_id: str, mid: str, payload: Annotated[dict | None, Body()] = None) -> dict:
        """Сдвинуть начало/конец (секунды, обычно ±1) и перерендерить."""
        moment_or_404(job_id, mid)
        d = job_dir(job_id)
        payload = payload or {}
        ds, de = float(payload.get("start_delta", 0)), float(payload.get("end_delta", 0))
        if ds or de:
            shift_moment(d, mid, ds, de)
            m = next(x for x in read_json(d / "moments.json", []) if x["id"] == mid)
            db.set_moment(job_id, mid, start=m["start"], end=m["end"])
        tid = db.enqueue(job_id, "rerender", moments=[mid], force=True)
        return {"task": tid, "moment": next(x for x in moments_view(job_id) if x["id"] == mid)}

    # ---------------------------------------------------------------- файлы
    @app.get("/files/{job_id}/{path:path}")
    def files(job_id: str, path: str, request: Request) -> Response:
        d = job_dir(job_id).resolve()
        p = (d / path).resolve()
        if d not in p.parents or not p.is_file():
            raise HTTPException(404, "нет файла")
        return range_response(p, request.headers.get("range"))

    @app.exception_handler(ValueError)
    def bad_value(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=400)

    return app


# -------------------------------------------------------------------- сдвиг момента
MIN_LEN = 3.0


def shift_moment(job_dir: Path, mid: str, start_delta: float, end_delta: float) -> dict:
    """Сдвигает границы момента в moments.json и пересобирает его слова из transcript.json."""
    mp = job_dir / "moments.json"
    moments = load_json(mp)
    m = next((x for x in moments if x["id"] == mid), None)
    if m is None:
        raise ValueError(f"нет момента {mid}")
    dur = float(load_json(job_dir / "meta.json").get("duration") or 1e9) if (job_dir / "meta.json").exists() else 1e9
    s = round(min(max(0.0, m["start"] + start_delta), dur), 2)
    e = round(min(max(0.0, m["end"] + end_delta), dur), 2)
    if e - s < MIN_LEN:
        raise ValueError(f"момент короче {MIN_LEN:.0f} с")
    m["start"], m["end"] = s, e
    tp = job_dir / "transcript.json"
    if tp.exists():
        m["words"] = [w.__dict__ for w in words_between(load_json(tp), s, e)]
    else:
        m["words"] = [w for w in m.get("words", []) if s <= Word(**w).start and Word(**w).end <= e]
    save_json(mp, moments)
    return m


# -------------------------------------------------------------------- Range
CHUNK = 1 << 20


def range_response(p: Path, rng: str | None) -> Response:
    size = p.stat().st_size
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or p.suffix == ".json":
        ctype += "; charset=utf-8"
    if not rng:
        return FileResponse(p, media_type=ctype, headers={"Accept-Ranges": "bytes"})
    m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:                                   # bytes=-N — последние N байт
        start, end = max(0, size - int(m.group(2))), size - 1
    if start >= size or start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def body():
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=206, media_type=ctype, headers={
        "Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(prog="server.app", description="веб-панель stream-shorts")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    app = create_app(load_config(a.config))
    print(f"панель: http://{a.host}:{a.port}", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
