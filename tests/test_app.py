import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app, shift_moment
from server.db import DB
from shorts.models import load_json

DEMO = Path(__file__).parent / "fixtures" / "job_demo"


@pytest.fixture
def client(cfg):
    work = Path(cfg["work_dir"])
    shutil.copytree(DEMO, work / "job_demo")
    app = create_app(cfg, DB(work / "shorts.db"))
    return TestClient(app)


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "stream-shorts" in r.text and "chart.js" in r.text


def test_demo_job_listed_from_work_dir(client):
    jobs = client.get("/api/jobs").json()
    assert [j["id"] for j in jobs] == ["job_demo"]
    j = jobs[0]
    assert j["status"] == "done" and j["title"] == "Демо-стрим"
    assert all(v == "done" for v in j["steps"].values())


def test_job_detail_with_moments(client):
    j = client.get("/api/jobs/job_demo").json()
    ms = j["moments"]
    assert [m["id"] for m in ms] == ["m00_0", "m01_36", "m02_91"] or len(ms) == 3
    m = next(x for x in ms if x["id"] == "m01_36")
    assert m["title"] == "Брокколи за 400 Робуксов?!"
    assert m["video"].startswith("/files/job_demo/renders/m01_36.mp4")
    assert m["thumb"] == "/files/job_demo/renders/m01_36.jpg"
    assert m["approved"] is None and m["text"]


def test_signals_downsampled(client):
    s = client.get("/api/jobs/job_demo/signals?points=50").json()
    assert len(s["score"]) == len(s["t"]) <= 140
    assert set(s["series"]) >= {"chat_rate", "emote_hype"}


def test_edit_title_and_approve(client):
    r = client.patch("/api/jobs/job_demo/moments/m00_0", json={"title": "  Новое название "})
    assert r.status_code == 200 and r.json()["title"] == "Новое название"
    assert client.patch("/api/jobs/job_demo/moments/m00_0", json={"approved": True}).json()["approved"] is True
    assert client.patch("/api/jobs/job_demo/moments/m00_0", json={"approved": None}).json()["approved"] is None
    assert client.patch("/api/jobs/job_demo/moments/m00_0", json={"title": ""}).status_code == 400
    assert client.patch("/api/jobs/job_demo/moments/nope", json={"approved": True}).status_code == 404


def test_shift_and_rerender_enqueues(client, cfg):
    before = next(m for m in client.get("/api/jobs/job_demo").json()["moments"] if m["id"] == "m01_36")
    r = client.post("/api/jobs/job_demo/moments/m01_36/rerender", json={"start_delta": -1})
    assert r.status_code == 200
    after = r.json()["moment"]
    assert after["start"] == pytest.approx(before["start"] - 1) and after["end"] == before["end"]
    assert after["rendering"] is True
    saved = next(m for m in load_json(Path(cfg["work_dir"]) / "job_demo" / "moments.json") if m["id"] == "m01_36")
    assert saved["start"] == pytest.approx(before["start"] - 1)
    tasks = client.app.state.db.tasks("job_demo", active_only=True)
    assert tasks[-1]["kind"] == "rerender" and tasks[-1]["moments"] == ["m01_36"]
    assert client.get("/api/jobs").json()[0]["status"] == "queued"


def test_shift_rejects_too_short(cfg):
    d = Path(cfg["work_dir"]) / "job_demo"
    shutil.copytree(DEMO, d)
    with pytest.raises(ValueError):
        shift_moment(d, "m00_0", 0, -27)


def test_shift_keeps_words_inside(cfg):
    d = Path(cfg["work_dir"]) / "job_demo"
    shutil.copytree(DEMO, d)
    m = shift_moment(d, "m01_36", 2, -2)
    assert all(m["start"] <= w["start"] and w["end"] <= m["end"] for w in m["words"])


def test_retry_from_step(client):
    r = client.post("/api/jobs/job_demo/retry", json={"step": "select"}).json()
    assert r["steps"] == ["select", "render"]
    assert client.post("/api/jobs/job_demo/retry", json={"step": "nope"}).status_code == 400
    assert client.post("/api/jobs/job_demo/cancel").json()["canceled"] == 1


def test_create_job_validation(client, tmp_path):
    assert client.post("/api/jobs", json={"source": ""}).status_code == 400
    assert client.post("/api/jobs", json={"source": str(tmp_path / "нет.mp4")}).status_code == 400
    r = client.post("/api/jobs", json={"source": "https://www.twitch.tv/videos/42"})
    assert r.status_code == 201 and r.json()["id"] == "v42" and r.json()["status"] == "queued"


def test_video_range_requests(client):
    full = client.get("/files/job_demo/renders/m00_0.mp4")
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"
    size = len(full.content)
    r = client.get("/files/job_demo/renders/m00_0.mp4", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == full.content[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{size}"
    r = client.get("/files/job_demo/renders/m00_0.mp4", headers={"Range": "bytes=-10"})
    assert r.status_code == 206 and r.content == full.content[-10:]
    r = client.get("/files/job_demo/renders/m00_0.mp4", headers={"Range": f"bytes={size + 5}-"})
    assert r.status_code == 416


def test_files_cannot_escape_job_dir(client):
    assert client.get("/files/job_demo/../shorts.db").status_code in (400, 404)
    assert client.get("/files/job_demo/..%2F..%2Fconfig.yaml").status_code == 404
    assert client.get("/files/job_demo/%2E%2E/%2E%2E/config.yaml").status_code in (400, 404)
    assert client.get("/files/..%2Fwork/meta.json").status_code in (400, 404)


def test_log_and_forget(client):
    assert client.get("/api/jobs/job_demo/log").status_code == 200
    assert client.delete("/api/jobs/job_demo").json() == {"ok": True}
