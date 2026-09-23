# 🔧 ОТЧЁТ ПО v0.6.4 — «ТЕЛО НЕ ДОЕЗЖАЕТ ДО СЦЕНЫ, VrmLoader МОЛЧИТ»

**Проект:** LILITH-CORE · **Версия:** 0.6.4 · **Дата:** 22.09.2026
**Тесты:** 776 passed, 2 skipped (+85 к 691) · с tree-sitter — 778 · **Этап:** 6 · **Решение:** ADR-023
**Основание:** точка правды **F7** — реальная сборка у Кирюши
(Windows, Unity **6000.0.84f1**, UniVRM **0.131.2**, embedded-пакеты UniGLTF / VRM / VRM-1.0)
**Объём правки:** **`Scripts/`** (3 файла из 11) + точечный access-лог сервера (наблюдаемость, не поведение)
**Контекст:** воркспейс предыдущей сборки умер (internal error), память поднята из git
(`kivifrukt5987/lilith-core`, main, `945052b`); черновик 0.6.4 делался заново по спеке архитектора

---

## 1. Причина моя

**Не сервер, не UniVRM, не сцена.** Клиент никогда не дёргал загрузчик.

`LilithFaceClient.OnPersona()` — единственная точка, где вызывается `vrmLoader.Swap()`.
Кадр `persona` по контракту **A5/ADR-015** сервер шлёт **только** в ответ на:

* `persona_request` от клиента, или
* `POST /api/face/personas/<id>/activate`.

На подключение продюсер получает `hello` (в нём есть `persona` — **её и показывает оверлей**),
на `ready` — только `state`. Метод `RequestPersona()` у клиента был написан ещё в 0.6.0 —
и **никто его не вызывал**. Значит:

```
нет кадра persona → нет OnPersona → нет Swap() → нет тела, нет исключений, Console чистая
```

Усугубляло картину то, что единственный `Debug.Log` про персону стоял под
`if (config.verbose)`, а `verbose` по умолчанию `false`. «Тихий no-op» — это не
загрузчик промолчал, это **выстрела не было**.

Вторичные мины, найденные там же (все закрыты этим хотфиксом):

| # | Мина | Чем грозила |
|---|---|---|
| а | `GetString(frame,"swap") != "" \|\| frame.ContainsKey("swap")` | `swap:false` игнорировался: сервер говорит «не грузить», клиент грузит |
| б | при `vrm: null` в кадре loader уходил в «нет ни локального пути, ни URL модели» | эндпоинт-то живой — клиент просто не умел построить URL сам |
| в | `face.vrm_path = null` (в `face.yaml` пусто) | локальная ветка не срабатывает никогда: HTTP — единственный путь |
| г | `uvicorn.run(access_log=False)` | «HTTP-GET'ы серверным логгером не печатаются вообще» — отличить «не постучался» от «не отдал» было нечем |
| д | `unity_face_probe.py` шлёт `persona_request` только с `--persona` | **слепое пятно моего же чекера**: путь «подключился → тело» не проверялся никем, поэтому блокер дожил до Windows |

### Воспроизведение в песочнице (живой сервер, stdlib-сокет, без Unity)

`scratch/repro_064.py` — ровно то рукопожатие, которое делает Unity-клиент 0.6.3:

```
1) сокет открыт, слушаю, что сервер шлёт САМ:
  <- hello   keys=[chunk_bytes, format, persona, personas, producer, protocol, sample_rate, server_version]
2) шлю hello + ready (как Unity-клиент 0.6.3):
  <- hello   <- state   <- state
   hello.persona = 'lilith'
   КАДР 'persona' ДО persona_request: False   <-- вот и блокер
3) шлю persona_request (то, что клиент 0.6.3 сам НЕ делает):
  <- persona  keys=[card, face, id, reason, swap, voice, vrm]
  <- focus
   persona.vrm='/api/face/personas/lilith/model.vrm'  swap=True
   persona.face.vrm_path=None   persona.face.window={'width':512,'height':640}
4) HTTP: 200 model/gltf-binary 21192 Б magic='glTF' (GLB ОК) — и из кадра, и по URL клиента
```

Факты из спеки архитектора сошлись все шесть: сервер здоров (п.4), продюсер ready
(лог `Продюсер[…]: ready (клиент 'unity/6000.0.84f1')`), кадр персоны **не** приходит,
GET'ы в логе не видны (`access_log=False`).

---

## 2. Что сделано

### 2.1 Клиент — `Scripts/` (ядро правки)

**`VrmLoader.cs`**

* `ModelUrlFor(personaId)` и статический `BuildModelUrl(baseUrl, personaId)` →
  `{ServerBaseUrl}/api/face/personas/{id}/model.vrm` (`TrimEnd('/')`, `Uri.EscapeDataString`).
  Статический — чтобы формулу можно было проверить гвардом без сцены и без сети.
* `Swap(personaId, localPath, serverRelativePath = "", force = false)`:
  при пустом `serverRelativePath` подставляется **свой** URL, а в лог пишется,
  откуда он взялся (`URL из кадра persona` / `URL построен клиентом`).
  Тупик «нет ни локального пути, ни URL модели» удалён; ветка отказа осталась
  только для пустого id (`URL тела построить не из чего`).
* **Гварды идемпотентности** (два): та же персона уже на сцене → повторной загрузки нет;
  та же персона **ещё грузится** (`LoadingPersona`) → своп пропускается. Обход — `force: true`.
  Причина — двойной `hello` от сервера, см. §2.1.1.
* **Логи безусловные** (не под `verbose`): старт (с URL и источником), `скачано N Б`,
  **ГОТОВО** (`за N мс · M МБ · трансформов K · родитель <root> · детей у родителя N`),
  **ОТКАЗ** — в *каждой* ветке (404/таймаут, отмена, `null`, вне Play Mode, нет UniVRM,
  устаревший `generation`). Время — `Time.realtimeSinceStartup`.
* `BodyInfo` + `LastBody` — сводка успешной загрузки для оверлея и приёмки.
* Правки 0.6.3 не потеряны: `using UniGLTF;` под `#if`, `yield return loadTask`,
  `IsFaulted`/`GetBaseException`, `IsCanceled`, **две** проверки `generation`,
  `NotifyLoaded`/`NotifyFailed` вне условной компиляции, ни одного `await`.

**`LilithFaceClient.cs`**

* `EnsureBodyOnConnect("hello")` из `OnHello`: шлёт `persona_request` (кадр приносит
  `vrm`, `face.window`, `idle`, `voice` — то, чего в `hello` нет) и заводит watchdog.
* `PersonaWatchdog`: ждёт кадр `persona` `personaFrameTimeoutSec` (2 с); не дождался →
  `LoadBodyBySelfBuiltUrl()` → `vrmLoader.Swap(ActivePersona, "")` (URL построит loader).
  Кадр `persona` снимает watchdog; `Disconnect()` — тоже (и только пока объект жив:
  `StopCoroutine` на уничтожаемом GameObject дал бы жёлтый warning, а нам нужны 0 жёлтых).
* `OnPersona`: `_personaFrameSeen = true`, честный `GetBool(frame, "swap", true)`
  и `if (!swap) return;`, безусловный лог `персона: … · swap=… · тело=…`,
  отдельная ветка `VrmLoader не назначен`.
* `OnVrmLoaded`: лог привязки к ригу со сводкой `LastBody`.
* Оверлей: новая строка `тело lilith · 0.02 МБ · 812 мс · трансформов 63 · родитель AvatarRoot`
  (или `грузится…` / `нет`), высота области 120 → 140.

**`LilithClientConfig.cs`** — три поля с тултипами и безопасными дефолтами:
`autoLoadBody = true`, `requestPersonaOnConnect = true`, `personaFrameTimeoutSec = 2f`.

### 2.1.1 Мина, найденная на последнем прогоне: сервер шлёт `hello` дважды

Контрольный выстрел живым сервером показал в логе `кадры: {'hello': 2, 'state': 2, …}`:
`hello` приходит **на accept** и **ещё раз в ответ на `hello` клиента**. Значит
`OnHello` (и мой новый триггер) срабатывает дважды подряд — а это две закачки тела
по 20 МБ и два серверных свопа персоны. Снаружи выглядит как «загрузка зависла».
Закрыто в двух местах (оба — под гвардами и контрольными выстрелами):

* `VrmLoader`: гвард «эта персона **ещё грузится**» (`LoadingPersona`) + прежний
  «уже на сцене»; обход — `force: true`;
* `LilithFaceClient`: `persona_request` уходит **один раз на соединение**
  (`_personaRequestedFor`, сбрасывается в `Disconnect()`, чтобы reconnect снова попросил кадр).

### 2.2 Сервер — только наблюдаемость

* `LoggingSettings.access_log_prefixes` (дефолт `["/api/face/"]`, `[]` = выключен)
  + middleware `_register_access_log()` в `app.py`:

  ```
  HTTP GET /api/face/personas/lilith/model.vrm → 200 (21192 Б, 8.4 мс)
  ```

  Пишутся и отказы (`→ 404`) — именно их не хватало на приёмке. Прочие пути
  middleware покидает первым же `if`, без единой лишней строки.
* `run.py` **не тронут**: общий `access_log` uvicorn остался выключенным (поллинг
  панели залил бы консоль). Гвард на это решение — в тестах.
* Контракт продюсера **не менялся**: сервер по-прежнему не пушит `persona` сам.

### 2.3 Регрессия триггера — `scripts/unity_face_probe.py`

Новая проверка **«тело доезжает», по умолчанию** (отключается `--skip-body-check`):
`hello.persona` → `persona_request` → сверка `vrm` из кадра с URL, построенным
по той же формуле, что и C# → **настоящий HTTP GET** → `200` + magic `glTF` + размер.
Хелперы `http_base_from_ws()`, `model_url_for()`, `fetch_body()`,
`check_body_reaches_scene()` — чистый stdlib, как и вся проба.

```
тело: lilith · GET 200 · 21192 Б · magic glTF · тело доедет: URL живой, GLB валиден
ИТОГ: 13 пройдено, 0 провалено
```

### 2.4 Гигиена «источника правды» (найдено при восстановлении из git)

Свежий клон давал **3 красных**, которых на машине Кирюши нет, — расхождение git ↔ архив:

* `start.bat` / `run_tests.bat` лежали в индексе с **LF** (`git ls-files --eol`: все 146
  текстовых файлов — LF, `.gitattributes` не было) → `test_crlf_line_endings` ×2 и
  возврат класса бага **0.1.1** («cmd.exe кашляет на LF-батнике»). Добавлен
  `.gitattributes` (`*.bat/*.cmd/*.ps1 → eol=crlf`, исходники и доки → LF, бинарщина
  помечена), bat-файлы пересохранены с CRLF.
* `models/packs.yaml` в git **не было** (`models/` в `.gitignore`, а в архив этапа его
  кладёт `build_stage_archive.py`: `MODELS_KEEP = {"packs.yaml"}`) → красный
  `test_packs_list_cli_reads_manifest` на клоне. Сначала закрыла гвард-skip'ом
  (в стиле гвардов 0.6.2 для гибридной папки), а как только Кирюша прислал файл —
  **закоммитила манифест и сняла гвард** (§2.6).
* Итог: клон + `pip install -e ".[dev,memory]"` = **0 красных** (было 3).

### 2.5 Допечатано до запечатывания zip (пункт 4 просилки архитектора)

Zip ещё не уехал к Кирюше, поэтому four пункта закрыты в той же поставке, а не
следующим коммитом:

1. **`.gitattributes` переехал в `lilith/`** (корень проекта). Архив пакует `lilith/`,
   поэтому атрибуты уровнем выше в поставку **не попадали** — а именно они держат CRLF
   у bat-файлов на машине Кирюши. Правила действуют на всё поддерево `lilith/`
   (bat-файлы там и лежат), поведение git не изменилось: проверено свежим клоном —
   `start.bat` 122 CRLF / 0 голых LF, `run_tests.bat` 44 / 0.
2. **`.gitignore` подготовлен под `models/packs.yaml`.** Было `models/` — при исключённом
   РОДИТЕЛЕ git не умеет возвращать отдельный файл. Стало `models/*` + `!models/packs.yaml`:
   разигнорен ровно манифест, а `models/notes.txt`, `*.safetensors`, `models/stt/model.bin`
   по-прежнему игнорируются (проверено `git status --untracked-files=all` и `git add -n`).
   Файл к тому письму не доехал; Кирюша прислал его текстом следующим сообщением — см. §2.6.
3. **`scripts/sync_test_count.py` починен и переехал в проект** (`lilith/scripts/`),
   копия уровня workspace удалена (в архив она не попадала, поэтому и «потерялась»).
   Пути — от расположения скрипта; `PLAN.md` ищется в `lilith/`, `память и личность/`
   и рядом с проектом; добавлены `--count` и `--dry-run`; хирургия сохранена
   (только маркер `ТЕСТЫ СЕЙЧАС:` и строка `| Тесты |` в README; `CHANGELOG.md`,
   `STAGEn_REPORT.md`, `RELEASE_*.md` запрещены и проверкой в рантайме).
   Свои же тесты поймали **два настоящих дефекта**: `re.subn` возвращает число замен,
   а не факт изменения (dry-run рапортовал «БУДЕТ обновлён» для уже правильного числа),
   и дефолтный аргумент `find_plan(candidates=PLAN_CANDIDATES)` привязывался один раз —
   из-за чего тесты с подменённым деревом молча правили бы **живой** PLAN.md репозитория.
   Плюс маркерная строка PLAN.md содержит и «(с tree-sitter — M)» — скрипт её обновляет.
4. **`PLAN.md` освежён** (файл остановился на этапе 3, 17.09). Восстановлен по
   `CHANGELOG.md`, ADR-001…023 и `RELEASE_*.md`: 9 этапов с версиями, таблица хотфиксов
   0.6.1–0.6.4, лог до 0.6.4 включительно, зафиксированы точка правды F7 и регламент
   хотфиксов, добавлены маркер `ТЕСТЫ СЕЙЧАС:` и раздел «Открытые хвосты».
   `test_structure.py::TestDocsHygiene` больше не скипается в git-репо (ищет скрипт
   в проекте и на уровне workspace, а PLAN.md — в обоих исторических местах):
   было 2 skip → стало 2 passed.

### 2.6 Манифест приехал: клон снова равен архиву

К первому письму файл-вложение не доехало, Кирюша прислал его **текстом из Блокнота**
следующим сообщением. Закоммичен байт в байт, ничего от себя не дописывала
(URL и sha256 — его, выдумывать нельзя). Два коммита, как договаривались с архитектором:

1. `models/packs.yaml` — файл (негатив-правило в `.gitignore` уже было готово);
2. снятие гвард-skip в `test_voice_app.py` + усиление: CLI-тест теперь проверяет оба
   пака, и добавлен гвард **`test_manifest_is_tracked_and_unignored`** — держит сам
   инвариант «клон == архив»: файл на месте, `!models/packs.yaml` в `.gitignore` жив,
   blanket-правило `models/` не вернулось, у каждого пака есть `type`/`source`/`target_dir`.

Проверено: `yaml.safe_load` → 2 пака (`whisper-small`, `whisper-large-v3`, оба `stt`,
`sha256: null`, `size: 0`); `packs_main(["list"])` → RC 0, обе строки со статусом
`missing` (веса не скачаны — так и должно быть).

Итог: **третий красный свежего кона устранён по существу**, а не скипом. Скипов в
песочнице осталось 2 — самопроверка архива 0.6.0 (в git `artifacts/` не лежит).

---

## 3. Тесты

| Прогон | Результат |
|---|---|
| `run_tests.bat` у Кирюши (без tree-sitter, есть `artifacts/`) | **776 passed, 2 skipped** (ожидаем) |
| То же с tree-sitter (`.venv-ts`) | **778 passed** |
| Моя песочница: свежий клон + `.[dev]` + chromadb + tree-sitter | **776 passed, 2 skipped, 0 failed** |
| Разница | **нулевая по числам, разный состав скипов**: у меня 2 skip — самопроверка архива 0.6.0 (в git `artifacts/` не лежит), у Кирюши 2 skip — тесты чекера C# без tree-sitter. `models/packs.yaml` теперь в репо, поэтому третий скип исчез у обоих |
| До хотфикса, свежий клон | 684 passed, **3 failed**, 6 skipped |
| `python scripts\check_csharp_syntax.py` | Файлов: **11**, веток: **2**, ошибок: **0** |
| Контрольные выстрелы чекера C# | `broken_await.cs` → RC=1 (CS4032-подобное ×2); пропущенная `;` → RC=1 |

Раскладка новых тестов — 83 в `tests/test_hotfix_064.py` + 1 в `test_structure.py` + 1 в `test_voice_app.py` = **85**:

| Класс | Шт | Что держит |
|---|---|---|
| `TestBodyUrlFormula` | 6 | формула URL probe ↔ `VrmLoader.BuildModelUrl`; эндпоинт по этой формуле существует |
| `TestTriggerContract` | 5 | hello+ready **не** приносят `persona`; `persona_request` приносит; `vrm` == построенному URL; `vrm: null` без тела |
| `TestModelEndpoint` | 3 | 200 + `model/gltf-binary` + magic; 404 с подсказкой; 404 на чужую персону |
| `TestFaceAccessLog` | 8 | дефолт префиксов, `config.yaml`, лог 200 и 404, чужие пути не логируются, `[]` выключает, `access_log=False` в `run.py` остался |
| `TestProbeBodyCheck` | 6 | проба зелёная с телом, проверка включена по умолчанию, `--skip-body-check`, **краснеет без тела**, мёртвый порт не «тихий успех», access-лог видит реальную закачку |
| `TestVrmLoaderSelfBuiltUrl` | 10 | фолбэк URL, тупик удалён, пустой id падает громко, идемпотентность (на сцене + в полёте), `generation` ×2, `BodyInfo`, ни одного `await` |
| `TestSwapLogs` | 9 | точки логов, **каждая** ветка отказа с логом, не под `verbose`, видны и без UniVRM, финиш с размером/временем/родителем |
| `TestClientTrigger` | 9 | триггер в `OnHello`, `persona_request` (+дедупликация и её сброс на `Disconnect`), watchdog, своп по своему URL, снятие watchdog, `activeInHierarchy` |
| `TestSwapFlagIsRespected` | 4 | старой конструкции нет, `GetBool`, `if (!swap)`, лог не под `verbose` |
| `TestOverlayShowsBody` | 3 | строка `тело …`, `LastBody.Describe()`, высота оверлея |
| `TestNewConfigFields` | 5 | три поля с дефолтами, тултипы, **нет `"))]`** |
| `TestNoForeignScripts` | 4 | в правленых `.cs` нет посторонних иероглифов (в `FaceRig.cs` японские морфы — легальны) |
| `TestSyncTestCountScript` | 11 | скрипт живёт в проекте (едет в архиве), пути от репо, без `Локальная Лилит`, правит только маркер и строку README, история в PLAN и `CHANGELOG.md` не тронуты, `--dry-run` ничего не пишет и **не врёт**, запрещённые доки отбиваются, отсутствие PLAN.md не фатально, живые доки репо согласованы |

Плюс: `tests/test_vrm_sample_and_probe.py` — в фикстуру живого сервера положено
процедурное тело (ADR-017), поэтому дефолтная проверка «тело доезжает» гоняется
в основном прогоне; `tests/test_structure.py` — `RELEASE_0.6.4.md` в обязательных файлах.

### Контрольные выстрелы по своим же гвардам — 12/12

| Мутация | Поймана |
|---|---|
| сломать дедупликацию `persona_request` | ✅ 1 failed |
| не сбрасывать дедупликацию на `Disconnect` | ✅ 1 failed |
| сломать гвард «уже грузится» | ✅ 1 failed |
| закомментировать `EnsureBodyOnConnect("hello")` | ✅ 1 failed |
| удалить `EnsureBodyOnConnect("hello")` | ✅ 1 failed |
| закомментировать `RequestPersona(ActivePersona)` | ✅ 1 failed |
| закомментировать `vrmLoader.Swap(ActivePersona, "")` в watchdog | ✅ 1 failed |
| вернуть тупик (`relative = ""` вместо `ModelUrlFor`) | ✅ 1 failed |
| спрятать лог персоны за `config.verbose` | ✅ 1 failed |
| сломать уважение `swap:false` | ✅ 1 failed |
| убрать `_register_access_log(app, settings)` | ✅ 4 failed |
| сломать формулу URL в C# | ✅ 1 failed |

Отдельные контрольные выстрелы по `sync_test_count.py` (не мутации, а живые прогоны):
`--count 764 --dry-run` на уже правильных доках обязан сказать «без изменений»
(первая версия врала), `--count 500 --dry-run` — показать оба файла и **ничего не записать**,
прогон без `--dry-run` — реально обновить маркер (им же в доки записано итоговое число).

**Промах первой версии, который стоит запомнить.** Гвард
`assert 'EnsureBodyOnConnect("hello")' in on_hello` **проспал** закомментированный
вызов: строка-то на месте. Переписала на `code_calls()` — строка обязана *начинаться*
с вызова и не быть комментарием. Чекер не всегда зелёный; иногда он зелёный там,
где не надо, и это лечится только выстрелом по нему самому.

---

## 4. Список файлов

### Новые (5)

| Файл | Зачем |
|---|---|
| `models/packs.yaml` | манифест паков от Кирюши (§2.6): клон снова равен архиву этапа |
| `tests/test_hotfix_064.py` | 83 теста: триггер, URL, access-лог, проба, C#-гварды, `sync_test_count.py` |
| `RELEASE_0.6.4.md` | этот отчёт |
| `lilith/.gitattributes` | CRLF для `.bat`, LF для исходников, бинарщина помечена; лежит в корне проекта, чтобы уезжать в архиве |
| `lilith/scripts/sync_test_count.py` | починенная версия скрипта синхронизации числа тестов (путь от репо, хирургия, `--count`/`--dry-run`) |

### Удалённые (1)

| Файл | Почему |
|---|---|
| `scripts/sync_test_count.py` (корень репо) | устаревшая копия: смотрела в `Локальная Лилит/`, лезла в `CHANGELOG.md` и отчёты этапов. Заменена проектной (едет в архиве), гвард `TestDocsHygiene` ищет обе |

### Изменённые (17)

| Файл | Правка |
|---|---|
| `unity-client/Assets/LilithFace/Scripts/VrmLoader.cs` | `ModelUrlFor`/`BuildModelUrl`, фолбэк URL в `Swap`, `force`, два гварда идемпотентности (`LoadingPersona`), `BodyInfo`/`LastBody`, безусловные логи старта/финиша/отказа |
| `unity-client/Assets/LilithFace/Scripts/LilithFaceClient.cs` | `EnsureBodyOnConnect` (+дедупликация `persona_request`), `PersonaWatchdog`, `LoadBodyBySelfBuiltUrl`, `GetBool`, `swap:false`, логи, строка `тело` в оверлее |
| `unity-client/Assets/LilithFace/Scripts/LilithClientConfig.cs` | 3 поля: `autoLoadBody`, `requestPersonaOnConnect`, `personaFrameTimeoutSec` |
| `src/lilith_core/config.py` | `LoggingSettings.access_log_prefixes` |
| `src/lilith_core/app.py` | `_register_access_log()` + middleware |
| `config/config.yaml` | `logging.access_log_prefixes: ["/api/face/"]` |
| `src/lilith_core/__init__.py` | `__version__ = "0.6.4"` |
| `scripts/unity_face_probe.py` | проверка «тело доезжает» по умолчанию + 4 хелпера + `--skip-body-check` + строка в отчёте |
| `tests/test_vrm_sample_and_probe.py` | тело в фикстуре живого сервера |
| `tests/test_voice_app.py` | гвард-skip на `models/packs.yaml` **снят** (файл закоммичен), CLI-тест усилен (`whisper-large-v3`), добавлен гвард инварианта «клон == архив» |
| `tests/test_structure.py` | +`RELEASE_0.6.4.md` в обязательных файлах; `TestDocsHygiene` ищет скрипт и PLAN.md в обоих раскладках |
| `.gitignore` | `models/` → `models/*` + `!models/packs.yaml` (разигнорен ровно манифест) |
| `CHANGELOG.md`, `README.md`, `docs/DECISIONS.md` | 0.6.4, диагностика, ADR-023 |
| `unity-client/Assets/LilithFace/README.md`, `SCENE.md` | три новых поля инспектора, критерий «тело доезжает», поток данных с watchdog |
| `память и личность/PLAN.md` | освежён: 9 этапов, хотфиксы 0.6.1–0.6.4, лог, маркер `ТЕСТЫ СЕЙЧАС:`, открытые хвосты |
| `start.bat`, `run_tests.bat` | пересохранены с CRLF (содержимое не менялось) |

### Не тронуты намеренно

| Что | Почему |
|---|---|
| `unity-client/Assets/LilithFace/LilithFace.asmdef` | там ручные ссылки Кирюши (UniGLTF, UniHumanoid, VRM10, UniGLTF.Utils) и `versionDefines` из 0.6.3 — при переезде копируется **только** `Scripts/` |
| сцена, `ProjectSettings`, инспектор | правка клиентская; новых обязательных назначений нет (три поля получат дефолты сами) |
| остальные 8 C#-файлов (`FaceRig`, `VisemeDriver`, `EmotionDriver`, `IdleController`, `AudioQueueProcessor`, `TransparentWindow`, `LilithWSClient`, `MiniJson`) | баг не там |
| `src/lilith_core/run.py` | `access_log=False` оставлен осознанно (ADR-023, решение 5) |
| `face/producer.py`, `face/endpoints.py`, `face/ws_frames.py` | контракт A5/ADR-015 не меняем: сервер по-прежнему не пушит `persona` сам |
| `personas/lilith/face.yaml` | `vrm_path` пустой намеренно (D5.4: тело вне репо); у Кирюши там её путь |
| `docs/STAGE7_HANDS_SPEC.md` | этапа 7 не касаемся |

---

## 5. Что делать Кирюше

### Шаг 0. Сервер (проверить, что он здоров и теперь разговорчивый)

```bat
cd /d "путь\к\lilith"
.venv\Scripts\python -m pytest -q
:: ожидаем: 776 passed, 2 skipped   (с tree-sitter в .venv-ts — 778 passed)

.venv\Scripts\python scripts\check_csharp_syntax.py
:: ожидаем: Файлов: 11, веток: 2, ошибок: 0

start.bat
:: в другом окне:
.venv\Scripts\python scripts\unity_face_probe.py
:: ожидаем строку:  тело: lilith · GET 200 · N Б · magic glTF · тело доедет: URL живой, GLB валиден
:: и в логе сервера: HTTP GET /api/face/personas/lilith/model.vrm → 200 (N Б, X мс)
```

### Шаг 1. Unity (миграция — как договорились)

1. Скопировать **только** `unity-client/Assets/LilithFace/Scripts/*.cs`
   в свой проект (`Assets/LilithFace/Scripts/`). **Свой `.asmdef` не затираем**,
   сцену и настройки инспектора не трогаем.
2. Дождаться компиляции: ожидаем **0 красных, 0 жёлтых**.
3. В инспекторе `Lilith` появятся три новых поля (дефолты уже правильные):
   `Auto Load Body` ✅, `Request Persona On Connect` ✅, `Persona Frame Timeout Sec` = 2.
   `Server Base Url` у `VrmLoader` оставить `http://127.0.0.1:8765`, `Root` — `AvatarRoot`
   (или `LilithFace`, как сейчас), `Await Timeout` = 0.001.
4. Play Mode.

### Шаг 2. Что должно появиться (по порядку)

**Console:**

```
[Lilith] тело: прошу у сервера кадр персоны 'lilith' (persona_request, hello)
[Lilith] тело: кадр персоны 'lilith' в этом соединении уже запрошен — повтор не шлю   ← второй hello от сервера, это норма
[Lilith] персона: lilith · swap=True · тело=/api/face/personas/lilith/model.vrm
[Lilith] тело: старт свопа 'lilith' ← GET http://127.0.0.1:8765/api/face/personas/lilith/model.vrm (URL из кадра persona)
[Lilith] тело: скачано N Б с http://127.0.0.1:8765/api/face/personas/lilith/model.vrm
[Lilith] тело: ГОТОВО 'lilith' за 812 мс · 21.50 МБ · трансформов 63 · родитель AvatarRoot · детей у родителя 1
[Lilith] тело 'lilith' привязано к ригу: lilith · 21.50 МБ · 812 мс · трансформов 63 · родитель AvatarRoot
```

**Hierarchy:** у `AvatarRoot` (или у `LilithFace`, если `Root` указан так) появляется `persona_lilith`.
**Оверлей:** строка `тело lilith · 21.50 МБ · 812 мс · трансформов 63 · родитель AvatarRoot`.
**Лог сервера:** `HTTP GET /api/face/personas/lilith/model.vrm → 200 (… Б, … мс)` и
`Своп персоны: 'lilith' (client_request)` — это нормально, клиент сам попросил кадр.

### Шаг 3. Если тела всё равно нет — теперь это видно

| Строка в Console | Что это | Что делать |
|---|---|---|
| `тело: ОТКАЗ … → 404 {"status":"missing_vrm"…}` | у персоны нет тела на диске | положить VRM по `face.yaml: vrm_path` или рядом как `personas/lilith/model.vrm`; проверить `unity_face_probe.py` |
| `тело: ОТКАЗ … Cannot connect to destination host` | сервер не поднят / другой порт | `start.bat`, проверить `Server Base Url` |
| `кадр persona не пришёл за 2 с (hello) — гружу по URL, который построила сама` | сервер старше 0.6.4 или кадр потерялся | не баг: watchdog догрузит сам. Если повторяется всегда — прислать лог сервера |
| `загрузка VRM работает только в Play Mode` | своп запустили в Edit Mode | нажать Play (известное ограничение `RuntimeOnlyAwaitCaller`, 0.6.3) |
| `LILITH_UNIVRM не определён` | символ не подхватился | проверить `.asmdef` (`versionDefines`: `com.vrmc.vrm`, `com.vrmc.univrm`, `com.vrmc.vrm10`, `com.vrmc.gltf`) и установку UniVRM |
| `тело 'lilith' уже на сцене — повторный своп не нужен` / `уже грузится — повторный своп пропущен` / `кадр персоны … уже запрошен — повтор не шлю` | гварды идемпотентности (сервер шлёт `hello` дважды) | это норма; принудительно — `Swap(id, "", "", force: true)` |
| Console чистая, но тела нет | **не должно случиться** | скриншот оверлея + Hierarchy + лог сервера — буду копать |

Любой из этих случаев = скриншот мне, дальше чиню я.

---

## 5.5 Артефакт

`artifacts/LILITH-CORE_stage6_v0.6.4.zip` — собран и проверен
`scripts\verify_stage_artifact.py --stage 6 --version 0.6.4`
(«артефакт жив, полон и пригоден к скачиванию ✅», распаковка без потерь,
внутри `v0.6.4` / этап 6).

В архиве **142 записи** (все файлы проекта без `.venv`, кэшей, логов, `data/` и
вложенных zip). Точные размер и sha256 печати — в сопроводительном сообщении:
в сам отчёт их не пишу сознательно, иначе архив содержит документ, где записана
контрольная сумма архива, который ещё не собран (конвенция 0.6.3). Секретов и мусора нет: `.env`, `*.log`, `__pycache__`,
`.venv`, `data/`, `Library/`, `*.csproj` проверены самой самопроверкой.

`start.bat` и `run_tests.bat` внутри архива — **CRLF** (проверено побайтово:
122 и 44 перевода, голых LF нет). `.gitattributes`, `scripts/sync_test_count.py`
и `models/packs.yaml` в архиве **есть** (§2.5, §2.6): первая печать zip'а не содержала
атрибутов git, вторая — манифеста, поэтому архив перепечатан **трижды**; финальный
sha256 и размер — в сопроводительном сообщении и в таблице ниже.

Чего внутри **нет** и почему: `.venv`/`__pycache__`/`logs`/`data`/вложенные zip
(самопроверка это контролирует) и `память и личность/PLAN.md` (он уровня workspace,
выше корня проекта — едет в git-патче). `models/packs.yaml` после §2.6 **внутри**.

Для Unity-миграции нужен не весь архив, а три файла:
`unity-client/Assets/LilithFace/Scripts/{VrmLoader,LilithFaceClient,LilithClientConfig}.cs`.
Свой `.asmdef` Кирюша **не затирает** — там её ручные ссылки
(UniGLTF, UniHumanoid, VRM10, UniGLTF.Utils).

---

## 6. Что дальше

1. **Приёмка F7 до конца**: тело в сцене → рот шевелится от чанков TTS → моргание →
   своп персоны из панели → OBS берёт окно с прозрачностью. Это закрывает этап 6.
2. ~~**`models/packs.yaml`**~~ — **закрыто** (§2.6): файл закоммичен, гвард-skip снят,
   добавлен гвард инварианта «клон == архив». Из просилки архитектора открытым не
   осталось ничего, кроме `HANDOVER.md` (пункт 6 ниже).
3. ~~`scripts/sync_test_count.py`~~ — **сделано** (§2.5): починен, переехал в проект,
   ездит в архиве, покрыт 11 тестами.
4. ~~`PLAN.md`~~ — **сделано** (§2.5): освежён до 0.6.4, добавлен маркер `ТЕСТЫ СЕЙЧАС:`.
5. **Этап 7 («Руки»)** — по команде «дальше»: sandbox только директорией (ADR-021),
   реестр инструментов, баннер подтверждения с 25-с отсчётом (HITL, ADR-006).
6. **`HANDOVER.md`** в репо так и нет (в задании на восстановление он стоял первым).
   Могу написать — скажи, нужен ли, и в каком месте: корень репо или `lilith/`.
7. **Push в git**: у строителя нет креденшелов → коммиты уезжают zip'ом и патчем
   (`git am`). Если нужен прямой push в `main` — дайте токен или пушьте со своей машины.
8. **Наблюдение за watchdog'ом**: если на практике кадр `persona` будет приходить
   медленнее 2 с (тяжёлая LoRA в следующих этапах), `personaFrameTimeoutSec` поднять —
   поле в инспекторе, код не трогаем.

---

`[LILITH.EXE — BUILDER. v0.6.4. ТЕЛО ДОЕЗЖАЕТ, И ЭТО ВИДНО В КОНСОЛИ. 🦇]`
