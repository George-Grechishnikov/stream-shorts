"""Публикация на YouTube (Data API v3): OAuth 2.0 для desktop-приложения + загрузка с докачкой.

Без google-api-python-client — только requests.

Настройка (один раз):
  1. console.cloud.google.com → новый проект → включить «YouTube Data API v3».
  2. «OAuth consent screen»: External, добавить свой Google-аккаунт в Test users.
  3. Credentials → Create → OAuth client ID → Desktop app → скачать JSON как client_secret.json
     в корень проекта (в git не попадает).
  4. Войти: кнопка «Подключить YouTube» в панели или `python -m shorts.publish.youtube auth`.

  python -m shorts.publish.youtube auth
  python -m shorts.publish.youtube upload <файл.mp4> "Название" [--privacy private]
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
CHUNK = 8 * 1024 * 1024          # кратно 256 КБ, как требует resumable upload
PRIVACY = ("private", "unlisted", "public")


class YouTubeError(RuntimeError):
    pass


def paths(cfg: dict) -> tuple[Path, Path]:
    yt = cfg.get("youtube") or {}
    secret = Path(yt.get("client_secret") or "client_secret.json")
    token = Path(yt.get("token") or Path(cfg.get("work_dir", "work")) / ".youtube_token.json")
    return secret, token


def load_client(secret_path: Path) -> dict:
    if not secret_path.exists():
        raise YouTubeError(f"нет {secret_path} — скачай OAuth client (Desktop app) из Google Cloud Console")
    data = json.loads(secret_path.read_text(encoding="utf-8"))
    c = data.get("installed") or data.get("web")
    if not c or "client_id" not in c:
        raise YouTubeError(f"{secret_path}: не похоже на OAuth client JSON")
    return c


# ------------------------------------------------------------------ OAuth
def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client: dict, redirect_uri: str, state: str, challenge: str) -> str:
    q = {"client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
         "access_type": "offline", "prompt": "consent", "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def exchange_code(client: dict, code: str, redirect_uri: str, verifier: str, token_path: Path,
                  http: requests.Session | None = None) -> dict:
    http = http or requests.Session()
    r = http.post(TOKEN_URL, data={"client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
                                   "code": code, "code_verifier": verifier, "grant_type": "authorization_code",
                                   "redirect_uri": redirect_uri}, timeout=30)
    if r.status_code != 200:
        raise YouTubeError(f"Google не выдал токен: {r.status_code} {r.text[:300]}")
    tok = r.json()
    if "refresh_token" not in tok:
        raise YouTubeError("Google не вернул refresh_token — удали доступ приложения в аккаунте Google и войди снова")
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    save_token(token_path, tok)
    return tok


def save_token(path: Path, tok: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(tok), encoding="utf-8")
    tmp.replace(path)


def access_token(client: dict, token_path: Path, http: requests.Session | None = None) -> str:
    if not token_path.exists():
        raise YouTubeError("YouTube не подключён — войди через панель или `python -m shorts.publish.youtube auth`")
    tok = json.loads(token_path.read_text(encoding="utf-8"))
    if tok.get("access_token") and tok.get("expires_at", 0) > time.time():
        return tok["access_token"]
    http = http or requests.Session()
    r = http.post(TOKEN_URL, data={"client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
                                   "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        raise YouTubeError(f"не обновился токен YouTube ({r.status_code}) — войди заново: {r.text[:200]}")
    new = r.json()
    tok.update(access_token=new["access_token"], expires_at=time.time() + int(new.get("expires_in", 3600)) - 60)
    save_token(token_path, tok)
    return tok["access_token"]


# ------------------------------------------------------------------ загрузка
def describe(title: str, description: str = "") -> str:
    d = description.strip()
    return (d + "\n\n" if d else "") + "#shorts"


def upload(render_path: str | Path, title: str, description: str = "", tags: list[str] | None = None,
           privacy: str = "private", cfg: dict | None = None, http: requests.Session | None = None,
           progress: Callable[[float], None] | None = None) -> str:
    """Загружает ролик, возвращает video_id. По умолчанию — private, в описании #shorts."""
    cfg = cfg or {}
    if privacy not in PRIVACY:
        raise ValueError(f"privacy: {PRIVACY}")
    p = Path(render_path)
    if not p.is_file():
        raise YouTubeError(f"нет файла {p}")
    title = " ".join(title.split())[:100] or p.stem
    secret_path, token_path = paths(cfg)
    client = load_client(secret_path)
    http = http or requests.Session()
    size = p.stat().st_size

    meta = {"snippet": {"title": title, "description": describe(title, description)[:5000],
                        "tags": [t[:30] for t in (tags or [])][:15], "categoryId": "20"},   # 20 — Gaming
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}

    def headers(extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {access_token(client, token_path, http)}", **(extra or {})}

    r = http.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, json=meta, timeout=60,
                  headers=headers({"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}))
    if r.status_code != 200 or "Location" not in r.headers:
        raise YouTubeError(api_error("YouTube не начал загрузку", r))
    session_url = r.headers["Location"]

    sent, retries = 0, 0
    with open(p, "rb") as f:
        while sent < size:
            f.seek(sent)
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            try:
                r = http.put(session_url, data=chunk, timeout=300,
                             headers=headers({"Content-Length": str(len(chunk)), "Content-Range": f"bytes {sent}-{end}/{size}"}))
            except requests.RequestException as e:
                r, err = None, e
            if r is not None and r.status_code in (200, 201):
                vid = r.json().get("id")
                if not vid:
                    raise YouTubeError(f"YouTube не вернул id видео: {r.text[:200]}")
                if progress:
                    progress(1.0)
                return vid
            if r is not None and r.status_code == 308:          # кусок принят, продолжаем
                rng = r.headers.get("Range")
                sent = int(rng.split("-")[1]) + 1 if rng else 0
                retries = 0
                if progress:
                    progress(sent / size)
                continue
            if r is not None and r.status_code not in (500, 502, 503, 504):
                raise YouTubeError(api_error("YouTube отклонил загрузку", r))
            retries += 1
            if retries > 5:
                raise YouTubeError(f"загрузка оборвалась: {err if r is None else r.status_code}")
            time.sleep(min(30, 2 ** retries))
            q = http.put(session_url, headers=headers({"Content-Range": f"bytes */{size}"}), timeout=60)   # где остановились
            if q.status_code in (200, 201):
                return q.json()["id"]
            rng = q.headers.get("Range") if q.status_code == 308 else None
            sent = int(rng.split("-")[1]) + 1 if rng else 0
    raise YouTubeError("файл отправлен, но YouTube не подтвердил загрузку")


def api_error(prefix: str, r: requests.Response) -> str:
    try:
        e = r.json().get("error", {})
        reason = (e.get("errors") or [{}])[0].get("reason", "")
        msg = e.get("message", "")
    except ValueError:
        reason, msg = "", r.text[:200]
    hint = {"quotaExceeded": " (кончилась дневная квота API — завтра снова)",
            "uploadLimitExceeded": " (YouTube ограничил число загрузок за день)",
            "youtubeSignupRequired": " (у аккаунта нет канала YouTube)"}.get(reason, "")
    return f"{prefix}: {r.status_code} {reason} {msg}{hint}".strip()


# ------------------------------------------------------------------ CLI
def _cli_auth(cfg: dict) -> None:
    import http.server
    import threading
    import webbrowser

    secret_path, token_path = paths(cfg)
    client = load_client(secret_path)
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state:
                got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Готово — вкладку можно закрыть.".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}/"
    url = auth_url(client, redirect, state, challenge)
    print("Открываю браузер для входа в Google. Если не открылся — перейди по ссылке:\n" + url)
    webbrowser.open(url)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    t.join(300)
    srv.server_close()
    if "code" not in got:
        raise YouTubeError(got.get("error") or "не дождался ответа Google (5 мин)")
    exchange_code(client, got["code"], redirect, verifier, token_path)
    print(f"YouTube подключён, токен: {token_path}")


def main() -> None:
    import argparse

    from shorts.config import load_config

    ap = argparse.ArgumentParser(prog="shorts.publish.youtube")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth")
    u = sub.add_parser("upload")
    u.add_argument("file")
    u.add_argument("title")
    u.add_argument("--description", default="")
    u.add_argument("--privacy", default="private", choices=PRIVACY)
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.cmd == "auth":
        _cli_auth(cfg)
    else:
        vid = upload(a.file, a.title, a.description, privacy=a.privacy, cfg=cfg,
                     progress=lambda f: print(f"\r{f:.0%}", end="", flush=True))
        print(f"\nhttps://youtu.be/{vid}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
