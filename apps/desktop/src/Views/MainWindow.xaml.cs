// ============================================================
// File: MainWindow.xaml.cs
// Project: Liz Coder Plus - Desktop
// Description: Code-behind for the main application window.
// Sprint: 1 - Prompt 2 (minimal chat UI)
// ============================================================

using System;
using System.Collections.ObjectModel;
using System.Threading;
using System.Threading.Tasks;
using LizCoderPlus.Desktop.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;

namespace LizCoderPlus.Desktop.Views;

/// <summary>
/// Main shell window of the desktop assistant. Wires the minimal chat
/// UI (textbox + send button + messages list) to the ChatService.
/// </summary>
public sealed partial class MainWindow : Window
{
    private readonly ChatService _chat;
    private readonly DispatcherQueue _dispatcher;
    private readonly ObservableCollection<UiMessage> _messages = new();

    // Pending streamed content for the current assistant bubble.
    private UiMessage? _currentAssistantBubble;

    public MainWindow()
    {
        this.InitializeComponent();
        Title = "Liz Coder Plus";

        _dispatcher = DispatcherQueue.GetForCurrentThread()!;
        _chat = new ChatService();

        MessagesList.ItemsSource = _messages;
        _chat.MessageReceived += OnMessageReceived;
        _chat.ConnectionStateChanged += OnConnectionStateChanged;

        _ = _chat.ConnectAsync();
    }

    /// <summary>
    /// Activates and brings the window to the foreground.
    /// </summary>
    public void Activate()
    {
        this.Activate();
    }

    // ------------------------------------------------------------------
    // UI event handlers
    // ------------------------------------------------------------------

    private void OnMessageInputKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter)
        {
            e.Handled = true;
            _ = DoSendAsync();
        }
    }

    private void OnSendButtonClick(object sender, RoutedEventArgs e)
    {
        _ = DoSendAsync();
    }

    // ------------------------------------------------------------------
    // Chat service event handlers
    // ------------------------------------------------------------------

    private void OnMessageReceived(object? sender, ChatMessageReceivedEventArgs args)
    {
        // Always marshal to the UI thread before touching _messages.
        _dispatcher.TryEnqueue(() => ApplyEnvelope(args.Envelope));
    }

    private void OnConnectionStateChanged(object? sender, ConnectionStateChangedEventArgs args)
    {
        _dispatcher.TryEnqueue(() =>
        {
            StatusBadge.Text = args.State switch
            {
                ChatConnectionState.Connected => "Conectado",
                ChatConnectionState.Connecting => "Conectando...",
                ChatConnectionState.Reconnecting => "Reconectando...",
                ChatConnectionState.Failed => "Error de conexión",
                _ => "Desconectado"
            };
        });
    }

    // ------------------------------------------------------------------
    // Internals
    // ------------------------------------------------------------------

    private void ApplyEnvelope(ChatEnvelope envelope)
    {
        switch (envelope.Type)
        {
            case "chunk":
                // Accumulate streamed chunks into the current assistant bubble.
                if (_currentAssistantBubble is null)
                {
                    _currentAssistantBubble = new UiMessage { Role = "Liz" };
                    _messages.Add(_currentAssistantBubble);
                }

                _currentAssistantBubble.Content += envelope.Content + " ";
                break;

            case "message":
                // Finalize the assistant bubble with the full text.
                if (_currentAssistantBubble is null)
                {
                    _currentAssistantBubble = new UiMessage { Role = "Liz" };
                    _messages.Add(_currentAssistantBubble);
                }

                _currentAssistantBubble.Content = envelope.Content;
                _currentAssistantBubble = null;
                break;

            case "error":
                _messages.Add(new UiMessage
                {
                    Role = "Error",
                    Content = envelope.Error ?? "Error desconocido"
                });
                _currentAssistantBubble = null;
                break;

            case "status":
            case "pong":
            case "ping":
                // Lifecycle messages; no UI action required.
                break;
        }

        // Auto-scroll to the latest message.
        MessagesScroll.ScrollToVerticalOffset(double.MaxValue);
    }

    private async Task DoSendAsync()
    {
        var text = MessageInput.Text.Trim();
        if (string.IsNullOrEmpty(text))
        {
            return;
        }

        MessageInput.Text = string.Empty;

        // Render the user message immediately.
        _messages.Add(new UiMessage { Role = "Tú", Content = text });
        _currentAssistantBubble = null;

        SendButton.IsEnabled = false;
        try
        {
            await _chat.SendMessageAsync(text);
        }
        catch (Exception ex)
        {
            _messages.Add(new UiMessage { Role = "Error", Content = ex.Message });
        }
        finally
        {
            SendButton.IsEnabled = true;
        }
    }

    /// <summary>
    /// Simple display model for the messages list.
    /// </summary>
    public sealed class UiMessage
    {
        public string Role { get; set; } = string.Empty;
        public string Content { get; set; } = string.Empty;
    }
}
