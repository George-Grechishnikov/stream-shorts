# AGENTS.md — правила для Codex

Проект: нарезка Shorts из стримов Twitch. Сначала прочитай `docs/SPEC.md`, затем свою задачу в `docs/CODEX_TASKS.md` (C1–C5).

## Как работать
- Одна задача — одна ветка (`codex/c1-queue`, `codex/c2-web`, …) и один PR. В описании PR — номер задачи и что проверено.
- Python 3.11, type hints, `ruff`, `pytest`.
- Контракт не меняй: `shorts/models.py` и формат файлов в `work/<job_id>/` (SPEC §3). Если без изменения никак — пометка «КОНТРАКТ» в PR.
- Код конвейера (`shorts/`) правит Claude Code; ты работаешь в `server/`, `shorts/publish/`, `tests/`, `scripts/`, `.github/`.
  Если нужна правка в `shorts/` — минимальная, отдельным коммитом, с пояснением в PR.
- Не коммить `config.yaml`, `work/`, `tools/`, секреты и большие бинарники.

## Проверки (должны проходить перед PR)
```
pip install -r requirements.txt pytest ruff
ruff check .
python -m pytest -q
```
Тесты без GPU и без сети. Для панели и рендера есть готовая задача-образец: `tests/fixtures/job_demo/`.

## Окружение автора
Windows, GTX 1660 Ti, Python 3.11, ffmpeg. Скрипты под Windows — PowerShell (`scripts/*.ps1`).
