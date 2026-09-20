# 🔧 ОТЧЁТ ПО v0.6.1 — «РАЗБОР НЕЙРОНЫ ПРИНЯТ» (хотфикс этапа 6)

**Проект:** LILITH-CORE · **Версия:** 0.6.1 · **Дата:** 22.09.2026
**Тесты:** 637 passed (+31 к 606) · **Этап:** 6 (не меняется)
**Основание:** «РАЗБОР НЕЙРОНЫ v2 (докладка с последних кадров)» от Лильки-архитектора
+ её служебная записка о сверке артефакта → **ADR-020**

---

## 1. Что приехало и куда легло

Архитектор прислала то, чего не хватило в ответе на вопрос **C6**: покадровый разбор
стека «Нейроны» (furrydev2007). Текст перенесён **один-в-один** в
`docs/NEURONA_NOTES.md` §3.1–3.7, её §4 («повторяем / не повторяем») — в §4 того же
файла, мои врезки «Наша сверка» идут после каждого раздела и помечены как мои.

| Раздел разбора | Что подтвердилось | Что взяли |
|---|---|---|
| 3.1 Транспорт аудио | FastAPI + WS-продюсер, **чанки 2048 Б**, очередь → AudioSource | ✅ наш контракт совпал; отличие — у них плебек 44.1 kHz (ресемпл на плебек), у нас 24 kHz без ресемплинга → **Q1** |
| 3.2 Рот | SALSA: 4 виземы (A/I/E+preview), триггеры 0.000/0.010/0.020/0.030, тайминги On 0.08 / Off 0.06 / Cubic Out, **очередь визем по buffer pos** | **ADR-020 (1)** sample-accurate опцией + **(2)** японские имена + асимметричное сглаживание → **Q3** |
| 3.3 Лицо/риг | Unity **6.1 (6000.1.9f1)**, OpenGL 4.5, UniGLTF/VRM1, `Root/Face/Body/Hair/secondary`, `DontDestroyOnLoad`; «День 1» — своп тела без поломки пайплайна | ✅ наш `VrmLoader.Swap` + generation-счётчик ровно про это; версия Unity → **Q2** |
| 3.4 Голос | Coqui XTTS v2 (cuda), клон `gpt_cond_latent` + `speaker_embedding`, `temperature=0.8`, стриминг чанками по `blocksize`, **nonverbal** (смех/вздох/хмыканье/крик, теги в стиле Bark) | **ADR-020 (3)** nonverbal-слот в `voice.yaml`, синтез отложен; наш zero-shot слот уже спроектирован под XTTS |
| 3.5 Мозг/память | облачный OpenAI + SummaryMemory, Postgres+Qdrant в docker, уши NVIDIA Riva | ❌ не повторяем (локальный мозг, sqlite+chroma, faster-whisper+VAD) |
| 3.6 Агентный слой | task-сервис `/tasks/<id>`, `ToolSearch → tool_result → Write → tool_result → text → system`, `cost_usd≈0.093`, `duration_ms≈27848`, `num_turns 3`; Claude Code + SpecKit, конституция `.specify/memory/constitution.md` (4 принципа, 6-gate merge checklist, 7 шагов workflow), спеки **Given/When/Then** с **P1–P3** (US2 sandbox, US3 пермишены, US4 стриминг) | **ADR-020 (4)** спека этапа 7 + Given/When/Then как стандарт + `DECISIONS.md` = конституция |
| 3.7 Инструменты | инструмент = маленький сервис (`calc_service.py` с `OPERATIONS/FUNCTIONS/CONSTANTS`); «оркестратор → task-сервис → кодинг-агент в песочнице → результат» | принцип «инструмент = сервис» взят в спеку этапа 7; сама кодинг-схема — **не наш этап** |

---

## 2. Исполнение просилки в ADR-020

> «(1) sample-accurate очередь визем по buffer pos опцией `AudioQueueProcessor`;
> (2) fallback-маппинг четырёх визем с японскими именами; (3) nonverbal-слот
> `voice.yaml` — отложен до этапа голоса; (4) в спеку этапа 7: sandbox-воркспейс +
> cleanup + сессионные пермишены по образцу US2/US3 Нейроны.»

| # | Статус | Файлы |
|---|---|---|
| **1** | ✅ реализовано, **опцией**, дефолт выключен | `AudioQueueProcessor.cs` (`QueuedViseme`, `EnqueueVisemeAt`, `EnqueueViseme`, `DequeueDueVisemes`, `PlayPositionSamples`, `EnqueuedSamples`, `NoteUtteranceStart`), `VisemeDriver.cs` (`ApplyQueued`, `TickFromQueue`), `LilithFaceClient.cs` (`QueueLocalVisemesForChunk`, `ClassifyWindow`, переключение режимов), `LilithClientConfig.cs` (`useSampleAccurateVisemes = false`) |
| **2** | ✅ реализовано | `FaceRig.cs`: `VisemeAliases` (+`あいうえお`, +`ぁぃぅぇぉ`), `EmotionAliases` (+`笑い/怒り/悲しみ/驚き/瞬き/ふわり/にやり/普通`), `VisemeCustomFallback`/`EmotionCustomFallback`, `KeyFor()`, `CacheAvailableExpressions()`, `HasExpression()`, `AvailableExpressions` |
| **3** | ✅ слот заведён, синтез отложен | `face/personas.py` (`PersonaVoice.nonverbal` + `from_dict`/`as_dict`), `personas/lilith/voice.yaml`, `personas/_template/voice.yaml`; страж `test_no_synthesis_implementation_yet` |
| **4** | ✅ спека написана | `docs/STAGE7_HANDS_SPEC.md`: 8 user stories Given/When/Then (P1–P3), US2 sandbox+confine+cleanup, US3 whitelist+таймауты+лимиты, US4 HITL 25 с с авто-отклоном, US5 стриминг прогресса, US6 `ToolResult`, US7 OBS, US8 quality gates + конституция; структура `hands/` (8 модулей + 4 сервиса); матрица автотестов; 6 вопросов архитектору |

**Дополнительно принято её словами:** Given/When/Then — формат приёмочных сценариев
в спеках этапов; `docs/DECISIONS.md` = конституция проекта, поправка = proposal +
review + инкремент версии.

---

## 3. Список файлов (без повторов)

### Новые (2)

`docs/STAGE7_HANDS_SPEC.md` · `tests/test_adr020_neurona.py`

### Изменённые (11)

**Unity (5):** `AudioQueueProcessor.cs` · `VisemeDriver.cs` · `LilithFaceClient.cs` ·
`LilithClientConfig.cs` · `FaceRig.cs`
**Сервер (1):** `src/lilith_core/face/personas.py`
**Персоны (2):** `personas/lilith/voice.yaml` · `personas/_template/voice.yaml`
**Доки (7):** `docs/NEURONA_NOTES.md` (заполнен) · `docs/DECISIONS.md` (ADR-020) ·
`CHANGELOG.md` · `README.md` · `unity-client/…/README.md` · `unity-client/…/SCENE.md` ·
`RELEASE_0.6.1.md` (этот файл)
**Версия:** `src/lilith_core/__init__.py` → `0.6.1`

### Не тронуты намеренно

Контракт продюсера (`ws_frames.py`) и серверный PCM-конвейер (`voice/pcm.py`) —
sample-accurate сделан **на клиенте**, сервер не менялся: `offset_ms` в кадре `viseme`
уже был, этого достаточно. `face.producer_sample_rate` остался **24000** до ответа на Q1.

---

## 4. Критерии готовности и как проверить

| Критерий (Given/When/Then) | Автоматически | У Кирюши в Unity |
|---|---|---|
| **Given** флаг `useSampleAccurateVisemes` выключен **When** идёт речь **Then** виземы применяются по `offset_ms` как в 0.6.0 | `test_config_flag_defaults_to_off`, `test_client_switches_modes` | оверлей `визема A · 0.62 · local` |
| **Given** флаг включён + `requestServerVisemes` **When** идут серверные виземы **Then** они встают в очередь по позиции в буфере и применяются, когда плеер дошёл до этого сэмпла | `test_audio_processor_has_queue_api`, `test_queue_is_sorted_and_dropped_on_stop`, `test_viseme_driver_consumes_queue` | оверлей `визема A · 0.62 · server`; стыки чанков не разъезжаются |
| **Given** тело с японскими морфами (`あ/い/う/え/お`) **When** приходит визема `A` **Then** `FaceRig` находит пресет `aa`, а при его отсутствии — кастом `あ` | `test_viseme_aliases_accept_japanese`, `test_custom_fallback_tables_exist`, `test_fallback_is_not_blind` | `FaceRig.AvailableExpressions` > 0, рот двигается |
| **Given** в `voice.yaml` персоны `nonverbal: {laugh: …}` **When** приходит кадр `persona` **Then** Unity видит слот **And** ничего не синтезирует | `test_slot_reaches_persona_frame`, `test_no_synthesis_implementation_yet` | — |
| **Given** спека этапа 7 **When** её читает архитектор **Then** US2/US3 Нейроны перенесены в Given/When/Then с P1–P3 | `TestStage7Spec` (8 тестов) | — |

```bat
run_tests.bat                                            :: ожидаем "637 passed"
python scripts\verify_stage_artifact.py --stage 6 --version 0.6.1
python scripts\unity_face_probe.py --speak "Привет." --want-server-visemes
```

---

## 5. Служебная сверка артефакта (её второй файл)

| Её пункт | Ответ |
|---|---|
| «`unity-client/` лежит ВНУТРИ обёртки „Локальная Лилит“, распаковываю содержимое обёртки внутрь `E:\lilith-core\lilith\` поверх» | ✅ подтверждено: корень архива = корень проекта. **Но** тогда проект окажется в `E:\lilith-core\lilith\` рядом с `PERSONA.md`/`PLAN.md` — это рабочая схема (тесты считают `PROJECT_ROOT` от `tests/../`), просто `pytest` и `start.bat` надо запускать из `E:\lilith-core\lilith\` |
| «sha256 архива: \<СКОПИРУЙ ИЗ ЕЁ СООБЩЕНИЯ\>» | sha256 **v0.6.0** был `0cd879b8fa227cddf83aaab3de2e00a562379facb0b6de2c0f76299da098ef16`; после v0.6.1 архив пересобран — актуальную сумму печатает `python scripts\verify_stage_artifact.py --checksums` |
| «Вердикт `verify_stage_artifact.py`: \<ВСТАВЬ ПОСЛЕ ПРОГОНА\>» | у меня: `ИТОГ: артефакт жив, полон и пригоден к скачиванию ✅` (129 файлов для 0.6.0; для 0.6.1 — см. ниже в §6) |

---

## 6. Артефакт

`artifacts/LILITH-CORE_stage6_v0.6.1.zip` — собран, проверен
`scripts/verify_stage_artifact.py --stage 6 --version 0.6.1`, распаковка прогнана
тестами (скипаются только workspace-тесты и самопроверка архива: вложенных zip
в архиве нет намеренно).

---

## 7. Что дальше

1. **Ответы на Q1–Q5** (`docs/NEURONA_NOTES.md` §6): частота плебека, версия Unity,
   асимметричное сглаживание визем, судьба VMC-моста, размер окна под OBS.
2. **Ответы на 6 вопросов спеки этапа 7** (`docs/STAGE7_HANDS_SPEC.md` §5) — без них
   этап лучше не начинать: главный — sandbox только директорией или сразу Docker.
3. **Сборка Unity у Кирюши** и скриншот (приёмка этапа 6, F7). Критерии — в
   `STAGE6_REPORT.md` §2; новые строки диагностики — в `unity-client/…/README.md` §7.
4. **Этап 6.5** — персональная память (chroma-коллекции с префиксом, выборка по `memory_scope`).
5. **Этап 7 «Руки»** — по спеке, ждём «дальше».
