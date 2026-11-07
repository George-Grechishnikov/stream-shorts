import json
import threading
import time

import pytest

from server.db import DB
from server.queue import Worker, import_existing, submit
from shorts.jobs import STEPS, open_job
from shorts.models import Moment, RenderResult, save_json

URL = "https://www.twitch.tv/videos/123"


def stub_target(cfg, job_id, kind, step, source, force, moments):
    """Заглушка конвейера: помечает шаг в job.json и пишет минимальные выходы."""
    job = open_job(cfg["work_dir"], job_id)
    job.set_step(step, "running")
    calls = job.p("calls.txt")
    with open(calls, "a", encoding="utf-8") as f:
        f.write(f"{kind}:{step}:{int(force)}:{','.join(moments)}\n")
    if step == "ingest":
        save_json(job.p("meta.json"), {"id": "123", "url": source, "channel": "deepins02", "title": "Стрим",
                                       "duration": 100, "created_at": "", "width": 0, "height": 0, "fps": 0})
    if step == "select":
        save_json(job.p("moments.json"), [Moment(id="m00_10", start=10, end=30, title="авто", score=9),
                                          Moment(id="m01_50", start=50, end=70, title="авто2", score=7)])
    if step == "render":
        for mid in ["m00_10", "m01_50"]:
            job.p(f"renders/{mid}.mp4").write_bytes(b"x")
            save_json(job.p(f"renders/{mid}.json"), RenderResult(moment_id=mid, path="", duration=20, layout="blur_bg"))
    if step == "transcribe" and job.p("fail_transcribe").exists():
        job.set_step(step, "error", error="CUDA сломалась")
        raise RuntimeError("CUDA сломалась")
    job.set_step(step, "done")


def slow_target(cfg, job_id, kind, step, source, force, moments):
    open_job(cfg["work_dir"], job_id).set_step(step, "running")
    time.sleep(60)


@pytest.fixture
def db(cfg):
    return DB(f"{cfg['work_dir']}/shorts.db")


def calls(cfg, job_id):
    return open_job(cfg["work_dir"], job_id).p("calls.txt").read_text(encoding="utf-8").split()


def test_job_runs_through_all_steps(db, cfg):
    job_id = submit(db, cfg, URL)
    assert job_id == "v123"
    assert db.get_job(job_id)["status"] == "queued"
    w = Worker(db, cfg, target=stub_target, in_process=True)
    assert w.run_once() is True
    assert w.run_once() is False                      # очередь пуста
    j = db.get_job(job_id)
    assert j["status"] == "done" and j["title"] == "Стрим" and j["channel"] == "deepins02"
    assert [c.split(":")[1] for c in calls(cfg, job_id)] == STEPS
    st = json.loads(open_job(cfg["work_dir"], job_id).p("job.json").read_text(encoding="utf-8"))
    assert all(st["steps"][s] == "done" for s in STEPS) and st["source"] == URL
    ms = db.moments(job_id)
    assert [m["moment_id"] for m in ms] == ["m00_10", "m01_50"]
    assert ms[0]["render_path"] == "renders/m00_10.mp4" and ms[0]["approved"] is None


def test_error_stops_job_and_retry_step(db, cfg):
    job_id = submit(db, cfg, URL)
    open_job(cfg["work_dir"], job_id).p("fail_transcribe").write_text("")
    w = Worker(db, cfg, target=stub_target, in_process=True)
    w.run_once()
    j = db.get_job(job_id)
    assert j["status"] == "error" and "CUDA" in j["error"]
    assert "select" not in " ".join(calls(cfg, job_id))   # после ошибки шаги не идут

    open_job(cfg["work_dir"], job_id).p("fail_transcribe").unlink()
    db.enqueue(job_id, "run", steps=["transcribe", "select", "render"], force=True)   # «повторить с шага»
    assert db.get_job(job_id)["status"] == "queued" and db.get_job(job_id)["error"] is None
    w.run_once()
    assert db.get_job(job_id)["status"] == "done"
    assert calls(cfg, job_id)[-3:] == ["run:transcribe:1:", "run:select:1:", "run:render:1:"]


def test_rerender_task_passes_moment_ids(db, cfg):
    job_id = submit(db, cfg, URL)
    w = Worker(db, cfg, target=stub_target, in_process=True)
    w.run_once()
    db.enqueue(job_id, "rerender", moments=["m01_50"], force=True)
    w.run_once()
    assert calls(cfg, job_id)[-1] == "rerender:render:1:m01_50"


def test_user_decisions_survive_resync(db, cfg):
    job_id = submit(db, cfg, URL)
    w = Worker(db, cfg, target=stub_target, in_process=True)
    w.run_once()
    db.set_moment(job_id, "m00_10", approved=1, title="Моё название")
    db.enqueue(job_id, "run", steps=["select", "render"], force=True)
    w.run_once()
    m = db.moment(job_id, "m00_10")
    assert m["approved"] == 1 and m["title"] == "Моё название"


def test_moments_removed_after_reselect(db, cfg):
    job_id = submit(db, cfg, URL)
    w = Worker(db, cfg, target=stub_target, in_process=True)
    w.run_once()
    job = open_job(cfg["work_dir"], job_id)
    save_json(job.p("moments.json"), [Moment(id="m00_10", start=10, end=30, title="авто", score=9)])
    w._sync(job)
    assert [m["moment_id"] for m in db.moments(job_id)] == ["m00_10"]


def test_cancel_queued(db, cfg):
    job_id = submit(db, cfg, URL)
    assert db.cancel_job(job_id) == 1
    assert db.get_job(job_id)["status"] == "canceled"
    assert Worker(db, cfg, target=stub_target, in_process=True).run_once() is False


def test_cancel_kills_running_step_process(db, cfg):
    job_id = submit(db, cfg, URL)
    w = Worker(db, cfg, target=slow_target, poll=0.2)    # настоящий дочерний процесс
    t0 = time.time()
    threading.Timer(3.0, db.cancel_job, args=(job_id,)).start()
    assert w.run_once() is True
    assert time.time() - t0 < 30
    assert db.get_job(job_id)["status"] == "canceled"
    assert db.tasks(job_id)[0]["status"] == "canceled"
    st = json.loads(open_job(cfg["work_dir"], job_id).p("job.json").read_text(encoding="utf-8"))
    assert st["steps"]["ingest"] == "canceled"


def test_subprocess_error_reported(db, cfg):
    job_id = submit(db, cfg, URL)
    open_job(cfg["work_dir"], job_id).p("fail_transcribe").write_text("")
    Worker(db, cfg, target=stub_target, poll=0.1).run_once()
    j = db.get_job(job_id)
    assert j["status"] == "error" and j["error"] == "transcribe: CUDA сломалась"


def test_requeue_stale_after_crash(db, cfg):
    job_id = submit(db, cfg, URL)
    assert db.claim_next()["job_id"] == job_id          # воркер взял и «упал»
    assert db.claim_next() is None
    assert db.requeue_stale() == 1
    assert db.claim_next()["job_id"] == job_id


def test_submit_local_file_and_missing(db, cfg, tmp_path):
    f = tmp_path / "Мой стрим.mp4"
    f.write_bytes(b"x")
    job_id = submit(db, cfg, str(f))
    assert db.get_job(job_id)["source_url"] == str(f.resolve())
    with pytest.raises(FileNotFoundError):
        submit(db, cfg, str(tmp_path / "нет.mp4"))


def test_import_existing_cli_jobs(db, cfg):
    job = open_job(cfg["work_dir"], "v777")
    for s in STEPS:
        job.set_step(s, "done", source="https://www.twitch.tv/videos/777")
    save_json(job.p("moments.json"), [Moment(id="m00_1", start=1, end=20, title="t", score=5)])
    assert import_existing(db, cfg) == ["v777"]
    assert db.get_job("v777")["status"] == "done"
    assert len(db.moments("v777")) == 1
    assert import_existing(db, cfg) == []
