# stream-shorts

Нарезка Shorts/TikTok из стримов Twitch и длинных видео: находит самые интересные моменты (клипы зрителей, всплески чата, громкость, смысл речи) и монтирует вертикальные ролики 9:16 с субтитрами.

Подробно — в [docs/SPEC.md](docs/SPEC.md). План задач C1–C5 — в [docs/CODEX_TASKS.md](docs/CODEX_TASKS.md) (все сделаны).

## Быстрый старт (Windows)

1. Установка — одна команда (ставит Python и ffmpeg через winget, если их нет; venv, зависимости,
   CUDA для whisper, TwitchDownloaderCLI, модель whisper ~3 ГБ, `config.yaml`):
   ```
   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
   ```
   Ключи: `-NoProxy` — если локальный прокси рвёт загрузку с PyPI/Hugging Face; `-Cpu` — без NVIDIA;
   `-Model medium` — модель полегче.
2. Запуск — воркер + панель, браузер откроется сам на http://127.0.0.1:8000:
   ```
   powershell -ExecutionPolicy Bypass -File scripts\run.ps1
   ```
3. В панели: вставь ссылку на VOD (или путь к файлу) → «Нарезать». На странице задачи — график интереса,
   карточки роликов: смотри, правь название, одобряй/отклоняй, сдвигай границы ±1 с с перерендером,
   загружай одобренные на YouTube.

По желанию в `config.yaml`:
- `twitch.client_id` / `client_secret` (dev.twitch.tv → Your Console → Register Your Application) — клипы зрителей как сигнал;
- `llm.provider` + API-ключ в переменной окружения — умный отбор, названия и хуки (без него — эвристика по чату).

## YouTube

1. [console.cloud.google.com](https://console.cloud.google.com) → проект → включи **YouTube Data API v3**.
2. OAuth consent screen: External, добавь свой аккаунт в Test users.
3. Credentials → OAuth client ID → **Desktop app** → скачай JSON в корень проекта как `client_secret.json`.
4. В панели на одобренном ролике — «подключи YouTube» (вход через Google), потом «Загрузить».
   Ролики уходят как **private** (`youtube.privacy` в `config.yaml`), в описании `#shorts`.

## Из командной строки

```
python -m shorts run https://www.twitch.tv/videos/<id>   # весь конвейер без панели
python -m shorts run D:\записи\stream.mp4
python -m server.queue --add <url>                        # поставить в очередь воркера
python -m shorts.publish.youtube auth                     # подключить YouTube без панели
```

Готовые ролики — в `work/<job>/renders/`.

## Шаги по отдельности

```
python -m shorts step <job> signals --force     # пересчитать сигналы
python -m shorts step <job> select --force      # заново выбрать моменты
python -m shorts render <job> m03_5123          # перерендерить один момент
```

## Вебкамера

Раскладка по умолчанию — `render.layout` в `config.yaml`; для IRL-стримов, где камера гуляет по кадру, ставь `layout: blur_bg` в профиль канала.

При раскладке `cam_top` область камеры ищется автоматически и сохраняется в `profiles/<канал>.yaml`. Если угадало неверно, поправь вручную (доли кадра):

```yaml
layout: cam_top
cam: [0.74, 0.02, 0.25, 0.25]   # x, y, ширина, высота
cam_h: 0.36                     # доля высоты ролика под камеру
```
