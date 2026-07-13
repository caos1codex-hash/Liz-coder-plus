// ============================================================
// File: ChatViewModel.cs
// Project: Liz Coder Plus - Desktop
// Description: View model for the chat conversation view.
// Sprint: 1 - Prompt 1 (Foundation stub)
// ============================================================

using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;

namespace LizCoderPlus.Desktop.ViewModels;

/// <summary>
/// View model backing the chat conversation view.
/// </summary>
public sealed partial class ChatViewModel : ObservableObject
{
    [ObservableProperty]
    private string _currentMessage = string.Empty;

    /// <summary>
    /// Gets the observable collection of chat messages.
    /// </summary>
    public ObservableCollection<ChatMessage> Messages { get; } = new();

    /// <summary>
    /// Sends the current message to the backend.
    /// </summary>
    // TODO (Sprint 4): wire up to backend WebSocket client.
    public void Send()
    {
        if (string.IsNullOrWhiteSpace(CurrentMessage))
        {
            return;
        }

        // TODO: implement send logic.
        CurrentMessage = string.Empty;
    }
}

/// <summary>
/// Represents a single message in the chat conversation.
/// </summary>
public sealed class ChatMessage
{
    public required string Role { get; init; }
    public required string Content { get; init; }
    public DateTimeOffset Timestamp { get; init; } = DateTimeOffset.UtcNow;
}
