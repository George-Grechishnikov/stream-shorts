# stream-shorts — контекст для Claude Code

Нарезка вертикальных Shorts (9:16, субтитры) из VOD Twitch и длинных видео. Работает локально на ноутбуке автора.
Описание — `README.md`, контракт — `docs/SPEC.md` и `shorts/models.py`, задачи Codex — `docs/CODEX_TASKS.md`.

## Окружение
- Windows, Ryzen 7 4800H, GTX 1660 Ti (6 ГБ), Python 3.11, ffmpeg в PATH.
- `tools/TwitchDownloaderCLI.exe` — чат; `config.yaml` (из `config.example.yaml`, в git не попадает).
- Тестовый канал: **deepins02**. Последний VOD на 7 ноя 2026: https://www.twitch.tv/videos/2922216838 (~4.5 ч).

## Команды
```
.venv\Scripts\activate
python -m shorts run <vod-url | файл>        # весь конвейер → work/<job>/renders/
python -m shorts step <job> <шаг> --force    # ingest|signals|candidates|transcribe|select|render
python -m shorts render <job> [moment_id...] # перерендер
ruff check . ; python -m pytest -q
```

## Конвейер (shorts/)
ingest (yt-dlp аудио, TwitchDownloaderCLI чат, Helix клипы) → signals (чат, хайп-эмоуты, клипы, громкость)
→ candidates (робастный z, пики) → transcribe (faster-whisper, слова) → select (LLM anthropic/openai или эвристика)
→ render (cam_top / blur_bg / full_crop, ASS-субтитры по словам, NVENC → libx264).
Файлы шагов лежат в `work/<job_id>/`; шаг пропускается, если его выход уже есть.

Вебкамера (`shorts/edit/facecam.py`): YuNet (`cv2.FaceDetectorYN`), модель в `assets/models/`.
Берётся лицо, стоящее на месте в большинстве кадров; рамка — догадка (лицо ×3.2, 4:3), сохраняется в
`profiles/<канал>.yaml` с пометкой «проверь». Проверено только на синтетике.

## Статус (7 ноя 2026)
- v0.1 + YuNet в `main`. Тесты: `tests/test_facecam.py`.
- **Ни разу не прогонялось на реальном VOD.** Первое дело: прогнать на VOD deepins02, посмотреть ролики
  и `profiles/deepins02.yaml` (верно ли найдена камера), поправить что сломается.
- Не проверены: faster-whisper на GPU (нужны `nvidia-cublas-cu12`, `nvidia-cudnn-cu12==9.*`), Helix-клипы
  (нужны client_id/secret), YuNet на настоящей вебкамере.
- LLM-отбор: у автора ChatGPT Plus без API-ключа → пока `llm.provider: none` (эвристика).

## Разделение работы
- **Codex** делает C1–C5 из `docs/CODEX_TASKS.md`, каждую в своей ветке с PR. Порядок: C5, C4, C1 параллельно → C2 → C3.
  Правила для него — `AGENTS.md`.
- **Claude Code** (здесь, на ноутбуке): реальные прогоны на GPU, отладка конвейера (`shorts/`), ревью PR Codex
  (`gh pr list`, `gh pr diff <n>`), проверка, что PR не ломает контракт `shorts/models.py` и формат `work/<job>/`.
- Не трогай чужие ветки Codex без просьбы; правки конвейера — отдельными коммитами/PR.

## Грабли
- Чат качать только через TwitchDownloaderCLI; не подставлять чужие Client-ID в запросы к Twitch.
- Целый VOD не качаем: аудио для анализа, видео — кусками под моменты (`fetch_section`).
- `profiles/` с найденной камерой — данные пользователя; не перезаписывать вручную поправленный профиль.
