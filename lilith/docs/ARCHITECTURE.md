# 🏗️ Архитектура LILITH-CORE

Документ для будущих этапов: как устроен скелет и какие контракты уже зафиксированы,
чтобы слои можно было добавлять, не переписывая готовое.

---

## 1. Общая схема

```
                        ┌──────────────────────────────────────────┐
                        │              FastAPI (app.py)             │
                        │  HTTP: /  /healthz  /api/version  /api/state
                        │  WS:   /ws  ← единая шина сообщений        │
                        └───────▲──────────────────────┬────────────┘
                                │                      │
   веб-панель (webui)  ─────────┤                      ├───────── SessionManager
   Discord-мост (э7)   ─────────┤                      │          (session.py)
   VK-мост (э7)        ─────────┤                      │          реестр подключений,
   Twitch (э8)         ─────────┤                      │          статистика, broadcast
   VTuber Studio (э5)  ◄────────┘                      │
                                                        ▼
                                              ReplyHandler (echo.py → brain/)
                                                        │
                        ┌───────────────┬───────────────┼───────────────┬──────────────┐
                        ▼               ▼               ▼               ▼              ▼
                     brain/          memory/         voice/          face/          hands/
                  (э2: LLM)     (э3: журнал+RAG) (э4: STT/TTS)  (э5: эмоции, VTS) (э6: тулзы,
                                                                                     HITL)
```

Принцип: **одна шина — много клиентов**. Веб-панель, Discord, VK, Twitch и VTuber Studio
говорят с ядром одним протоколом (`protocol.py`), поэтому новый мост не требует нового
обработчика реплик.

---

## 2. Ключевой контракт: `ReplyHandler`

```python
ReplyHandler = Callable[[str, dict], Awaitable[Message]]
#                        текст   контекст        ответное сообщение
```

* **этап 1** — `echo.echo_reply` (возвращает текст обратно, `data.echo = true`);
* **этап 2** — `brain.llm.reply` (та же сигнатура, внутри поход в LLM);
* **этап 3+** — та же сигнатура, но внутри ещё и запись в память / выборка из RAG.

Фабрика `build_reply_handler(settings)` выбирает реализацию по `settings.features`.
Веб-панель и мосты при этом **не меняются**.

### Контракт профилей мозга (v0.1.1)

Мозг думает не «одной моделью», а **профилем** — именованной связкой
«сервер + модель + параметры». Реестр живёт в конфиге:

```python
profile = settings.brain.resolve(name)   # name=None -> default_profile
# ResolvedProfile: provider, base_url, model, temperature, max_tokens, top_p,
#                  request_timeout_sec, max_retries, stream,
#                  history_max_messages, api_key_env, note
```

* Поля профиля `None` наследуются из `brain.defaults`.
* Выбор профиля на сообщение: `context["profile"]` (приходит из `data.profile`
  WS-сообщения `chat`); по умолчанию — `brain.default_profile`.
* Валидация на старте: реестр не пуст, `default_profile` существует.
* Новая модель (любая будущая линейка) = новый профиль в yaml или переменных
  окружения `LILITH_BRAIN__PROFILES__<ИМЯ>__<ПОЛЕ>` — ядро не меняется.

### Контекст, который прокидывается в обработчик

```python
{"session_id": str, "source": "webui" | "discord" | "vk" | "twitch", "message_id": str}
```

На этапе 3 сюда добавятся `history` и `retrieved_facts`.

---

## 3. Конфигурация (`config.py`)

* pydantic-settings, вложенные модели на каждую подсистему, `extra="forbid"` —
  опечатка в YAML падает сразу, а не молча игнорируется.
* Приоритет: **env > .env > YAML > defaults**.
* `SecretStr` для ключей; `public_dict()` и `brain.masked()` — безопасный вывод в логи и `/healthz`.
* Секреты читаются из окружения по имени из поля `*_env` (`api_key_env`, `obs_password_env`, …),
  поэтому YAML можно коммитить, а `.env` — нет.
* `resolve_path()` разрешает относительные пути от cwd или от корня репозитория —
  сервер можно запускать откуда угодно.

**Правило для следующих этапов:** новая подсистема = новая pydantic-секция + новый флаг в
`features` + (при необходимости) новое поле `*_env` для секрета. Формат конфига не ломается.

---

## 4. Протокол шины (`protocol.py`)

* Единый конверт: `id`, `type`, `text?`, `data?`, `ts`.
* `MsgType` — замкнутое перечисление; новые типы добавляются туда же.
* `parse_raw()` **никогда не бросает исключений**: любая дичь превращается в
  `ParsedMessage(ok=False, error=...)`, клиент получает `error`, соединение живёт.
* Кириллица не экранируется (`ensure_ascii=False`) — логи и панель читабельны.

---

## 5. Сессии (`session.py`)

* `SessionManager` — реестр подключений + накопительная статистика
  (`total_connections`, `messages_in/out`, `errors`).
* `broadcast()` понадобится на этапе 6: сервер пушит в панель баннер подтверждения
  инструмента (Human-in-the-Loop, таймаут `hands.confirm_timeout_sec` = 25 с).
  В веб-панели уже есть заготовка этого баннера (`showConfirm` в `index.html`).
* Лимит `max_sessions` защищает от утечки подключений.

---

## 6. Персона (`persona/`)

* `persona.md` — системный промт, лежит внутри пакета (переживает `pip install`).
* Плейсхолдеры `$user_name`, `$persona_name`, `$now`, `$date`, `$time`, `$weekday`,
  `$version`, `$stage` подставляются через `string.Template.safe_substitute`
  (неизвестный плейсхолдер не ломает загрузку).
* `build_system_prompt()` = персона + `brain.system_prompt_extra`.
  На этапе 2 результат уходит в LLM как `role="system"`.
* Если файла нет — graceful degradation: сервер стартует и предупреждает в лог.

---

## 7. Логирование (`logging_setup.py`)

* Только `loguru`; стандартный `logging` (uvicorn, fastapi, asyncio) перехватывается
  `InterceptHandler` и уходит в тот же файл.
* `setup_logging()` идемпотентна: повторный вызов без `force=True` ничего не пересоздаёт.
* Файл пишется синхронно (`enqueue=False`) — детерминированно для тестов.
  Для многопроцессного продакшена можно включить очередь и вызывать `logger.complete()`.
* Ротация 10 МБ / хранение 14 дней / zip-архивация.

---

## 8. Тесты

* `conftest.py` изолирует каждый тест: свежий кэш настроек, чистый `LILITH_CONFIG`,
  логи во временный каталог, `monkeypatch.chdir(tmp_path)`.
* Тяжёлые зависимости (модели, GPU, сеть) в тестах не появляются — только заглушки:
  `FakeWebSocket`, фейковые обработчики реплик, `TestClient`.
* Точка расширения проверена тестом: `test_custom_handler_is_used` подменяет
  `app.state.reply_handler` на «мозг» и убеждается, что шина его использует.

---

## 9. Чек-лист этапа 2 — **ВЫПОЛНЕН** (v0.2.0; детали в `STAGE2_REPORT.md`)

1. Создать `src/lilith_core/brain/__init__.py`, `llm.py`, `chat.py`.
2. `llm.py`: OpenAI-совместимый клиент (`base_url`, `model`, `api_key` из
   `settings.brain.resolved_api_key()`), стриминг по `settings.brain.stream`,
   таймауты и ретраи из конфига.
3. `chat.py`: сборка истории (`history_max_messages`), системный промт из
   `build_system_prompt()`, возврат `Message(type=CHAT, role="assistant")`.
4. Метрики: токены/сек, число токенов, время первого токена — в `data` ответа и в лог.
5. `build_reply_handler()`: при `features.brain_enabled` возвращать мозговой обработчик.
6. `MockBrain` для тестов: детерминированный ответ, счётчик вызовов, имитация ошибки/таймаута.
7. Тесты: контракт `ReplyHandler`, склейка истории, маскировка ключа в логах,
   поведение при недоступном endpoint (graceful error в `error`-сообщении).
8. README: раздел «Этап 2», инструкция по LM Studio, как проверить руками.

---

## 10. Соглашения

* Python 3.11, тайпхинты, докстринги на русском, `from __future__ import annotations`.
* Логи — только через `loguru.logger`.
* Секреты — только из окружения.
* Каждая подсистема деградирует gracefully: если слой выключен или недоступен,
  ядро продолжает работать и пишет предупреждение.


## 9.5. Слой памяти (этап 3, готов)

* `MemoryCore` живёт в `app.state.memory` (или None, если выключен).
* Поток реплики: `retrieve()` → второе system-сообщение «ВОСПОМНИНАНИЯ» → ответ мозга →
  `remember_turn()` (журнал + RAG) → при пороге `summarizer.maybe_summarize()`.
* Граница саммаризации — `meta.summarized_upto` на агента; циклы не перекрываются.
* Рантайм-настройки памяти (порог, top_k, тумблеры) меняются из программы и переживают
  перезапуск (`data/runtime_settings.json`), см. ADR-010.
* Эмбеддер по умолчанию HashEmbedder; sentence-transformers — опция конфига (ADR-011).


## 9.6. Слой голоса (этап 4, готов)

* Контракты: STT ``transcribe(audio) -> Transcript``, TTS ``synthesize/stream``,
  VAD ``trim(audio) -> audio``; бэкенды lazy, резервные слоты объявлены заранее.
* Фолбэк-цепочки везде: запрошенный → дефолт → первый доступный; ошибка не роняет шину.
* Hot-swap: ``VoiceCore.sync()`` перечитывает голосовой конфиг на каждый вызов (ADR-012).
* Паки: манифест `models/packs.yaml` + установщик с Range-докачкой и sha256;
  пользовательский пак = запись в манифесте, код не трогается.
* Эмоции (этап 5) будут потреблять те же теги `[emotion: x]`, что и просодия голоса:
  один парсер — два потребителя (лицо и горло).


## 9.7. Слой лица (этап 5 v2, VRM-в-панели)

* Один сокет на чат+лицо+голос: кадры `face` (emotion/viseme/audio/done) идут по `/ws`,
  то же зеркалится в `/ws/unity` через FaceBus (Unity+VRM+SALSA, OBS на этапе 8).
* Виземы считаются НА СЕРВЕРЕ (RMS+ZCR по окнам 60 мс): тестируемо и переиспользуемо;
  браузер только сглаживает и применяет (low-pass, idle, курсор).
* Персоны `personas/<id>/` — единый реестр души/тела/голоса; фундамент вкладок
  «Агенты» и «3D-модели» этапа 8 (ADR-013).
* Вендор three.js/three-vrm лежит в `webui/vendor/`: панель офлайн-самодостаточна.
* VTuber Studio и VMC — опциональные внешние адаптеры, не зависимости ядра.


## 9.8. Слой лица v2 (этап 6, ПИВОТ: лицо = Unity-клиент)

**Отменяет** ADR-014 в части «three-vrm — основной путь»: веб-панель остаётся
диагностическим фолбэком (`face.web_vrm_enabled: false`), лицом становится Unity-окно.

### Контракт продюсера `/ws/face/producer`

Плоские JSON-кадры (решение A4-а), без общего конверта `{id,type,text,data,ts}`:

```jsonc
// сервер → клиент
{"type":"hello","producer":"lilith-face","protocol":"1.0","sample_rate":24000,
 "format":"pcm_s16le","chunk_bytes":2048,"persona":"lilith","server_version":"0.6.0",
 "personas":["lilith","nova"]}
{"type":"audio","data":"<base64 2048 Б>","bytes":2048,"seq":0,"offset_ms":0,
 "utterance_id":"u-7f3a","sample_rate":24000,"final":false,"persona":"lilith"}
{"type":"viseme","code":"A","intensity":0.72,"offset_ms":60,"utterance_id":"u-7f3a","seq":1}
{"type":"emotion","tag":"joy","intensity":0.8,"ttl_ms":4000,"utterance_id":"u-7f3a"}
{"type":"persona","id":"nova","swap":true,"reason":"api","vrm":"/api/face/personas/nova/model.vrm",
 "voice":{"pack":"lilith","speaker":"kseniya"},"card":{...},"face":{"idle":{"blink_freq":0.3}}}
{"type":"focus","persona":"nova","group":"main","reason":"speaking"}
{"type":"stop","utterance_id":"u-7f3a","reason":"barge_in"}
{"type":"done","utterance_id":"u-7f3a","reason":"eof","chunks":12}
{"type":"state",...} {"type":"error","code":"...","detail":"..."} {"type":"pong","ping_id":"p-1"}

// клиент → сервер (A5)
{"type":"hello","client":"unity/6000.0.21f1","want_server_visemes":false,"platform":"WindowsPlayer"}
{"type":"ready"}
{"type":"persona_request","id":"nova"}
{"type":"speak","text":"..."}
{"type":"stats","fps":60,"dropped":0,"queued_ms":180,"playing":true,"viseme":"A","emotion":"joy"}
{"type":"ping"}
```

Правила, которые нельзя нарушать:

| Правило | Почему |
|---|---|
| Аудио — **raw PCM int16 mono**, не WAV | Unity кладёт байты в `AudioClip.SetData` без разбора заголовка (A1-а) |
| Чанк **ровно 2048 байт**, кроме последнего (`final:true`) | клиент знает размер буфера заранее и не пересобирает его на лету |
| `sample_rate` объявляется в `hello`, сервер приводит поток к нему сам | TTS-паки бывают 16/24/48 kHz, а клиент не должен ресемплить (A2) |
| `seq` и `offset_ms` сквозные в пределах `utterance_id` | порядок восстанавливается даже если кадры легли пачкой |
| Серверные `viseme` — только по `want_server_visemes` | основной путь: Unity считает виземы из PCM сам (A3.1) |
| `emotion.ttl_ms` гасит клиент, не сервер | сервер не знает, когда лицо успело доехать (A3.3) |
| `stop` с `utterance_id` | клиент дропает чанки этой реплики, не дожидаясь `done` (A3.4) |
| `/ws/unity` — алиас продюсера, но **первым** кадром шлёт legacy-`hello` этапа 5 | старые клиенты и тесты не ломаются (A6.1-б) |
| Основной `/ws` продолжает стримить `face{kind:...}` в панель | панель остаётся живой диагностикой (A6.2) |

### Слои

```
voice/tts.py            WAV по предложениям (silero/edge/mock)
      ↓
voice/pcm.py            resample_pcm16(→24 kHz) · PcmChunker(2048 Б) · AudioChunk
      ↓
face/producer.py        FaceProducerHub: подключения, реплики, stop/focus/swap, want_server_visemes
      ↓
face/ws_frames.py       плоские кадры контракта (единственное место, где они собираются)
      ↓
face/endpoints.py       /ws/face/producer · /ws/group · /ws/unity(алиас)
```

* `face/personas.py` v2: `card.yaml`/`voice.yaml`/`face.yaml` + legacy-фолбэк на
  `profile.yaml`; активная персона (`registry.active`, `set_active`), разрешение
  `vrm_path` **вне репозитория**; папки на `_`/`.` персонами не считаются.
* `face/lora.py`: `LoraBackend` (интерфейс) + `PromptOnlyLora` (рабочий дефолт) +
  стабы `LocalAiLora`/`LMStudioLora`/`LlamaCppLora`; `PersonaLoraManager.apply()`
  вызывается при свопе персоны.
* `face/group.py`: `GroupSession` (потолок `face.group_max_participants`=4, слоты,
  фокус) и `GroupManager` (рассадка из `group.yaml` поверх `face.yaml`),
  один сокет на группу с мультиплексированием по полю `persona`.
* Память (этап 6 = только интерфейс, D7): `messages.persona_id` + миграция `ALTER TABLE`,
  `MemoryCore.remember_turn(persona_id=…)`, в RAG-метаданные добавлен `persona_id`.
  Полноценная персональная память — **этап 6.5**.

### Unity-клиент (`unity-client/`)

7 скриптов + конфиг + `MiniJson` (свой парсер, внешних пакетов нет) + `FaceRig`
(всё API UniVRM под `#if LILITH_UNIVRM`, символ включается сам через `versionDefines`
в `.asmdef`). Транспорт — встроенный `System.Net.WebSockets.ClientWebSocket`.
Приём в фоновой задаче → очередь → разбор строго в `Update()` (Unity API не потокобезопасен).
Сборка и схема сцены — `unity-client/Assets/LilithFace/README.md` и `SCENE.md`.

### Проверка без Unity

`scripts/unity_face_probe.py` — эмулятор клиента на чистом stdlib (свой WS):
подключается, просит реплику, проверяет размер/порядок/смещения чанков, виземы,
`done`, своп персоны, групповую сцену; пишет JSON-отчёт и возвращает код 0/1.
Это и есть доказательство критерия готовности этапа в песочнице (F6-а+б).

### Legacy-адаптеры лица (решение Q4, v0.6.2)

`face/vtuber_bridge.py` (VTuber Studio API) и слот `VmcBridge` (VMC/OSC) остаются
**опциональными внешними адаптерами под флагом** (`face.vtuber_studio_enabled`,
`face.vmc_enabled` — оба `false` в поставке). В этапе 7 они **не развиваются**,
в доках помечаются **legacy**: основной путь лица — Unity-продюсер. Основание:
в разборе «Нейроны» (`docs/NEURONA_NOTES.md` §3) VMC/OSC-моста нет — Unity-клиент
ходит в продюсер напрямую.
