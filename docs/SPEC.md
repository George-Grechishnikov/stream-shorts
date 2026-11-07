# stream-shorts — спецификация

Сервис берёт запись стрима Twitch (VOD) или любое длинное видео, находит самые интересные моменты и монтирует из них вертикальные ролики 9:16 (Shorts / TikTok / Reels) с субтитрами.

Работает локально: Windows, Python 3.11, ffmpeg, NVIDIA GPU (у автора GTX 1660 Ti, 6 ГБ).

## 1. Конвейер

```
VOD url / файл
  └─ ingest     → meta.json, source.mp4, chat.jsonl, clips.json
      └─ signals → signals.json   (по секундам: чат, эмоуты, клипы, громкость)
          └─ candidates → candidates.json   (20–40 окон-кандидатов)
              └─ transcribe → transcript.json (только окна-кандидаты, слова с таймингами)
                  └─ select → moments.json   (5–15 лучших, точные границы, название)
                      └─ render → renders/<id>.mp4 + renders/<id>.json
                          └─ (web) просмотр/правка/одобрение → (publish) YouTube
```

Каждый шаг — отдельная команда, читает и пишет файлы в папке задачи `work/<job_id>/`. Шаг можно перезапустить, не повторяя предыдущие. Если выходной файл шага уже есть, шаг пропускается (флаг `--force` перезапускает).

```
python -m shorts run <url|file>          # весь конвейер
python -m shorts ingest|signals|candidates|transcribe|select|render <job_id>
```

## 2. Модули и ответственные

| Модуль | Путь | Кто | Суть |
|---|---|---|---|
| Модели данных | `shorts/models.py` | Claude | dataclass'ы ниже — **общий контракт** |
| Загрузка | `shorts/ingest/` | Claude | yt-dlp (видео), TwitchDownloaderCLI (чат), Helix API (клипы) |
| Сигналы | `shorts/signals/` | Claude | chat / clips / audio → посекундные ряды |
| Кандидаты | `shorts/candidates.py` | Claude | сумма сигналов → пики → окна |
| Расшифровка | `shorts/transcribe.py` | Claude | faster-whisper, word timestamps, CUDA |
| Отбор | `shorts/select.py` | Claude | LLM (Claude/OpenAI API) + запасная эвристика |
| Монтаж | `shorts/edit/` | Claude | раскладка 9:16, субтитры ASS, рендер ffmpeg/NVENC |
| CLI и конфиг | `shorts/cli.py`, `shorts/config.py` | Claude | |
| Очередь и БД | `server/db.py`, `server/queue.py` | **Codex** | SQLite + фоновый воркер |
| Веб-панель | `server/app.py`, `server/web/` | **Codex** | FastAPI + простая страница |
| Публикация | `shorts/publish/youtube.py` | **Codex** | YouTube Data API v3 |
| Тесты, CI | `tests/`, `.github/workflows/` | **Codex** | pytest, ruff |

Codex не меняет `shorts/models.py` без согласования: это общий контракт между модулями.

## 3. Модели данных (`shorts/models.py`)

Все времена — секунды от начала VOD (float).

```python
@dataclass
class VodMeta:      id: str; url: str; channel: str; title: str; duration: float; created_at: str; width: int; height: int; fps: float
@dataclass
class ChatMessage:  t: float; user: str; text: str; emotes: list[str]
@dataclass
class Clip:         id: str; title: str; views: int; vod_offset: float; duration: float; created_at: str; url: str
@dataclass
class Candidate:    start: float; end: float; peak: float; score: float; reasons: dict[str, float]
@dataclass
class Word:         start: float; end: float; text: str; prob: float
@dataclass
class Moment:       id: str; start: float; end: float; title: str; hook: str; score: float; reason: str; words: list[Word]
@dataclass
class RenderResult: moment_id: str; path: str; duration: float; layout: str; thumbnail: str
```

Файлы:

- `meta.json` — `VodMeta`
- `chat.jsonl` — по одному `ChatMessage` в строке
- `clips.json` — `list[Clip]`
- `signals.json` — `{"step": 1.0, "series": {"chat_rate": [...], "emote_hype": [...], "clips": [...], "loudness": [...], "laughter": [...]}}`, по значению на секунду
- `candidates.json` — `list[Candidate]`
- `transcript.json` — `{"<start>-<end>": [Word, ...]}`
- `moments.json` — `list[Moment]`
- `renders/<moment_id>.mp4` + `renders/<moment_id>.json` (`RenderResult`)
- `job.json` — статус шагов `{"ingest": "done", "signals": "running", ...}` и ошибки. Его читает веб-панель.

## 4. Сигналы и оценка

Каждый ряд нормируется робастно: `z = (x - median) / (1.4826*MAD)` по скользящему окну 10 мин, затем обрезается в [0, 6].

| Ряд | Как считается | Вес |
|---|---|---|
| `clips` | для каждого клипа +log(1+views) на [vod_offset, vod_offset+duration] | 3.0 |
| `chat_rate` | сообщений/сек, сглаживание 5 с | 1.5 |
| `emote_hype` | сообщения со смех/хайп-эмоутами и словами (KEKW, LUL, OMEGALUL, Pog, PogChamp, ахах, хаха, кек, ору, лол, жесть, ЛОЛ, 😂…) | 2.0 |
| `loudness` | RMS громкости аудио, дБ | 0.7 |
| `laughter` | (позже) классификатор смеха | 1.0 |

Чат реагирует с задержкой, поэтому ряды чата сдвигаются на −6 с (настраивается: `chat_delay`).

`score(t) = Σ w_i * z_i(t)`. Берутся пики с минимальным расстоянием 90 с, топ-N (по умолчанию 30). Окно кандидата — [peak − 45, peak + 20] с, потом его уточняет `select`.

## 5. Отбор (`select.py`)

Вход — кандидат с расшифровкой окна ±60 с вокруг пика и контекстом (название стрима, игра, топ сообщений чата в окне, названия клипов).

LLM возвращает JSON: `{keep: bool, start, end, title, hook, score 0-10, reason}`. Правила для модели:

- длина 15–59 с;
- ролик начинается с фразы или действия, которое цепляет, и заканчивается на развязке, а не на середине фразы;
- ролик понятен без контекста стрима.

Границы привязываются к границам слов и паузам. Ограничений по провайдеру нет: `llm.provider: anthropic | openai | none`. При `none` используется эвристика: окно по клипу с максимумом просмотров, границы по паузам речи, название = название клипа.

Важно: подписка ChatGPT Plus **не даёт** API-ключ. API оплачивается отдельно (по факту это центы за стрим).

## 6. Монтаж (`shorts/edit/`)

- Выход 1080×1920, 30/60 fps, H.264 через `h264_nvenc` (запасной вариант — `libx264`).
- Раскладки (`layout`):
  - `cam_top` — вебкамера сверху (≈35% высоты), игра снизу (crop по центру);
  - `full_crop` — центральный crop 9:16 с отслеживанием лица;
  - `blur_bg` — исходник целиком по центру, сзади размытая копия.
- Область вебкамеры задаётся в профиле стримера (`profiles/<channel>.yaml`: x, y, w, h в долях кадра). Для автоопределения есть `edit/facecam.py` — поиск прямоугольника с лицом по нескольким кадрам.
- Субтитры: ASS, слова из `Moment.words`, по 1–3 слова на экране, текущее слово подсвечено, жирный шрифт с обводкой, в нижней трети над игрой.
- Название-плашка `hook` в первые 2 с.
- Громкость: нормализация до −14 LUFS.

## 7. Конфиг (`config.yaml`)

```yaml
work_dir: work
twitch: {client_id: "", client_secret: ""}   # dev.twitch.tv → Register Your Application, для Helix (клипы)
tools: {ffmpeg: ffmpeg, twitch_downloader: TwitchDownloaderCLI.exe}
whisper: {model: large-v3, device: cuda, compute_type: int8_float16, language: ru}
llm: {provider: none, model: "", api_key_env: ANTHROPIC_API_KEY}
candidates: {top_n: 30, min_gap: 90, chat_delay: 6}
render: {encoder: h264_nvenc, layout: cam_top, fps: 30}
```

## 8. Нефункциональное

- Секреты только через переменные окружения или `config.yaml` в `.gitignore`.
- Исходники VOD удаляются после рендера (`keep_source: false`).
- Логи в `work/<job_id>/log.txt`.
- Права: сервис рассчитан на свои стримы и стримы с разрешения автора.
