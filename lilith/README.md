# 🦇 LILITH-CORE

Домашний Python-стек ИИ-компаньона **Лилит**: оркестратор, память, уши, горло, лицо, руки и мосты.
Всё живёт локально на машине Кирюши, мозг — любой OpenAI-совместимый endpoint (LM Studio, llama.cpp,
vLLM, Ollama, OpenRouter).

**Текущий статус: ЭТАП 1 — «Скелет»** (конфиг, логи, FastAPI + WebSocket-эхо, мини веб-панель).

| | |
|---|---|
| Версия | `0.5.0` (этап 5: лицо — VRM-вьювер в панели, виземы, эмоции, персоны) |
| Этап | 5 из 8 (`face`) — лицо в панели, ждём «дальше» для этапа 6 |
| Python | 3.11+ |
| Тесты | **477 passed** (pytest) |
| Целевая машина | Windows 11, RTX 3060 12GB, i7-12700KF, 32GB RAM |

---

## 🗺️ Дорожная карта

| Этап | Слой | Что делаем | Статус |
|------|------|-----------|--------|
| **1** | **Скелет** | конфиг (YAML + `.env`, pydantic-settings), loguru, FastAPI + uvicorn, WebSocket `/ws` эхо, мини веб-панель | ✅ **готово** |
| 2 | Мозг | `brain/llm.py` — OpenAI-совместимый клиент, чат-цикл с `persona.md`, логирование ток/сек, mock в тестах | ⏳ ждёт команды «дальше» |
| 3 | Память | aiosqlite-журнал (сообщения, факты), chromadb-RAG, саммаризатор каждые N сообщений | ⏳ |
| 4 | Уши и горло | `voice/stt.py` (faster-whisper + silero-vad + push-to-talk), `voice/tts.py` (silero-tts, фолбэк edge-tts) | ⏳ |
| 5 | Лицо | `face/emotions.py` (парсер `[emotion: X]`), `face/vtuber_bridge.py` (WS к VTuber Studio, VMC, реконнект) | ⏳ |
| 6 | Руки | `hands/tools.py` (psutil-виталс, playwright open_url, obs-websocket) + баннер подтверждения на 25 с (Human-in-the-Loop) | ⏳ |
| 7 | Мосты | `bridges/discord_bot.py` (discord.py), `bridges/vk_bot.py` (vkbottle) — один мозг, общая память | ⏳ |
| 8 | Стрим и сервис | twitchio-триггеры, сцены OBS, docker-compose (searxng, redis), автозапуск `.bat`, бэкап репо | ⏳ |

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
477 passed
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
│   └── webui/
│       └── index.html         # мини веб-панель (один файл, без CDN)
├── tests/                     # 477 тестов
├── scripts/
│   ├── ws_client.py           # ручная проверка шины из консоли (-i = диалог)
│   └── build_stage_archive.py # сборка zip-архива этапа в artifacts/
├── logs/                      # lilith.log (создаётся автоматически)
├── artifacts/                 # zip-архивы этапов
├── docs/ARCHITECTURE.md       # архитектура и контракты между этапами
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
├── brain/     # этап 2: llm.py, chat.py, tokenizer_stats.py
├── memory/    # этап 3: journal.py, rag.py, summarizer.py
├── voice/     # этап 4: stt.py, vad.py, tts.py, hotkey.py
├── face/      # этап 5: emotions.py, vtuber_bridge.py, vmc.py
├── hands/     # этап 6: tools.py, registry.py, confirm.py
├── bridges/   # этап 7: discord_bot.py, vk_bot.py, base.py
└── stream/    # этап 8: twitch.py, obs.py, service.py
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

## 😊 Лицо: VRM в панели, виземы, персоны (этап 5 готов)

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
