# 🔧 ОТЧЁТ ПО v0.6.3 — «ДВА КРАСНЫХ В UNITY-СБОРКЕ»

**Проект:** LILITH-CORE · **Версия:** 0.6.3 · **Дата:** 22.09.2026
**Тесты:** 691 passed (+26) · **Этап:** 6 · **Решение:** ADR-022
**Основание:** точка правды **F7** — реальная сборка у Кирюши
(Windows, Unity **6000.0.84f1**, UniVRM **0.131.2**, embedded-пакеты UniGLTF / VRM / VRM-1.0;
ссылки в `.asmdef` проставлены вручную: UniGLTF, UniHumanoid, VRM10, UniGLTF.Utils)
**Объём правки:** **ТОЛЬКО `Scripts/`** — сцена и настройки инспектора не трогаются (по плану переезда)

---

## 1. Красные — оба закрыты

### №1 `VrmLoader.cs(179,38): CS0246 — 'RuntimeOnlyAwaitCaller' could not be found`

**Причина моя.** По исходникам UniVRM я проверила **сигнатуру** `Vrm10.LoadBytesAsync`
и **существование** класса `RuntimeOnlyAwaitCaller`, но не его **неймспейс**. Тип
объявлен в `namespace UniGLTF`, физически — `Packages/UniGLTF/Runtime/Utils/AwaitCaller/
RuntimeOnlyAwaitCaller.cs`, сборка **`UniGLTF.Utils`** (её-то Кирюша в ссылки и добавила).
`using UniGLTF;` в файле не было → тип не находился, хотя ссылка на сборку стояла.

**Сверено по исходникам (vrm-c/UniVRM, master):**

```csharp
namespace UniGLTF
{
    public sealed class RuntimeOnlyAwaitCaller : IAwaitCaller
    {
        public RuntimeOnlyAwaitCaller(float timeOutInSeconds = 1f / 1000f) { … }
        public Task NextFrame();  public Task Run(Action);  public Task<T> Run<T>(Func<T>);
        public Task NextFrameIfTimedOut();
    }
}
```

**Что сделано**

* добавлен `using UniGLTF;` — **внутри** `#if LILITH_UNIVRM` (иначе проект не соберётся
  до импорта UniVRM);
* конструктор вызывается с явным параметром: `new RuntimeOnlyAwaitCaller(awaitTimeoutSeconds)`,
  новое поле инспектора `awaitTimeoutSeconds = 0.001f` (дефолт UniVRM — 1 мс; больше =
  плавнее загрузка, меньше просадка fps);
* **важная деталь, найденная там же в исходниках:** `NextFrameTaskScheduler` в конструкторе
  проверяет `Application.isPlaying` и вне Play Mode бросает `NotSupportedException`.
  Добавлена отдельная ветка `catch (NotSupportedException)` с внятным сообщением
  «загрузка VRM работает только в Play Mode» — вместо загадочного падения;
* в `LilithFace.asmdef` добавлены `versionDefines` для **`com.vrmc.vrm10`** и
  **`com.vrmc.gltf`** (раньше были только `com.vrmc.vrm` и `com.vrmc.univrm`) — чтобы
  символ `LILITH_UNIVRM` определялся и при embedded-установке, как у Кирюши;
* `references` в репозитории **остались пустыми** намеренно: со ссылками на `VRM10`
  проект не соберётся до импорта UniVRM. Её ручные ссылки живут у неё — при переезде
  копируется только `Scripts/`, `.asmdef` не затирается.

### №2 `VrmLoader.cs(174,28): CS4032 — 'await' can only be used within an async method`

**Причина моя.** `SwapRoutine` — `IEnumerator`-корутина (нужна для `yield return`
в фазе скачивания через `UnityWebRequest`), а внутри `#if LILITH_UNIVRM` я написала
`instance = await Vrm10.LoadBytesAsync(…)`. Песочница это не поймала: ветка не
компилировалась без символа `LILITH_UNIVRM`.

**Что сделано** — стандартный паттерн «Task внутри корутины», без `async void`:

```csharp
System.Threading.Tasks.Task<Vrm10Instance> loadTask = null;
try
{
    loadTask = Vrm10.LoadBytesAsync(bytes, canLoadVrm0X: true,
        controlRigGenerationOption: ControlRigGenerationOption.Generate,
        showMeshes: true, awaitCaller: new RuntimeOnlyAwaitCaller(awaitTimeoutSeconds));
}
catch (NotSupportedException notSupported) { … «только Play Mode» … yield break; }
catch (Exception ex)                       { … «не запустился на загрузку» … yield break; }

yield return loadTask;                     // Unity умеет ждать Task в корутине

if (loadTask.IsFaulted)  { var cause = loadTask.Exception?.GetBaseException() ?? loadTask.Exception; … }
if (loadTask.IsCanceled) { … }
instance = loadTask.Result;

if (generation != _generation)             // пока грузилось — пришёл новый своп
{
    if (instance != null) Destroy(instance.gameObject);
    Loading = false;
    yield break;
}
```

Три вещи, которые тут важны, а не «для красоты»:

1. **Исключения достаём из задачи явно.** Без `IsFaulted`/`GetBaseException` ошибка
   загрузки утонула бы в `UnobservedTaskException`, а тело «молча» не появилось —
   ровно тот класс багов, который мы лечили в хотфиксе 0.4.1 («окно больше не умеет
   умирать молча»).
2. **Повторная проверка `generation` ПОСЛЕ загрузки.** Раньше она была только до
   скачивания: при двух быстрых свопах персоны второе тело могло перезаписать первое.
   Теперь устаревший результат уничтожается.
3. **`IsCanceled`** — отдельная ветка, чтобы отмена не выглядела как «вернул null».

---

## 2. Жёлтый CS0067 (`VrmLoader.Loaded is never used`) — закрыт по существу

Предупреждение было **следствием** красного №2: раз ветка `#if` не компилировалась,
`Loaded?.Invoke(…)` внутри неё для компилятора не существовал. После починки оно
уходит само. Но «само» — не гарантия, поэтому добавлены легальные точки вызова
**вне** условной компиляции:

* `NotifyLoaded(personaId, model)` — сообщить подписчикам, что тело загружено и
  привязано. Пригодится, если тело поставили в сцену руками в Editor'е и его надо
  привязать к `FaceRig` без загрузки файла;
* `NotifyFailed(personaId, result, reason)` — то же для ошибки.

Оба метода покрыты тестом «находятся вне `#if LILITH_UNIVRM`» (стриппер блоков с
учётом вложенности — `#if UNITY_STANDALONE_WIN` внутри вырезаться не должен).

---

## 3. Новый инструмент: `scripts/check_csharp_syntax.py`

**Зачем.** Оба красных — из класса «песочница не видит». C# у меня не компилируется,
а ветка под `#if LILITH_UNIVRM` вообще не разбиралась. Отдавать Кирюше непроверенный
файл второй раз — непозволительно.

**Что делает.** Парсит все `*.cs` клиента грамматикой **tree-sitter-c-sharp** и
проверяет **обе ветки** условной компиляции (без символа и с `LILITH_UNIVRM`):

* синтаксические `ERROR`/`MISSING`-узлы;
* **структурная проверка `await` вне `async`-метода** — аналог CS4032, то есть ровно
  та ошибка, которая приехала в приёмке.

Грамматика tree-sitter не переваривает `#else`, поэтому перед разбором файл
прогоняется через **селектор веток** `select_branches()` (чистый Python: `#if/#elif/
#else/#endif`, `!`, `&&`, `||`, скобки; неактивные строки заменяются пустыми, чтобы
номера строк в отчёте оставались настоящими).

```bat
:: окружение один раз (вне проекта, в архив этапа не попадает)
python3 -m venv .venv-ts && .venv-ts\Scripts\pip install tree-sitter tree-sitter-c-sharp pytest pytest-asyncio

:: проверка
.venv-ts\Scripts\python scripts\check_csharp_syntax.py
.venv-ts\Scripts\python scripts\check_csharp_syntax.py --define LILITH_UNIVRM
```

**Проверено, что чекер не «всегда зелёный»:**

| Контрольный выстрел | Результат |
|---|---|
| убрать `;` в `VrmLoader.cs` | `[FAIL] VrmLoader.cs:128:23 ERROR → «true»` |
| вернуть `await loadTask` в корутину | `[FAIL] VrmLoader.cs:221:21 CS4032-подобное: await в не-async методе` |
| чистый код, обе ветки | `Файлов: 11, веток: 2, ошибок: 0` |

**Побочно найден и починен баг самого чекера:** токенизатор склеивал `!X` в одно
слово → `#if !X` всегда вычислялось как «неизвестный символ» = ложь. Пойман тестом
`test_negation_and_logic` (в файле оставлен комментарий об этой регрессии).

**Чего чекер НЕ делает:** не проверяет типы, имена и неймспейсы. CS0246 (не найден
тип) он бы не поймал — это работа компилятора Unity. Поэтому параллельно добавлены
**стражи на конкретные факты** (см. §4).

---

## 4. Тесты — 691 passed (+26 к 665)

`tests/test_hotfix_063.py`:

| Группа | Что держит |
|---|---|
| `TestAwaitCallerNamespace` (5) | `using UniGLTF;` есть **и** находится внутри `#if`; конструктор с таймаутом; `NotSupportedException`/Play Mode задокументированы; именованный аргумент `awaitCaller:` |
| `TestNoAwaitInCoroutine` (6) | `SwapRoutine` — корутина; `yield return loadTask`; **ни одного `await` в `VrmLoader.cs`**; `IsFaulted`+`GetBaseException`+`IsCanceled`; повторная проверка `generation` после загрузки; `async` во всём клиенте живёт только в `LilithWSClient.cs` |
| `TestEventHasLegalCallSite` (3) | `NotifyLoaded`/`NotifyFailed` существуют и лежат **вне** `#if LILITH_UNIVRM` |
| `TestAsmdefVersionDefines` (4) | покрыты `com.vrmc.vrm/vrm10/gltf`, все определяют `LILITH_UNIVRM`, `references` в репо пустые, README объясняет ручные ссылки |
| `TestBranchSelector` (6) | `#if/#else`, отрицание и логика (регрессия токенизатора), вложенность, сохранение номеров строк |
| `TestCSharpSyntaxChecker` (2) | все 11 файлов парсятся в обеих ветках; контрольный образец `tests/samples/broken_await.cs` **обязан** давать CS4032-подобную ошибку. Скипается, если tree-sitter не установлен |

Плюс 2 теста структуры: `scripts/check_csharp_syntax.py` и `RELEASE_0.6.3.md` в списке
обязательных файлов.

---

## 5. Список файлов (без повторов)

### Новые (3)

`scripts/check_csharp_syntax.py` · `tests/test_hotfix_063.py` · `tests/samples/broken_await.cs`
(образец с намеренной ошибкой, лежит **вне** `unity-client/`, поэтому Unity его не видит)

### Изменённые (6)

`unity-client/Assets/LilithFace/Scripts/VrmLoader.cs` (оба красных + жёлтый) ·
`unity-client/Assets/LilithFace/LilithFace.asmdef` (`versionDefines` + `_comment` про ручные ссылки) ·
`unity-client/Assets/LilithFace/README.md` (пункт 4 про ссылки в `.asmdef`) ·
`tests/test_structure.py` (2 новых обязательных файла) · `src/lilith_core/__init__.py` (0.6.3) ·
доки: `CHANGELOG.md`, `docs/DECISIONS.md` (ADR-022), `README.md`, `../lilith/PLAN.md`,
`../lilith/PERSONA.md`, `../HANDOVER.md`

### Не тронуты — намеренно

**Все остальные 10 C#-файлов** (Кирюша просила править только `Scripts/`, и внутри них
изменён один `VrmLoader.cs`), `.unity`-сцена, `ProjectSettings`, её `.asmdef`-ссылки,
серверный Python (ни одной правки: ошибка была чисто клиентской).

---

## 6. Что делать Кирюше

```bat
:: 1. скачать LILITH-CORE_stage6_v0.6.3.zip, распаковать содержимое обёртки поверх
:: 2. в Unity скопировать ТОЛЬКО Scripts (один раз, как договаривались):
::    unity-client\Assets\LilithFace\Scripts\*.cs  →  <проект>\Assets\LilithFace\Scripts\
:: 3. .asmdef НЕ перезаписывать (там её ссылки) — но добавить два имени в versionDefines,
::    если символ LILITH_UNIVRM вдруг отвалится: com.vrmc.vrm10, com.vrmc.gltf
:: 4. дождаться компиляции
```

**Ожидаемый результат:** оба красных уходят, жёлтый CS0067 уходит вместе с ними.

**Если CS0067 останется** — значит `Loaded` по-прежнему не вызывается ни в одной
скомпилированной ветке; тогда скажи, я добавлю явный `#pragma warning disable 0067`
с комментарием (архитектор оставила это на моё усмотрение, я выбрала «по существу»).

**Проверка перед копированием (у меня в песочнице):**

```bat
.venv-ts\Scripts\python scripts\check_csharp_syntax.py     :: Файлов: 11, веток: 2, ошибок: 0
run_tests.bat                                              :: ожидаем "691 passed"
```

---

## 7. Артефакт

`artifacts/LILITH-CORE_stage6_v0.6.3.zip` — собран и проверен
`scripts/verify_stage_artifact.py --stage 6 --version 0.6.3`, распаковка прогнана тестами.

**Что дальше:** скриншот окна 512×640 (приёмка F7), ответы на Q2–Q6 спеки этапа 7,
затем «дальше» → этап 6.5 (память персон) или 7 («Руки»).
