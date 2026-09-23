# 🦇 LILITH-CORE · unity-client (этап 6)

Unity-клиент лица: окно с VRM-персоной, которое слушает продюсер
`/ws/face/producer` и говорит, моргает, улыбается и меняет тело по команде сервера.

> **Unity в песочнице не запускается** — поэтому здесь исходники скриптов и точная
> инструкция сборки. Собирает Кирюша; всё, что можно было проверить без Unity
> (протокол, аудио-конвейер, реестр персон), покрыто pytest на стороне сервера
> и эмулятором `scripts/unity_face_probe.py`.

| Параметр | Значение | Откуда |
|---|---|---|
| Unity | **6000.0.x LTS** — у Кирюши **6000.0.84f1**; за «Нейрониной» 6.1 (6000.1.9f1) не гонимся | решения C2, **Q2** |
| Render pipeline | **Built-in** (URP/HDRP не нужны) | решение C3-а |
| UniVRM | **0.131.2** (два unitypackage) | решение C1-а |
| Формат модели | **VRM 1.0** | решение C5.2 |
| Прозрачность окна | переключатель в инспекторе, дефолт **DWM** | решение C4-г |
| Аудио | raw PCM **int16 mono 24000 Гц**, чанк **2048 байт** (1024 сэмпла) | решения A1-а, A2 |
| Виземы | считает **Unity** из PCM; серверные — только по `want_server_visemes`; опция sample-accurate по позиции в буфере | решения A3.1, ADR-020 (1) |
| Сброс эмоции | по `ttl_ms` **в Unity** | решение A3.3 |

---

## 1. Что лежит в папке

```
unity-client/
├── Packages/manifest.json          ← зависимости Unity 6 (Built-in RP, без URP)
└── Assets/LilithFace/
    ├── LilithFace.asmdef           ← наша assembly + versionDefines → LILITH_UNIVRM
    ├── Scripts/
    │   ├── LilithClientConfig.cs   ← все настройки в инспекторе (URL, окно, виземы, idle)
    │   ├── LilithWSClient.cs       ← WS-транспорт на System.Net.WebSockets (без пакетов)
    │   ├── LilithFaceClient.cs     ← оркестратор: кадры → драйверы, stats раз в 5 с
    │   ├── AudioQueueProcessor.cs  ← чанки PCM → кольцевой AudioClip → AudioSource
    │   ├── VisemeDriver.cs         ← RMS + zero-crossing → aa/ih/ou/ee/oh (зеркало сервера)
    │   ├── EmotionDriver.cs        ← тег + ttl_ms → happy/angry/sad/relaxed/surprised
    │   ├── IdleController.cs       ← моргание, дыхание, взгляд за курсором
    │   ├── VrmLoader.cs            ← горячий своп model.vrm (файл или HTTP с сервера)
    │   ├── TransparentWindow.cs    ← DWM / color key / off, «справа снизу», F8·F9
    │   ├── FaceRig.cs              ← прослойка к API UniVRM (всё под #if LILITH_UNIVRM)
    │   └── MiniJson.cs             ← свой JSON-парсер (без Newtonsoft)
    ├── SCENE.md                    ← схема сцены: что куда положить (текст + SVG)
    └── README.md                   ← этот файл
```

**Внешних Unity-пакетов у клиента нет**: транспорт на встроенном `ClientWebSocket`,
JSON — свой `MiniJson`, оверлей — `OnGUI`. Единственная зависимость — UniVRM.

---

## 2. Установка (15 минут)

### 2.1. Unity
1. Unity Hub → **Installs** → поставить **Unity 6000.0.x LTS**
   (модуль *Windows Build Support* — по желанию).
2. **New project → 3D (Built-In Render Pipeline)** — шаблон без URP.
   Имя: `LilithFace`. Папка — любая, **не** внутри `Локальная Лилит`.

### 2.2. Перенести наши файлы
Скопировать в корень нового проекта:
```
unity-client/Assets/LilithFace   →   <проект>/Assets/LilithFace
unity-client/Packages/manifest.json →  <проект>/Packages/manifest.json
```
> `manifest.json` можно не копировать целиком: достаточно, чтобы в проекте были
> модули `com.unity.modules.unitywebrequest*` и `com.unity.modules.imgui`
> (они в дефолтном проекте Unity 6 уже есть).

Открыть проект — Unity скомпилирует скрипты. **Ошибок быть не должно**: весь код,
который трогает UniVRM, закрыт `#if LILITH_UNIVRM`, а до установки UniVRM этот
символ не определён.

### 2.3. UniVRM 0.131.2
1. Скачать со страницы релиза **два** файла:
   <https://github.com/vrm-c/UniVRM/releases/tag/v0.131.2>
   * `VRM-0.131.2_a471.unitypackage`
   * `UniVRM-0.131.2_a471.unitypackage`
2. В Unity: **Assets → Import Package → Custom Package…** → сначала `VRM-…`,
   затем `UniVRM-…` → *Import* (все галочки).
3. Дождаться компиляции. `LilithFace.asmdef` содержит `versionDefines` по именам
   пакетов **`com.vrmc.vrm`, `com.vrmc.univrm`, `com.vrmc.vrm10`, `com.vrmc.gltf`** —
   поэтому символ **`LILITH_UNIVRM` определится сам** и при установке unitypackage,
   и при embedded-варианте (UniGLTF / VRM / VRM-1.0 в `Packages/`).
   Если оверлей пишет «LILITH_UNIVRM не определён» — добавь вручную:
   **Edit → Project Settings → Player → Other Settings → Scripting Define Symbols**
   → `LILITH_UNIVRM`.
4. **Ссылки в `.asmdef`** (хотфикс 0.6.3). В репозитории `references` пустой —
   намеренно, иначе проект не соберётся **до** импорта UniVRM. После импорта
   проставь руками (Inspected → Assembly Definition References):
   **`UniGLTF`, `UniGLTF.Utils`, `UniHumanoid`, `VRM10`** (+ `VRM`, `MToon`,
   `MToon10.Runtime` при надобности). `UniGLTF.Utils` обязателен: там живут
   `IAwaitCaller` и `RuntimeOnlyAwaitCaller`.
   При переезде на новую версию копируется **только `Scripts/`** — твой `.asmdef`
   со ссылками не затирается.

### 2.4. Тело персоны (VRM 1.0)
* Кирюша печатает модель в **VRoid Studio** (C5.1) → экспорт **VRM 1.0**.
* В репозиторий тело **не кладём** (D5.4): git не должен таскать десятки мегабайт.
  Путь прописывается в `personas/<id>/face.yaml`:
  ```yaml
  vrm_path: "C:/Users/User/LilithBodies/lilith.vrm"   # абсолютный
  # или относительно папки персоны:
  # vrm_path: "lilith.vrm"
  ```
* Для проверки пайплайна до появления настоящего тела есть процедурный тест-куб:
  `tests/samples/` (генерируется `scripts/make_test_vrm.py`).

---

## 3. Сборка сцены

Полная схема с раскладкой по GameObject'ам — в **`SCENE.md`** (там же SVG-картинка).
Коротко:

```
LilithFace (Scene)
├── Lilith            ← пустой GameObject
│     • LilithFaceClient      (оркестратор)
│     • LilithClientConfig    (в инспекторе клиента — поля конфига)
│     • VrmLoader             (добавится сам, если не назначить)
│     • AudioSource           (добавится сам)
│     • TransparentWindow     (только для билда-оверлея)
│     └── AvatarRoot          ← пустой, сюда встанет model.vrm
├── Main Camera       ← Solid Color, альфа 0 (для прозрачности)
└── Directional Light
```

**Что назначить в инспекторе `LilithFaceClient`:**

| Поле | Значение |
|---|---|
| `Config → Url` | `ws://127.0.0.1:8765/ws/face/producer` |
| `Config → Sample Rate` | `24000` (уточнится из `hello`) |
| `Config → Chunk Bytes` | `2048` (уточнится из `hello`) |
| `Config → Use Local Visemes` | ✅ (A3.1) |
| `Config → Request Server Visemes` | ❌ (только для отладки) |
| `Config → Use Sample Accurate Visemes` | ❌ (ADR-020.1: виземы по позиции в буфере; включить вместе с `Request Server Visemes`) |
| `Config → Auto Load Body` | ✅ (**0.6.4**: грузить тело сразу после рукопожатия) |
| `Config → Request Persona On Connect` | ✅ (**0.6.4**: попросить у сервера кадр `persona` — в нём `vrm`, `window`, `idle`, `voice`) |
| `Config → Persona Frame Timeout Sec` | `2` (**0.6.4**: не дождались кадра — watchdog грузит тело по URL, построенному клиентом) |
| `Config → Transparency` | `Dwm` (C4-г) |
| `Config → Dock Bottom Right` | ✅ (требование архитектора) |
| `Avatar Root` | перетащить `AvatarRoot` |
| `Scene Camera` | `Main Camera` |
| `Show Debug Overlay` | ✅ на время настройки, потом снять |

**Камера под прозрачность** (Built-in RP):
* `Main Camera → Clear Flags = Solid Color`
* `Background = (0, 0, 0, 0)` — **альфа 0**
* `Allow MSAA = off` (MSAA ломает color key по краям)
* `TransparentWindow.PrepareCamera` сделает это сам, если назначить камеру в поле
  `Transparent Camera`.

---

## 4. Запуск и проверка (критерий готовности этапа)

```bat
:: 1. Сервер (в папке «Локальная Лилит»)
start.bat

:: 2. Проверить тракт БЕЗ Unity — эмулятор клиента
::    с 0.6.4 проба ПО УМОЛЧАНИЮ проверяет «тело доезжает» (persona_request → GET model.vrm → glTF)
python scripts\unity_face_probe.py --speak "Привет, Кирюша."
::    ожидаем строку: тело: lilith · GET 200 · N Б · magic glTF · тело доедет: URL живой, GLB валиден

:: 3. Unity: нажать Play
```

| Критерий | Как проверить |
|---|---|
| **Unity-окно подключается к WS** | оверлей: `Open · сервер 0.6.4 · персона lilith`; в логе сервера `Продюсер[unity]: подключён` |
| **Тело доезжает до сцены** (**0.6.4**, критерий F7) | в Console: `[Lilith] тело: прошу у сервера кадр персоны 'lilith'` → `[Lilith] персона: lilith · swap=True · тело=/api/face/personas/lilith/model.vrm` → `[Lilith] тело: старт свопа … ← GET http://127.0.0.1:8765/…` → `[Lilith] тело: ГОТОВО 'lilith' за N мс · M МБ · трансформов K · родитель AvatarRoot`; в Hierarchy у `AvatarRoot` — `persona_lilith`; в оверлее строка `тело lilith · …`; в логе сервера `HTTP GET /api/face/personas/lilith/model.vrm → 200 (…)` |
| **Рот шевелится от чанков TTS** | в панели нажать 🔊 или отправить `speak` из Unity; оверлей показывает `визема A · 0.62 · local` в такт речи |
| **Моргает** | смотреть на глаза; в оверлее `idle: морг 0.00→1.00` |
| **Своп персоны меняет vrm + голос + карточку** | в веб-панели выбрать другую персону в селекторе 🎭 → Unity грузит новое тело, в логе `[Lilith] персона: nova · swap=True · тело=…` |
| **Загрузка не вешает редактор** (**0.6.5**) | Play → в Console фазы `0…7` по порядку, между ними редактор отзывчив (можно двигать окно); при проблеме — красный `[Lilith] тело: ОТКАЗ …` с причиной, а не зависание. Диагноз A/B: `Use Immediate Await Caller` ✅ (загрузка в одном кадре, без next-frame планировщика) против ❌ (по кадру, плавнее) |
| **Отказ не бывает тихим** (**0.6.4**) | убрать `model.vrm` у персоны → в Console `[Lilith] тело: ОТКАЗ 'lilith' — не скачать …: 404`, в оверлее `тело нет`, в логе сервера `→ 404` |
| **OBS берёт окно с прозрачностью** | в OBS: *Window Capture* → окно `LilithFace` → на десктопе и в OBS фон прозрачный. Обязательное условие (**0.6.6**): Player Settings → Fullscreen Mode = `Fullscreen Window` и **Use Flip Model Swapchain = ❌**, иначе DWM получает непрозрачный кадр и фон чёрный (`SCENE.md` §4.5) |
| **Режим прозрачности меняется без ребилда** (**0.6.6**) | в билде нажать **F7** → в Player.log `[Lilith] F7: режим прозрачности Dwm → LayeredColorKey`, окно перестраивается на месте. F8 — вкл/выкл, F9 — в правый нижний угол, **Ctrl+Alt+Q** — выход |
| **Окно-инструмент не мешает Alt+Tab** (**0.6.6**) | `Window Tool Window = ✅` (дефолт) — окна нет ни в Alt+Tab, ни в таскбаре; снять флаг → появляется WS_EX_APPWINDOW, окно переключается как обычное приложение |
| **Оверлей не дрожит в билде** (**0.6.6**) | текст оверлея собирается один раз за кадр в `Update` и рисуется только на `EventType.Repaint` — строка `визема … · … · local` стабильна |

**Горячие клавиши билда:** `F8` — вкл/выкл прозрачность, `F9` — переставить окно
в правый нижний угол.

---

## 5. Протокол (шпаргалка)

**Сервер → клиент**

```jsonc
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
{"type":"state","producer":1,"clients":["unity"],"persona":"lilith"}
{"type":"error","code":"voice_disabled","detail":"голос выключен"}
{"type":"pong","ping_id":"p-1"}
```

**Клиент → сервер**

```jsonc
{"type":"hello","client":"unity/6000.0.21f1","want_server_visemes":false,"platform":"WindowsPlayer"}
{"type":"ready"}
{"type":"persona_request","id":"nova"}
{"type":"speak","text":"Скажи что-нибудь"}
{"type":"stats","fps":60,"dropped":0,"queued_ms":180,"playing":true,"viseme":"A","emotion":"joy"}
{"type":"ping"}
```

Групповая сцена: `ws://127.0.0.1:8765/ws/group?group=main` — первым кадром
`hello-group` с `participants[{persona,slot,position}]`, дальше те же кадры, но с
полем `persona` (мультиплексирование, E3).

---

## 6. Билд под OBS

1. **File → Build Settings** → платформа *Windows, Mac, Linux* → **Windows x86_64**.
2. **Player Settings**:
   * Company/Product: `Lilith` / `LilithFace`
   * **Resolution and Presentation**:
     * `Fullscreen Mode = Windowed`
     * `Default Screen Size = 512 × 640` (**Q5**: портрет 4:5, переопределяется `face.yaml: window`)
     * снять `Resizable Window` (иначе DWM-рамка гуляет)
   * `Color Space = Linear` — MToon-материалы VRM в Linear выглядят правильно
3. **Build** → папка `Build/LilithFace` → `LilithFace.exe`.
4. В OBS: **Sources → + → Window Capture** → `LilithFace.exe`.
   Если режим прозрачности `Off` — добавить фильтр **Color Key** с цветом из
   `Config → Color Key`.

> Окно живёт на рабочем столе справа снизу и прозрачное **само по себе**, а не
> только внутри OBS — это требование архитектора (C4-г).

---

## 7. Если что-то не так

| Симптом | Причина / лечение |
|---|---|
| `LILITH_UNIVRM не определён` | UniVRM не импортирован или `versionDefines` не сработал → добавить символ вручную (п. 2.3) |
| `на объекте нет Vrm10Instance` | модель в VRM 0.x → пересохранить в **VRM 1.0** или включить миграцию при импорте |
| Оверлей `Disconnected/Failed` | сервер не запущен, или в `Config → Url` не тот порт; проверить `http://127.0.0.1:8765/healthz` |
| Звук есть, рот не двигается | `Use Local Visemes` выключен, а `Request Server Visemes` нет → включить первое |
| Рот двигается, звука нет | в Windows микшере Unity не заглушён? `AudioSource.volume` из `Config → Volume` |
| Рот чуть отстаёт/спешит на стыках чанков | включить `Use Sample Accurate Visemes` + `Request Server Visemes` (ADR-020.1) |
| Модель с японскими морфами (`あ/い/う/え/お`) молчит | ничего настраивать не надо: `FaceRig` подхватит их как fallback; проверить `FaceRig.AvailableExpressions` |
| Рассинхрон звука при 44.1 kHz выводе | Project Settings → Audio → Sample Rate; наш клип 24 kHz, Unity ресемплит вывод сама (см. NEURONA_NOTES §6 Q1) |
| Окно не того размера | `Use Persona Window Size` включён, а в `face.yaml` персоны нет блока `window` → берётся дефолт 512×640; проверь кадр `persona.face.window` в логе при `Verbose` |
| Рот «дёрганый» на закрытии | **Q3**: проверь `Viseme Off Seconds = 0.06` и `Viseme Cubic Out = ✅`; если On/Off = 0, работает старый low-pass `Viseme Smoothing` |
| Окно не прозрачное | режим `Dwm` не сработал на этой сборке Windows → попробовать `LayeredColorKey`; в OBS — фильтр Color Key |
| `не скачать VRM: 404` | тело не лежит по пути из `face.yaml`; проверить `GET /api/face/personas/<id>/model.vrm` |
| Всё работает, но тормозит | `Config → Verbose` выключить (логи в рантайме дорогие), `Stats Interval` поднять до 10 |

---

## 8. Что здесь сознательно НЕ сделано

* **Unity-сцена группового режима** (E7): серверная часть `/ws/group` готова и
  тестируется, сцена с несколькими персонами и фокус-камерой — следующий шаг.
* **Реальная загрузка LoRA** (D10-а): слот описан, бэкенды — стабы, работает
  prompt-only. Полноценная переделка памяти персон — **этап 6.5** (D7).
* **C# EditMode-тесты** (F6-в): бонус, если останется время; серверная половина
  и так покрыта 70+ тестами.
