# 🎬 SCENE.md — схема сцены Unity-клиента лица (этап 6)

Картинка: **`SCENE.svg`** рядом с этим файлом (открывается в браузере или в превью).
Ниже — то же текстом, чтобы можно было читать без картинок и копипастить в чат.

---

## 1. Иерархия сцены

```
LilithFace (Scene)
│
├── Lilith                     ← ПУСТОЙ GameObject, «мозг» клиента
│   ├── LilithFaceClient       ← оркестратор (единственный обязательный компонент)
│   │   • Config: LilithClientConfig (inline в инспекторе)
│   │   • Audio Source: (пусто → создаст сам)
│   │   • Vrm Loader:   (пусто → добавит сам на этот же объект)
│   │   • Avatar Root:  → AvatarRoot
│   │   • Scene Camera: → Main Camera
│   │   • Show Debug Overlay: ✅ (на время настройки)
│   │
│   ├── VrmLoader              ← горячий своп model.vrm
│   │   • Root: AvatarRoot
│   │   • Local Position: (0, 0, 0)
│   │   • Local Euler Angles: (0, 180, 0)   ← VRM смотрит в +Z, камера смотрит в +Z
│   │   • Server Base Url: http://127.0.0.1:8765
│   │   • Download Timeout Sec: 60
│   │   • Await Timeout: 0.001              ← RuntimeOnlyAwaitCaller (0.6.3)
│   │   • Load Timeout Seconds: 60          ← 0.6.5: 0 = ждать вечно
│   │   • Load From Server Only: ❌         ← 0.6.5: форсировать HTTP-ветку
│   │   • Use Immediate Await Caller: ❌    ← 0.6.5: загрузка в одном кадре (диагноз/авария)
│   │
│   ├── AudioSource            ← создаётся кодом: loop=true, playOnAwake=false, 2D
│   │
│   ├── TransparentWindow      ← только для билда-оверлея (в Editor не работает)
│   │   • Config: тот же LilithClientConfig
│   │   • Transparent Camera: Main Camera
│   │   • Topmost: ✅   Borderless: ✅   Apply On Start: ✅
│   │   • Хоткеи (0.6.6): F7 — сменить режим прозрачности ЖИВЬЁМ (A/B без ребилда)
│   │                     F8 — вкл/выкл прозрачность, F9 — в правый нижний угол
│   │                     Ctrl+Alt+Q — выход (окно без рамок не имеет крестика)
│   │
│   └── AvatarRoot             ← ПУСТОЙ: сюда VrmLoader поставит persona_<id>
│
├── Main Camera
│   • Clear Flags: Solid Color
│   • Background: RGBA (0, 0, 0, 0)          ← альфа 0 = прозрачность (DWM)
│   • Allow MSAA: ❌                          ← иначе color key рвёт края
│   • Projection: Perspective, FOV 30
│   • Position: подбирается FrameAvatar() под рост модели
│
└── Directional Light
    • Rotation: (50, -30, 0)
    • Color: чуть тёплый (255, 244, 214) — MToon любит мягкий свет
```

---

## 2. Поля `LilithClientConfig` (инспектор)

| Раздел | Поле | Значение | Зачем |
|---|---|---|---|
| Соединение | `Url` | `ws://127.0.0.1:8765/ws/face/producer` | продюсер лица (`/ws/unity` — алиас) |
| | `Group Url` | `ws://127.0.0.1:8765/ws/group?group=main` | групповая сцена (пока не используется) |
| | `Auto Reconnect` | ✅ | сервер перезапускается — клиент вернётся сам |
| | `Reconnect Delay` / `Max Delay` | `2` / `30` | экспоненциальный backoff |
| Аудио | `Sample Rate` | `24000` | уточняется из `hello` (A2) |
| | `Chunk Bytes` | `2048` | уточняется из `hello` (A1-а) |
| | `Volume` | `1` | громкость AudioSource |
| | `Max Buffered Seconds` | `3` | запас до дропа старых чанков |
| Виземы | `Use Local Visemes` | ✅ | основной путь (A3.1) |
| | `Request Server Visemes` | ❌ | фолбэк: серверная разметка |
| | `Use Sample Accurate Visemes` | ❌ | ADR-020.1: виземы по позиции в буфере (приём «Нейроны») |
| | `Silence Threshold` | `0.012` | порог «рот закрыт» |
| | `Viseme Smoothing` | `0.55` | фолбэк low-pass (работает, если On/Off = 0) |
| | `Viseme On Seconds` | `0.08` | **Q3/ADR-021**: время открытия виземы (SALSA timings On) |
| | `Viseme Off Seconds` | `0.06` | **Q3/ADR-021**: время закрытия (SALSA timings Off) |
| | `Viseme Cubic Out` | ✅ | **Q3**: easing `1-(1-t)³`, как у SALSA |
| Эмоции | `Emotion Scale` | `1` | множитель веса |
| | `Emotion Fade Seconds` | `0.25` | плавность сведения |
| Простой | `Blink Frequency` | `0.28` | переопределяется `face.yaml:idle.blink_freq` |
| | `Breath Amplitude` | `0.5` | переопределяется `face.yaml:idle.breath_amp` |
| | `Look Speed` | `4` | переопределяется `face.yaml:idle.look_speed` |
| | `Look At Cursor` | ✅ | взгляд за мышью (для стрима) |
| Тело (**0.6.4**) | `Auto Load Body` | ✅ | добиваться тела сразу после `hello`: сервер сам кадр `persona` не шлёт (A5) |
| | `Request Persona On Connect` | ✅ | слать `persona_request` — в кадре приезжают `vrm`, `face.window`, `idle`, `voice` |
| | `Persona Frame Timeout Sec` | `2` | watchdog: кадр не пришёл → грузим по `{ServerBaseUrl}/api/face/personas/{id}/model.vrm` |
| Окно | `Transparency` | `Dwm` | C4-г: `Dwm` / `LayeredColorKey` / `Off` |
| | `Color Key` | `(255, 0, 255)` | для режима `LayeredColorKey` |
| | `Window Size` | `512 × 640` | **Q5**: портрет 4:5 под OBS |
| | `Use Persona Window Size` | ✅ | **Q5**: размер из `face.yaml: window` персоны |
| | `Dock Bottom Right` | ✅ | требование: справа снизу на рабочем столе |
| | `Window Tool Window` | ✅ | **0.6.6**: ✅ — окна нет в Alt+Tab и таскбаре (WS_EX_TOOLWINDOW); ❌ — WS_EX_APPWINDOW, переключается как обычное приложение |
| | `Show Window Frame` | ❌ | **0.6.6**: ✅ — оставить заголовок/рамку/крестик (прозрачность при этом не работает). Основной способ закрытия — Ctrl+Alt+Q |
| | `Close Hotkey Enabled` | ✅ | **0.6.6**: закрывать приложение хоткеем |
| | `Close Hotkey` | `Q` | **0.6.6**: клавиша, срабатывает вместе с Ctrl+Alt |
| | `Window Margin` | `24` | отступ от краёв экрана |
| Служебное | `Stats Interval` | `5` | `stats` на сервер раз в 5 с (A5) |
| | `Verbose` | ❌ | подробный лог кадров (дорого в рантайме) |

---

## 3. Поток данных

```
[Python] voice/tts.py ── WAV по предложениям
            │
            ▼
[Python] voice/pcm.py ── resample_pcm16(→24 kHz) → PcmChunker(2048 Б)
            │
            ▼
[Python] face/producer.py ── FaceProducerHub.broadcast(audio/viseme/emotion/…)
            │
            ▼  WS  /ws/face/producer   (плоские JSON-кадры, A4-а)
            │
[Unity]  LilithWSClient ── приём в фоновой задаче → очередь
            │
            ▼  Update() (главный поток: Unity API трогать только здесь)
[Unity]  LilithFaceClient.HandleFrame
            ├── "hello"    → ActivePersona + EnsureBodyOnConnect("hello")   ← хотфикс 0.6.4
            │                     ├── persona_request{id}  → сервер шлёт кадр "persona"
            │                     └── watchdog 2 с: кадр не пришёл →
            │                            VrmLoader.Swap(id, "")  → URL строим сами
            ├── "audio"    → AudioQueueProcessor.Enqueue(pcm, utterance_id)
            │                     └── кольцевой AudioClip.SetData → AudioSource.Play
            ├── "viseme"   → VisemeDriver.ApplyServerMark      (только если просили)
            ├── "emotion"  → EmotionDriver.Apply(tag, intensity, ttl_ms)
            ├── "persona"  → if (swap) VrmLoader.Swap(id, face.vrm_path, vrm)
            │                     ├── vrm пуст → ModelUrlFor(id) (0.6.4)
            │                     ├── та же персона уже на сцене → пропуск (гвард)
            │                     └── Vrm10.LoadBytesAsync → Vrm10Instance
            │                            └── FaceRig.Bind(model) + LastBody в оверлей
            ├── "focus"    → FocusedPersona (камера — на стороне Unity, E6)
            ├── "stop"     → AudioQueueProcessor.Stop(utterance_id) + Visemes.Reset
            ├── "done"     → AudioQueueProcessor.MarkUtteranceEnd
            └── "state"/"error"/"pong" → лог

[Unity]  каждый кадр:
            Audio.Tick → Idle.Speaking → Visemes.Tick (RMS+ZCR из PCM)
                       → Emotions.Tick (ttl_ms) → Idle.Tick (моргание/дыхание/взгляд)
            LateUpdate:  Idle.LateTick → FaceRig.ApplyLook
            раз в 5 с:   stats {fps, dropped, queued_ms, playing, viseme, emotion}
```

---

## 4. Почему именно так

| Решение | Причина |
|---|---|
| Кольцевой `AudioClip` + `SetData`, а не очередь из кусочков | нет щелчков на стыках чанков; `stop` дропает реплику мгновенно |
| Виземы в Unity, а не на сервере | A3.1: меньше трафика (12 кадров/с → 0), задержка ниже, сервер не знает про плеер |
| Алгоритм RMS + zero-crossing, как в `face/visemes.py` | рот шевелится одинаково в панели и в Unity — легко сверять |
| Свой `MiniJson`, а не `JsonUtility`/Newtonsoft | кадры плоские, но состав полей меняется (hello/persona/state); внешних пакетов нет |
| `System.Net.WebSockets.ClientWebSocket` | встроен в Unity 6 (Mono), не нужен NativeWebSocket/websocket-sharp |
| Всё VRM-API под `#if LILITH_UNIVRM` | проект компилируется ДО установки UniVRM: можно проверить соединение и сцену |
| `versionDefines` в `.asmdef` | символ `LILITH_UNIVRM` включается сам при появлении пакета `com.vrmc.vrm` |
| `TransparentWindow` — три режима | DWM красив, но капризен на старых сборках Windows; color key — надёжный фолбэк |
| Камера `Solid Color` + альфа 0 |Built-in RP отдаёт альфу в окно только так; `Skybox` даёт непрозрачный фон |

---

## 4.5. Player Settings — ОБЯЗАТЕЛЬНО для прозрачности в билде (0.6.6)

Замер Г показал: режим `Dwm` применялся (`прозрачность окна: Dwm (hwnd=…)` в Player.log),
но фон на Win10 19045 оставался **чёрным непрозрачным**. Причина не в Win32-коде,
а в двух настройках плеера: Unity 6 по умолчанию отдаёт кадр через **flip model
swapchain**, и альфа-канал до DWM не доезжает.

| Где | Настройка | Значение | Зачем |
|---|---|---|---|
| Resolution and Presentation | **Fullscreen Mode** | `Fullscreen Window` | в Exclusive Fullscreen DWM-композиции нет вовсе |
| Other Settings | **Use Flip Model Swapchain** | ❌ **снять** | flip-model отдаёт непрозрачный кадр — отсюда чёрный фон |
| Quality | **Anti Aliasing** | `Disabled` | код и сам ставит `QualitySettings.antiAliasing = 0`, но в настройках надёжнее; MSAA размывает альфу по краям |
| Graphics | **Always Included Shaders** | 6 старых + `Standard` + `UniGLTF/UniUnlit` | без этого в билде розовые/чёрные материалы (найдено на замере Г) |
| Graphics | **Always Included Shaders** += `MToon10` | когда приедет настоящая VRoid-модель | процедурному кубику MToon не нужен |
| Splash Image | **Show Splash Screen** | ❌ (Unity Plus/Pro) | пункт 5 донесения: сплэш Unity — это лицензионное окно плеера, оно появляется до нашей сцены и закрывается само. На Unity Personal отключить нельзя — это **норма**, не баг |

Если после этих настроек фон всё равно чёрный: в билде нажать **F7** и посмотреть,
в каком режиме стекло появляется (лог пишет каждую смену режима).

---

## 5. Порядок сборки (чек-лист)

- [ ] Unity Hub → Unity **6000.0.x LTS**
- [ ] New project → **3D (Built-In Render Pipeline)**
- [ ] Скопировать `Assets/LilithFace` в проект
- [ ] Unity скомпилировал без ошибок (UniVRM ещё нет — это нормально)
- [ ] Создать иерархию по разделу 1
- [ ] Назначить поля по разделу 2
- [ ] Импортировать **VRM-0.131.2** и **UniVRM-0.131.2** unitypackage
- [ ] Убедиться, что `LILITH_UNIVRM` определился (оверлей перестаёт ругаться)
- [ ] Запустить сервер: `start.bat`
- [ ] Прогнать `python scripts/unity_face_probe.py --url ws://127.0.0.1:8765/ws/face/producer --speak "Привет."`
- [ ] Unity → **Play** → оверлей `Open · сервер 0.6.0 · персона lilith`
- [ ] В панели нажать 🔊 → рот шевелится, звук идёт
- [ ] Положить `lilith.vrm`, прописать `vrm_path` в `face.yaml` → своп в селекторе 🎭 меняет тело
- [ ] Player Settings по разделу 4.5 (**Fullscreen Window** + **Use Flip Model Swapchain = ❌**)
- [ ] Build → Windows x86_64 → `LilithFace.exe` → окно прозрачное, справа снизу
- [ ] В Player.log есть строка `прозрачность окна: <режим> (hwnd=…) · toolWindow=… · frame=…`
- [ ] Ctrl+Alt+Q закрывает окно; F7 меняет режим прозрачности живьём
- [ ] OBS → Window Capture → фон прозрачный
- [ ] Скриншот Кирюши = приёмка этапа (F7)
