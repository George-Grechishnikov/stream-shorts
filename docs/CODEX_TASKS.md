# Задачи для Codex

Перед началом прочитай `docs/SPEC.md`. Контракт — `shorts/models.py` и файлы в `work/<job_id>/`: их формат не меняй, а если нужно изменить — оставь пометку в PR.

Каждая задача — отдельная ветка и PR. Стиль: Python 3.11, type hints, `ruff`, `pytest`.

---

## C1. Очередь задач и БД
**Файлы:** `server/db.py`, `server/queue.py`

- SQLite (`work/shorts.db`). Таблицы:
  - `jobs` (id, source_url, channel, title, status, created_at, error);
  - `moments` (job_id, moment_id, start, end, title, hook, score, approved bool, render_path).
- Воркер в отдельном процессе берёт задачи по одной (на ноутбуке одна GPU) и вызывает шаги конвейера как функции: `from shorts import pipeline; pipeline.run(job_id, steps=[...])`. Если `pipeline` ещё не готов — сделай заглушку с той же сигнатурой.
- Статусы шагов берутся из `work/<job_id>/job.json` (см. SPEC §3).
- Отмена и повтор шага.

**Готово, когда:** тест создаёт задачу, воркер проходит её на заглушках, статусы меняются.

## C2. Веб-панель
**Файлы:** `server/app.py`, `server/web/` (FastAPI + Jinja2 или один HTML с fetch; без тяжёлых фреймворков)

1. **Главная:** поле для ссылки на VOD или пути к файлу → кнопка «Нарезать»; список задач со статусами шагов (обновление раз в 3 с).
2. **Страница задачи:**
   - график `signals.json` (итоговый score по времени, Chart.js);
   - список моментов карточками: видео-превью рендера, название (редактируемое), score, причина, кнопки «Одобрить» / «Отклонить» / «Перерендерить»;
   - сдвиг начала и конца ±1 с с перерендером: `POST /api/jobs/{id}/moments/{mid}/rerender`.
3. Отдача видео из `work/` с поддержкой Range.

**Готово, когда:** панель открывается на `http://127.0.0.1:8000` и работает на тестовой папке `tests/fixtures/job_demo/` (её сделаю я).

## C3. Публикация в YouTube
**Файл:** `shorts/publish/youtube.py`

- OAuth 2.0 desktop flow (`client_secret.json` от пользователя, токен хранится в `work/.youtube_token.json`).
- `upload(render_path, title, description, tags, privacy="private") -> video_id`.
- По умолчанию загружает как private, а в описание добавляет `#shorts`.
- Кнопка «Загрузить» на карточке одобренного момента в панели.

## C4. Тесты и CI
- `tests/` для `signals`, `candidates` (на синтетических рядах) и `select` в режиме `provider: none`.
- GitHub Actions: ruff + pytest на ubuntu, без GPU и без сети.

## C5. Установщик для Windows
- `scripts/setup_windows.ps1`: проверяет Python 3.11 и ffmpeg (если нет — ставит через winget), CUDA-колёса для faster-whisper (ctranslate2 + cuDNN), скачивает TwitchDownloaderCLI в `tools/`, создаёт venv, ставит requirements.
- `scripts/run.ps1`: запускает воркер и веб-панель.
