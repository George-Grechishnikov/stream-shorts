"""Папка задачи work/<job_id>/ и её статус job.json."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

STEPS = ["ingest", "signals", "candidates", "transcribe", "select", "render"]


@dataclass
class Job:
    id: str
    dir: Path

    def p(self, name: str) -> Path:
        return self.dir / name

    # ---- статус ----
    def status(self) -> dict:
        f = self.p("job.json")
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"id": self.id, "steps": {}}

    def set_step(self, step: str, state: str, error: str | None = None, **extra) -> None:
        st = self.status()
        st.setdefault("steps", {})[step] = state
        st["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        if error:
            st.setdefault("errors", {})[step] = error
        elif step in st.get("errors", {}):
            st["errors"].pop(step)
        st.update(extra)
        tmp = self.p("job.json.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.p("job.json"))

    def log(self, msg: str) -> None:
        line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        with open(self.p("log.txt"), "a", encoding="utf-8") as f:
            f.write(line + "\n")


def job_id_for(source: str) -> str:
    m = re.search(r"twitch\.tv/(?:\w+/)?videos?/(\d+)", source)
    if m:
        return f"v{m.group(1)}"
    stem = Path(source).stem if not source.startswith("http") else re.sub(r"\W+", "_", source)[-40:]
    return re.sub(r"[^\w\-]+", "_", stem)[:60] or "job"


def open_job(work_dir: str | Path, job_id: str) -> Job:
    d = Path(work_dir) / job_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "renders").mkdir(exist_ok=True)
    return Job(job_id, d)
