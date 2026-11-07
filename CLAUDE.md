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

## Статус (8 ноя 2026)
- **Первый реальный прогон прошёл** (ветка `claude/real-run-fixes`): VOD 2920331767 deepins02 (2.1 ч) → 12 роликов
  за ~45 мин (аудио+чат ~7 мин, расшифровка 28 окон на GPU ~5 мин, рендер ~20 с/ролик).
  Починено: DLL CUDA для whisper на Windows, моменты короче `min_len`, громкость в конце VOD, двоение субтитров
  (`.ass` рядом с mp4 подхватывали плееры — теперь `work/<job>/ass/`).
- deepins02 — **IRL-стрим**: камера то на весь экран, то окном в разных местах. Фиксированная рамка `cam_top`
  не подходит, в `profiles/deepins02.yaml` стоит `blur_bg`. Автопоиск камеры на IRL ошибается (рамка в центре кадра).
- Эвристика отбора = «где взорвался чат»: смысла не видит, название = заголовок стрима, бывают моменты почти без речи.
  Нормальные названия/хуки — только с LLM (`llm.provider`), а API-ключа у автора нет.
- Не проверены: Helix-клипы (нужны client_id/secret), YuNet на настоящей вебкамере (не IRL).

## Окружение ноутбука (грабли первого прогона)
- Python только 3.12 (не 3.11) — работает. venv: `.venv`.
- Локальный прокси `127.0.0.1:10809` (env + системный): через него **PyPI таймаутит**, а huggingface_hub (Xet)
  виснет на больших файлах. pip — с очищенными `HTTP(S)_PROXY` для процесса; модели whisper качать curl'ом
  `--noproxy "*"`. large-v3 лежит в `%USERPROFILE%\.cache\whisper\faster-whisper-large-v3`, путь — в `config.yaml`.
- Нет `gh` и не задано `git user.name/email` (коммиты — через `git -c user.name=... -c user.email=...`).

## Разделение работы
- **Codex сейчас не работает** — задачи C1–C5 из `docs/CODEX_TASKS.md` делает Claude Code (8 ноя 2026, по просьбе автора).
  Порядок: C5, C4, C1 → C2 → C3. Правила из `AGENTS.md` (контракт, ruff, pytest без GPU и сети) действуют и для Claude.
- **Claude Code** (здесь, на ноутбуке): реальные прогоны на GPU, отладка конвейера (`shorts/`), задачи C1–C5.
- Правки конвейера — отдельными коммитами.

## Грабли
- Чат качать только через TwitchDownloaderCLI; не подставлять чужие Client-ID в запросы к Twitch.
- Целый VOD не качаем: аудио для анализа, видео — кусками под моменты (`fetch_section`).
- `profiles/` с найденной камерой — данные пользователя; не перезаписывать вручную поправленный профиль.
