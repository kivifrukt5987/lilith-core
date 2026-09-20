// LILITH-CORE · Unity-клиент лица (этап 6)
// Прослойка к VRM-телу: экспрессии, взгляд, моргание.
//
// API проверен по исходникам UniVRM 0.131.x (vrm-c/UniVRM, master):
//   Vrm10Instance.Runtime.Expression.SetWeight(ExpressionKey, float)
//   UniVRM10.ExpressionKey.Aa/Ih/Ou/Ee/Oh · Happy/Angry/Sad/Relaxed/Surprised/Neutral/Blink
//   UniVRM10.ExpressionKey.CreateCustom("имя")
//   Vrm10Instance.Runtime.LookAt.CalculateYawPitchFromLookAtPosition(Vector3) + SetYawPitch
//
// Весь код, который трогает UniVRM, закрыт директивой LILITH_UNIVRM.
// Определи её в Player Settings → Scripting Define Symbols ПОСЛЕ импорта
// VRM-*.unitypackage и UniVRM-*.unitypackage. До этого проект компилируется,
// соединение с сервером проверяется, а тело просто не анимируется.

using System.Collections.Generic;
using UnityEngine;

#if LILITH_UNIVRM
using UniVRM10;
#endif

namespace Lilith.Face
{
    /// <summary>
    /// Доступ к «лицу» персоны. Все имена приводятся к VRM 1.0 (C5.2):
    /// виземы <c>aa/ih/ou/ee/oh</c>, эмоции <c>happy/angry/sad/relaxed/surprised/neutral</c>.
    /// Словарные имена этапа 5 (<c>joy/sorrow/smug/love/evil</c> и VRM 0.x <c>A/I/U/E/O</c>,
    /// <c>Joy/Angry/Sorrow/Fun</c>) маппятся автоматически.
    /// </summary>
    public class FaceRig
    {
#if LILITH_UNIVRM
        private Vrm10Instance _instance;
        private Vrm10RuntimeExpression _expression;
        private Vrm10RuntimeLookAt _lookAt;
        private readonly Dictionary<ExpressionKey, float> _weights = new Dictionary<ExpressionKey, float>();
#endif

        /// <summary>Тело загружено и готово к анимации.</summary>
        public bool IsValid
        {
#if LILITH_UNIVRM
            get { return _instance != null && _expression != null; }
#else
            get { return false; }
#endif
        }

        /// <summary>Имя объекта с телом — для логов и debug-оверлея.</summary>
        public string Name { get; private set; } = "";

        /// <summary>Мировая точка, куда смотрит персона.</summary>
        public Vector3 LookTarget { get; set; }

        /// <summary>Следить ли за точкой взгляда.</summary>
        public bool LookEnabled { get; set; } = true;

        /// <summary>0..1 — насколько открыты глаза (их дёргает IdleController).</summary>
        public float EyeOpen { get; set; } = 1f;

        /// <summary>VRM 0.x, японские и словарные имена → виземы VRM 1.0.</summary>
        /// <remarks>
        /// Японские строки добавлены по ADR-020.2: у «Нейроны» SALSA настроена на
        /// блендшейпы с именами «え», «あ» и т.д., и многие VRoid-модели подписывают
        /// морфы ровно так. Мы принимаем их и на входе (код виземы), и используем
        /// как fallback на выходе, если в модели нет пресета VRM 1.0.
        /// </remarks>
        private static readonly Dictionary<string, string> VisemeAliases = new Dictionary<string, string>
        {
            { "a", "aa" }, { "i", "ih" }, { "u", "ou" }, { "e", "ee" }, { "o", "oh" },
            { "aa", "aa" }, { "ih", "ih" }, { "ou", "ou" }, { "ee", "ee" }, { "oh", "oh" },
            { "あ", "aa" }, { "い", "ih" }, { "う", "ou" }, { "え", "ee" }, { "お", "oh" },
            { "ぁ", "aa" }, { "ぃ", "ih" }, { "ぅ", "ou" }, { "ぇ", "ee" }, { "ぉ", "oh" },
            { "rest", "" }, { "sil", "" }, { "neutral", "" }, { "", "" },
        };

        /// <summary>
        /// Fallback-имена блендшейпов (ADR-020.2): если в модели нет пресета VRM 1.0,
        /// пробуем японское имя как кастомную экспрессию. Четыре виземы — как у SALSA
        /// в конфигурации «Нейроны» (A/I/E + U/O), плюс полный японский набор.
        /// </summary>
        private static readonly Dictionary<string, string> VisemeCustomFallback = new Dictionary<string, string>
        {
            { "aa", "あ" }, { "ih", "い" }, { "ou", "う" }, { "ee", "え" }, { "oh", "お" },
        };

        /// <summary>Теги эмоций сервера (включая японские) → экспрессии VRM 1.0.</summary>
        private static readonly Dictionary<string, string> EmotionAliases = new Dictionary<string, string>
        {
            { "joy", "happy" }, { "happy", "happy" }, { "笑い", "happy" }, { "にやり", "relaxed" },
            { "smug", "relaxed" }, { "love", "relaxed" }, { "embarrassment", "relaxed" },
            { "anger", "angry" }, { "angry", "angry" }, { "evil", "angry" }, { "怒り", "angry" },
            { "sadness", "sad" }, { "sad", "sad" }, { "sorrow", "sad" }, { "sleepy", "sad" },
            { "悲しみ", "sad" }, { "悲しい", "sad" },
            { "surprise", "surprised" }, { "surprised", "surprised" }, { "驚き", "surprised" },
            { "relaxed", "relaxed" }, { "ふわり", "relaxed" },
            { "blink", "blink" }, { "瞬き", "blink" }, { "まばたき", "blink" },
            { "neutral", "neutral" }, { "普通", "neutral" },
        };

        /// <summary>Японские имена экспрессий — fallback, когда пресета в модели нет.</summary>
        private static readonly Dictionary<string, string> EmotionCustomFallback = new Dictionary<string, string>
        {
            { "happy", "笑い" }, { "angry", "怒り" }, { "sad", "悲しみ" },
            { "relaxed", "ふわり" }, { "surprised", "驚き" }, { "blink", "瞬き" },
        };

        /// <summary>Все экспрессии, которые клиент умеет гасить при смене эмоции.</summary>
        private static readonly string[] EmotionNames =
        {
            "happy", "angry", "sad", "relaxed", "surprised", "neutral",
        };

        /// <summary>Привязать прослойку к загруженному телу.</summary>
        public void Bind(GameObject root)
        {
            Unbind();
            Name = root != null ? root.name : "";
#if LILITH_UNIVRM
            _instance = root != null ? root.GetComponent<Vrm10Instance>() : null;
            if (_instance == null)
            {
                Debug.LogWarning("[Lilith] на объекте нет Vrm10Instance: нужна VRM 1.0 (решение C5.2). " +
                                 "Пересохрани модель в VRM 1.0 или включи миграцию при импорте.");
                return;
            }

            var runtime = _instance.Runtime;
            _expression = runtime != null ? runtime.Expression : null;
            _lookAt = runtime != null ? runtime.LookAt : null;
            CacheAvailableExpressions();
            if (_expression == null)
            {
                Debug.LogWarning("[Lilith] Vrm10Instance.Runtime ещё не построен: попробуй в следующем кадре.");
            }
#else
            Debug.LogWarning("[Lilith] LILITH_UNIVRM не определён: тело загружено, но не анимируется. " +
                             "Добавь LILITH_UNIVRM в Scripting Define Symbols после установки UniVRM.");
#endif
        }

        /// <summary>
        /// Запомнить, какие экспрессии есть в модели: без этого fallback на японские
        /// имена срабатывал бы вслепую (ADR-020.2).
        /// </summary>
        private void CacheAvailableExpressions()
        {
            _available.Clear();
            if (_expression == null || _expression.ExpressionKeys == null)
            {
                return;
            }

            foreach (var key in _expression.ExpressionKeys)
            {
                if (!string.IsNullOrEmpty(key.Name))
                {
                    _available.Add(key.Name);
                }
            }
        }

        /// <summary>Есть ли в модели экспрессия с таким именем (для диагностики).</summary>
        public bool HasExpression(string name)
        {
#if LILITH_UNIVRM
            return _available.Contains(name);
#else
            return false;
#endif
        }

        /// <summary>Сколько экспрессий найдено в модели.</summary>
        public int AvailableExpressions
        {
#if LILITH_UNIVRM
            get { return _available.Count; }
#else
            get { return 0; }
#endif
        }

        /// <summary>Отвязаться от тела (перед горячим свопом персоны).</summary>
        public void Unbind()
        {
#if LILITH_UNIVRM
            _instance = null;
            _expression = null;
            _lookAt = null;
            _weights.Clear();
            _available.Clear();
#endif
            Name = "";
        }

        /// <summary>
        /// Установить визему (0..1). Пустое/неизвестное имя гасит рот.
        /// Остальные ротовые экспрессии обнуляются, чтобы не было «каши».
        /// </summary>
        public void SetViseme(string code, float weight)
        {
#if LILITH_UNIVRM
            if (_expression == null)
            {
                return;
            }

            var target = NormalizeViseme(code);
            foreach (var name in new[] { "aa", "ih", "ou", "ee", "oh" })
            {
                var value = name == target ? Mathf.Clamp01(weight) : 0f;
                _expression.SetWeight(KeyFor(name, VisemeCustomFallback), value);
            }
#endif
        }

        /// <summary>
        /// Установить эмоцию (0..1). Неизвестный тег уходит в <c>neutral</c>.
        /// Остальные эмоции гасятся.
        /// </summary>
        public void SetEmotion(string tag, float weight)
        {
#if LILITH_UNIVRM
            if (_expression == null)
            {
                return;
            }

            var target = NormalizeEmotion(tag);
            foreach (var name in EmotionNames)
            {
                var value = name == target ? Mathf.Clamp(weight, 0f, 1.5f) : 0f;
                _expression.SetWeight(KeyFor(name, EmotionCustomFallback), value);
            }
#endif
        }

        /// <summary>
        /// Произвольный blendshape по имени — для per-persona override из
        /// <c>face.yaml</c> (решение D4-гибрид).
        /// </summary>
        public void SetBlendshape(string name, float weight)
        {
#if LILITH_UNIVRM
            if (_expression == null || string.IsNullOrEmpty(name))
            {
                return;
            }

            _expression.SetWeight(ExpressionKey.CreateCustom(name), Mathf.Clamp(weight, -1f, 2f));
#endif
        }

        /// <summary>Открыть/закрыть глаза. 1 — открыты, 0 — закрыты (моргание).</summary>
        public void SetEyeOpen(float value)
        {
            EyeOpen = Mathf.Clamp01(value);
#if LILITH_UNIVRM
            if (_expression != null)
            {
                _expression.SetWeight(KeyFor("blink", EmotionCustomFallback), 1f - EyeOpen);
            }
#endif
        }

        /// <summary>
        /// Довести взгляд до <see cref="LookTarget"/>. Вызывать в <c>LateUpdate</c>
        /// (после анимаций и пружинных костей), иначе кадр «дрожит».
        /// </summary>
        public void ApplyLook()
        {
#if LILITH_UNIVRM
            if (_lookAt == null || !LookEnabled)
            {
                return;
            }

            var (yaw, pitch) = _lookAt.CalculateYawPitchFromLookAtPosition(LookTarget);
            _lookAt.SetYawPitchManually(yaw, pitch);
#endif
        }

        /// <summary>Имя виземы в терминах VRM 1.0 (пустая строка = рот закрыт).</summary>
        public static string NormalizeViseme(string code)
        {
            if (string.IsNullOrEmpty(code))
            {
                return "";
            }

            var trimmed = code.Trim();
            // Японские имена регистр не имеют, но ToLowerInvariant их не портит.
            if (VisemeAliases.TryGetValue(trimmed, out var exact))
            {
                return exact;
            }

            var key = trimmed.ToLowerInvariant();
            return VisemeAliases.TryGetValue(key, out var mapped) ? mapped : key;
        }

        /// <summary>Тег эмоции в терминах VRM 1.0 Expressions.</summary>
        public static string NormalizeEmotion(string tag)
        {
            if (string.IsNullOrEmpty(tag))
            {
                return "neutral";
            }

            var trimmed = tag.Trim();
            if (EmotionAliases.TryGetValue(trimmed, out var exact))
            {
                return exact;
            }

            var key = trimmed.ToLowerInvariant();
            return EmotionAliases.TryGetValue(key, out var mapped) ? mapped : "neutral";
        }

#if LILITH_UNIVRM
        /// <summary>Список экспрессий, которые реально есть в модели (кэш на Bind).</summary>
        private HashSet<string> _available = new HashSet<string>();

        /// <summary>
        /// Ключ экспрессии с fallback'ом (ADR-020.2): если в модели есть пресет VRM 1.0 —
        /// берём его, иначе пробуем японское имя как кастомную экспрессию.
        /// </summary>
        private ExpressionKey KeyFor(string presetName, Dictionary<string, string> customFallback)
        {
            var preset = ExpressionKey.CreateFromPreset(PresetOf(presetName));
            if (_available.Count == 0 || _available.Contains(preset.Name))
            {
                return preset;
            }

            if (customFallback != null && customFallback.TryGetValue(presetName, out var japanese))
            {
                return ExpressionKey.CreateCustom(japanese);
            }

            return preset;
        }

        /// <summary>Имя пресета → ExpressionPreset (имена в enum — строчные).</summary>
        private static ExpressionPreset PresetOf(string name)
        {
            switch (name)
            {
                case "aa": return ExpressionPreset.aa;
                case "ih": return ExpressionPreset.ih;
                case "ou": return ExpressionPreset.ou;
                case "ee": return ExpressionPreset.ee;
                case "oh": return ExpressionPreset.oh;
                case "happy": return ExpressionPreset.happy;
                case "angry": return ExpressionPreset.angry;
                case "sad": return ExpressionPreset.sad;
                case "relaxed": return ExpressionPreset.relaxed;
                case "surprised": return ExpressionPreset.surprised;
                case "blink": return ExpressionPreset.blink;
                default: return ExpressionPreset.neutral;
            }
        }
#endif
    }
}
