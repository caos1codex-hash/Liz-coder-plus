// ============================================================
// File: MainWindow.xaml.cs
// Project: Liz Coder Plus - Desktop
// Description: Code-behind for the main application window.
// Sprint: 5 — Full integration with ChatViewModel.
// ============================================================

using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Net.Http;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using LizCoderPlus.Desktop.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;

namespace LizCoderPlus.Desktop.Views;

/// <summary>
/// Main shell window of the desktop assistant. Provides the full
/// chat UI with streaming support, connection management, and
/// permission mode toggling.
/// </summary>
public sealed partial class MainWindow : Window
{
    private readonly ChatService _chat;
    private readonly DispatcherQueue _dispatcher;
    private readonly HttpClient _httpClient = new();
    private readonly ObservableCollection<UiMessage> _messages = new();
    private readonly ObservableCollection<ConversationItem> _conversations = new();

    // Pending streamed content for the current assistant bubble.
    private UiMessage? _currentAssistantBubble;

    // Permission mode tracking.
    private string _currentMode = "confirmation";

    // Selected model for chat.
    private string _selectedModel = "auto";

    // Settings visibility.
    private bool _settingsVisible;

    // Backend base URL.
    private const string BackendUrl = "http://localhost:8000";

    public MainWindow()
    {
        this.InitializeComponent();
        Title = "Liz Coder Plus — AI Desktop Assistant";

        _dispatcher = DispatcherQueue.GetForCurrentThread()!;
        _chat = new ChatService("ws://localhost:8000/ws/chat");

        MessagesList.ItemsSource = _messages;
        ConversationsList.ItemsSource = _conversations;
        _chat.MessageReceived += OnMessageReceived;
        _chat.ConnectionStateChanged += OnConnectionStateChanged;

        // Load some sample conversations for demo.
        _conversations.Add(new ConversationItem
        {
            Title = "Bienvenida",
            Preview = "Hola Liz, como estas?",
            Timestamp = DateTime.Now.ToString("HH:mm"),
        });
    }

    // ------------------------------------------------------------------
    // Connection
    // ------------------------------------------------------------------

    private async void OnConnectClick(object sender, RoutedEventArgs e)
    {
        try
        {
            await _chat.ConnectAsync();

            // After connecting, fetch available models and agent status.
            _ = FetchAvailableModelsAsync();
            _ = FetchAgentStatusAsync();
        }
        catch (Exception ex)
        {
            UpdateStatus("Error", ex.Message);
        }
    }

    private async void OnDisconnectClick(object sender, RoutedEventArgs e)
    {
        await _chat.DisconnectAsync();
    }

    // ------------------------------------------------------------------
    // Permission Mode
    // ------------------------------------------------------------------

    private void OnModeConfirmClick(object sender, RoutedEventArgs e)
    {
        _currentMode = "confirmation";
        _chat.Mode = "confirmation";
        ModeConfirmBtn.IsChecked = true;
        ModeAutoBtn.IsChecked = false;
    }

    private void OnModeAutoClick(object sender, RoutedEventArgs e)
    {
        _currentMode = "automatic";
        _chat.Mode = "automatic";
        ModeAutoBtn.IsChecked = true;
        ModeConfirmBtn.IsChecked = false;
    }

    // ------------------------------------------------------------------
    // Chat
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

    private void OnClearClick(object sender, RoutedEventArgs e)
    {
        _messages.Clear();
        _currentAssistantBubble = null;
    }

    // ------------------------------------------------------------------
    // Sidebar
    // ------------------------------------------------------------------

    private void OnNewChatClick(object sender, RoutedEventArgs e)
    {
        _messages.Clear();
        _currentAssistantBubble = null;

        // Create a new conversation entry.
        var title = $"Conversacion {_conversations.Count + 1}";
        _conversations.Insert(0, new ConversationItem
        {
            Title = title,
            Preview = "Nueva conversacion...",
            Timestamp = DateTime.Now.ToString("HH:mm"),
        });
        ConversationsList.SelectedIndex = 0;
    }

    private void OnConversationSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        // Placeholder: switch conversation context when conversations are implemented.
    }

    private void OnConversationDoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        // Placeholder: open conversation details.
    }

    // ------------------------------------------------------------------
    // Settings
    // ------------------------------------------------------------------

    private void OnSettingsClick(object sender, RoutedEventArgs e)
    {
        _settingsVisible = true;
        SettingsOverlay.Visibility = Visibility.Visible;
    }

    private void OnSaveSettingsClick(object sender, RoutedEventArgs e)
    {
        // Apply settings (placeholder — in production would save to file/env).
        _settingsVisible = false;
        SettingsOverlay.Visibility = Visibility.Collapsed;
    }

    private void OnCancelSettingsClick(object sender, RoutedEventArgs e)
    {
        _settingsVisible = false;
        SettingsOverlay.Visibility = Visibility.Collapsed;
    }

    // ------------------------------------------------------------------
    // Chat service event handlers
    // ------------------------------------------------------------------

    private void OnMessageReceived(object? sender, ChatMessageReceivedEventArgs args)
    {
        _dispatcher.TryEnqueue(() => ApplyEnvelope(args.Envelope));
    }

    private void OnConnectionStateChanged(object? sender, ConnectionStateChangedEventArgs args)
    {
        _dispatcher.TryEnqueue(() =>
        {
            switch (args.State)
            {
                case ChatConnectionState.Connected:
                    UpdateStatus("Conectado", null, connected: true);
                    SessionInfo.Text = $"Sesión: {_chat.SessionId[..8]}...";
                    break;

                case ChatConnectionState.Connecting:
                case ChatConnectionState.Reconnecting:
                    UpdateStatus("Conectando...", null, connecting: true);
                    break;

                case ChatConnectionState.Failed:
                    UpdateStatus("Error", args.Reason ?? "Desconocido", failed: true);
                    break;

                default:
                    UpdateStatus("Desconectado", null);
                    break;
            }
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
                    _currentAssistantBubble = new UiMessage
                    {
                        Role = "Liz",
                        IsAssistant = true,
                    };
                    _messages.Add(_currentAssistantBubble);
                }

                _currentAssistantBubble.Content += envelope.Content;
                break;

            case "message":
                // Finalize the assistant bubble with the full text.
                if (_currentAssistantBubble is not null)
                {
                    _currentAssistantBubble.Content = envelope.Content;
                    _currentAssistantBubble = null;
                }
                else if (!string.IsNullOrEmpty(envelope.Content))
                {
                    _messages.Add(new UiMessage
                    {
                        Role = "Liz",
                        Content = envelope.Content,
                        IsAssistant = true,
                    });
                }
                break;

            case "error":
                _messages.Add(new UiMessage
                {
                    Role = "Error",
                    Content = envelope.Error ?? envelope.Content ?? "Error desconocido",
                    IsError = true,
                });
                _currentAssistantBubble = null;
                break;

            case "status":
            case "pong":
            case "ping":
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
        _messages.Add(new UiMessage
        {
            Role = "Tú",
            Content = text,
            IsUser = true,
        });
        _currentAssistantBubble = null;

        SendButton.IsEnabled = false;
        SendButton.Content = "Enviando...";

        try
        {
            _chat.Mode = _currentMode;
            await _chat.SendMessageAsync(text);
        }
        catch (Exception ex)
        {
            _dispatcher.TryEnqueue(() =>
            {
                _messages.Add(new UiMessage
                {
                    Role = "Error",
                    Content = ex.Message,
                    IsError = true,
                });
            });
        }
        finally
        {
            SendButton.IsEnabled = true;
            SendButton.Content = "Enviar";
        }
    }

    private void UpdateStatus(
        string text,
        string? reason,
        bool connected = false,
        bool connecting = false,
        bool failed = false)
    {
        StatusBadge.Text = text;
        SessionInfo.Text = reason ?? "";

        // Update badge color via code-behind (WinUI 3).
        if (connected)
        {
            StatusBorder.Background = new SolidColorBrush(
                Microsoft.UI.Colors.Green);
        }
        else if (connecting)
        {
            StatusBorder.Background = new SolidColorBrush(
                Microsoft.UI.Colors.Orange);
        }
        else if (failed)
        {
            StatusBorder.Background = new SolidColorBrush(
                Microsoft.UI.Colors.Red);
        }
        else
        {
            StatusBorder.Background = new SolidColorBrush(
                Microsoft.UI.Colors.Gray);
        }
    }

    // ------------------------------------------------------------------
    // Model selection
    // ------------------------------------------------------------------

    private void OnModelSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ModelSelector.SelectedItem is ComboBoxItem item)
        {
            _selectedModel = item.Tag?.ToString() ?? "auto";
        }
    }

    /// <summary>
    /// Fetches the list of available models from the backend API
    /// and populates the model selector ComboBox.
    /// </summary>
    private async Task FetchAvailableModelsAsync()
    {
        try
        {
            var response = await _httpClient.GetStringAsync(
                $"{BackendUrl}/api/models");

            using var doc = JsonDocument.Parse(response);
            var models = doc.RootElement.GetProperty("models");

            _dispatcher.TryEnqueue(() =>
            {
                // Keep the "Auto" item.
                ModelSelector.Items.Clear();
                ModelSelector.Items.Add(new ComboBoxItem
                {
                    Content = "Auto (por defecto)",
                    Tag = "auto",
                });

                foreach (var model in models.EnumerateArray())
                {
                    var modelId = model.GetProperty("id").GetString() ?? "";
                    var provider = model.GetProperty("provider").GetString() ?? "";
                    var displayName = modelId.Contains('/')
                        ? modelId.Split('/').Last()
                        : modelId;

                    ModelSelector.Items.Add(new ComboBoxItem
                    {
                        Content = $"{displayName} ({provider})",
                        Tag = modelId,
                    });
                }

                ModelSelector.SelectedIndex = 0;
            });
        }
        catch (Exception ex)
        {
            _dispatcher.TryEnqueue(() =>
            {
                AgentStatusText.Text = $"Modelos: N/A";
            });
        }
    }

    /// <summary>
    /// Fetches the multiagent status from the backend API
    /// and updates the agent status indicator.
    /// </summary>
    private async Task FetchAgentStatusAsync()
    {
        try
        {
            var response = await _httpClient.GetStringAsync(
                $"{BackendUrl}/api/multiagent/status");

            using var doc = JsonDocument.Parse(response);
            var status = doc.RootElement.GetProperty("status").GetString();
            var agentsCount = doc.RootElement.GetProperty("agents_count").GetInt32();

            _dispatcher.TryEnqueue(() =>
            {
                if (status == "active")
                {
                    AgentStatusText.Text = $"{agentsCount} agentes activos";
                    AgentStatusIcon.Foreground = new SolidColorBrush(
                        Microsoft.UI.Colors.Green);
                }
                else
                {
                    AgentStatusText.Text = "Multiagent: inactivo";
                    AgentStatusIcon.Foreground = new SolidColorBrush(
                        Microsoft.UI.Colors.Gray);
                }
            });
        }
        catch
        {
            // Silently ignore if the endpoint isn't available yet.
        }
    }

    /// <summary>
    /// Simple display model for the messages list.
    /// Implements INotifyPropertyChanged for data binding updates.
    /// </summary>
    public sealed class UiMessage : INotifyPropertyChanged
    {
        private string _role = string.Empty;
        private string _content = string.Empty;

        public string Role
        {
            get => _role;
            set { _role = value; OnPropertyChanged(); }
        }

        public string Content
        {
            get => _content;
            set { _content = value; OnPropertyChanged(); }
        }

        public bool IsUser { get; init; }
        public bool IsAssistant { get; init; }
        public bool IsError { get; init; }

        public event PropertyChangedEventHandler? PropertyChanged;

        private void OnPropertyChanged([CallerMemberName] string? name = null)
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        }
    }

    /// <summary>
    /// Display model for conversation items in the sidebar.
    /// </summary>
    public sealed class ConversationItem
    {
        public string Title { get; init; } = string.Empty;
        public string Preview { get; init; } = string.Empty;
        public string Timestamp { get; init; } = string.Empty;
    }
}
