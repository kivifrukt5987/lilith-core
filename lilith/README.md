# 🦇 LILITH-CORE

Домашний Python-стек ИИ-компаньона **Лилит**: оркестратор, память, уши, горло, лицо, руки и мосты.
Всё живёт локально на машине Кирюши, мозг — любой OpenAI-совместимый endpoint (LM Studio, llama.cpp,
vLLM, Ollama, OpenRouter).

**Текущий статус: ЭТАП 6 — «Лицо = Unity-клиент»** (пивот: продюсер `/ws/face/producer`,
реестр персон v2, LoRA-слот prompt-only, групповые сцены, `unity-client/`).

| | |
|---|---|
| Версия | `0.6.2` (этап 6 + ADR-020/ADR-021: приёмы «Нейроны», ответы Q1–Q5, три красных приёмки закрыты) |
| Этап | 6 из 9 (`face-unity`) — ждём сборки Unity у Кирюши и команду «дальше» для этапа 7 |
| Python | 3.11+ |
| Тесты | **665 passed** (pytest), 0 warnings |
| Unity | **6000.0.x LTS** (у Кирюши 6000.0.84f1) · Built-in RP · UniVRM 0.131.2 · VRM 1.0 |
| Целевая машина | Windows 11, RTX 3060 12GB, i7-12700KF, 32GB RAM |

---

## 🗺️ Дорожная карта

| Этап | Слой | Что делаем | Статус |
|------|------|-----------|--------|
| **1** | **Скелет** | конфиг (YAML + `.env`, pydantic-settings), loguru, FastAPI + uvicorn, WebSocket `/ws` эхо, мини веб-панель | ✅ готово · v0.1.2 |
| **2** | **Мозг** | `brain/llm.py` — OpenAI-совместимый клиент, реестр профилей, чат-цикл с `persona.md`, стриминг, ток/сек | ✅ готово · v0.2.1 |
| **3** | **Память** | aiosqlite-журнал, chromadb-RAG (HashEmbedder), саммаризатор с настраиваемым порогом | ✅ готово · v0.3.1 |
| **4** | **Уши и горло** | `voice/stt.py` (faster-whisper + silero-vad + push-to-talk), `voice/tts.py` (silero/edge/zero-shot), паки моделей | ✅ готово · v0.4.2 |
| **5** | **Лицо v1→v2** | `face/emotions.py` (парсер `[emotion: X]`), VTuber Studio/VMC-адаптер → затем VRM в панели, виземы, реестр персон | ✅ готово · v0.5.1 |
| **6** | **Лицо = Unity** | **ПИВОТ**: продюсер `/ws/face/producer` (raw PCM 24 kHz / 2048 Б), персоны v2 (`card`/`voice`/`face.yaml`), LoRA-слот prompt-only, группы `/ws/group`, `unity-client/` (7 C#-скриптов) | ✅ **готово** · v0.6.0 |
| 6.5 | Память персон | своя область памяти на персону (chroma-коллекции с префиксом, выборка по `memory_scope`) | ⏳ запланировано |
| 7 | Руки | `hands/tools.py` (psutil-виталс, playwright open_url, obs-websocket) + баннер подтверждения на 25 с (Human-in-the-Loop) | ⏳ |
| 8 | Мосты | `bridges/discord_bot.py` (discord.py), `bridges/vk_bot.py` (vkbottle) — один мозг, общая память | ⏳ |
| 9 | Стрим и сервис | twitchio-триггеры, сцены OBS, docker-compose (searxng, redis), автозапуск `.bat`, бэкап репо | ⏳ |

---

## ⚡ Быстрый старт на Windows

### 1. Установить Python 3.11+

Проверить: `py -3.11 --version` (или `python --version`).
Скачать, если нет: <https://www.python.org/downloads/> — **обязательно** поставить галку «Add python.exe to PATH».

### 2. Создать виртуальное окружение и поставить зависимости

Из корня проекта (`Локальная Лилит`):

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[dev,memory]"
```

> `-e .` ставит проект в редактируемом режиме: правки кода подхватываются без переустановки.
> `[dev]` добавляет pytest. Только рантайм: `pip install -e .`

### 3. Настроить конфиг и секреты

```bat
copy .env.example .env
notepad config\config.yaml
notepad .env
```

На этапе 1 менять ничего не обязательно — всё работает на значениях по умолчанию.
Секреты (`api_key`, токены Discord/VK/Twitch/OBS) пишем **только в `.env`**, он в `.gitignore`.

Приоритет значений (сверху важнее):

```
переменные окружения  >  .env  >  config/config.yaml  >  значения по умолчанию в коде
```

Переопределение любого поля из окружения: `LILITH_<РАЗДЕЛ>__<ПОЛЕ>` (двойное подчёркивание).
Пример: `set LILITH_SERVER__PORT=9000`.

### 4. Запустить

Двойной клик по **`start.bat`** внутри папки проекта (или по файлу
**`ПРОЧТИ_МЕНЯ.txt`** сначала, там три строки для людей). Скрипт сам создаст `.venv`,
сам поставит зависимости (только первый запуск, пара минут), сам создаст `.env`,
поднимет сервер и **сам откроет браузер** с панелью. Чёрное окно консоли — сервер:
не закрывайте его, пока работает панель (можно свернуть). Остановка — `Ctrl+C` в нём.

Полезные флаги:

```bat
python -m lilith_core.run --port 9000            :: другой порт
python -m lilith_core.run --host 0.0.0.0         :: доступ из локальной сети
python -m lilith_core.run --reload               :: автоперезагрузка при правке кода
python -m lilith_core.run --log-level DEBUG      :: подробные логи
python -m lilith_core.run --show-config          :: напечатать эффективный конфиг и выйти
python -m lilith_core.run --config my.yaml       :: свой файл конфига
python -m lilith_core.run --mock-brain             :: демо этапа 2 без модели (mock-мозг)
```

Сборка архива этапа (то, что улетает Кирюше в виде артефакта):

```bat
python scripts\build_stage_archive.py --stage 1
```

### 5. Открыть веб-панель

| Что | Где |
|---|---|
| Панель (чат) | <http://127.0.0.1:8765/> |
| Диагностика | <http://127.0.0.1:8765/healthz> |
| Версия и roadmap | <http://127.0.0.1:8765/api/version> |
| Состояние подключений | <http://127.0.0.1:8765/api/state> |
| Swagger (при `app.debug: true`) | <http://127.0.0.1:8765/docs> |
| WebSocket | `ws://127.0.0.1:8765/ws` |
| Лог-файл | `logs/lilith.log` |
| Статистика токенов | <http://127.0.0.1:8765/api/brain/stats> |
| Перечитка персоны | `POST /api/persona/reload` (правка persona.md подхватывается и сама) |

Остановка сервера: `Ctrl+C` в консоли.

---

## 🧪 Тесты

```bat
.venv\Scripts\activate
pytest -q
```

Ожидаемый результат этапа 1:

```
665 passed
```

Покрытие: конфиг и приоритет источников, секреты не протекают в логи/healthz,
протокол шины (включая кривые пакеты), менеджер сессий, логирование и перехват
стандартного `logging`, персона и подстановка плейсхолдеров, HTTP-эндпоинты,
WebSocket-эхо, CLI-обёртка запуска.

Проверка, что ничего не лишнего не попало в пакет:

```bat
pytest tests/test_structure.py -q
```

---

## 🔌 Протокол WebSocket

Клиент и сервер обмениваются JSON одного формата:

```json
{"id": "a1b2c3d4e5f6", "type": "chat", "text": "привет", "data": {"source": "webui"}, "ts": "2026-09-16T19:38:00+03:00"}
```

### Типы сообщений

| `type` | Направление | Назначение |
|---|---|---|
| `hello` | ↔ | рукопожатие. Сервер присылает `name`, `version`, `stage`, `mode`, `session_id` |
| `chat` | ↔ | реплика пользователя / ответ персонажа. На этапе 1 ответ — эхо с `data.echo = true` |
| `ping` / `pong` | → / ← | проверка живости, в `pong` приходит `latency_ms` |
| `system` | ↔ | служебные сообщения (ответа не требуют) |
| `log` | ← | строка лога для панели |
| `error` | ← | ошибка: `data.code` ∈ `empty_text`, `message_too_large`, `reply_failed`, `unsupported_type`, `error` |
| `state` | ← | состояние сервера; на этапе 6 сюда приедет баннер подтверждения инструментов |

**Важно:** кривой пакет не рвёт соединение — сервер отвечает `error` и продолжает работу.

### Пример на чистом Python

```python
import asyncio, json, websockets   # pip install websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8765/ws") as ws:
        print(json.loads(await ws.recv()))          # hello
        print(json.loads(await ws.recv()))          # system
        await ws.send(json.dumps({"type": "chat", "text": "Лиль, ты тут?"}))
        print(json.loads(await ws.recv()))          # эхо

asyncio.run(main())
```

---

## 📂 Структура проекта

```
Локальная Лилит/
├── config/
│   └── config.yaml            # основная конфигурация (без секретов)
├── src/lilith_core/
│   ├── __init__.py            # версия, номер этапа, кодовое имя
│   ├── __main__.py            # python -m lilith_core
│   ├── run.py                 # CLI + запуск uvicorn
│   ├── config.py              # pydantic-settings: YAML + .env + окружение
│   ├── logging_setup.py       # loguru + InterceptHandler
│   ├── app.py                 # FastAPI: HTTP, /ws, веб-панель
│   ├── protocol.py            # схема сообщений шины
│   ├── session.py             # менеджер подключений и статистика
│   ├── echo.py                # обработчик реплик этапа 1 (точка замены на мозг)
│   ├── persona/
│   │   ├── __init__.py        # загрузка persona.md + плейсхолдеры
│   │   └── persona.md         # системный промт Лилит
│   ├── voice/
│   │   ├── stt.py · tts.py · packs.py · hotkey.py
│   │   └── pcm.py             # ЭТАП 6: ресемплинг + PcmChunker (2048 Б)
│   ├── face/
│   │   ├── emotions.py · visemes.py · bus.py · vtuber_bridge.py
│   │   ├── personas.py        # ЭТАП 6: card/voice/face.yaml + legacy-фолбэк
│   │   ├── ws_frames.py       # ЭТАП 6: плоские кадры продюсера
│   │   ├── producer.py        # ЭТАП 6: FaceProducerHub (реплики, stop, focus, swap)
│   │   ├── endpoints.py       # ЭТАП 6: /ws/face/producer · /ws/group · /ws/unity(алиас)
│   │   ├── lora.py            # ЭТАП 6: PersonaLoraManager (prompt-only) + стабы
│   │   └── group.py           # ЭТАП 6: групповые сцены (потолок 4, слоты, фокус)
│   └── webui/
│       ├── index.html         # мини веб-панель (VRM-сцена под флагом web_vrm_enabled)
│       └── vendor/            # three.js/three-vrm — фолбэк-путь этапа 5
├── unity-client/              # ЭТАП 6: Unity-клиент лица (C#, UniVRM 0.131.2)
│   ├── Packages/manifest.json
│   └── Assets/LilithFace/     # 7 скриптов + README.md (сборка) + SCENE.md/SVG (схема сцены)
├── personas/
│   ├── lilith/                # card.yaml · voice.yaml · face.yaml · persona.md · fallback.jpg
│   └── _template/             # заготовка новой персоны (реестр её не считает)
├── tests/                     # 665 тестов
├── scripts/
│   ├── ws_client.py           # ручная проверка шины из консоли (-i = диалог)
│   ├── unity_face_probe.py    # ЭТАП 6: эмулятор Unity-клиента (проверка тракта без Unity)
│   ├── make_test_vrm.py       # ЭТАП 6: генератор тестовой VRM 1.0 (процедурный куб)
│   └── build_stage_archive.py # сборка zip-архива этапа в artifacts/
├── logs/                      # lilith.log (создаётся автоматически)
├── artifacts/                 # zip-архивы этапов
├── docs/ARCHITECTURE.md       # архитектура и контракты между этапами (9.8 — продюсер лица)
├── docs/DECISIONS.md          # журнал решений: ADR-001…ADR-019
├── docs/NEURONA_NOTES.md      # разбор «Нейроны» (furrydev2007) как референса пивота
├── CHANGELOG.md               # что сделано на каждом этапе
├── start.bat                  # запуск на Windows
├── run_tests.bat              # прогон тестов на Windows
├── pyproject.toml             # зависимости, extras по этапам, pytest, ruff
├── requirements.txt           # рантайм-зависимости этапа 1 (для pip -r)
├── .env.example               # шаблон секретов
└── .gitignore
```

### Как проект будет расти

Каждый следующий этап добавляет **свой пакет**, не переписывая предыдущие:

```
src/lilith_core/
├── brain/     # этап 2: llm.py, chat.py, stats.py        ✅
├── memory/    # этап 3: journal.py, rag.py, summarizer.py ✅ (6.5 — память персон)
├── voice/     # этап 4: stt.py, tts.py, packs.py + pcm.py (этап 6) ✅
├── face/      # этапы 5–6: эмоции, виземы, персоны, продюсер, группы, LoRA ✅
├── hands/     # этап 7: tools.py, registry.py, confirm.py
├── bridges/   # этап 8: discord_bot.py, vk_bot.py, base.py
└── stream/    # этап 9: twitch.py, obs.py, service.py
```

Контракт между слоями уже зафиксирован: `echo.build_reply_handler(settings)` возвращает
`ReplyHandler = Callable[[str, dict], Awaitable[Message]]`. На этапе 2 мозг вернёт функцию
с той же сигнатурой — веб-панель и мосты переписывать не придётся.

---

## ⚙️ Конфигурация: что уже заложено под будущие этапы

`config/config.yaml` **уже** содержит секции всех восьми этапов, поэтому на этапах 2–8
не придётся менять формат конфига — только значения:

| Секция | Для этапа | Ключевые поля |
|---|---|---|
| `app` | 1 | `user_name`, `persona_name`, `persona_path`, `debug`, `env` |
| `server` | 1 | `host`, `port`, `ws_path`, `ws_max_message_size`, `cors_origins` |
| `logging` | 1 | `level`, `dir`, `rotation`, `retention`, `compression`, `diagnose` |
| `brain` | 2 | **реестр профилей моделей**: `default_profile`, `defaults` (общие параметры), `profiles.<имя>` (`base_url`, `model`, `temperature`, `max_tokens`, `api_key_env`, `note`), `api_key_env`, `system_prompt_extra` |
| `memory` | 3 | `db_path`, `summarize_every_n`, `chroma_path`, `embedding_model`, `top_k` |
| `voice` | 4 | `stt_model`, `stt_compute_type`, `vad_threshold`, `push_to_talk_key`, `tts_backend`, `tts_voice` |
| `face` | 5 | `vtuber_studio_url`, `vmc_port`, `reconnect_delay_sec`, `emotion_default` |
| `hands` | 6 | `confirm_timeout_sec` (25), `require_confirmation`, `obs_host`, `obs_port` |
| `bridges` | 7 | `discord_token_env`, `vk_token_env`, `discord_command_prefix` |
| `stream` | 8 | `twitch_channel`, `obs_scene_on_chat`, `obs_scene_on_stream`, `autostart` |
| `features` | все | флаги `brain_enabled`, `memory_enabled`, `voice_enabled`, … |

Секреты читаются из **переменных окружения** по имени из поля `*_env`
(`api_key_env`, `obs_password_env`, `discord_token_env`, …) — так токен никогда
не попадает в YAML и в логи. Метод `settings.public_dict()` маскирует ключ
перед выводом в `/healthz`.

---

## 🎭 Лицо = Unity-клиент (этап 6 готов) — ПИВОТ

Включается флагом `features.face_enabled: true` или `--with-face`.
Код сервера: `face/producer.py`, `face/ws_frames.py`, `face/endpoints.py`,
`face/personas.py`, `face/lora.py`, `face/group.py`, `voice/pcm.py`.
Код клиента: `unity-client/` (инструкция сборки — `unity-client/Assets/LilithFace/README.md`).

**Что изменилось:** three-vrm больше не основной путь (ADR-015). Лицо — отдельное
Unity-окно: прозрачное, живёт справа снизу на рабочем столе, захватывается OBS.
Веб-панель осталась диагностикой: debug-оверлей, фолбэк-аватар, селектор персон,
а VRM-сцена в ней включается флагом `face.web_vrm_enabled` (по умолчанию `false`).

### Продюсер `/ws/face/producer`

Сервер отдаёт **плоские JSON-кадры** (без общего конверта) — так C#-клиенту нечего
разворачивать, а на 12 кадрах в секунду это заметный трафик:

| Кадр | Что несёт |
|---|---|
| `hello` | `producer`, `protocol`, `sample_rate` **24000**, `format` **pcm_s16le**, `chunk_bytes` **2048**, активная `persona`, список `personas`, `server_version` |
| `audio` | base64 **raw PCM int16 mono**, ровно 2048 байт (1024 сэмпла), `seq`, `offset_ms`, `utterance_id`, `final` |
| `viseme` | серверная разметка A/I/U/E/O — **только** если клиент попросил `want_server_visemes` |
| `emotion` | `tag`, `intensity`, `ttl_ms` (гасит эмоцию **клиент**, не сервер) |
| `persona` | своп: `id`, `vrm`, `voice{pack,speaker}`, `card`, `face{idle,…}` |
| `focus` | на кого смотреть камере (групповая сцена) |
| `stop` | прервать реплику: клиент дропает чанки с этим `utterance_id` |
| `done` | конец реплики + число чанков |
| `state` / `error` / `pong` | служебное |

Клиент → сервер: `hello` (с `want_server_visemes`), `ready`, `persona_request`,
`speak`, `stats{fps,dropped,queued_ms,…}` раз в 5 с, `ping`.

`/ws/unity` сохранён **алиасом** продюсера: первым кадром отдаёт legacy-`hello`
этапа 5, дальше работает по полному протоколу. Основной `/ws` по-прежнему стримит
`face{kind:audio|viseme|done}` в веб-панель — оба потребителя получают один `utterance_id`.

### Персоны-агенты v2

```
personas/<id>/
├── card.yaml    # id, display_name, brain_profile, greeting, tags,
│                # lora{path,trigger_word,scale,autoload}, tools[], memory_scope
├── voice.yaml   # pack, speaker, sample_rate 24000, speed, pitch_shift,
│                # fallback_pack, reference_wav
├── face.yaml    # vrm_path (тело ВНЕ репо), fallback, slot/position для группы,
│                # idle{blink_freq,breath_amp,look_speed}, оверрайды эмоций/визем
├── persona.md   # душа
└── fallback.jpg # статичный аватар
```

* Старый `profile.yaml` продолжает читаться (legacy-фолбэк) — миграция не обязательна.
* Папки на `_`/`.` персонами не считаются: `personas/_template/` — заготовка.
* `GET /api/face/personas` — публичная сводка (карточка целиком **не** отдаётся);
  `GET /api/personas` — алиас этапа 5.
* `GET /api/face/personas/<id>/model.vrm` — отдаёт тело по пути из `face.yaml`
  (модель может лежать где угодно на диске).
* `POST /api/face/personas/<id>/activate` — глобальный своп: рассылает кадр `persona`
  всем подключённым клиентам. То же из Unity (`persona_request`) и из панели (селектор 🎭).
* **LoRA** — слот интерфейсный (`PersonaLoraManager`): этап 6 работает в режиме
  **prompt-only**, бэкенды LocalAi/LM Studio/llama.cpp объявлены стабами.
* **Память** — в этапе 6 добавлена только колонка `messages.persona_id` (+ автоматическая
  миграция старых БД) и `memory_scope` в карточке. Полноценная персональная память — **этап 6.5**.

### Групповые сцены `/ws/group`

Один сокет на группу, кадры мультиплексируются полем `persona`. Первым приходит
`hello-group` с составом и рассадкой (`participants[{persona,slot,position}]`,
потолок `face.group_max_participants: 4`, текущий `focus`). Рассадка — `group.yaml`
поверх `face.yaml`. Говорит только фокусная персона: «хор» запрещён, запрос от
не-фокусной получает `error{code:"not_focused"}`. Камера — на стороне Unity,
OBS-сцены — этап 9. В этом этапе готова **серверная часть и контракт**; Unity-сцена
группы — следующий шаг.

### Unity-клиент

`unity-client/Assets/LilithFace/Scripts/`:

| Скрипт | За что отвечает |
|---|---|
| `LilithFaceClient.cs` | оркестратор: кадры → драйверы, `stats` раз в 5 с, debug-оверлей |
| `LilithWSClient.cs` | WS-транспорт на встроенном `ClientWebSocket` + автореконнект |
| `LilithClientConfig.cs` | все настройки в инспекторе (URL, окно, виземы, idle) |
| `AudioQueueProcessor.cs` | чанки PCM → кольцевой `AudioClip` → `AudioSource` |
| `VisemeDriver.cs` | RMS + zero-crossing по окну 60 мс → `aa/ih/ou/ee/oh` (тот же алгоритм, что на сервере) |
| `EmotionDriver.cs` | `tag` + `ttl_ms` → экспрессии VRM 1.0, плавное сведение |
| `IdleController.cs` | моргание, дыхание 0.25 Гц, взгляд за курсором |
| `VrmLoader.cs` | горячий своп тела (`Vrm10.LoadBytesAsync`): с диска или по HTTP |
| `TransparentWindow.cs` | DWM / color key / off, borderless + topmost, справа снизу, F8·F9 |
| `FaceRig.cs` | прослойка к UniVRM (всё под `#if LILITH_UNIVRM`) |
| `MiniJson.cs` | свой JSON-парсер: внешних пакетов у клиента нет |

### Приёмы «Нейроны» (ADR-020, v0.6.1)

Разбор стека furrydev2007 (`docs/NEURONA_NOTES.md`) дал четыре заимствования —
**приёмы, а не продукты**:

1. **Sample-accurate очередь визем** — виземы привязываются к позиции в аудио-буфере
   в сэмплах, а не к такту `Update()`. Включается в инспекторе:
   `Use Sample Accurate Visemes` (+ `Request Server Visemes` для серверной разметки).
   **Дефолт выключен**: `offset_ms` остаётся основным путём.
2. **Японские имена блендшейпов** — `FaceRig` понимает `あ/い/う/え/お` и
   `笑い/怒り/悲しみ/驚き/瞬き` и на входе, и как fallback через
   `ExpressionKey.CreateCustom`, если в модели нет пресета VRM 1.0. Фолбэк не слепой:
   при `Bind()` снимается список реально имеющихся экспрессий.
3. **Nonverbal-слот** в `personas/<id>/voice.yaml` (`laugh/sigh/hum/cry`) — объявлен,
   доезжает до Unity в кадре `persona.voice`; синтез отложен до этапа голоса.
4. **Спека этапа 7** (`docs/STAGE7_HANDS_SPEC.md`) — sandbox-воркспейс + confine +
   гарантированный cleanup (US2) и сессионные пермишены с лимитами ресурсов (US3),
   8 user stories в Given/When/Then, quality gates и «конституция» = `DECISIONS.md`.

Не повторяем осознанно: SALSA (платный ассет), облачный OpenAI, Postgres+Qdrant,
NVIDIA Riva, Claude Code/SpecKit как продукты.

### Проверка без Unity

```bat
:: сервер
start.bat

:: в другом окне: эмулятор Unity-клиента (чистый stdlib, зависимостей нет)
python scripts\unity_face_probe.py --url ws://127.0.0.1:8765/ws/face/producer --speak "Привет, Кирюша."

:: то же с серверными виземами и свопом персоны
python scripts\unity_face_probe.py --speak "Раз два три." --want-server-visemes --persona nova

:: групповая сцена
python scripts\unity_face_probe.py --url ws://127.0.0.1:8765/ws/group --group main --speak "Раз."

:: JSON-отчёт (приложить к отчёту по этапу)
python scripts\unity_face_probe.py --speak "Проверка." --json probe.json
```

Проба проверяет: размер каждого чанка (ровно 2048 Б), непрерывность `seq`,
монотонность `offset_ms`, кратность байт двум, приход `done`, наличие/отсутствие
серверных визем, своп персоны. Код возврата `0/1` — можно вешать в CI.

**Тестовое тело** (пока Кирюша не напечатал настоящее в VRoid Studio):

```bat
python scripts\make_test_vrm.py --out personas\lilith\model.vrm --name Lilith
```

Собирает валидный **VRM 1.0** на чистом stdlib: 55 humanoid-костей в T-позе,
два скиннутых меша, 11 морф-таргетов и все экспрессии (`aa ih ou ee oh`,
`happy angry sad relaxed surprised`, `blink`). ~21 КБ; копия для тестов лежит в
`tests/samples/test_cube.vrm`.

---

## 😊 Лицо v2: VRM в панели (этап 5) — теперь фолбэк-путь

> **Этап 6 перевёл лицо в Unity.** Раздел ниже описывает браузерный путь; он жив,
> но выключен флагом `face.web_vrm_enabled: false`. Основной путь — `unity-client/`.

Включается флагом `features.face_enabled: true` или `--with-face`.

* **Сцена над чатом**: VRM-вьювер (three.js + three-vrm из локального `webui/vendor/`,
  без CDN). Модель берётся из `personas/<id>/model.vrm`; без модели — фолбэк-аватар
  (`fallback.jpg/png`). Поверх — face-debug оверлей: режим, визема, интенсивность,
  эмоция, fps — критерии проверяются даже без VRM.
* **Рот**: сервер режет TTS-чанки на окна 60 мс и шлёт корзины визем A/I/U/E/O
  с интенсивностью; панель сглаживает (low-pass) и применяет в такт аудио.
* **Эмоции**: теги `[emotion: …]` вырезаются из реплики, уходят кадрами в панель
  (blend-shapes) и в опциональный VTuber Studio-адаптер.
* **Idle**: случайное моргание, дыхание, покачивание, взгляд за курсором мыши.
* **Персоны**: `personas/<id>/` = persona.md + model.vrm + fallback + profile.yaml;
  `/api/personas` и статика `/personas/…`; новые папки подхватываются горячо.
  Это фундамент вкладок «Агенты» и «3D-модели» этапа 8.
* **Голос в панели**: тумблер «🔊» — ответы озвучиваются стримом по тому же WS,
  рот движется по слогам.
* **/ws/unity** — зарезервированный адаптер Unity+VRM+SALSA (зеркало face-кадров);
  OBS-сцена с лицом — апгрейд этапа 8.

## 🎙 Голосовой контур и паки моделей (этап 4 готов)

Включается флагом `features.voice_enabled: true` или `--with-voice`.
Зависимости: `pip install -e ".[voice]"` (torch/faster-whisper/pynput/edge-tts).

* **Уши**: хоткей `ctrl+space` → VAD (silero или energy без зависимостей) →
  faster-whisper (small/medium/large-v3, GPU/CPU) → текст в общую шину.
  Резервные слоты: whisper-cpp, vosk (объявлены, подключаются по необходимости).
* **Горло**: voice-профили в конфиге (`voice.profiles`): silero v4_ru по умолчанию,
  edge-tts как облачный фолбэк, **zero-shot слот** под клон-движок
  (IndexTTS/XTTS/CosyVoice/F5): положите `voices/<имя>/reference.wav` (3–10 с)
  и укажите `reference` в профиле — код не правится.
* **Hot-swap**: смена stt-бэкенда и голосового профиля действует на следующий вызов,
  без перезапуска; при ошибке — фолбэк на дефолт.
* **Паки моделей**: `models/packs.yaml` (источник hf/hf-mirror/url/local, size, sha256,
  куда класть) + CLI `lilith-core packs list|install|remove` с докачкой и проверкой хеша.
  Свой пак = новая строка в манифесте. Секция «Паки» в панели приедет на этапе 6.
* HTTP: `/api/voice/profiles`, `/api/voice/transcribe` (WAV→текст),
  `/api/voice/say` (текст→wav), `/api/packs` (+ install/remove).

## 📜 Память и настраиваемое саммари (этап 3 готов)

Память включается флагом `features.memory_enabled: true` или `--with-memory`.
Зависимости памяти (aiosqlite + chromadb) ставятся вместе с проектом:
`pip install -e ".[dev,memory]"` — `start.bat` делает это сам.

* **Журнал** `data/lilith.db` (aiosqlite): все реплики и ответы, факты и саммари,
  изоляция по агентам, переживает перезапуски.
* **RAG** `data/chroma/` (chromadb): перед ответом релевантные воспоминания
  подмешиваются в контекст вторым системным сообщением.
* **Авто-саммари**: каждые N сообщений журнал жуётся в выжимку профилем `summarizer`.
  **N настраивается из программы**: шестерёнка «⚙ память» в панели или
  `POST /api/memory/settings` (`{"summarize_every_n": 25}`); там же `top_k`,
  `auto_summarize`, `rag_enabled`. Значения живут в `data/runtime_settings.json`
  и переживают перезапуск. YAML трогать не нужно.
* Диагностика: `GET /api/memory/state`, блок `memory` в `/healthz`.

## 🧠 Мозг включается здесь (этап 2 готов)

Мозг уже в сборке. Три способа поднять его:

1. **Демо без модели:** `python -m lilith_core.run --mock-brain` — ответы-заглушки,
   но со стримингом, метриками ток/сек и профилями. Панель показывает всё как с настоящей моделью.
2. **LM Studio / Ollama / llama.cpp:** подними сервер модели, впиши имя модели
   в `config.yaml` → `brain.profiles.chat.model`, поставь `features.brain_enabled: true`
   и перезапусти. Всё, эхо умерло — началась Лилит.
3. **Своя связка:** у каждого профиля свой `base_url` (разные порты разных серверов),
   свой ключ из `.env` (`api_key_env`), своя температура. Переключение на лету —
   селектором в панели или `data.profile` в WS-сообщении.

Что видно в панели после включения: частичные кадры стриминга («печатает…»),
в мете ответа — мс, ток/сек, число токенов и имя профиля; селектор профилей
в шапке показывает здоровье каждого сервера (`ok` / `offline` / `mock`).

Диагностика мозга: `GET /api/brain/profiles` (реестр + healthcheck, кэш 10 с),
`logs\lilith.log` (строки `BRAIN[профиль]: N токенов, X мс, Y ток/сек`).

## 🧠 Модели и профили (слой «мозг», этап 2)

Одна Лилит — **много моделей**. В конфиге живёт реестр именованных профилей:
профиль = конкретная модель на конкретном сервере (порт, имя, ключ, параметры).

```yaml
brain:
  default_profile: chat          # кем она думает по умолчанию
  defaults:                      # наследуется всеми профилями
    base_url: "http://127.0.0.1:1234/v1"
    temperature: 0.8
    max_tokens: 1024
  profiles:
    chat:                        # болтовня, голос, реакции: быстрая малышка
      note: "Qwen3.5-4B/9B (LM Studio), живёт постоянно"
      temperature: 0.9
    coder:                       # код и тяжёлые задачи
      base_url: "http://127.0.0.1:1235/v1"
      temperature: 0.2
      max_tokens: 4096
    vision:                      # скриншоты и OCR (инструмент этапа 6)
      base_url: "http://127.0.0.1:1236/v1"
    summarizer:                  # саммаризация памяти (этап 3)
      temperature: 0.3
    experiment:                  # СЛОТ ДЛЯ ЭКСПЕРИМЕНТОВ
      note: "сюда любую новую модель"
```

### Как добавить новую модель (без правки кода)

1. Докачали GGUF/модель и подняли её сервер (LM Studio / llama.cpp / Ollama).
2. Добавили профиль в `config.yaml` **или** просто переменными окружения:

```bat
set LILITH_BRAIN__PROFILES__EXPERIMENT__MODEL=qwen4-2b-whatever-Q4_K_M
set LILITH_BRAIN__PROFILES__EXPERIMENT__BASE_URL=http://127.0.0.1:1237/v1
```

3. Переключение: поменять `default_profile`, либо послать в WS-сообщении `chat`
   поле `data.profile` (`{"type":"chat","text":"...","data":{"profile":"coder"}}`).

Так переживёт и Qwen 4.x, и Gemma 5, и всё, что выдумают через пару лет:
новые модели = новые профили, код ядра не меняется.

### Куда какой сервер

| Сервер | Особенность | Как профилировать |
|---|---|---|
| LM Studio | один сервер, модель выбирается именем в запросе | разные `model` на одном `base_url` |
| llama.cpp server | один инстанс = одна модель = один порт | тяжёлой модели — свой порт/профиль |
| Ollama | много моделей на одном порту | разные `model` на одном `base_url` |
| Облако (OpenRouter и т.п.) | свой `base_url` + ключ из `.env` | профиль `coder` с `api_key_env` |

---

## 🩺 Диагностика

```bat
python -m lilith_core.run --show-config
```

Покажет: откуда взят конфиг, эффективные значения всех секций (ключи замаскированы),
путь к `persona.md` и существует ли он.

`GET /healthz` возвращает то же плюс аптайм, список включённых подсистем и статистику
WebSocket-подключений — удобно для автозапуска и мониторинга на этапе 8.

Логи: `logs/lilith.log`, ротация 10 МБ, хранение 14 дней, архивация в zip.
Весь стандартный `logging` (uvicorn, fastapi, asyncio) перехватывается и уходит в тот же файл.

### Частые проблемы

| Симптом | Причина / решение |
|---|---|
| `address already in use` | Порт занят. `--port 9000` или освободить 8765 |
| Панель «отключено», красная точка | Сервер не запущен или другой порт |
| `persona.md не найден` в логе | Запуск не из корня проекта; проверьте `app.persona_path` и `--show-config` |
| Изменения конфига не применяются | Сервер читает конфиг при старте — перезапустите (`--reload` для разработки) |
| `pip install -e .` ругается на кодировку | В Windows-консоли выполните `chcp 65001` |
| Куча строк `'...' is not recognized as an internal or external command` при запуске `start.bat` | Это архив **v0.1.0**: bat-файл был сохранён с LF-переводами строк и кириллицей в UTF-8, а `cmd.exe` такое не переваривает. Возьмите архив от **17.09.2026 (v0.1.1 и новее)** — там bat-файлы CRLF и чисто ASCII |
| Красная простыня в **PowerShell** при ручных командах (`Set-Location ... PositionalParameterNotFound`, `Не удалось загрузить модуль ".venv"`) | Команды инструкции были для **cmd**: в PowerShell нет `cd /d`, а `.[dev]` без кавычек съедается. В PS правильно: `cd "E:\Вокальная Лилит"` и `pip install -e ".[dev]"`. Но начиная с **v0.1.2** в консоль вообще можно не ходить: двойной клик по `start.bat` делает всё сам и открывает браузер |
| `ERROR: file:///C:/... does not appear to be a Python project` | `start.bat` запустили НЕ из папки проекта (например, из «Загрузок» или из окна архива). Распакуйте zip, войдите во внутреннюю папку проекта (где лежит `pyproject.toml`) и двойной клик по `start.bat` уже там. В v0.1.1 скрипт сам проверяет это и пишет понятную подсказку вместо каши |
| Консоль мгновенно закрывается после ошибки | В v0.1.0 так и было. В v0.1.1 каждая ветка ошибки заканчивается `pause` — окно остаётся открытым, пока не нажмёте клавишу |

---

## 📝 Стиль кода

- Python 3.11, тайпхинты везде, докстринги на русском.
- `loguru` для всех логов; `print` — только в отладочных скриптах.
- `from __future__ import annotations` в каждом модуле.
- Никаких секретов в коде и в YAML.
- Каждый этап сопровождается pytest-тестами на mock/заглушках: тяжёлые зависимости
  (модели, GPU, сеть) в тестах не появляются.

---

## 📜 Лицензия

MIT. Проект личный, собирается для одного конкретного Кирюши. 🦇
