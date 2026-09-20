// LILITH-CORE · Unity-клиент лица (этап 6)
// WS-транспорт на встроенном System.Net.WebSockets.ClientWebSocket.
// Внешних пакетов не требует: Unity 6 (Mono / .NET Standard 2.1) тянет его из коробки.

using System;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace Lilith.Face
{
    /// <summary>Состояния транспорта — для UI и логов.</summary>
    public enum WsState
    {
        Disconnected,
        Connecting,
        Open,
        Reconnecting,
        Failed,
    }

    /// <summary>
    /// WS-клиент продюсера лица. Потокобезопасен: приём/отправка живут в задачах,
    /// наружу отдаём только очереди, которые <see cref="LilithFaceClient"/> разбирает
    /// в <c>Update()</c> (Unity API трогать из фонового потока нельзя).
    /// </summary>
    public sealed class LilithWSClient : IDisposable
    {
        private readonly string _url;
        private readonly bool _autoReconnect;
        private readonly float _reconnectDelay;
        private readonly float _reconnectMaxDelay;

        private ClientWebSocket _socket;
        private CancellationTokenSource _cts;
        private Task _receiveTask;
        private Task _connectTask;

        private readonly ConcurrentQueue<string> _incoming = new ConcurrentQueue<string>();
        private readonly ConcurrentQueue<string> _outgoing = new ConcurrentQueue<string>();

        private int _receiveBufferSize = 64 * 1024;
        private float _currentDelay;
        private bool _disposed;

        /// <summary>Текущее состояние транспорта.</summary>
        public WsState State { get; private set; } = WsState.Disconnected;

        /// <summary>Последняя ошибка (для экрана/лога).</summary>
        public string LastError { get; private set; } = "";

        /// <summary>Сколько входящих кадров потеряно из-за переполнения (для stats.dropped).</summary>
        public long DroppedFrames { get; private set; }

        /// <summary>Кадров принято всего.</summary>
        public long ReceivedFrames { get; private set; }

        /// <summary>Событие: состояние изменилось (вызывается в фоновом потоке!).</summary>
        public event Action<WsState> StateChanged;

        public LilithWSClient(
            string url,
            bool autoReconnect = true,
            float reconnectDelay = 2f,
            float reconnectMaxDelay = 30f)
        {
            _url = url;
            _autoReconnect = autoReconnect;
            _reconnectDelay = Mathf.Max(0.25f, reconnectDelay);
            _reconnectMaxDelay = Mathf.Max(_reconnectDelay, reconnectMaxDelay);
            _currentDelay = _reconnectDelay;
        }

        /// <summary>Начать подключение (не блокирует вызывающий поток).</summary>
        public void Connect()
        {
            if (_disposed)
            {
                return;
            }

            DisconnectInternal();
            _cts = new CancellationTokenSource();
            _connectTask = Task.Run(() => ConnectLoopAsync(_cts.Token));
        }

        /// <summary>Поставить текст в очередь отправки (уйдёт, когда сокет открыт).</summary>
        public void Send(string json)
        {
            if (!string.IsNullOrEmpty(json))
            {
                _outgoing.Enqueue(json);
            }
        }

        /// <summary>Забрать один принятый кадр; <c>false</c>, если очередь пуста.</summary>
        public bool TryDequeueIncoming(out string json)
        {
            return _incoming.TryDequeue(out json);
        }

        /// <summary>Разорвать соединение и не переподключаться.</summary>
        public void Disconnect()
        {
            _autoReconnectDisabled = true;
            DisconnectInternal();
            SetState(WsState.Disconnected);
        }

        private volatile bool _autoReconnectDisabled;

        private void DisconnectInternal()
        {
            try
            {
                _cts?.Cancel();
            }
            catch (ObjectDisposedException)
            {
                // уже разобрано — не страшно
            }

            var socket = _socket;
            _socket = null;
            if (socket != null && socket.State == WebSocketState.Open)
            {
                try
                {
                    socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None)
                        .Wait(TimeSpan.FromSeconds(1));
                }
                catch (Exception)
                {
                    // закрытие может упасть, если сеть уже пропала
                }
            }

            socket?.Dispose();
            _cts?.Dispose();
            _cts = null;
        }

        private async Task ConnectLoopAsync(CancellationToken token)
        {
            while (!token.IsCancellationRequested && !_autoReconnectDisabled)
            {
                SetState(_receivedAnyFrame ? WsState.Reconnecting : WsState.Connecting);
                var socket = new ClientWebSocket();
                socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(15);

                try
                {
                    await socket.ConnectAsync(new Uri(_url), token).ConfigureAwait(false);
                    _socket = socket;
                    _currentDelay = _reconnectDelay;
                    LastError = "";
                    SetState(WsState.Open);

                    await RunSessionAsync(socket, token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    socket.Dispose();
                    return;
                }
                catch (Exception ex)
                {
                    LastError = Describe(ex);
                    SetState(WsState.Failed);
                    socket.Dispose();
                    _socket = null;
                }

                if (!_autoReconnect || _autoReconnectDisabled || token.IsCancellationRequested)
                {
                    break;
                }

                SetState(WsState.Reconnecting);
                try
                {
                    await Task.Delay(TimeSpan.FromSeconds(_currentDelay), token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }

                _currentDelay = Mathf.Min(_reconnectMaxDelay, _currentDelay * 1.7f);
            }
        }

        private bool _receivedAnyFrame;

        private async Task RunSessionAsync(ClientWebSocket socket, CancellationToken token)
        {
            _receiveTask = Task.Run(() => ReceiveLoopAsync(socket, token), token);

            var sendBuffer = new byte[_receiveBufferSize];
            while (!token.IsCancellationRequested && socket.State == WebSocketState.Open)
            {
                if (_outgoing.TryDequeue(out var json))
                {
                    var bytes = Encoding.UTF8.GetBytes(json);
                    if (bytes.Length > sendBuffer.Length)
                    {
                        sendBuffer = new byte[bytes.Length];
                    }

                    try
                    {
                        await socket.SendAsync(
                            new ArraySegment<byte>(bytes),
                            WebSocketMessageType.Text,
                            true,
                            token).ConfigureAwait(false);
                    }
                    catch (Exception ex)
                    {
                        LastError = Describe(ex);
                        break;
                    }
                }
                else
                {
                    await Task.Delay(2, token).ConfigureAwait(false);
                }
            }

            try
            {
                await _receiveTask.ConfigureAwait(false);
            }
            catch (Exception)
            {
                // ошибка приёма уже зафиксирована в LastError
            }
        }

        private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken token)
        {
            var buffer = new byte[_receiveBufferSize];
            var builder = new StringBuilder();

            while (!token.IsCancellationRequested && socket.State == WebSocketState.Open)
            {
                WebSocketReceiveResult result;
                try
                {
                    result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
                catch (Exception ex)
                {
                    LastError = Describe(ex);
                    return;
                }

                if (result.MessageType == WebSocketMessageType.Close)
                {
                    LastError = "сервер закрыл соединение";
                    return;
                }

                builder.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                if (!result.EndOfMessage)
                {
                    continue;
                }

                var message = builder.ToString();
                builder.Clear();
                ReceivedFrames++;
                _receivedAnyFrame = true;

                // Очередь не ограничена жёстко: если потребитель не успевает,
                // считаем потери и сбрасываем старьё (аудио всё равно уже не синхронно).
                if (_incoming.Count > 512)
                {
                    DroppedFrames += _incoming.Count;
                    while (_incoming.TryDequeue(out _))
                    {
                    }
                }

                _incoming.Enqueue(message);
            }
        }

        private void SetState(WsState next)
        {
            if (State == next)
            {
                return;
            }

            State = next;
            try
            {
                StateChanged?.Invoke(next);
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[Lilith] обработчик StateChanged упал: {ex.Message}");
            }
        }

        private static string Describe(Exception ex)
        {
            var text = ex.Message;
            while (ex.InnerException != null)
            {
                ex = ex.InnerException;
                text += " → " + ex.Message;
            }

            return text;
        }

        public void Dispose()
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            DisconnectInternal();
        }
    }
}
