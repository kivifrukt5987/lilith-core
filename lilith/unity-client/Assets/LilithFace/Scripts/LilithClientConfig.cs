// LILITH-CORE · Unity-клиент лица (этап 6)
// Настройки подключения к продюсеру лица. Вешается на тот же GameObject,
// что и LilithFaceClient, либо используется как ScriptableObject-конфиг.

using System;
using UnityEngine;

namespace Lilith.Face
{
    /// <summary>
    /// Как добиваться прозрачности окна (<c>TransparentWindow</c>, решение C4-г).
    /// </summary>
    public enum TransparencyMode
    {
        /// <summary>Ничего не делать: OBS сам вырезает фон Color Key'ем.</summary>
        Off = 0,

        /// <summary>DWM: DwmExtendFrameIntoClientArea + margin -1 (нативная альфа, дефолт).</summary>
        Dwm = 1,

        /// <summary>WS_EX_LAYERED + SetLayeredWindowAttributes (color key, фолбэк).</summary>
        LayeredColorKey = 2,
    }

    /// <summary>
    /// Конфиг Unity-клиента лица. Всё, что может отличаться на машине Кирюши,
    /// вынесено в инспектор — код трогать не нужно.
    /// </summary>
    [Serializable]
    public class LilithClientConfig
    {
        [Header("Соединение")]
        [Tooltip("WS продюсера лица. /ws/unity — алиас того же сокета (A6.1-б).")]
        public string url = "ws://127.0.0.1:8765/ws/face/producer";

        [Tooltip("Групповая сцена: /ws/group (E3). Пусто — одиночный продюсер.")]
        public string groupUrl = "";

        [Tooltip("Переподключаться с экспоненциальным backoff'ом.")]
        public bool autoReconnect = true;

        [Tooltip("Пауза между попытками подключения, сек.")]
        public float reconnectDelay = 2f;

        [Tooltip("Потолок паузы между попытками, сек.")]
        public float reconnectMaxDelay = 30f;

        [Header("Аудио")]
        [Tooltip("Частота продюсера; уточняется из кадра hello (A2: сервер фиксирует 24000).")]
        public int sampleRate = 24000;

        [Tooltip("Размер чанка в байтах; уточняется из hello (A1-а: 2048 = 1024 сэмпла int16).")]
        public int chunkBytes = 2048;

        [Tooltip("Громкость AudioSource, 0..1.")]
        [Range(0f, 1f)]
        public float volume = 1f;

        [Tooltip("Сколько секунд аудио держать в запасе до просадки (anti-dropout).")]
        public float maxBufferedSeconds = 3f;

        [Header("Виземы")]
        [Tooltip("Считать виземы локально из PCM (A3.1: основной путь).")]
        public bool useLocalVisemes = true;

        [Tooltip("Попросить у сервера разметку визем (want_server_visemes в hello).")]
        public bool requestServerVisemes = false;

        [Tooltip("ADR-020.1 (приём из «Нейроны»): привязывать виземы к позиции в аудио-буфере\n" +
                 "в сэмплах, а не ко времени кадра. Точнее на стыках, но требует серверной\n" +
                 "разметки (requestServerVisemes) ИЛИ локального анализа в момент приёма чанка.\n" +
                 "Выключено по умолчанию: offset_ms остаётся основным путём.")]
        public bool useSampleAccurateVisemes = false;

        [Tooltip("Порог тишины по RMS: ниже — рот закрыт.")]
        [Range(0f, 0.2f)]
        public float silenceThreshold = 0.012f;

        [Tooltip("Сглаживание визем (low-pass), 0..1; больше — плавнее, но инертнее.\n" +
                 "Используется только если visemeOnSeconds/visemeOffSeconds = 0.")]
        [Range(0f, 1f)]
        public float visemeSmoothing = 0.55f;

        [Tooltip("Q3/ADR-021: время ОТКРЫТИЯ виземы, сек (SALSA timings On).")]
        public float visemeOnSeconds = 0.08f;

        [Tooltip("Q3/ADR-021: время ЗАКРЫТИЯ виземы, сек (SALSA timings Off).")]
        public float visemeOffSeconds = 0.06f;

        [Tooltip("Q3/ADR-021: easing Cubic Out как у SALSA (выключи — будет линейно).")]
        public bool visemeCubicOut = true;

        [Header("Эмоции и простой")]
        [Tooltip("Множитель веса эмоций (перемножается с intensity из кадра).")]
        [Range(0f, 2f)]
        public float emotionScale = 1f;

        [Tooltip("Плавность сведения эмоции, сек.")]
        public float emotionFadeSeconds = 0.25f;

        [Tooltip("Частота морганий, раз в секунду (переопределяется face.yaml персоны).")]
        public float blinkFrequency = 0.28f;

        [Tooltip("Амплитуда дыхания, 0..1.")]
        [Range(0f, 1f)]
        public float breathAmplitude = 0.5f;

        [Tooltip("Скорость доводки взгляда.")]
        public float lookSpeed = 4f;

        [Tooltip("Следить взглядом за курсором мыши (полезно для стрима).")]
        public bool lookAtCursor = true;

        [Header("Окно (C4-г)")]
        [Tooltip("Режим прозрачности: DWM по умолчанию, LayeredColorKey — фолбэк, Off — для OBS Color Key.")]
        public TransparencyMode transparency = TransparencyMode.Dwm;

        [Tooltip("Ключевой цвет для режима LayeredColorKey (0x00BBGGRR).")]
        public Color32 colorKey = new Color32(255, 0, 255, 255);

        [Tooltip("Размер окна. Q5/ADR-021: дефолт архитектора — 512×640 (портрет 4:5).\n" +
                 "Персона может переопределить его в face.yaml: window.width/height —\n" +
                 "тогда значение приедет в кадре persona и перезапишет это.")]
        public Vector2Int windowSize = new Vector2Int(512, 640);

        [Tooltip("Перезаписывать windowSize значением из face.yaml персоны (Q5).")]
        public bool usePersonaWindowSize = true;

        [Tooltip("Позиция окна: справа снизу на рабочем столе (требование архитектора).")]
        public bool dockBottomRight = true;

        [Tooltip("Отступ от краёв экрана, px.")]
        public int windowMargin = 24;

        [Header("Служебное")]
        [Tooltip("Слать серверу stats (fps/dropped) раз в N секунд (A5).")]
        public float statsInterval = 5f;

        [Tooltip("Подробный лог кадров в Console.")]
        public bool verbose = false;
    }
}
