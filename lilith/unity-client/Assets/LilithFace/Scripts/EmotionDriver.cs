// LILITH-CORE · Unity-клиент лица (этап 6)
// Эмоции: теги сервера → позы лица (и опционально тела).
//
// Контракт (A3): {"type":"emotion","tag":"joy","intensity":0.8,"ttl_ms":4000}.
// Решение A3.3: сбрасывает эмоцию В UNITY по ttl_ms; сервер шлёт neutral только
// при явной смене, поэтому таймер живёт здесь.

using System.Collections.Generic;
using UnityEngine;

namespace Lilith.Face
{
    /// <summary>
    /// Держит текущую эмоцию, плавно сводит веса и гасит её по TTL.
    /// Не MonoBehaviour: владелец дёргает <see cref="Tick"/> из <c>Update()</c>.
    /// </summary>
    public class EmotionDriver
    {
        private readonly FaceRig _rig;

        private string _tag = "neutral";
        private float _intensity = 1f;
        private float _ttl;
        private float _age;
        private float _current;

        /// <summary>Множитель веса из конфига (emotionScale).</summary>
        public float Scale { get; set; } = 1f;

        /// <summary>Время сведения/разведения, сек.</summary>
        public float FadeSeconds { get; set; } = 0.25f;

        /// <summary>
        /// Позы тела по тегам: название Animation/Animator-стейта или Trigger.
        /// Заполняется из <c>personas/&lt;id&gt;/face.yaml</c> (кадр <c>persona.face</c>).
        /// </summary>
        public Dictionary<string, string> BodyPoses { get; } = new Dictionary<string, string>();

        /// <summary>Animator тела (опционально) — для поз из <see cref="BodyPoses"/>.</summary>
        public Animator BodyAnimator { get; set; }

        /// <summary>Текущий тег эмоции.</summary>
        public string Tag
        {
            get { return _tag; }
        }

        /// <summary>Текущий вес эмоции (0..1·Scale) — для debug-оверлея.</summary>
        public float Weight
        {
            get { return _current; }
        }

        /// <summary>Сколько секунд осталось до автосброса.</summary>
        public float Remaining
        {
            get { return Mathf.Max(0f, _ttl - _age); }
        }

        public EmotionDriver(FaceRig rig)
        {
            _rig = rig;
            _ttl = 0f;
            _age = 0f;
            _current = 0f;
        }

        /// <summary>
        /// Применить кадр <c>emotion</c>.
        /// </summary>
        /// <param name="tag">Тег из словаря сервера (joy/anger/sadness/…).</param>
        /// <param name="intensity">Интенсивность 0..1.</param>
        /// <param name="ttlMs">Время жизни в миллисекундах; 0 = держать до явной смены.</param>
        public void Apply(string tag, float intensity, int ttlMs)
        {
            _tag = string.IsNullOrEmpty(tag) ? "neutral" : tag.Trim().ToLowerInvariant();
            _intensity = Mathf.Clamp01(intensity);
            _ttl = ttlMs > 0 ? ttlMs / 1000f : 0f;
            _age = 0f;

            if (_tag == "neutral")
            {
                // Явный neutral: гасим сразу (без ожидания TTL).
                _ttl = 0f;
            }

            ApplyBodyPose();
        }

        /// <summary>Полный сброс (например, при свопе персоны).</summary>
        public void Reset()
        {
            _tag = "neutral";
            _intensity = 1f;
            _ttl = 0f;
            _age = 0f;
            _current = 0f;
            _rig?.SetEmotion("neutral", 0f);
        }

        /// <summary>Шаг анимации: сводит вес и гасит эмоцию по TTL.</summary>
        public void Tick(float deltaTime)
        {
            _age += deltaTime;
            var expired = _ttl > 0f && _age >= _ttl;
            var target = expired ? 0f : _intensity * Scale;

            var speed = Mathf.Max(0.0001f, deltaTime / Mathf.Max(0.01f, FadeSeconds));
            _current = Mathf.MoveTowards(_current, target, speed);

            if (_rig != null)
            {
                if (_current <= 0.001f)
                {
                    _rig.SetEmotion("neutral", 0f);
                    if (expired)
                    {
                        _tag = "neutral";
                        _ttl = 0f;
                    }
                }
                else
                {
                    _rig.SetEmotion(_tag, _current);
                }
            }
        }

        private void ApplyBodyPose()
        {
            if (BodyAnimator == null || BodyPoses.Count == 0)
            {
                return;
            }

            if (BodyPoses.TryGetValue(_tag, out var stateName) && !string.IsNullOrEmpty(stateName))
            {
                // Trigger приоритетнее прямого перехода: позы можно ставить в любой момент.
                BodyAnimator.SetTrigger(stateName);
            }
        }

        /// <summary>Строка для debug-оверлея.</summary>
        public string Describe()
        {
            var left = _ttl > 0f ? $" · {_ttl - _age:F1}с" : "";
            return $"эмоция {_tag} · {_current:F2}{left}";
        }
    }
}
