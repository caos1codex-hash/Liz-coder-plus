// ============================================================
// File: ChatService.cs
// Project: Liz Coder Plus - Desktop
// Description: WebSocket client used by the desktop UI to talk
//              to the backend /ws/chat endpoint.
// Sprint: 1 - Prompt 2
// ============================================================

using System;
using System.Collections.Generic;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace LizCoderPlus.Desktop.Services;

/// <summary>
/// Event args for an incoming WebSocket message from the backend.
/// </summary>
public sealed class ChatMessageReceivedEventArgs : EventArgs
{
    /// <summary>Raw envelope returned by the backend.</summary>
    public required ChatEnvelope Envelope { get; init; }
}

/// <summary>
/// Event args for connection state changes.
/// </summary>
public sealed class ConnectionStateChangedEventArgs : EventArgs
{
    public required ChatConnectionState State { get; init; }

    public string? Reason { get; init; }
}

/// <summary>
/// Connection states observable by the UI.
/// </summary>
public enum ChatConnectionState
{
    Disconnected,
    Connecting,
    Connected,
    Reconnecting,
    Failed
}

/// <summary>
/// Wire-format envelope used for server -> client messages.
/// Mirrors <c>WebSocketChatResponse</c> in packages/shared/src/ws_models.py.
/// </summary>
public sealed class ChatEnvelope
{
    public string Type { get; set; } = "message";
    public string Content { get; set; } = string.Empty;
    public string Status { get; set; } = "completed";
    public string? SessionId { get; set; }
    public string? MessageId { get; set; }
    public string? Error { get; set; }
}

/// <summary>
/// Wire-format request used for client -> server messages.
/// Mirrors <c>WebSocketChatRequest</c> in packages/shared/src/ws_models.py.
/// </summary>
public sealed class ChatRequest
{
    public string Message { get; set; } = string.Empty;
    public string SessionId { get; set; } = string.Empty;
    public string Mode { get; set; } = "confirmation";
}

/// <summary>
/// WebSocket client for the /ws/chat backend endpoint.
/// </summary>
public sealed class ChatService : IDisposable
{
    private const int ReceiveBufferSize = 8 * 1024;
    private const double PingIntervalSeconds = 30;

    private readonly Uri _wsUri;
    private ClientWebSocket? _socket;
    private CancellationTokenSource? _receiveCts;
    private CancellationTokenSource? _pingCts;

    private string _sessionId = Guid.NewGuid().ToString("D");
    private string _mode = "confirmation";

    /// <summary>
    /// Creates a ChatService targeting the given WebSocket URL.
    /// </summary>
    /// <param name="webSocketUrl">e.g. "ws://localhost:8000/ws/chat".</param>
    public ChatService(string webSocketUrl = "ws://localhost:8000/ws/chat")
    {
        _wsUri = new Uri(webSocketUrl);
    }

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------

    /// <summary>Raised whenever a complete envelope is received.</summary>
    public event EventHandler<ChatMessageReceivedEventArgs>? MessageReceived;

    /// <summary>Raised when the connection state changes.</summary>
    public event EventHandler<ConnectionStateChangedEventArgs>? ConnectionStateChanged;

    // ------------------------------------------------------------------
    // Properties
    // ------------------------------------------------------------------

    /// <summary>Current connection state.</summary>
    public ChatConnectionState State { get; private set; } = ChatConnectionState.Disconnected;

    /// <summary>Session id used in outbound messages.</summary>
    public string SessionId
    {
        get => _sessionId;
        set => _sessionId = string.IsNullOrWhiteSpace(value)
            ? Guid.NewGuid().ToString("D")
            : value;
    }

    /// <summary>Permission mode used in outbound messages.</summary>
    public string Mode
    {
        get => _mode;
        set => _mode = value is "confirmation" or "automatic" ? value : "confirmation";
    }

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    /// <summary>
    /// Connect to the backend WebSocket endpoint.
    /// </summary>
    public async Task ConnectAsync(CancellationToken cancellationToken = default)
    {
        if (State is ChatConnectionState.Connected or ChatConnectionState.Connecting)
        {
            return;
        }

        SetState(ChatConnectionState.Connecting);

        _socket?.Dispose();
        _socket = new ClientWebSocket();

        try
        {
            await _socket.ConnectAsync(_wsUri, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            SetState(ChatConnectionState.Failed, ex.Message);
            throw;
        }

        SetState(ChatConnectionState.Connected);

        _receiveCts = new CancellationTokenSource();
        _pingCts = new CancellationTokenSource();

        _ = Task.Run(() => ReceiveLoopAsync(_receiveCts.Token), _receiveCts.Token);
        _ = Task.Run(() => PingLoopAsync(_pingCts.Token), _pingCts.Token);
    }

    /// <summary>
    /// Send a chat message to the backend.
    /// </summary>
    public async Task SendMessageAsync(string message, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(message))
        {
            throw new ArgumentException("Message cannot be empty.", nameof(message));
        }

        if (_socket is null || _socket.State != WebSocketState.Open)
        {
            throw new InvalidOperationException("WebSocket is not connected.");
        }

        var request = new ChatRequest
        {
            Message = message,
            SessionId = _sessionId,
            Mode = _mode
        };

        var json = JsonSerializer.Serialize(request);
        var bytes = Encoding.UTF8.GetBytes(json);
        var segment = new ArraySegment<byte>(bytes);

        await _socket.SendAsync(
            segment,
            WebSocketMessageType.Text,
            endOfMessage: true,
            cancellationToken
        ).ConfigureAwait(false);
    }

    /// <summary>
    /// Convenience helper that awaits the next inbound envelope and
    /// returns it. Useful for tests and simple scripts.
    /// </summary>
    public async Task<ChatEnvelope> ReceiveMessageAsync(CancellationToken cancellationToken = default)
    {
        var tcs = new TaskCompletionSource<ChatEnvelope>(TaskCreationOptions.RunContinuationsAsynchronously);

        void OnMessage(object? sender, ChatMessageReceivedEventArgs args)
        {
            try { tcs.TrySetResult(args.Envelope); } catch { /* ignore */ }
        }

        MessageReceived += OnMessage;
        try
        {
            using (cancellationToken.Register(() => tcs.TrySetCanceled(cancellationToken)))
            {
                return await tcs.Task.ConfigureAwait(false);
            }
        }
        finally
        {
            MessageReceived -= OnMessage;
        }
    }

    /// <summary>
    /// Disconnect from the backend gracefully.
    /// </summary>
    public async Task DisconnectAsync(CancellationToken cancellationToken = default)
    {
        _receiveCts?.Cancel();
        _pingCts?.Cancel();

        if (_socket is not null && _socket.State == WebSocketState.Open)
        {
            try
            {
                await _socket.CloseAsync(
                    WebSocketCloseStatus.NormalClosure,
                    "client disconnect",
                    cancellationToken
                ).ConfigureAwait(false);
            }
            catch
            {
                // Best effort - we are tearing down anyway.
            }
        }

        SetState(ChatConnectionState.Disconnected);
    }

    /// <inheritdoc/>
    public void Dispose()
    {
        try { DisconnectAsync().GetAwaiter().GetResult(); } catch { /* ignore */ }
        _receiveCts?.Dispose();
        _pingCts?.Dispose();
        _socket?.Dispose();
    }

    // ------------------------------------------------------------------
    // Internals
    // ------------------------------------------------------------------

    private async Task ReceiveLoopAsync(CancellationToken cancellationToken)
    {
        if (_socket is null)
        {
            return;
        }

        var buffer = new byte[ReceiveBufferSize];
        var messageBuilder = new StringBuilder();

        try
        {
            while (!cancellationToken.IsCancellationRequested &&
                   _socket.State == WebSocketState.Open)
            {
                WebSocketReceiveResult result;
                messageBuilder.Clear();

                do
                {
                    result = await _socket.ReceiveAsync(
                        new ArraySegment<byte>(buffer),
                        cancellationToken
                    ).ConfigureAwait(false);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        SetState(ChatConnectionState.Disconnected, "server closed");
                        return;
                    }

                    var chunk = Encoding.UTF8.GetString(buffer, 0, result.Count);
                    messageBuilder.Append(chunk);
                }
                while (!result.EndOfMessage);

                if (result.MessageType != WebSocketMessageType.Text)
                {
                    continue;
                }

                var envelope = TryParseEnvelope(messageBuilder.ToString());
                if (envelope is null)
                {
                    continue;
                }

                MessageReceived?.Invoke(
                    this,
                    new ChatMessageReceivedEventArgs { Envelope = envelope }
                );
            }
        }
        catch (OperationCanceledException)
        {
            // Expected on disconnect.
        }
        catch (Exception ex)
        {
            SetState(ChatConnectionState.Failed, ex.Message);
        }
    }

    private async Task PingLoopAsync(CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(PingIntervalSeconds), cancellationToken)
                          .ConfigureAwait(false);

                if (_socket is null || _socket.State != WebSocketState.Open)
                {
                    continue;
                }

                var ping = JsonSerializer.Serialize(new { type = "ping" });
                var bytes = Encoding.UTF8.GetBytes(ping);

                await _socket.SendAsync(
                    new ArraySegment<byte>(bytes),
                    WebSocketMessageType.Text,
                    endOfMessage: true,
                    cancellationToken
                ).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException)
        {
            // Expected on disconnect.
        }
        catch
        {
            // Ping failures are non-fatal.
        }
    }

    private static ChatEnvelope? TryParseEnvelope(string json)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            return null;
        }

        try
        {
            var envelope = JsonSerializer.Deserialize<ChatEnvelope>(json);
            return envelope ?? null;
        }
        catch
        {
            return null;
        }
    }

    private void SetState(ChatConnectionState newState, string? reason = null)
    {
        if (State == newState && reason is null)
        {
            return;
        }

        State = newState;
        ConnectionStateChanged?.Invoke(
            this,
            new ConnectionStateChangedEventArgs { State = newState, Reason = reason }
        );
    }
}
