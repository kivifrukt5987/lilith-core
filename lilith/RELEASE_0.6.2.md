# 🔧 ОТЧЁТ ПО v0.6.2 — «ПРИЁМКА НА WINDOWS + ОТВЕТЫ Q1–Q5»

**Проект:** LILITH-CORE · **Версия:** 0.6.2 · **Дата:** 22.09.2026
**Тесты:** **665 passed, 0 warnings** (+27 к 637) · **Этап:** 6
**Основание:** локальная приёмка Кирюши (Windows, Python 3.13.9, venv: 601 passed /
**3 failed** / 2 skipped / 4 warnings) + ответы Лильки-архитектора на Q1–Q5 и на главный
вопрос спеки этапа 7 → **ADR-021**

---

## 1. Три красных приёмки — закрыты все

### №1 `test_brain_llm.py::TestErrors::test_connection_refused`

**Диагноз её:** на Windows/py3.13 подключение к `127.0.0.1:1` **таймаутится**, а не
отказывается → всплывал `BrainTimeoutError` вместо `BrainConnectionError`.

**Взят её первый вариант (маппинг), а не платформенный скип** — он лечит продукт,
а не тест. В `httpx` класс `ConnectTimeout` наследует **и** `TimeoutException`,
**и** `ConnectError`, поэтому раньше его перехватывала первая же ветка `TimeoutException`.

```python
except httpx.ConnectTimeout as exc:      # ← НОВАЯ ветка, ДО общего таймаута
    raise self._wrap_connection_error(profile, exc) from exc
except httpx.TimeoutException as exc:
    raise BrainTimeoutError(...)         # таймаут ОТВЕТА: сервер поднялся, но молчит
except httpx.ConnectError as exc:
    raise self._wrap_connection_error(profile, exc) from exc
```

Для пользователя это разные беды: «сервер не поднят» и «сервер думает дольше N секунд»
лечатся по-разному. Та же ветка добавлена в `healthcheck()` с текстом
«не достучался за N c».

Сам тест переведён на `httpx.MockTransport(ConnectError)` — **детерминированно на
любой ОС**, реального похода в `127.0.0.1:1` больше нет. Добавлены:
`test_connect_timeout_maps_to_connection_error` (регрессия), а `test_timeout`
переведён на `ReadTimeout` (проверяет именно таймаут ответа). Страж
`test_no_test_hits_real_loopback_port` следит, чтобы реальный loopback в тесты не вернулся.

### №2 `test_structure.py::TestDocsHygiene::test_sync_script_is_surgical`

**Диагноз её:** в гибридной папке (workspace + распакованный бандл) лежит **старый**
`sync_test_count.py` без маркера.

**Гвард:** skip, если файла нет **или** в нём нет ни `PLAN_MARKER`, ни строки
«ТЕСТЫ СЕЙЧАС» (то есть копия старше 0.6.1). Классовый `pytest.mark.skipif` убран —
он вычислялся не так, как нужно для гибридной папки; теперь проверка внутри теста.

### №3 `test_structure.py::TestDocsHygiene::test_plan_has_marker_line`

**Гвард:** skip, если `lilith/PLAN.md` отсутствует в корне (в архив этапа он не входит).

### Её два skipped — подтверждение

Самопроверка архива (`test_verify_artifact_passes_on_current_zip`) и workspace-тесты
скипаются при прогоне из распаковки — **это правильное поведение**, вложенных zip
в архиве нет намеренно.

---

## 2. Косметика warnings — закрыта по её списку

| Warning | Лечение |
|---|---|
| `SyntaxWarning: invalid escape sequence '\S'` (`test_structure.py:322`) | строка `set "PYTHON=.venv\Scripts\python.exe"` стала **raw** (`r'...'`); страж `test_raw_string_for_windows_path` |
| 2 × `PytestWarning: marked with '@pytest.mark.asyncio' but it is not an async function` | марк был **не явный** — его вешал `asyncio_mode = "auto"` на весь класс `TestOtherBridges`. Синхронные `test_vmc_reserve_unavailable` и `test_state_shape` вынесены в отдельный **`TestSyncBridges`** с докстрингом-объяснением |
| `StarletteDeprecationWarning: Using httpx with starlette.testclient` | отфильтрован в `pyproject.toml` с комментарием: обвязка starlette, `httpx` у нас осознанная dev-зависимость |

Прогон с `-W error::SyntaxWarning`: **665 passed, 0 warnings**.

---

## 3. Ответы Q1–Q5 → ADR-021

| # | Решение архитектора | Что сделано в 0.6.2 |
|---|---|---|
| **Q1** | 24 kHz **нативно**, `AudioClip` с `frequency 24000`, без ресемпла; sample-accurate считает сэмплы в частоте клипа | ✅ уже так и было: `face.producer_sample_rate: 24000`, `_sampleRate` из `hello`, `PlayPositionSamples`/`EnqueuedSamples` в сэмплах клипа. **Код не менялся** |
| **Q2** | **6000.0.x LTS**, у Кирюши **6000.0.84f1**; за 6.1 не гонимся | версия зафиксирована в `unity-client/…/README.md`; API 6.1 в клиенте не используется |
| **Q3** | **Да**: On 0.08 / Off 0.06 / Cubic Out дефолтом для локальных визем; серверная очередь под флагом остаётся sample-accurate | `LilithClientConfig.visemeOnSeconds=0.08` / `visemeOffSeconds=0.06` / `visemeCubicOut=true`; `VisemeDriver.OpenSeconds/CloseSeconds/CubicOut` + `Approach()` (выбирает On/Off по направлению) + `EvaluateCubicOut()` (**static** — проверяется без сцены, задел под F6-в); прежний `visemeSmoothing` остался фолбэком при On/Off = 0 |
| **Q4** | VMC/VTuber Studio — опциональный внешний адаптер под флагом, в этапе 7 не развивать, в доках **legacy** | `vtuber_bridge.py` помечен `LEGACY` в докстринге модуля; `ARCHITECTURE.md` §9.8 → раздел «Legacy-адаптеры лица»; оба флага в поставке `false`; тесты `TestVmcMarkedLegacy` |
| **Q5** | **512×640** (портрет 4:5), borderless + topmost + dockBottomRight; размеры из `face.yaml (window.width/height)`; приёмка — скриншот F7 | `WindowSpec` в `face/personas.py` (дефолт 512×640, мусор → дефолт, минимум 64 px), `face.yaml` персоны и `_template`, доезжает в `persona.face.window`; `LilithFaceClient.ApplyPersonaWindow()` → `TransparentWindow.ApplyWindowSize()` (реальный `SetWindowPos` с перестановкой в правый нижний угол); `LilithClientConfig.windowSize = (512, 640)` + `usePersonaWindowSize` |

**Этап 7, главный вопрос — решён заранее:** sandbox **ТОЛЬКО директорией**
(dir + confine + cleanup), Docker — опциональный бэкенд позже («Кирюша докер выселил
из логова, кхххх»). Внесено в `docs/STAGE7_HANDS_SPEC.md` §5 Q1 и в US2.

---

## 4. Список файлов (без повторов)

### Новые (2)

`tests/test_hotfix_062.py` (26 тестов) · `RELEASE_0.6.2.md` (этот файл)

### Изменённые (14)

**Сервер (3):** `src/lilith_core/brain/llm.py` (ветка `ConnectTimeout` в `complete`
и `healthcheck`) · `src/lilith_core/face/personas.py` (`WindowSpec`, `PersonaFace.window`,
`as_dict`) · `src/lilith_core/face/vtuber_bridge.py` (LEGACY в докстринге)

**Unity (4):** `LilithClientConfig.cs` (On/Off/CubicOut, `windowSize 512×640`,
`usePersonaWindowSize`) · `VisemeDriver.cs` (`OpenSeconds/CloseSeconds/CubicOut`,
`Approach`, `EvaluateCubicOut`) · `LilithFaceClient.cs` (проброс параметров,
`ApplyPersonaWindow`) · `TransparentWindow.cs` (`ApplyWindowSize`)

**Тесты (4):** `tests/test_brain_llm.py` (MockTransport + 2 новых) ·
`tests/test_structure.py` (гварды, raw-строка) · `tests/test_face_bridge.py`
(`TestSyncBridges`) · `tests/test_hotfix_062.py`

**Персоны (2):** `personas/lilith/face.yaml` · `personas/_template/face.yaml` (блок `window`)

**Доки/конфиг (7):** `pyproject.toml` (filterwarnings) · `docs/DECISIONS.md` (ADR-021) ·
`docs/NEURONA_NOTES.md` (§6 → таблица ответов) · `docs/ARCHITECTURE.md` (legacy-адаптеры) ·
`docs/STAGE7_HANDS_SPEC.md` (Q1 закрыт) · `CHANGELOG.md` · `README.md` ·
`unity-client/…/README.md` · `unity-client/…/SCENE.md` · `src/lilith_core/__init__.py` (0.6.2)

### Не тронуты

Серверный PCM-конвейер и контракт продюсера (Q1 подтвердил 24 kHz как есть),
`face/ws_frames.py` (`window` уезжает внутри уже существующего `face`-словаря кадра
`persona` — схема не менялась), `producer_sample_rate`.

---

## 5. Критерии готовности (Given/When/Then) и проверка

| Сценарий | Проверка |
|---|---|
| **Given** Windows/py3.13 и закрытый порт **When** мозг подключается **Then** `BrainConnectionError` с «профиль 'chat'» и адресом | `TestConnectTimeoutMapping` (5 тестов) + локальный прогон Кирюши |
| **Given** сервер поднялся, но молчит **When** вышел таймаут **Then** `BrainTimeoutError` | `test_read_timeout_is_still_timeout_error` |
| **Given** гибридная папка без `lilith/PLAN.md` и со старым `sync_test_count.py` **When** `pytest` **Then** 2 skipped, 0 failed | `TestHybridFolderGuards` (5 тестов) + её прогон |
| **Given** локальные виземы **When** рот открывается/закрывается **Then** открытие 0.08 с, закрытие 0.06 с, easing Cubic Out | `TestVisemeSmoothingDefaults` (4 теста); в Unity — на глаз |
| **Given** персона с `window: {width, height}` **When** приходит кадр `persona` **Then** окно Unity меняет размер и остаётся справа снизу | `TestPersonaWindowSpec` (9 тестов) + скриншот F7 |
| **Given** VTuber Studio/VMC **When** читаешь доки **Then** помечены legacy и выключены в поставке | `TestVmcMarkedLegacy` (3 теста) |

```bat
run_tests.bat                                                  :: ожидаем "665 passed"
python scripts\verify_stage_artifact.py --stage 6 --version 0.6.2
python scripts\unity_face_probe.py --speak "Привет, Кирюша." --want-server-visemes
```

---

## 6. Артефакт и переезд

`artifacts/LILITH-CORE_stage6_v0.6.2.zip` — собран и проверен
`scripts/verify_stage_artifact.py`; распаковка прогнана тестами.

**План переезда Кирюши подтверждён и безопасен:**
1. скачать `stage6_v0.6.2.zip`;
2. распаковать **поверх** (содержимое обёртки «Локальная Лилит» → в `E:\lilith-core\lilith\`);
3. `Assets` в Unity скопировать **один раз** — дальше только `Scripts/` (сцена и
   настройки инспектора не теряются, `.unity`-файл мы не трогаем);
4. `run_tests.bat` → 665 passed;
5. в Unity перепроверить два новых поля инспектора: `Viseme On/Off Seconds` (0.08/0.06)
   и `Window Size` (512×640).

⚠️ После распаковки поверх в гибридной папке останутся **старые** `scripts/sync_test_count.py`
и `lilith/PLAN.md` из workspace — именно поэтому тесты №2/№3 теперь скипаются, а не падают.
Если хочешь полную консистентность — скопируй свежий `scripts/sync_test_count.py` из
воркспейса агента (он точечный, историю не портит).

---

## 7. Что дальше

1. **Скриншот Unity-окна** (приёмка этапа 6, F7) — окно 512×640 справа снизу,
   прозрачное, рот шевелится.
2. **Остальные 5 вопросов спеки этапа 7** (`docs/STAGE7_HANDS_SPEC.md` §5, Q2–Q6) —
   Q1 закрыт. Список переслан архитектору.
3. **Этап 6.5** — персональная память (chroma-коллекции с префиксом персоны,
   выборка по `memory_scope`).
4. **Этап 7 «Руки»** — по спеке, после ответов на Q2–Q6 и команды «дальше».
