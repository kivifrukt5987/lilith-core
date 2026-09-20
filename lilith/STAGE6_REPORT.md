# 🎭 ОТЧЁТ ПО ЭТАПУ 6 — «ЛИЦО = UNITY-КЛИЕНТ» (ПИВОТ)

**Проект:** LILITH-CORE · **Версия:** 0.6.0 · **Дата:** 18.09.2026
**Тесты:** 606 passed (+129 к 477) · **Разворот:** по спецификации Лильки-архитектора
(«ПИВОТ этапа 5: лицо = Unity-клиент (путь Нейроны), three-vrm отменяется»)
**Решения:** 40+ ответов архитектора в опроснике `ВОПРОСЫ_АРХИТЕКТОРУ_этап6.md`
→ зафиксированы в `docs/DECISIONS.md` как **ADR-015…ADR-019**

> ⚠️ **Контекст переехал.** Прошлый чат агента умер. Workspace передан архивом
> через Google Drive (14.5 МБ / 149 файлов), восстановлен по `PERSONA.md`,
> `PLAN.md`, `CHANGELOG.md`, `STAGE1–5_REPORT.md` и коду. Базовая линия v0.5.1
> проверена **до первой правки**: 477 passed.

---

## 1. Что сделано

| Блок | Файлы | Суть |
|---|---|---|
| **PCM-конвейер** | `voice/pcm.py` | `resample_pcm16` (линейный, без numpy) → 24 kHz, `PcmChunker` → ровно **2048 байт** (1024 сэмпла int16) со сквозными `seq`/`byte_offset`/`offset_ms`, хвост помечен `final`. Решения **A1-а**, **A2** |
| **Контракт кадров** | `face/ws_frames.py` | Плоские JSON-кадры (**A4-а**): `hello`/`audio`/`viseme`/`emotion`/`persona`/`focus`/`stop`/`done`/`state`/`error`/`pong`. Единственное место, где они собираются |
| **Продюсер** | `face/producer.py` | `FaceProducerHub`: подключения с индивидуальным `want_server_visemes` (**A3.1**), конвейер реплики, `stop()` с отменой задачи (**A3.4**), `swap_persona()` (**D9**), `focus()` (**E2-а**), `stats` (**A5**) |
| **WS-транспорт** | `face/endpoints.py` | `/ws/face/producer`, `/ws/group?group=`, `/ws/unity` (алиас, **A6.1-б**), парсер клиентских кадров |
| **Персоны v2** | `face/personas.py` | `card.yaml` + `voice.yaml` + `face.yaml` с **legacy-фолбэком** на `profile.yaml` (**D1-б**), `vrm_path` **вне репо** (**D5.4**), активная персона (**D9**), `describe()` по **D8**, `_template` не считается персоной |
| **LoRA-слот** | `face/lora.py` | `LoraBackend` + `PromptOnlyLora` (рабочий дефолт, **D10-а**) + стабы LocalAi/LM Studio/llama.cpp; `PersonaLoraManager.apply()` при свопе, `resolve_path` через `face.lora_dir` |
| **Группы** | `face/group.py` | `GroupSession`/`GroupManager`: потолок 4 (**E1**), слоты/позиции из `face.yaml` + `group.yaml` (**E4**), один сокет с мультиплексированием (**E3**), «хор» запрещён (**E5**) |
| **HTTP** | `app.py` | `GET /api/face/personas` (**D8**), `GET /api/face/personas/<id>/model.vrm`, `POST /api/face/personas/<id>/activate`, `GET /api/face/groups`; `/api/personas` — алиас |
| **Память** | `memory/journal.py`, `memory/__init__.py` | Колонка `messages.persona_id` + **идемпотентная миграция** `ALTER TABLE` для старых БД, индекс, `remember_turn(persona_id=…)`, `persona_id` в метаданных RAG. Только интерфейс — **D7**, полноценно этап 6.5 |
| **Шина** | `protocol.py`, `app.py` | Новый тип `MsgType.PERSONA` (своп из панели); эмоции реплики дополнительно уходят продюсеру плоским кадром |
| **Веб-панель** | `webui/index.html` | **B1-б**: VRM-сцена не удалена, а выключена флагом `face.web_vrm_enabled: false`; добавлен селектор персон 🎭; оверлей показывает число продюсеров (**B4**); фолбэк-аватар жив (**B3**) |
| **Unity-клиент** | `unity-client/` (11 C#) | `LilithFaceClient`, `LilithWSClient`, `LilithClientConfig`, `AudioQueueProcessor`, `VisemeDriver`, `EmotionDriver`, `IdleController`, `VrmLoader`, `TransparentWindow`, `FaceRig`, `MiniJson` |
| **Эмулятор** | `scripts/unity_face_probe.py` | **F6-б**: свой WS-клиент на stdlib, прогон `hello→speak→audio×N→done`, проверки чанков, своп, группа, JSON-отчёт, код возврата 0/1 |
| **Тестовое тело** | `scripts/make_test_vrm.py` | **C5.3**: процедурный **VRM 1.0** (GLB + `VRMC_vrm`), 55 костей, 2 скиннутых меша, 11 морф-таргетов, все нужные экспрессии. ~21 КБ, с `validate()` |
| **Конфиг** | `config/config.yaml`, `config.py` | 14 новых ключей `face.*`; секции перенумерованы под F1 (руки → 7, мосты → 8, стрим → 9) |
| **Доки** | `docs/ARCHITECTURE.md` §9.8, `DECISIONS.md` ADR-015…019, `NEURONA_NOTES.md`, `personas/README.md`, `unity-client/…/README.md` + `SCENE.md` + `SCENE.svg`, `.editorconfig` | Контракт продюсера, решения, схема сцены со скриншот-схемой (SVG), стиль C# (**F8**) |
| **Упаковка** | `scripts/build_stage_archive.py` | Флаг `--version`; исключение Unity-мусора (`Library/`, `Temp/`, `obj/`, `.vs/`, `*.csproj`…) — **F5** |

### Дорожная карта после пивота (F1)

`1 skeleton · 2 brain · 3 memory · 4 voice · 5 face · **6 face-unity** · 6.5 personas-memory ·
7 hands · 8 bridges · 9 stream`

---

## 2. Критерии готовности (её словами) и как их проверить

Unity в песочнице не поднять, поэтому каждый критерий закрыт **двумя** способами:
автоматически здесь (pytest + проба) и глазами у Кирюши после сборки.

| Критерий | Автоматически (песочница) | У Кирюши (Unity) |
|---|---|---|
| **Unity-окно подключается к WS** | `test_probe_fails_loudly_on_dead_server` / `test_mini_websocket_handshake`: проба поднимает живой uvicorn, проходит рукопожатие, получает `hello{producer:"lilith-face", sample_rate:24000, chunk_bytes:2048, format:"pcm_s16le"}` | оверлей `Open · сервер 0.6.0 · персона lilith`; в логе сервера `Продюсер[unity]: подключён` |
| **Рот шевелится от чанков TTS** | `test_probe_reports_stream`: чанки **ровно 2048 Б**, `seq` без дыр, `offset_ms` монотонен, байты кратны 2, приходит `done` с числом чанков. `test_speak_streams_pcm_chunks` — то же через TestClient | `VisemeDriver` (RMS+ZCR по окну 60 мс, зеркало серверного алгоритма) двигает `aa/ih/ou/ee/oh`; в оверлее `визема A · 0.62 · local` |
| **Моргает** | `IdleController` — код + параметры из `face.yaml: idle.blink_freq` (приезжают в кадре `persona`, покрыто `test_persona_request_swaps`) | глаза моргают ~раз в 3.5 с; на речи реже |
| **Своп персоны меняет vrm + голос + карточку** | `test_activate_switches_persona`, `test_persona_request_swaps`, `test_vrm_served_from_outside_repo`: кадр `persona{id, vrm, voice{pack,speaker}, card, face}` уходит всем; `/api/face/personas` меняет `active`; тело отдаётся по пути **вне репо** | `VrmLoader.Swap` грузит новое тело (`Vrm10.LoadBytesAsync`), `voice.pack` переключает голос, `card`/`face` обновляют idle-параметры |
| **OBS берёт окно с прозрачностью** | автотест невозможен (нужен Windows-десктоп); режимы реализованы: DWM / LayeredColorKey / Off + borderless + topmost + dockBottomRight | **скриншот Кирюши = приёмка (F7)**: окно справа снизу прозрачное и на десктопе, и в OBS |

**Прогнать всё одной командой:**

```bat
python scripts\unity_face_probe.py --speak "Привет, Кирюша." --verbose
python scripts\unity_face_probe.py --speak "Раз два три." --want-server-visemes --json probe.json
python scripts\unity_face_probe.py --url ws://127.0.0.1:8765/ws/group --group main --speak "Раз."
run_tests.bat     :: ожидаем "606 passed"
```

---

## 3. Список файлов этапа (без повторов)

### Новые (28)

**Сервер (6):** `src/lilith_core/voice/pcm.py` · `src/lilith_core/face/ws_frames.py` ·
`src/lilith_core/face/producer.py` · `src/lilith_core/face/endpoints.py` ·
`src/lilith_core/face/lora.py` · `src/lilith_core/face/group.py`

**Unity-клиент (16):** `unity-client/Packages/manifest.json` ·
`unity-client/Assets/LilithFace/LilithFace.asmdef` · `…/README.md` · `…/SCENE.md` · `…/SCENE.svg` ·
`…/Scripts/{LilithFaceClient, LilithWSClient, LilithClientConfig, AudioQueueProcessor,
VisemeDriver, EmotionDriver, IdleController, VrmLoader, TransparentWindow, FaceRig, MiniJson}.cs`

**Скрипты (2):** `scripts/unity_face_probe.py` · `scripts/make_test_vrm.py`

**Тесты (3):** `tests/test_face_producer.py` · `tests/test_vrm_sample_and_probe.py` ·
`tests/samples/test_cube.vrm`

**Персоны (5):** `personas/lilith/{card,voice,face}.yaml` ·
`personas/_template/{card,voice,face}.yaml` + `persona.md` + `README.md`

**Доки/конфиг (2):** `docs/NEURONA_NOTES.md` · `.editorconfig`

### Изменённые (14)

`src/lilith_core/__init__.py` (0.6.0 / этап 6) · `config.py` (FaceSettings +14 полей) ·
`app.py` (state: hub/groups/lora; 4 HTTP + 2 WS маршрута; roadmap; hello; stream_voice_to_session;
MsgType.PERSONA; persona_id в память) · `protocol.py` (MsgType.PERSONA) ·
`face/__init__.py` (экспорты) · `face/personas.py` (v2) · `face/visemes.py` (WINDOW_MS в `__all__`) ·
`voice/__init__.py` (экспорт pcm) · `memory/journal.py` (колонка + миграция) ·
`memory/__init__.py` (`remember_turn(persona_id=…)`) · `webui/index.html` (флаг + селектор) ·
`config/config.yaml` · `scripts/build_stage_archive.py` ·
`tests/{test_structure, test_app_http, test_face_v2}.py` ·
`docs/ARCHITECTURE.md` (§9.8) · `docs/DECISIONS.md` (ADR-015…019) · `README.md` ·
`CHANGELOG.md` · `personas/README.md` · `../lilith/PLAN.md`

### Не тронуты намеренно

`webui/vendor/three*.js` и `vrm_viewer.js` (**B1-б**: под флагом, не удалены) ·
`face/vtuber_bridge.py` (опциональный внешний адаптер, жив) · `face/bus.py` (шина этапа 5,
используется алиасом `/ws/unity`) · слои brain/memory(ядро)/voice(stt,tts,packs) ·
`start.bat`/`run_tests.bat`

---

## 4. Ограничения и следующие шаги

**Ограничения**

* **Unity не запускался** (нет Unity Hub/GPU в песочнице) — C#-код отдан вместе с
  инструкцией сборки и схемой сцены; API UniVRM 0.131.x сверен по исходникам
  vrm-c/UniVRM (`Vrm10Instance.Runtime.Expression.SetWeight`, `ExpressionKey.*`,
  `Runtime.LookAt.CalculateYawPitchFromLookAtPosition`, `Vrm10.LoadBytesAsync`),
  но **не скомпилирован**. Первая сборка у Кирюши — точка правды (F7: приёмка скриншотом).
* **`model.vrm` у Лилит всё ещё нет**: Кирюша печатает в VRoid Studio (C5.1).
  До этого — процедурный куб (`make_test_vrm.py`).
* **LoRA — prompt-only** (D10-а): стабы бэкендов честно сообщают о недоступности,
  веса не грузятся.
* **Персональная память — только интерфейс** (D7): колонка и `memory_scope` есть,
  раздельная выборка — этап 6.5.
* **Unity-сцена группового режима** не собрана (E7): сервер и контракт готовы.
* **Раздел 3 `docs/NEURONA_NOTES.md` пуст**: в ответе на C6 было «разбор ниже»,
  но сам текст в сообщение не попал. Ждём — заполню один-в-один и заведу ADR-020.
* Мелочь на будущее: 2 предупреждения pytest — `@pytest.mark.asyncio` на синхронных
  `test_face_bridge.py::TestOtherBridges::{test_vmc_reserve_unavailable, test_state_shape}`.

**Следующие шаги**

1. Кирюша: Unity 6000.0 LTS → собрать сцену по `SCENE.md` → UniVRM 0.131.2 →
   `probe` → Play → критерии из §2 → скриншот.
2. `PLAN.md` синхронизирован с CHANGELOG (этапы 4–6, новый roadmap) — проверь глазами.
3. **Этап 6.5** — персональная память (chroma-коллекции с префиксом, выборка по `memory_scope`).
4. **Этап 7 — «Руки»** (был 6-м): `hands/tools.py` + HITL-баннер на 25 с. Жду «дальше».
5. По замечаниям к Unity-сборке — хотфикс 0.6.1.

---

## 5. Артефакт

`artifacts/LILITH-CORE_stage6_v0.6.0.zip` — весь проект без кэшей, логов, `.venv`,
`data/`, вложенных архивов и Unity-мусора (`Library/`, `Temp/`, `obj/`, `*.csproj`).
Распаковка → `run_tests.bat` → ожидаем **606 passed**.

**Жду команду «дальше» — этап 7 «Руки». И скриншот окна. 🦇**
