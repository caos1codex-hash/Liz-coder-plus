// ============================================================
// File: AppSettings.cs
// Project: Liz Coder Plus - Desktop
// Description: Strongly-typed application settings model.
// Sprint: 1 - Prompt 1 (Foundation)
// ============================================================

using System;
using System.Collections.Generic;

namespace LizCoderPlus.Desktop.Models;

/// <summary>
/// Top-level configuration model for the desktop application.
/// </summary>
public sealed class AppSettings
{
    public string Version { get; init; } = "0.13.0";

    public string Environment { get; init; } = "development";

    public BackendSettings Backend { get; init; } = new();

    public PermissionSettings Permissions { get; init; } = new();
}

/// <summary>
/// Backend connection settings.
/// </summary>
public sealed class BackendSettings
{
    public string HttpUrl { get; init; } = "http://localhost:8000";

    public string WebSocketUrl { get; init; } = "ws://localhost:8000/ws/chat";

    public int TimeoutSeconds { get; init; } = 30;
}

/// <summary>
/// Permission policy settings.
/// </summary>
public sealed class PermissionSettings
{
    /// <summary>
    /// Permission mode: "Confirmation" or "Automatic".
    /// </summary>
    public string Mode { get; init; } = "confirmation";

    /// <summary>
    /// Whitelisted commands that can run automatically.
    /// </summary>
    public IReadOnlyList<string> AllowedCommands { get; init; } = Array.Empty<string>();
}
