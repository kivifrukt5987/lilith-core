// LILITH-CORE · Unity-клиент лица (этап 6)
// Виземы: амплитуда/спектр PCM → блендшейпы A/I/U/E/O.
//
// Решение A3.1: Unity считает виземы САМ из PCM — это основной путь.
// Серверная разметка (кадры "viseme") используется только если клиент попросил
// её в hello полем want_server_visemes (фолбэк/веб-панель).
//
// Алгоритм повторяет серверный face/visemes.py (окно ~60 мс, RMS + zero-crossing),
// чтобы рот шевелился одинаково и в панели, и в Unity.

using UnityEngine;

namespace Lilith.Face
{
    /// <summary>Корзина виземы в терминах сервера.</summary>
    public enum VisemeCode
    {
        Rest = 0,
        A,
        I,
        U,
        E,
        O,
    }

    /// <summary>
    /// Считает визему из аудио-окна и плавно доводит её до <see cref="FaceRig"/>.
    /// Не MonoBehaviour: владелец дёргает <see cref="Tick"/> из <c>Update()</c>.
    /// </summary>
    public class VisemeDriver
    {
        private readonly FaceRig _rig;
        private readonly float[] _window;
        private readonly int _sampleRate;

        private readonly float[] _current = new float[5]; // A I U E O
        private readonly float[] _target = new float[5];

        private float _analysisTimer;
        private string _serverCode = "";
        private float _serverIntensity;
        private float _serverAge = 999f;

        /// <summary>Порог тишины по RMS: ниже — рот закрыт.</summary>
        public float SilenceThreshold { get; set; } = 0.012f;

        /// <summary>Сглаживание (0 — мгновенно, 0.95 — очень инертно). Фолбэк-путь.</summary>
        public float Smoothing { get; set; } = 0.55f;

        /// <summary>
        /// Время открытия виземы, сек (ADR-021 / Q3: SALSA «timings On 0.08»).
        /// Ноль или меньше — работаем через <see cref="Smoothing"/>.
        /// </summary>
        public float OpenSeconds { get; set; } = 0.08f;

        /// <summary>Время закрытия виземы, сек (SALSA «timings Off 0.06»).</summary>
        public float CloseSeconds { get; set; } = 0.06f;

        /// <summary>Easing Cubic Out (как у SALSA). false — линейная доводка.</summary>
        public bool CubicOut { get; set; } = true;

        /// <summary>Период пересчёта локальной виземы, сек (~60 мс как на сервере).</summary>
        public float AnalysisPeriod { get; set; } = 0.06f;

        /// <summary>Сколько секунд жить серверной виземе, если их шлют (A3.1).</summary>
        public float ServerMarkTtl { get; set; } = 0.35f;

        /// <summary>Текущая корзина — для debug-оверлея.</summary>
        public VisemeCode Current { get; private set; } = VisemeCode.Rest;

        /// <summary>Текущая интенсивность 0..1 — для debug-оверлея.</summary>
        public float Intensity { get; private set; }

        /// <summary>Использовалась ли серверная разметка в последнем кадре.</summary>
        public bool LastFromServer { get; private set; }

        public VisemeDriver(FaceRig rig, int sampleRate, int windowSamples = 1024)
        {
            _rig = rig;
            _sampleRate = Mathf.Max(8000, sampleRate);
            // окно ~60 мс, но не больше, чем нам может дать плеер
            var samples = Mathf.Max(64, _sampleRate * 60 / 1000);
            _window = new float[Mathf.Min(samples, Mathf.Max(64, windowSamples))];
        }

        /// <summary>Принять серверную разметку (кадр <c>viseme</c>).</summary>
        public void ApplyServerMark(string code, float intensity)
        {
            _serverCode = code ?? "";
            _serverIntensity = Mathf.Clamp01(intensity);
            _serverAge = 0f;
        }

        /// <summary>
        /// Применить визему из sample-accurate очереди (ADR-020.1).
        /// Отличается от <see cref="ApplyServerMark"/> тем, что не сбрасывает «возраст»
        /// серверной разметки: очередь живёт по позиции в буфере, а не по времени кадра.
        /// </summary>
        public void ApplyQueued(string code, float intensity)
        {
            var normalized = FaceRig.NormalizeViseme(code);
            for (var i = 0; i < _target.Length; i++)
            {
                _target[i] = 0f;
            }

            var index = IndexOf(normalized);
            if (index >= 0)
            {
                _target[index] = Mathf.Clamp01(intensity);
                Current = (VisemeCode)(index + 1);
            }
            else
            {
                Current = VisemeCode.Rest;
            }

            Intensity = Mathf.Clamp01(intensity);
            _serverAge = 0f;
            LastFromServer = true;
        }

        /// <summary>
        /// Шаг для sample-accurate режима: виземы берутся из очереди плеера
        /// (позиция в буфере), локальный анализ при этом выключен.
        /// </summary>
        public void TickFromQueue(System.Collections.Generic.List<QueuedViseme> due, float deltaTime)
        {
            if (due != null)
            {
                // Если на одну позицию пришло несколько кадров — побеждает последний.
                for (var i = 0; i < due.Count; i++)
                {
                    ApplyQueued(due[i].Code, due[i].Intensity);
                }
            }

            if (due == null || due.Count == 0)
            {
                _serverAge += deltaTime;
                if (_serverAge > ServerMarkTtl)
                {
                    DecayTargets(deltaTime);
                }
            }

            BlendAndApply(deltaTime);
        }

        /// <summary>Сбросить рот (кадр <c>stop</c> или новая реплика).</summary>
        public void Reset()
        {
            _serverCode = "";
            _serverAge = 999f;
            for (var i = 0; i < _target.Length; i++)
            {
                _target[i] = 0f;
                _current[i] = 0f;
            }

            Current = VisemeCode.Rest;
            Intensity = 0f;
            _rig?.SetViseme("", 0f);
        }

        /// <summary>
        /// Основной шаг: берём окно из плеера, определяем корзину, плавно доводим.
        /// </summary>
        /// <param name="audio">Источник PCM (его окно анализируем).</param>
        /// <param name="deltaTime">Время кадра.</param>
        /// <param name="useLocal">Считать локально (конфиг useLocalVisemes).</param>
        public void Tick(AudioQueueProcessor audio, float deltaTime, bool useLocal = true)
        {
            _serverAge += deltaTime;
            _analysisTimer += deltaTime;

            if (useLocal && audio != null && _analysisTimer >= AnalysisPeriod)
            {
                _analysisTimer = 0f;
                audio.ReadWindow(_window);
                Classify(_window);
                LastFromServer = false;
            }
            else if (!useLocal && _serverAge <= ServerMarkTtl)
            {
                ApplyServerCode();
                LastFromServer = true;
            }
            else if (audio == null || !audio.IsPlaying)
            {
                DecayTargets(deltaTime);
            }

            BlendAndApply(deltaTime);
        }

        private void ApplyServerCode()
        {
            var normalized = FaceRig.NormalizeViseme(_serverCode);
            for (var i = 0; i < _target.Length; i++)
            {
                _target[i] = 0f;
            }

            var index = IndexOf(normalized);
            if (index >= 0)
            {
                _target[index] = _serverIntensity;
                Current = (VisemeCode)(index + 1);
            }
            else
            {
                Current = VisemeCode.Rest;
            }

            Intensity = _serverIntensity;
        }

        /// <summary>RMS + zero-crossing → корзина A/I/U/E/O (зеркало серверного кода).</summary>
        private void Classify(float[] samples)
        {
            var amp = Rms(samples);
            if (amp < SilenceThreshold)
            {
                Current = VisemeCode.Rest;
                Intensity = 0f;
                for (var i = 0; i < _target.Length; i++)
                {
                    _target[i] = 0f;
                }

                return;
            }

            var zcr = ZeroCrossingRate(samples);
            var intensity = Mathf.Clamp01(amp / 0.35f);

            VisemeCode code;
            if (zcr < 0.10f)
            {
                code = amp > 0.16f ? VisemeCode.A : VisemeCode.O;
            }
            else if (zcr < 0.20f)
            {
                code = VisemeCode.E;
            }
            else if (zcr < 0.32f)
            {
                code = amp < 0.18f ? VisemeCode.I : VisemeCode.E;
            }
            else
            {
                code = VisemeCode.I;
            }

            if (amp > 0.10f && zcr > 0.06f && zcr < 0.12f)
            {
                code = VisemeCode.U;
            }

            Current = code;
            Intensity = intensity;
            for (var i = 0; i < _target.Length; i++)
            {
                _target[i] = i == (int)code - 1 ? intensity : 0f;
            }
        }

        private void DecayTargets(float deltaTime)
        {
            var speed = Mathf.Max(0.001f, deltaTime * 12f);
            for (var i = 0; i < _target.Length; i++)
            {
                _target[i] = Mathf.MoveTowards(_target[i], 0f, speed);
            }

            if (Intensity > 0f)
            {
                Intensity = Mathf.MoveTowards(Intensity, 0f, speed);
                if (Intensity <= 0f)
                {
                    Current = VisemeCode.Rest;
                }
            }
        }

        private void BlendAndApply(float deltaTime)
        {
            if (_rig == null)
            {
                return;
            }

            var best = 0;
            if (OpenSeconds > 0f || CloseSeconds > 0f)
            {
                // ADR-021 / Q3: асимметричная доводка — открытие медленнее закрытия,
                // easing Cubic Out. Рот успевает «доехать» до формы и не залипает.
                for (var i = 0; i < _current.Length; i++)
                {
                    _current[i] = Approach(_current[i], _target[i], deltaTime);
                    if (_current[i] > _current[best])
                    {
                        best = i;
                    }
                }
            }
            else
            {
                // Фолбэк: прежний low-pass по Smoothing.
                var factor = 1f - Mathf.Clamp01(Smoothing);
                factor = Mathf.Clamp01(factor + deltaTime * 6f);
                for (var i = 0; i < _current.Length; i++)
                {
                    _current[i] = Mathf.Lerp(_current[i], _target[i], factor);
                    if (_current[i] > _current[best])
                    {
                        best = i;
                    }
                }
            }

            // Гасим все, кроме ведущей — так рот не «размазывается» по пяти формам.
            _rig.SetViseme("", 0f);
            if (_current[best] > 0.01f)
            {
                _rig.SetViseme(NameOf(best), _current[best]);
            }
        }

        /// <summary>
        /// Один шаг доводки: выбирает On/Off-время по направлению и применяет easing.
        /// </summary>
        private float Approach(float current, float target, float deltaTime)
        {
            var duration = target > current
                ? Mathf.Max(0.0001f, OpenSeconds)
                : Mathf.Max(0.0001f, CloseSeconds);
            var step = Mathf.Clamp01(deltaTime / duration);
            var eased = CubicOut ? EvaluateCubicOut(step) : step;
            return Mathf.Lerp(current, target, eased);
        }

        /// <summary>
        /// Easing Cubic Out: <c>1 - (1 - t)^3</c>. Вынесена в static-метод, чтобы её
        /// можно было проверить EditMode-тестом без сцены (F6-в).
        /// </summary>
        public static float EvaluateCubicOut(float t)
        {
            var clamped = Mathf.Clamp01(t);
            var inv = 1f - clamped;
            return 1f - inv * inv * inv;
        }

        private static int IndexOf(string normalized)
        {
            switch (normalized)
            {
                case "aa": return 0;
                case "ih": return 1;
                case "ou": return 2;
                case "ee": return 3;
                case "oh": return 4;
                default: return -1;
            }
        }

        private static string NameOf(int index)
        {
            switch (index)
            {
                case 0: return "aa";
                case 1: return "ih";
                case 2: return "ou";
                case 3: return "ee";
                case 4: return "oh";
                default: return "";
            }
        }

        private static float Rms(float[] samples)
        {
            if (samples == null || samples.Length == 0)
            {
                return 0f;
            }

            double sum = 0.0;
            for (var i = 0; i < samples.Length; i++)
            {
                sum += samples[i] * (double)samples[i];
            }

            return (float)System.Math.Sqrt(sum / samples.Length);
        }

        private static float ZeroCrossingRate(float[] samples)
        {
            if (samples == null || samples.Length < 2)
            {
                return 0f;
            }

            var crossings = 0;
            for (var i = 1; i < samples.Length; i++)
            {
                if ((samples[i - 1] < 0f) != (samples[i] < 0f))
                {
                    crossings++;
                }
            }

            return crossings / (float)(samples.Length - 1);
        }

        /// <summary>Строка для debug-оверлея: «визема A · 0.62 · local».</summary>
        public string Describe()
        {
            return $"визема {Current} · {Intensity:F2} · {(LastFromServer ? "server" : "local")}";
        }
    }
}
