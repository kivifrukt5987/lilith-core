// LILITH-CORE · Unity-клиент лица (этап 6)
// Простой: моргание, дыхание, взгляд.
//
// Параметры приходят из personas/<id>/face.yaml (кадр "persona"):
//   idle.blink_freq — доля секунды между морганиями (0.28 ≈ раз в 3.5 с)
//   idle.breath_amp — амплитуда дыхания 0..1
//   idle.look_speed — скорость доводки взгляда
// Конфиг клиента задаёт те же значения по умолчанию, если face.yaml молчит.

using UnityEngine;

namespace Lilith.Face
{
    /// <summary>
    /// Оживляет персону между репликами. Не MonoBehaviour: владелец дёргает
    /// <see cref="Tick"/> из <c>Update()</c> и <see cref="LateTick"/> из <c>LateUpdate()</c>.
    /// </summary>
    public class IdleController
    {
        private readonly FaceRig _rig;
        private readonly Transform _head;

        private float _blinkTimer;
        private float _nextBlinkIn = 3f;
        private float _blinkPhase = -1f;
        private float _breathPhase;

        /// <summary>Частота морганий: «доля секунды» из face.yaml (0.28 ≈ раз в 3.5 с).</summary>
        public float BlinkFrequency { get; set; } = 0.28f;

        /// <summary>Амплитуда дыхания 0..1.</summary>
        public float BreathAmplitude { get; set; } = 0.5f;

        /// <summary>Скорость доводки взгляда.</summary>
        public float LookSpeed { get; set; } = 4f;

        /// <summary>Следить за курсором мыши (для стрима).</summary>
        public bool LookAtCursor { get; set; } = true;

        /// <summary>Трансформер, к которому персона «дышит»/наклоняется.</summary>
        public Transform BreathTarget { get; set; }

        /// <summary>Базовая позиция грудной клетки (снимается на старте).</summary>
        public Vector3 BreathOrigin { get; private set; }

        /// <summary>Камера, через которую считаем точку взгляда.</summary>
        public Camera ViewCamera { get; set; }

        /// <summary>Моргание в движении: 0 — открыты, 1 — закрыты (для оверлея).</summary>
        public float BlinkState { get; private set; }

        /// <summary>Говорит ли персона сейчас — на речи моргаем реже и взгляд держим.</summary>
        public bool Speaking { get; set; }

        public IdleController(FaceRig rig, Transform head = null)
        {
            _rig = rig;
            _head = head;
            if (BreathTarget != null)
            {
                BreathOrigin = BreathTarget.localPosition;
            }
        }

        /// <summary>Зафиксировать точку отсчёта дыхания (после загрузки тела).</summary>
        public void CaptureBreathOrigin(Transform chest)
        {
            BreathTarget = chest;
            if (chest != null)
            {
                BreathOrigin = chest.localPosition;
            }
        }

        /// <summary>Применить параметры idle из face.yaml персоны.</summary>
        public void ApplyPersonaIdle(float blinkFreq, float breathAmp, float lookSpeed)
        {
            if (blinkFreq > 0f)
            {
                BlinkFrequency = blinkFreq;
            }

            if (breathAmp >= 0f)
            {
                BreathAmplitude = Mathf.Clamp01(breathAmp);
            }

            if (lookSpeed > 0f)
            {
                LookSpeed = lookSpeed;
            }
        }

        /// <summary>Основной шаг: моргание, дыхание, расчёт точки взгляда.</summary>
        public void Tick(float deltaTime)
        {
            Blink(deltaTime);
            Breathe(deltaTime);
            UpdateLookTarget(deltaTime);
        }

        /// <summary>Шаг в LateUpdate: доводим взгляд после анимаций и пружин.</summary>
        public void LateTick()
        {
            _rig?.ApplyLook();
        }

        // -- моргание ------------------------------------------------------------- //
        private void Blink(float deltaTime)
        {
            if (_rig == null)
            {
                return;
            }

            if (_blinkPhase < 0f)
            {
                _blinkTimer += deltaTime;
                if (_blinkTimer < _nextBlinkIn)
                {
                    _rig.SetEyeOpen(1f);
                    BlinkState = 0f;
                    return;
                }

                _blinkTimer = 0f;
                _blinkPhase = 0f;
                // На речи моргаем реже и «короче»: живой человек так и делает.
                _nextBlinkIn = Random.Range(0.6f, 1.8f) / Mathf.Max(0.05f, BlinkFrequency) * (Speaking ? 1.6f : 1f);
            }

            // Одна фаза моргания ≈ 140 мс: закрытие → открытие.
            _blinkPhase += deltaTime / 0.14f;
            if (_blinkPhase >= 1f)
            {
                _blinkPhase = -1f;
                _rig.SetEyeOpen(1f);
                BlinkState = 0f;
                return;
            }

            var closed = _blinkPhase < 0.5f
                ? Mathf.SmoothStep(0f, 1f, _blinkPhase * 2f)
                : Mathf.SmoothStep(1f, 0f, (_blinkPhase - 0.5f) * 2f);
            BlinkState = closed;
            _rig.SetEyeOpen(1f - closed);
        }

        // -- дыхание ------------------------------------------------------------- //
        private void Breathe(float deltaTime)
        {
            if (BreathTarget == null || BreathAmplitude <= 0.001f)
            {
                return;
            }

            // ~0.25 Гц = 15 вдохов в минуту
            _breathPhase += deltaTime * 0.25f * 2f * Mathf.PI;
            if (_breathPhase > 2f * Mathf.PI)
            {
                _breathPhase -= 2f * Mathf.PI;
            }

            var lift = Mathf.Sin(_breathPhase) * 0.012f * BreathAmplitude;
            var sway = Mathf.Cos(_breathPhase * 0.5f) * 0.006f * BreathAmplitude;
            BreathTarget.localPosition = BreathOrigin + new Vector3(sway, lift, 0f);
        }

        // -- взгляд --------------------------------------------------------------- //
        private void UpdateLookTarget(float deltaTime)
        {
            if (_rig == null || !_rig.LookEnabled)
            {
                return;
            }

            var desired = _rig.LookTarget;
            if (LookAtCursor && ViewCamera != null && !Speaking)
            {
                var mouse = Input.mousePosition;
                if (mouse.z > 0f || mouse.x > 0f || mouse.y > 0f)
                {
                    var ray = ViewCamera.ScreenPointToRay(mouse);
                    desired = ray.GetPoint(Mathf.Max(0.5f, ViewCamera.nearClipPlane * 20f));
                }
            }

            _rig.LookTarget = Vector3.Lerp(
                _rig.LookTarget,
                desired,
                Mathf.Clamp01(deltaTime * Mathf.Max(0.1f, LookSpeed)));
        }

        /// <summary>Строка для debug-оверлея.</summary>
        public string Describe()
        {
            return $"idle: морг {BlinkState:F2} · дых {BreathAmplitude:F2} · взгляд {(LookAtCursor ? "курсор" : "цель")}";
        }
    }
}
