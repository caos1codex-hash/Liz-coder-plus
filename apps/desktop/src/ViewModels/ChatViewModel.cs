// ============================================================
// File: ChatViewModel.cs
// Project: Liz Coder Plus - Desktop
// Description: View model for the chat conversation view.
// Sprint: 5 — Full WebSocket integration with streaming.
// ============================================================

using System;
using System.Collections.ObjectModel;
using System.Text.Json;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using LizCoderPlus.Desktop.Services;

namespace LizCoderPlus.Desktop.ViewModels;

/// <summary>
/// View model backing the chat conversation view.
/// Handles WebSocket connection, message sending, streaming responses,
/// model selection, and permission mode toggling.
/// </summary>
public sealed partial class ChatViewModel : ObservableObject, IDisposable
{
    private readonly ChatService _chatService;
    private bool _disposed;

    [ObservableProperty]
    private string _currentMessage = string.Empty;

    [ObservableProperty]
    private string _connectionStatus = "Desconectado";

    [ObservableProperty]
    private bool _isConnected;

    [ObservableProperty]
    private bool _isProcessing;

    [ObservableProperty]
    private string _currentModel = "Auto";

    [ObservableProperty]
    private string _permissionMode = "confirmation";

    [ObservableProperty]
    private string _sessionInfo = string.Empty;

    /// <summary>
    /// Gets the observable collection of chat messages.
    /// </summary>
    public ObservableCollection<ChatMessage> Messages { get; } = new();

    /// <summary>
    /// Gets the available permission modes.
    /// </summary>
    public static string[] PermissionModes { get; } = { "confirmation", "automatic" };

    public ChatViewModel()
    {
        _chatService = new ChatService("ws://localhost:8000/ws/chat");
        _chatService.MessageReceived += OnMessageReceived;
        _chatService.ConnectionStateChanged += OnConnectionStateChanged;
    }

    // ------------------------------------------------------------------
    // Commands
    // ------------------------------------------------------------------

    [RelayCommand]
    private async Task Connect()
    {
        try
        {
            await _chatService.ConnectAsync();
        }
        catch (Exception ex)
        {
            ConnectionStatus = $"Error: {ex.Message}";
        }
    }

    [RelayCommand]
    private async Task Disconnect()
    {
        await _chatService.DisconnectAsync();
    }

    /// <summary>
    /// Sends the current message to the backend.
    /// </summary>
    [RelayCommand]
    private async Task Send()
    {
        if (string.IsNullOrWhiteSpace(CurrentMessage))
        {
            return;
        }

        if (!_chatService.State.ToString().Equals("Connected", StringComparison.OrdinalIgnoreCase))
        {
            Messages.Add(new ChatMessage
            {
                Role = "system",
                Content = "No hay conexión con el backend. Conecta primero.",
            });
            return;
        }

        var userMessage = CurrentMessage;
        CurrentMessage = string.Empty;

        // Add user message to the UI.
        Messages.Add(new ChatMessage
        {
            Role = "user",
            Content = userMessage,
        });

        IsProcessing = true;

        try
        {
            _chatService.Mode = PermissionMode;
            await _chatService.SendMessageAsync(userMessage);
        }
        catch (Exception ex)
        {
            Messages.Add(new ChatMessage
            {
                Role = "system",
                Content = $"Error al enviar: {ex.Message}",
            });
            IsProcessing = false;
        }
    }

    [RelayCommand]
    private void TogglePermissionMode()
    {
        PermissionMode = PermissionMode == "confirmation" ? "automatic" : "confirmation";
    }

    [RelayCommand]
    private void ClearChat()
    {
        Messages.Clear();
    }

    // ------------------------------------------------------------------
    // Event handlers
    // ------------------------------------------------------------------

    private void OnMessageReceived(object? sender, ChatMessageReceivedEventArgs e)
    {
        var envelope = e.Envelope;

        switch (envelope.Type)
        {
            case "chunk":
                // Streaming chunk — append to the last assistant message or create new.
                AppendToLastAssistantMessage(envelope.Content ?? "");
                break;

            case "message":
                // Final message — update/replace the last assistant message.
                if (!string.IsNullOrEmpty(envelope.Content))
                {
                    SetLastAssistantMessage(envelope.Content);
                }
                IsProcessing = false;
                break;

            case "error":
                Messages.Add(new ChatMessage
                {
                    Role = "system",
                    Content = $"Error: {envelope.Error ?? envelope.Content}",
                });
                IsProcessing = false;
                break;
        }
    }

    private void OnConnectionStateChanged(object? sender, ConnectionStateChangedEventArgs e)
    {
        switch (e.State)
        {
            case ChatConnectionState.Connected:
                ConnectionStatus = "Conectado";
                IsConnected = true;
                SessionInfo = $"Sesión: {_chatService.SessionId[..8]}...";
                break;

            case ChatConnectionState.Connecting:
            case ChatConnectionState.Reconnecting:
                ConnectionStatus = "Conectando...";
                break;

            case ChatConnectionState.Disconnected:
                ConnectionStatus = "Desconectado";
                IsConnected = false;
                break;

            case ChatConnectionState.Failed:
                ConnectionStatus = $"Falló: {e.Reason ?? "desconocido"}";
                IsConnected = false;
                break;
        }
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private void AppendToLastAssistantMessage(string content)
    {
        if (Messages.Count > 0 && Messages[^1].Role == "assistant")
        {
            // Append to existing streaming message.
            var lastMsg = Messages[^1];
            lastMsg.Content += content;
            // Force UI update by replacing the item.
            Messages[^1] = lastMsg with { Content = lastMsg.Content };
        }
        else
        {
            // Create new assistant message.
            Messages.Add(new ChatMessage
            {
                Role = "assistant",
                Content = content,
            });
        }
    }

    private void SetLastAssistantMessage(string content)
    {
        if (Messages.Count > 0 && Messages[^1].Role == "assistant")
        {
            var lastMsg = Messages[^1];
            Messages[^1] = lastMsg with { Content = content };
        }
        else
        {
            Messages.Add(new ChatMessage
            {
                Role = "assistant",
                Content = content,
            });
        }
    }

    // ------------------------------------------------------------------
    // IDisposable
    // ------------------------------------------------------------------

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;

        _chatService.MessageReceived -= OnMessageReceived;
        _chatService.ConnectionStateChanged -= OnConnectionStateChanged;
        _chatService.Dispose();
    }
}

/// <summary>
/// Represents a single message in the chat conversation.
/// </summary>
public sealed record ChatMessage
{
    public required string Role { get; init; }
    public required string Content { get; init; }
    public DateTimeOffset Timestamp { get; init; } = DateTimeOffset.UtcNow;
}
