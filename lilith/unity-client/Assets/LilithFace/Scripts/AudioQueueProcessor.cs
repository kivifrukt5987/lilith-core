// LILITH-CORE · Unity-клиент лица (этап 6)
// Очередь аудио: raw PCM int16 mono из кадров "audio" → непрерывный AudioSource.
//
// Контракт сервера (A1-а/A2): base64-строка длиной ровно chunk_bytes (2048) =
// 1024 сэмпла int16, частота sample_rate (24000). Последний чанк реплики помечен
// final:true, а за ним приходит кадр "done".
//
// Плеер построен на одном длинном AudioClip, который мы дописываем кольцом
// (SetData по позиции записи) и играем с позиции чтения — это даёт непрерывный
// звук без щелчков на стыках чанков и позволяет дропнуть реплику по кадру "stop".

using System;
using System.Collections.Generic;
using UnityEngine;

namespace Lilith.Face
{
    /// <summary>
    /// Обрабатывает аудио-чанки продюсера и крутит <see cref="AudioSource"/>.
    /// Не MonoBehaviour: владелец (<see cref="LilithFaceClient"/>) дёргает
    /// <see cref="Tick"/> каждый кадр из <c>Update()</c>.
    /// </summary>
    /// <summary>
    /// Визема, привязанная к позиции в аудио-буфере (ADR-020.1, приём из «Нейроны»:
    /// <c>[BlendShapes] Queued 2 shapes at buffer pos N</c>). Позиция абсолютная —
    /// в сэмплах от начала потока, поэтому стыки чанков не разъезжаются.
    /// </summary>
    public struct QueuedViseme
    {
        /// <summary>Абсолютная позиция в сэмплах, на которой визему нужно применить.</summary>
        public long Sample;

        /// <summary>Код виземы (A/I/U/E/O или aa/ih/ou/ee/oh).</summary>
        public string Code;

        /// <summary>Интенсивность 0..1.</summary>
        public float Intensity;

        /// <summary>Реплика, к которой относится визема (для дропа по stop).</summary>
        public string UtteranceId;
    }

    public class AudioQueueProcessor : IDisposable
    {
        private readonly AudioSource _source;
        private AudioClip _clip;
        private float[] _ring;
        private int _ringCapacity;
        private int _writePos;
        private int _buffered;
        private int _sampleRate;
        private bool _playing;
        private long _playedSamples;
        private readonly List<QueuedViseme> _visemeQueue = new List<QueuedViseme>();

        /// <summary>Текущий utterance_id: по нему дропаем реплику на stop (A3.4).</summary>
        public string UtteranceId { get; private set; } = "";

        /// <summary>Сколько миллисекунд аудио сейчас в запасе.</summary>
        public int BufferedMs
        {
            get { return _sampleRate > 0 ? _buffered * 1000 / _sampleRate : 0; }
        }

        /// <summary>Идёт ли воспроизведение.</summary>
        public bool IsPlaying
        {
            get { return _playing && _buffered > 0; }
        }

        /// <summary>
        /// Мгновенная амплитуда играющего сейчас участка (0..1) — её ест
        /// <see cref="VisemeDriver"/>, когда считает виземы локально (A3.1).
        /// </summary>
        public float CurrentAmplitude { get; private set; }

        /// <summary>Сколько байт PCM принято всего (для stats.dropped/отладки).</summary>
        public long BytesReceived { get; private set; }

        /// <summary>Сколько чанков дропнуто из-за переполнения кольца.</summary>
        public long ChunksDropped { get; private set; }

        public AudioQueueProcessor(AudioSource source, int sampleRate, float maxBufferedSeconds = 3f)
        {
            _source = source;
            _sampleRate = Mathf.Max(8000, sampleRate);
            Rebuild(Mathf.Max(0.5f, maxBufferedSeconds));
        }

        /// <summary>Перестроить кольцо под новую частоту/буфер (вызывается из hello).</summary>
        public void Reconfigure(int sampleRate, float maxBufferedSeconds)
        {
            var rateChanged = sampleRate > 0 && sampleRate != _sampleRate;
            if (rateChanged)
            {
                _sampleRate = sampleRate;
            }

            Rebuild(maxBufferedSeconds);
        }

        private void Rebuild(float maxBufferedSeconds)
        {
            Stop();
            EnqueuedSamples = 0;
            _playedSamples = 0;
            _utteranceStarts.Clear();
            _ringCapacity = Mathf.Max(2048, (int)(_sampleRate * Mathf.Max(0.5f, maxBufferedSeconds)));
            _ring = new float[_ringCapacity];
            _clip = AudioClip.Create("LilithVoice", _ringCapacity, 1, _sampleRate, false);
            if (_source != null)
            {
                _source.clip = _clip;
                _source.loop = true;
                _source.playOnAwake = false;
            }
        }

        /// <summary>
        /// Принять чанк PCM (raw int16 mono) из кадра <c>audio</c>.
        /// </summary>
        /// <param name="pcm">Байты чанка (уже раскодированный base64).</param>
        /// <param name="utteranceId">Идентификатор реплики.</param>
        public void Enqueue(byte[] pcm, string utteranceId)
        {
            if (pcm == null || pcm.Length < 2)
            {
                return;
            }

            if (!string.IsNullOrEmpty(utteranceId) && UtteranceId != utteranceId)
            {
                // Новая реплика: предыдущую больше не играем (иначе наложатся).
                DropCurrent();
                UtteranceId = utteranceId;
            }

            var count = pcm.Length / 2;
            var samples = new float[count];
            for (var i = 0; i < count; i++)
            {
                short value = (short)(pcm[i * 2] | (pcm[i * 2 + 1] << 8));
                samples[i] = value / 32768f;
            }

            BytesReceived += pcm.Length;
            if (EnqueuedSamples == 0 && !string.IsNullOrEmpty(utteranceId))
            {
                NoteUtteranceStart(utteranceId, 0);
            }

            EnqueuedSamples += count;
            if (_buffered + count > _ringCapacity)
            {
                // Сервер льёт быстрее, чем мы играем: дропаем старьё и считаем потерю.
                ChunksDropped++;
                var overflow = _buffered + count - _ringCapacity;
                Consume(overflow);
            }

            for (var i = 0; i < count; i++)
            {
                _ring[_writePos] = samples[i];
                _writePos = (_writePos + 1) % _ringCapacity;
            }

            _buffered += count;
            _clip.SetData(_ring, 0);
        }

        /// <summary>Кадр <c>done</c>: реплика закончилась, доигрываем остаток.</summary>
        public void MarkUtteranceEnd(string utteranceId)
        {
            if (string.IsNullOrEmpty(utteranceId) || utteranceId == UtteranceId)
            {
                UtteranceId = "";
            }
        }

        /// <summary>Кадр <c>stop</c>: немедленно выбросить реплику (A3.4).</summary>
        public void Stop(string utteranceId = "")
        {
            if (!string.IsNullOrEmpty(utteranceId) && !string.IsNullOrEmpty(UtteranceId) && utteranceId != UtteranceId)
            {
                return;
            }

            DropCurrent();
        }

        private void DropCurrent()
        {
            _buffered = 0;
            _writePos = 0;
            _playedSamples = 0;
            CurrentAmplitude = 0f;
            UtteranceId = "";
            _visemeQueue.Clear();
            if (_source != null && _source.isPlaying)
            {
                _source.Stop();
            }

            _playing = false;
        }

        private void Consume(int samples)
        {
            samples = Mathf.Min(samples, _buffered);
            _buffered -= samples;
        }

        /// <summary>
        /// Вызывать каждый кадр: подкручивает AudioSource и считает амплитуду
        /// играющего участка для локальных визем.
        /// </summary>
        public void Tick(float deltaTime)
        {
            if (_source == null)
            {
                return;
            }

            if (_buffered > 0 && !_source.isPlaying)
            {
                _source.time = 0f;
                _source.Play();
                _playing = true;
            }

            if (_playing)
            {
                var consumed = Mathf.RoundToInt(_source.time * _sampleRate);
                if (consumed > 0)
                {
                    _source.time = 0f;
                    Consume(consumed);
                    // ADR-020.1: помним абсолютную позицию, чтобы виземы вставали
                    // «по месту в буфере», а не «когда дошёл Update».
                    _playedSamples += consumed;
                }

                CurrentAmplitude = SampleAmplitude();
                if (_buffered <= 0)
                {
                    _source.Stop();
                    _playing = false;
                    CurrentAmplitude = 0f;
                }
            }
            else
            {
                CurrentAmplitude = Mathf.MoveTowards(CurrentAmplitude, 0f, deltaTime * 8f);
            }
        }

        /// <summary>RMS текущего участка кольца — «громкость рта» для визем.</summary>
        private float SampleAmplitude()
        {
            if (_buffered <= 0)
            {
                return 0f;
            }

            var window = Mathf.Min(_buffered, _sampleRate / 50); // ~20 мс
            var start = (_writePos - _buffered + _ringCapacity * 2) % _ringCapacity;
            double sum = 0.0;
            for (var i = 0; i < window; i++)
            {
                var value = _ring[(start + i) % _ringCapacity];
                sum += value * value;
            }

            return (float)Math.Sqrt(sum / window);
        }

        // --------------------------------------------------------------------- //
        //  Sample-accurate очередь визем (ADR-020.1)
        // --------------------------------------------------------------------- //
        /// <summary>Текущая позиция воспроизведения в сэмплах от начала потока.</summary>
        public long PlayPositionSamples
        {
            get
            {
                if (!_playing || _source == null)
                {
                    return _playedSamples;
                }

                return _playedSamples + (long)(_source.time * _sampleRate);
            }
        }

        /// <summary>Сколько сэмплов всего влито в плеер (для расчёта абсолютных позиций).</summary>
        public long EnqueuedSamples { get; private set; }

        /// <summary>Сколько визем ждёт своей позиции в буфере.</summary>
        public int PendingVisemes
        {
            get { return _visemeQueue.Count; }
        }

        /// <summary>
        /// Поставить визему в очередь на абсолютную позицию в сэмплах.
        /// </summary>
        public void EnqueueVisemeAt(long samplePosition, string code, float intensity, string utteranceId = "")
        {
            _visemeQueue.Add(new QueuedViseme
            {
                Sample = samplePosition,
                Code = code ?? "",
                Intensity = Mathf.Clamp01(intensity),
                UtteranceId = utteranceId ?? "",
            });

            // Очередь держим отсортированной: сервер шлёт по порядку, но stop/своп
            // могут добавить кадры вразнобой.
            _visemeQueue.Sort((a, b) => a.Sample.CompareTo(b.Sample));
        }

        /// <summary>
        /// Поставить визему относительно начала реплики: <c>offsetMs</c> пересчитывается
        /// в абсолютную позицию (база реплики + миллисекунды).
        /// </summary>
        public void EnqueueViseme(string code, float intensity, int offsetMs, string utteranceId)
        {
            if (!TryGetUtteranceStart(utteranceId, out var baseSample))
            {
                baseSample = EnqueuedSamples;
            }

            var delaySamples = _sampleRate > 0 ? (long)offsetMs * _sampleRate / 1000L : 0L;
            EnqueueVisemeAt(baseSample + delaySamples, code, intensity, utteranceId);
        }

        /// <summary>
        /// Забрать виземы, чья позиция уже пройдена плеером. Вызывать из Update().
        /// </summary>
        public List<QueuedViseme> DequeueDueVisemes()
        {
            var due = new List<QueuedViseme>();
            if (_visemeQueue.Count == 0)
            {
                return due;
            }

            var position = PlayPositionSamples;
            while (_visemeQueue.Count > 0 && _visemeQueue[0].Sample <= position)
            {
                due.Add(_visemeQueue[0]);
                _visemeQueue.RemoveAt(0);
            }

            return due;
        }

        /// <summary>Запомнить, с какого сэмпла начинается реплика (для offset_ms).</summary>
        public void NoteUtteranceStart(string utteranceId, long startSample)
        {
            if (string.IsNullOrEmpty(utteranceId))
            {
                return;
            }

            for (var i = 0; i < _utteranceStarts.Count; i++)
            {
                if (_utteranceStarts[i].Key == utteranceId)
                {
                    _utteranceStarts[i] = new KeyValuePair<string, long>(utteranceId, startSample);
                    return;
                }
            }

            _utteranceStarts.Add(new KeyValuePair<string, long>(utteranceId, startSample));
            if (_utteranceStarts.Count > 8)
            {
                _utteranceStarts.RemoveAt(0);
            }
        }

        /// <summary>Найти начало реплики в сэмплах.</summary>
        public bool TryGetUtteranceStart(string utteranceId, out long startSample)
        {
            foreach (var pair in _utteranceStarts)
            {
                if (pair.Key == utteranceId)
                {
                    startSample = pair.Value;
                    return true;
                }
            }

            startSample = 0;
            return false;
        }

        private readonly List<KeyValuePair<string, long>> _utteranceStarts = new List<KeyValuePair<string, long>>();

        /// <summary>Читает буфер для спектрального анализа (нужен VisemeDriver'у).</summary>
        public void ReadWindow(float[] destination)
        {
            if (destination == null || destination.Length == 0 || _buffered <= 0)
            {
                return;
            }

            var start = (_writePos - _buffered + _ringCapacity * 2) % _ringCapacity;
            var count = Mathf.Min(destination.Length, _buffered);
            for (var i = 0; i < count; i++)
            {
                destination[i] = _ring[(start + i) % _ringCapacity];
            }

            for (var i = count; i < destination.Length; i++)
            {
                destination[i] = 0f;
            }
        }

        /// <summary>Сводка для debug-оверлея и кадра stats.</summary>
        public Dictionary<string, object> Describe()
        {
            return new Dictionary<string, object>
            {
                { "buffered_ms", BufferedMs },
                { "playing", IsPlaying },
                { "bytes_received", BytesReceived },
                { "chunks_dropped", ChunksDropped },
                { "utterance_id", UtteranceId },
                { "sample_rate", _sampleRate },
                { "play_pos_samples", PlayPositionSamples },
                { "pending_visemes", PendingVisemes },
            };
        }

        public void Dispose()
        {
            DropCurrent();
            if (_clip != null)
            {
                UnityEngine.Object.Destroy(_clip);
                _clip = null;
            }
        }
    }
}
