import base64
import hashlib
import json
import shutil
import time
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import DB
from shorts.publish import youtube

DEMO = Path(__file__).parent / "fixtures" / "job_demo"


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeGoogle:
    """Имитирует OAuth token endpoint и resumable upload YouTube."""

    def __init__(self, size, fail_at=None):
        self.size, self.fail_at, self.received = size, fail_at, 0
        self.posts, self.puts, self.meta = [], [], None

    def post(self, url, params=None, json=None, data=None, headers=None, timeout=None):
        self.posts.append((url, params, json, data, headers))
        if url == youtube.TOKEN_URL:
            if data["grant_type"] == "refresh_token":
                return Resp(200, {"access_token": "fresh", "expires_in": 3600})
            return Resp(200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600})
        self.meta = json
        assert params["uploadType"] == "resumable"
        return Resp(200, {}, {"Location": "https://upload.example/session"})

    def put(self, url, data=None, headers=None, timeout=None):
        self.puts.append(headers.get("Content-Range"))
        cr = headers["Content-Range"]
        if cr.startswith("bytes */"):                       # запрос статуса после сбоя
            return Resp(308, None, {"Range": f"bytes=0-{self.received - 1}"} if self.received else {})
        if self.fail_at is not None and self.received >= self.fail_at:
            self.fail_at = None
            return Resp(503, {"error": {"message": "backend"}})
        self.received += len(data)
        if self.received >= self.size:
            return Resp(200, {"id": "VID123"})
        return Resp(308, None, {"Range": f"bytes=0-{self.received - 1}"})


@pytest.fixture
def yt_cfg(cfg, tmp_path):
    secret = tmp_path / "client_secret.json"
    secret.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "cs"}}))
    token = tmp_path / "tok.json"
    youtube.save_token(token, {"access_token": "a1", "refresh_token": "r1", "expires_at": time.time() + 999})
    cfg["youtube"] = {"client_secret": str(secret), "token": str(token), "privacy": "private", "tags": []}
    return cfg


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(youtube.time, "sleep", lambda s: None)


def make_video(tmp_path, size):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\0" * size)
    return p


def test_upload_in_chunks_private_with_shorts_tag(yt_cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "CHUNK", 256 * 1024)
    size = 256 * 1024 * 3 + 100
    g = FakeGoogle(size)
    seen = []
    vid = youtube.upload(make_video(tmp_path, size), "  Мама   стала директором  ", "Со стрима", ["deepins02"],
                         cfg=yt_cfg, http=g, progress=seen.append)
    assert vid == "VID123"
    assert g.meta["status"]["privacyStatus"] == "private"
    assert g.meta["snippet"]["title"] == "Мама стала директором"
    assert g.meta["snippet"]["description"].endswith("#shorts")
    assert len(g.puts) == 4 and g.puts[0] == f"bytes 0-{256 * 1024 - 1}/{size}"
    assert seen[-1] == 1.0 and seen == sorted(seen)


def test_upload_resumes_after_server_error(yt_cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "CHUNK", 256 * 1024)
    size = 256 * 1024 * 2 + 10
    g = FakeGoogle(size, fail_at=256 * 1024)
    assert youtube.upload(make_video(tmp_path, size), "t", cfg=yt_cfg, http=g) == "VID123"
    assert f"bytes */{size}" in g.puts                    # спросил, где остановились
    assert g.received == size                             # ничего не отправил дважды


def test_expired_token_refreshed(yt_cfg, tmp_path):
    tok_path = Path(yt_cfg["youtube"]["token"])
    youtube.save_token(tok_path, {"access_token": "old", "refresh_token": "r1", "expires_at": time.time() - 10})
    g = FakeGoogle(10)
    youtube.upload(make_video(tmp_path, 10), "t", cfg=yt_cfg, http=g)
    assert g.posts[0][3]["grant_type"] == "refresh_token"
    assert json.loads(tok_path.read_text())["access_token"] == "fresh"


def test_not_connected_and_bad_privacy(cfg, tmp_path):
    cfg["youtube"] = {"client_secret": str(tmp_path / "nope.json")}
    with pytest.raises(youtube.YouTubeError, match="client"):
        youtube.upload(make_video(tmp_path, 10), "t", cfg=cfg)
    with pytest.raises(ValueError):
        youtube.upload(make_video(tmp_path, 10), "t", privacy="secret", cfg=cfg)


def test_quota_error_is_readable(yt_cfg, tmp_path):
    class Quota(FakeGoogle):
        def post(self, url, **kw):
            return Resp(403, {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}})

    with pytest.raises(youtube.YouTubeError, match="квота"):
        youtube.upload(make_video(tmp_path, 10), "t", cfg=yt_cfg, http=Quota(10))


def test_pkce_and_auth_url():
    verifier, challenge = youtube.pkce()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = youtube.auth_url({"client_id": "cid"}, "http://127.0.0.1:8000/cb", "st", challenge)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert q["scope"] == youtube.SCOPE and q["access_type"] == "offline" and q["code_challenge_method"] == "S256"


def test_exchange_code_saves_refresh_token(tmp_path):
    tok = tmp_path / "t.json"
    youtube.exchange_code({"client_id": "cid"}, "code", "http://127.0.0.1/cb", "v", tok, http=FakeGoogle(1))
    saved = json.loads(tok.read_text())
    assert saved["refresh_token"] == "r1" and saved["expires_at"] > time.time()


# ------------------------------------------------------------------ панель
@pytest.fixture
def client(yt_cfg):
    work = Path(yt_cfg["work_dir"])
    shutil.copytree(DEMO, work / "job_demo")
    app = create_app(yt_cfg, DB(work / "shorts.db"))
    calls = []

    def fake_upload(path, title, desc, tags, privacy, cfg, progress):
        calls.append((Path(path).name, title, privacy, tags))
        progress(0.5)
        return "YT42"

    app.state.uploader = fake_upload
    c = TestClient(app)
    c.calls = calls
    return c


def wait_upload(client, mid):
    for _ in range(100):
        m = next(x for x in client.get("/api/jobs/job_demo").json()["moments"] if x["id"] == mid)
        if not m["upload"]:
            return m
        time.sleep(0.02)
    raise AssertionError("загрузка не закончилась")


def test_panel_publish_flow(client):
    st = client.get("/api/youtube").json()
    assert st["configured"] is True and st["authorized"] is True
    assert client.post("/api/jobs/job_demo/moments/m00_0/publish").status_code == 409   # не одобрен
    client.patch("/api/jobs/job_demo/moments/m00_0", json={"approved": True, "title": "Моё"})
    assert client.post("/api/jobs/job_demo/moments/m00_0/publish").status_code == 202
    m = wait_upload(client, "m00_0")
    assert m["youtube_id"] == "YT42"
    assert client.calls == [("m00_0.mp4", "Моё", "private", ["demo"])]
    r = client.post("/api/jobs/job_demo/moments/m00_0/publish")
    assert r.status_code == 409 and "youtu.be/YT42" in r.json()["detail"]


def test_panel_publish_error_shown(client):
    def boom(*a, **k):
        raise youtube.YouTubeError("квота")

    client.app.state.uploader = boom
    client.patch("/api/jobs/job_demo/moments/m01_36", json={"approved": True})
    client.post("/api/jobs/job_demo/moments/m01_36/publish")
    for _ in range(100):
        m = next(x for x in client.get("/api/jobs/job_demo").json()["moments"] if x["id"] == "m01_36")
        if m["upload"] and m["upload"]["error"]:
            break
        time.sleep(0.02)
    assert m["upload"]["error"] == "квота" and m["youtube_id"] is None
    client.app.state.uploader = lambda *a, **k: "OK1"   # повтор после ошибки разрешён
    assert client.post("/api/jobs/job_demo/moments/m01_36/publish").status_code == 202


def test_auth_redirects_to_google_and_callback_checks_state(client):
    r = client.get("/api/youtube/auth", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith(youtube.AUTH_URL)
    r = client.get("/api/youtube/callback?state=bad&code=x", follow_redirects=False)
    assert "youtube=error" in r.headers["location"]
