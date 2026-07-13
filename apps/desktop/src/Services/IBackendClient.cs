// ============================================================
// File: IBackendClient.cs
// Project: Liz Coder Plus - Desktop
// Description: Abstraction for backend communication.
// Sprint: 1 - Prompt 1 (Foundation stub)
// ============================================================

namespace LizCoderPlus.Desktop.Services;

/// <summary>
/// Contract for backend communication (REST + WebSocket).
/// </summary>
public interface IBackendClient
{
    /// <summary>
    /// Opens a persistent connection to the backend.
    /// </summary>
    Task ConnectAsync(CancellationToken cancellationToken = default);

    /// <summary>
    /// Sends a message and returns the assistant response stream.
    /// </summary>
    Task SendMessageAsync(string message, CancellationToken cancellationToken = default);

    /// <summary>
    /// Closes the backend connection.
    /// </summary>
    Task DisconnectAsync(CancellationToken cancellationToken = default);
}
