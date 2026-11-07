# stream-shorts

Нарезка Shorts/TikTok из стримов Twitch и длинных видео: находит самые интересные моменты (клипы зрителей, всплески чата, громкость, смысл речи) и монтирует вертикальные ролики 9:16 с субтитрами.

Подробно — в [docs/SPEC.md](docs/SPEC.md). Задачи для Codex — в [docs/CODEX_TASKS.md](docs/CODEX_TASKS.md).

## Быстрый старт (Windows)

1. Поставь Python 3.11+ и ffmpeg: `winget install Gyan.FFmpeg`.
2. Скачай [TwitchDownloaderCLI](https://github.com/lay295/TwitchDownloader/releases) и положи `TwitchDownloaderCLI.exe` в `tools/` — через него качается чат.
3. Установи зависимости:
   ```
   python -m venv .venv && .venv\Scripts\activate
   pip install -r requirements.txt
   ```
   Для GPU (GTX/RTX) faster-whisper нужны CUDA-библиотеки: `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*`.
4. `copy config.example.yaml config.yaml`, затем при желании впиши:
   - `twitch.client_id` / `client_secret` (dev.twitch.tv → Your Console → Register Your Application) — для клипов;
   - `llm.provider` + API-ключ в переменной окружения — для умного отбора и названий.
5. Запусти:
   ```
   python -m shorts run https://www.twitch.tv/videos/<id>
   python -m shorts run D:\записи\stream.mp4
   ```
   Готовые ролики появятся в `work/<job>/renders/`.

## Шаги по отдельности

```
python -m shorts step <job> signals --force     # пересчитать сигналы
python -m shorts step <job> select --force      # заново выбрать моменты
python -m shorts render <job> m03_5123          # перерендерить один момент
```

## Вебкамера

При раскладке `cam_top` область камеры ищется автоматически и сохраняется в `profiles/<канал>.yaml`. Если угадало неверно, поправь вручную (доли кадра):

```yaml
layout: cam_top
cam: [0.74, 0.02, 0.25, 0.25]   # x, y, ширина, высота
cam_h: 0.36                     # доля высоты ролика под камеру
```
