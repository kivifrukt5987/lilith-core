// LILITH-CORE · Unity-клиент лица (этап 6)
// Оркестратор: держит соединение с продюсером, разбирает кадры и раздаёт их
// драйверам (аудио → виземы → эмоции → простой). Единственный компонент,
// который нужно повесить на сцену вручную; остальное он соберёт сам.
//
// Порядок кадров от сервера (см. src/lilith_core/face/ws_frames.py):
//   hello → [persona] → audio* → viseme* → emotion* → done
//   stop — прервать реплику;  focus — на кого смотрит камера;  state — служебное.
//
// Клиент → сервер (A5): hello, ready, persona_request, speak, stats (раз в 5 с), ping.

using System.Collections;
using System.Collections.Generic;
using UnityEngine;

namespace Lilith.Face
{
    /// <summary>
    /// Точка входа Unity-клиента лица. Повесить на пустой GameObject «Lilith»,
    /// рядом — AudioSource (создаётся сам, если не назначен) и VrmLoader.
    /// </summary>
    [DisallowMultipleComponent]
    public class LilithFaceClient : MonoBehaviour
    {
        [Header("Компоненты")]
        [Tooltip("Конфиг подключения и поведения. Если пусто — создастся дефолтный.")]
        public LilithClientConfig config;

        [Tooltip("Источник звука. Если пусто — создадим сами.")]
        public AudioSource audioSource;

        [Tooltip("Загрузчик VRM. Если пусто — добавим на этот же объект.")]
        public VrmLoader vrmLoader;

        [Tooltip("Трансформ-родитель для тела персоны.")]
        public Transform avatarRoot;

        [Tooltip("Камера сцены (для взгляда за курсором и прозрачности).")]
        public Camera sceneCamera;

        [Tooltip("Показывать отладочный оверлей (OnGUI) — режим/визема/эмоция/буфер.")]
        public bool showDebugOverlay = true;

        [Header("Поведение")]
        [Tooltip("Подключаться к серверу на старте.")]
        public bool connectOnStart = true;

        [Tooltip("Слать speak-запросы можно и из Unity (например, по хоткею).")]
        public bool sendReadyOnConnect = true;

        /// <summary>Прослойка к телу (доступна после инициализации).</summary>
        public FaceRig Rig { get; private set; }

        /// <summary>Плеер аудио-чанков.</summary>
        public AudioQueueProcessor Audio { get; private set; }

        /// <summary>Драйвер визем.</summary>
        public VisemeDriver Visemes { get; private set; }

        /// <summary>Драйвер эмоций.</summary>
        public EmotionDriver Emotions { get; private set; }

        /// <summary>Контроллер простоя (моргание/дыхание/взгляд).</summary>
        public IdleController Idle { get; private set; }

        /// <summary>Активная персона (из кадра hello/persona).</summary>
        public string ActivePersona { get; private set; } = "";

        /// <summary>Кто сейчас в фокусе (групповая сцена, E2-а).</summary>
        public string FocusedPersona { get; private set; } = "";

        /// <summary>Состояние транспорта.</summary>
        public WsState State
        {
            get { return _client != null ? _client.State : WsState.Disconnected; }
        }

        /// <summary>Последняя ошибка соединения.</summary>
        public string LastError
        {
            get { return _client != null ? _client.LastError : ""; }
        }

        /// <summary>Версия сервера из hello.</summary>
        public string ServerVersion { get; private set; } = "";

        private LilithWSClient _client;
        private System.Action<WsState> _onStateChanged;
        private readonly Queue<System.Action> _mainThread = new Queue<System.Action>();
        private float _statsTimer;
        private float _pingTimer;
        private int _framesHandled;
        private int _audioFrames;
        private int _droppedAudio;
        private string _lastUtterance = "";

        private void Awake()
        {
            if (config == null)
            {
                config = new LilithClientConfig();
            }

            if (audioSource == null)
            {
                audioSource = gameObject.AddComponent<AudioSource>();
                audioSource.playOnAwake = false;
                audioSource.spatialBlend = 0f;
            }

            audioSource.volume = config.volume;

            if (vrmLoader == null)
            {
                vrmLoader = GetComponent<VrmLoader>() ?? gameObject.AddComponent<VrmLoader>();
            }

            vrmLoader.root = avatarRoot != null ? avatarRoot : transform;
            vrmLoader.serverBaseUrl = HttpBaseUrlFrom(config.url);
            vrmLoader.Loaded += OnVrmLoaded;
            vrmLoader.Failed += OnVrmFailed;

            Rig = new FaceRig();
            Audio = new AudioQueueProcessor(audioSource, config.sampleRate, config.maxBufferedSeconds);
            Visemes = new VisemeDriver(Rig, config.sampleRate)
            {
                SilenceThreshold = config.silenceThreshold,
                Smoothing = config.visemeSmoothing,
                OpenSeconds = config.visemeOnSeconds,
                CloseSeconds = config.visemeOffSeconds,
                CubicOut = config.visemeCubicOut,
            };
            Emotions = new EmotionDriver(Rig)
            {
                Scale = config.emotionScale,
                FadeSeconds = config.emotionFadeSeconds,
            };
            Idle = new IdleController(Rig)
            {
                BlinkFrequency = config.blinkFrequency,
                BreathAmplitude = config.breathAmplitude,
                LookSpeed = config.lookSpeed,
                LookAtCursor = config.lookAtCursor,
                ViewCamera = sceneCamera != null ? sceneCamera : Camera.main,
            };

            if (Rig != null && Idle != null)
            {
                Rig.LookTarget = (Idle.ViewCamera != null ? Idle.ViewCamera.transform.position : Vector3.forward) + Vector3.forward;
            }
        }

        private void Start()
        {
            if (connectOnStart)
            {
                Connect();
            }
        }

        /// <summary>Подключиться к продюсеру лица.</summary>
        public void Connect()
        {
            Disconnect();
            _client = new LilithWSClient(config.url, config.autoReconnect, config.reconnectDelay, config.reconnectMaxDelay);
            _onStateChanged = state => Enqueue(() => OnStateChanged(state));
            _client.StateChanged += _onStateChanged;
            _client.Connect();
        }

        /// <summary>Разорвать соединение.</summary>
        public void Disconnect()
        {
            if (_client != null)
            {
                if (_onStateChanged != null)
                {
                    _client.StateChanged -= _onStateChanged;
                }

                _client.Dispose();
                _client = null;
            }
        }

        private void OnStateChanged(WsState state)
        {
            if (config.verbose)
            {
                Debug.Log($"[Lilith] WS: {state}");
            }

            if (state == WsState.Open)
            {
                SendClientHello();
            }
        }

        /// <summary>Рукопожатие клиента (A5): сразу говорим, нужны ли серверные виземы.</summary>
        public void SendClientHello()
        {
            Send(new Dictionary<string, object>
            {
                { "type", "hello" },
                { "client", $"unity/{Application.unityVersion}" },
                { "want_server_visemes", config.requestServerVisemes },
                { "platform", Application.platform.ToString() },
            });

            if (sendReadyOnConnect)
            {
                Send(new Dictionary<string, object> { { "type", "ready" } });
            }
        }

        /// <summary>Попросить сервер сменить персону (D9: клиент — один из инициаторов).</summary>
        public void RequestPersona(string personaId)
        {
            Send(new Dictionary<string, object>
            {
                { "type", "persona_request" },
                { "id", personaId },
            });
        }

        /// <summary>Попросить сервер сказать текст (удобно для тестов и хоткеев).</summary>
        public void Speak(string text, string personaId = "")
        {
            var frame = new Dictionary<string, object> { { "type", "speak" }, { "text", text } };
            if (!string.IsNullOrEmpty(personaId))
            {
                frame["persona"] = personaId;
            }

            Send(frame);
        }

        private void Send(Dictionary<string, object> frame)
        {
            if (_client == null)
            {
                return;
            }

            _client.Send(MiniJson.Serialize(frame));
        }

        private void Enqueue(System.Action action)
        {
            lock (_mainThread)
            {
                _mainThread.Enqueue(action);
            }
        }

        private void Update()
        {
            DrainMainThreadQueue();
            DrainIncoming();

            var dt = Time.deltaTime;
            Audio.Tick(dt);
            Idle.Speaking = Audio.IsPlaying;
            if (config.useSampleAccurateVisemes)
            {
                // ADR-020.1: виземы встают по позиции в аудио-буфере, а не по такту Update.
                Visemes.TickFromQueue(Audio.DequeueDueVisemes(), dt);
            }
            else
            {
                Visemes.Tick(Audio, dt, config.useLocalVisemes);
            }

            Emotions.Tick(dt);
            Idle.Tick(dt);

            HandleStats(dt);
        }

        private void LateUpdate()
        {
            Idle.LateTick();
        }

        private void DrainMainThreadQueue()
        {
            lock (_mainThread)
            {
                while (_mainThread.Count > 0)
                {
                    var action = _mainThread.Dequeue();
                    try
                    {
                        action();
                    }
                    catch (System.Exception ex)
                    {
                        Debug.LogWarning($"[Lilith] действие в главном потоке упало: {ex.Message}");
                    }
                }
            }
        }

        private void DrainIncoming()
        {
            if (_client == null)
            {
                return;
            }

            // Ограничиваем число кадров на Update, чтобы долгая очередь не уронила fps.
            for (var i = 0; i < 64 && _client.TryDequeueIncoming(out var json); i++)
            {
                HandleFrame(json);
            }
        }

        private void HandleFrame(string json)
        {
            var frame = MiniJson.Deserialize(json) as Dictionary<string, object>;
            if (frame == null)
            {
                return;
            }

            _framesHandled++;
            var type = GetString(frame, "type");
            switch (type)
            {
                case "hello":
                    OnHello(frame);
                    break;
                case "hello-group":
                    OnHello(frame);
                    OnGroupLayout(frame);
                    break;
                case "audio":
                    OnAudio(frame);
                    break;
                case "viseme":
                    OnServerViseme(frame);
                    break;
                case "emotion":
                    OnEmotion(frame);
                    break;
                case "persona":
                    OnPersona(frame);
                    break;
                case "focus":
                    OnFocus(frame);
                    break;
                case "stop":
                    OnStop(frame);
                    break;
                case "done":
                    OnDone(frame);
                    break;
                case "layout":
                    OnGroupLayout(frame);
                    break;
                case "state":
                    OnState(frame);
                    break;
                case "error":
                    Debug.LogWarning($"[Lilith] ошибка сервера: {GetString(frame, "detail")} ({GetString(frame, "code")})");
                    break;
                case "pong":
                    break;
                default:
                    if (config.verbose)
                    {
                        Debug.Log($"[Lilith] неизвестный кадр: {type}");
                    }

                    break;
            }
        }

        // -- обработчики кадров ---------------------------------------------------- //
        private void OnHello(Dictionary<string, object> frame)
        {
            var sampleRate = GetInt(frame, "sample_rate", config.sampleRate);
            var chunkBytes = GetInt(frame, "chunk_bytes", config.chunkBytes);
            ServerVersion = GetString(frame, "server_version");
            ActivePersona = GetString(frame, "persona");

            if (sampleRate != config.sampleRate || chunkBytes != config.chunkBytes)
            {
                config.sampleRate = sampleRate;
                config.chunkBytes = chunkBytes;
                Audio.Reconfigure(sampleRate, config.maxBufferedSeconds);
                Visemes = new VisemeDriver(Rig, sampleRate)
                {
                    SilenceThreshold = config.silenceThreshold,
                    Smoothing = config.visemeSmoothing,
                    OpenSeconds = config.visemeOnSeconds,
                    CloseSeconds = config.visemeOffSeconds,
                    CubicOut = config.visemeCubicOut,
                };
            }

            if (config.verbose)
            {
                Debug.Log($"[Lilith] hello: сервер {ServerVersion}, {sampleRate} Гц, чанк {chunkBytes} Б, персона {ActivePersona}");
            }
        }

        private void OnAudio(Dictionary<string, object> frame)
        {
            var data = GetString(frame, "data");
            if (string.IsNullOrEmpty(data))
            {
                return;
            }

            byte[] pcm;
            try
            {
                pcm = System.Convert.FromBase64String(data);
            }
            catch (System.FormatException)
            {
                _droppedAudio++;
                return;
            }

            var utterance = GetString(frame, "utterance_id");
            if (!string.IsNullOrEmpty(_lastUtterance) && utterance != _lastUtterance)
            {
                // Новая реплика: предыдущая больше не нужна (сервер мог прислать stop).
                Visemes.Reset();
            }

            _lastUtterance = utterance;
            _audioFrames++;
            if (config.useSampleAccurateVisemes && config.useLocalVisemes)
            {
                // Локальный анализ в момент приёма чанка: знаем точную позицию в буфере.
                var startSample = Audio.EnqueuedSamples;
                Audio.Enqueue(pcm, utterance);
                QueueLocalVisemesForChunk(pcm, startSample, utterance);
                return;
            }

            Audio.Enqueue(pcm, utterance);
        }

        private void OnServerViseme(Dictionary<string, object> frame)
        {
            if (!config.requestServerVisemes)
            {
                return;
            }

            var code = GetString(frame, "code");
            var intensity = GetFloat(frame, "intensity", 0f);
            if (config.useSampleAccurateVisemes)
            {
                // offset_ms сервера пересчитывается в абсолютную позицию в буфере.
                Audio.EnqueueViseme(code, intensity, GetInt(frame, "offset_ms", 0), GetString(frame, "utterance_id"));
                return;
            }

            Visemes.ApplyServerMark(code, intensity);
        }

        /// <summary>
        /// ADR-020.1 + A3.1: локальный анализ чанка, но привязка к позиции в буфере.
        /// Окно 60 мс как на сервере; каждая корзина ставится в очередь на свой сэмпл.
        /// </summary>
        private void QueueLocalVisemesForChunk(byte[] pcm, long startSample, string utteranceId)
        {
            var windowSamples = Mathf.Max(64, config.sampleRate * 60 / 1000);
            var samples = new float[pcm.Length / 2];
            for (var i = 0; i < samples.Length; i++)
            {
                short value = (short)(pcm[i * 2] | (pcm[i * 2 + 1] << 8));
                samples[i] = value / 32768f;
            }

            for (var offset = 0; offset + windowSamples <= samples.Length; offset += windowSamples)
            {
                var window = new float[windowSamples];
                System.Array.Copy(samples, offset, window, 0, windowSamples);
                var code = ClassifyWindow(window);
                Audio.EnqueueVisemeAt(startSample + offset, code.Item1, code.Item2, utteranceId);
            }
        }

        /// <summary>RMS + zero-crossing → (код виземы, интенсивность). Зеркало сервера.</summary>
        private static System.Tuple<string, float> ClassifyWindow(float[] window)
        {
            double sum = 0.0;
            for (var i = 0; i < window.Length; i++)
            {
                sum += window[i] * (double)window[i];
            }

            var amp = (float)System.Math.Sqrt(sum / window.Length);
            if (amp < 0.012f)
            {
                return System.Tuple.Create("rest", 0f);
            }

            var crossings = 0;
            for (var i = 1; i < window.Length; i++)
            {
                if ((window[i - 1] < 0f) != (window[i] < 0f))
                {
                    crossings++;
                }
            }

            var zcr = crossings / (float)(window.Length - 1);
            var intensity = Mathf.Clamp01(amp / 0.35f);
            string code;
            if (zcr < 0.10f)
            {
                code = amp > 0.16f ? "A" : "O";
            }
            else if (zcr < 0.20f)
            {
                code = "E";
            }
            else if (zcr < 0.32f)
            {
                code = amp < 0.18f ? "I" : "E";
            }
            else
            {
                code = "I";
            }

            if (amp > 0.10f && zcr > 0.06f && zcr < 0.12f)
            {
                code = "U";
            }

            return System.Tuple.Create(code, intensity);
        }

        private void OnEmotion(Dictionary<string, object> frame)
        {
            Emotions.Apply(
                GetString(frame, "tag"),
                GetFloat(frame, "intensity", 1f),
                GetInt(frame, "ttl_ms", 4000));
        }

        private void OnPersona(Dictionary<string, object> frame)
        {
            var personaId = GetString(frame, "id");
            if (string.IsNullOrEmpty(personaId))
            {
                return;
            }

            ActivePersona = personaId;
            Emotions.Reset();
            Visemes.Reset();

            var vrmUrl = GetString(frame, "vrm");
            var localPath = "";
            var face = frame.TryGetValue("face", out var faceObj) ? faceObj as Dictionary<string, object> : null;
            if (face != null)
            {
                localPath = GetString(face, "vrm_path");
                ApplyPersonaWindow(face);
                var idle = face.TryGetValue("idle", out var idleObj) ? idleObj as Dictionary<string, object> : null;
                if (idle != null)
                {
                    Idle.ApplyPersonaIdle(
                        GetFloat(idle, "blink_freq", config.blinkFrequency),
                        GetFloat(idle, "breath_amp", config.breathAmplitude),
                        GetFloat(idle, "look_speed", config.lookSpeed));
                }
            }

            if (GetString(frame, "swap") != "" || frame.ContainsKey("swap"))
            {
                vrmLoader.Swap(personaId, localPath, vrmUrl);
            }

            if (config.verbose)
            {
                Debug.Log($"[Lilith] персона: {personaId}, vrm={vrmUrl ?? localPath}");
            }
        }

        /// <summary>
        /// Q5/ADR-021: размер окна берётся из ``face.yaml: window`` персоны и
        /// применяется к ``TransparentWindow`` (портрет 4:5 по умолчанию).
        /// </summary>
        private void ApplyPersonaWindow(Dictionary<string, object> face)
        {
            if (!config.usePersonaWindowSize)
            {
                return;
            }

            var window = face.TryGetValue("window", out var windowObj) ? windowObj as Dictionary<string, object> : null;
            if (window == null)
            {
                return;
            }

            var width = GetInt(window, "width", config.windowSize.x);
            var height = GetInt(window, "height", config.windowSize.y);
            if (width <= 0 || height <= 0)
            {
                return;
            }

            config.windowSize = new Vector2Int(width, height);
            var transparent = GetComponent<TransparentWindow>();
            if (transparent != null)
            {
                transparent.ApplyWindowSize();
            }
        }

        private void OnFocus(Dictionary<string, object> frame)
        {
            FocusedPersona = GetString(frame, "persona");
            // Камера — на стороне Unity (E6): здесь только запоминаем, кого показать крупно.
            if (config.verbose)
            {
                Debug.Log($"[Lilith] фокус: {FocusedPersona}");
            }
        }

        private void OnStop(Dictionary<string, object> frame)
        {
            var utterance = GetString(frame, "utterance_id");
            Audio.Stop(utterance);
            Visemes.Reset();
            _lastUtterance = "";
        }

        private void OnDone(Dictionary<string, object> frame)
        {
            Audio.MarkUtteranceEnd(GetString(frame, "utterance_id"));
        }

        private void OnGroupLayout(Dictionary<string, object> frame)
        {
            // E4: состав и рассадка групповой сцены. Unity-сцена группы — следующий шаг,
            // здесь только логируем, чтобы контракт был виден в рантайме.
            if (config.verbose && frame.TryGetValue("participants", out var participants))
            {
                Debug.Log($"[Lilith] группа: {MiniJson.Serialize(participants)}");
            }
        }

        private void OnState(Dictionary<string, object> frame)
        {
            if (!config.verbose)
            {
                return;
            }

            Debug.Log($"[Lilith] state: {MiniJson.Serialize(frame)}");
        }

        private void OnVrmLoaded(string personaId, GameObject model)
        {
            Rig.Bind(model);
            Idle.CaptureBreathOrigin(FindChest(model.transform));
            if (sceneCamera != null)
            {
                FrameAvatar(model, sceneCamera);
            }
        }

        private void OnVrmFailed(string personaId, VrmLoadResult result)
        {
            Rig.Unbind();
            Debug.LogWarning($"[Lilith] тело '{personaId}' не загружено: {result} — {vrmLoader.LastError}");
        }

        // -- stats / ping (A5) ----------------------------------------------------- //
        private void HandleStats(float deltaTime)
        {
            if (_client == null)
            {
                return;
            }

            _pingTimer += deltaTime;
            if (_pingTimer >= 15f)
            {
                _pingTimer = 0f;
                Send(new Dictionary<string, object> { { "type", "ping" } });
            }

            _statsTimer += deltaTime;
            if (_statsTimer < Mathf.Max(1f, config.statsInterval))
            {
                return;
            }

            _statsTimer = 0f;
            Send(new Dictionary<string, object>
            {
                { "type", "stats" },
                { "fps", Mathf.RoundToInt(1f / Mathf.Max(0.0001f, deltaTime)) },
                { "dropped", _droppedAudio + (int)(_client != null ? _client.DroppedFrames : 0) },
                { "queued_ms", Audio.BufferedMs },
                { "playing", Audio.IsPlaying },
                { "viseme", Visemes.Current.ToString() },
                { "emotion", Emotions.Tag },
                { "persona", ActivePersona },
            });
        }

        private void OnGUI()
        {
            if (!showDebugOverlay)
            {
                return;
            }

            var text =
                $"{State} · сервер {ServerVersion} · персона {ActivePersona}\n" +
                $"аудио {Audio.BufferedMs} мс · {_audioFrames} чанков · потерь {Audio.ChunksDropped}\n" +
                $"{Visemes.Describe()}\n{Emotions.Describe()}\n{Idle.Describe()}";

            if (!string.IsNullOrEmpty(LastError))
            {
                text += $"\nошибка: {LastError}";
            }

            GUI.Label(new Rect(8, 8, Screen.width - 16, 120), text);
        }

        // -- вспомогательное ------------------------------------------------------- //
        private static Transform FindChest(Transform root)
        {
            if (root == null)
            {
                return null;
            }

            var animator = root.GetComponentInChildren<Animator>();
            if (animator != null && animator.isHuman)
            {
                var chest = animator.GetBoneTransform(HumanBodyBones.Chest);
                if (chest != null)
                {
                    return chest;
                }
            }

            var byName = root.Find("J_Bip_C_Chest");
            return byName != null ? byName : root;
        }

        /// <summary>Подогнать камеру под рост тела, чтобы в OBS был крупный план.</summary>
        public static void FrameAvatar(GameObject model, Camera cam, float distanceFactor = 1.35f)
        {
            if (model == null || cam == null)
            {
                return;
            }

            var bounds = new Bounds(model.transform.position, Vector3.zero);
            var renderers = model.GetComponentsInChildren<Renderer>();
            foreach (var renderer in renderers)
            {
                bounds.Encapsulate(renderer.bounds);
            }

            var height = Mathf.Max(0.1f, bounds.size.y);
            var distance = height * distanceFactor;
            cam.transform.position = bounds.center + new Vector3(0f, height * 0.18f, -distance);
            cam.transform.LookAt(bounds.center + new Vector3(0f, height * 0.18f, 0f));
        }

        private static string HttpBaseUrlFrom(string wsUrl)
        {
            if (string.IsNullOrEmpty(wsUrl))
            {
                return "http://127.0.0.1:8765";
            }

            var url = wsUrl.Replace("wss://", "https://").Replace("ws://", "http://");
            var index = url.IndexOf("/ws", System.StringComparison.Ordinal);
            return index > 0 ? url.Substring(0, index) : url;
        }

        private static string GetString(Dictionary<string, object> frame, string key)
        {
            object value;
            if (!frame.TryGetValue(key, out value) || value == null)
            {
                return "";
            }

            return value is string s ? s : value.ToString();
        }

        private static int GetInt(Dictionary<string, object> frame, string key, int fallback)
        {
            object value;
            if (!frame.TryGetValue(key, out value) || value == null)
            {
                return fallback;
            }

            if (value is long l)
            {
                return (int)l;
            }

            if (value is double d)
            {
                return (int)d;
            }

            return int.TryParse(value.ToString(), out var parsed) ? parsed : fallback;
        }

        private static float GetFloat(Dictionary<string, object> frame, string key, float fallback)
        {
            object value;
            if (!frame.TryGetValue(key, out value) || value == null)
            {
                return fallback;
            }

            if (value is double d)
            {
                return (float)d;
            }

            if (value is long l)
            {
                return l;
            }

            return float.TryParse(value.ToString(), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var parsed) ? parsed : fallback;
        }

        private void OnDestroy()
        {
            Disconnect();
            if (Audio != null)
            {
                Audio.Dispose();
            }

            if (vrmLoader != null)
            {
                vrmLoader.Loaded -= OnVrmLoaded;
                vrmLoader.Failed -= OnVrmFailed;
            }
        }

        private void OnApplicationQuit()
        {
            Disconnect();
        }
    }
}
