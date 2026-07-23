// ============================================================
// File: UpdateService.cs
// Project: Liz Coder Plus - Desktop
// Description: GitHub Releases-based auto-update service.
//              Checks for new versions, downloads, and applies.
// ============================================================

using System;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace LizCoderPlus.Desktop.Services;

/// <summary>
/// Represents a GitHub release as returned by the API.
/// </summary>
public sealed class GitHubRelease
{
    public string TagName { get; set; } = string.Empty;
    public string Name { get; set; } = string.Empty;
    public string Body { get; set; } = string.Empty;
    public bool Prerelease { get; set; }
    public bool Draft { get; set; }
    public DateTimeOffset PublishedAt { get; set; }
    public string HtmlUrl { get; set; } = string.Empty;
    public GitHubAsset[] Assets { get; set; } = Array.Empty<GitHubAsset>();
}

/// <summary>
/// Represents a downloadable asset attached to a release.
/// </summary>
public sealed class GitHubAsset
{
    public string Name { get; set; } = string.Empty;
    public string BrowserDownloadUrl { get; set; } = string.Empty;
    public long Size { get; set; }
    public string ContentType { get; set; } = string.Empty;
}

/// <summary>
/// Event args raised when an update check completes.
/// </summary>
public sealed class UpdateCheckEventArgs : EventArgs
{
    public bool UpdateAvailable { get; init; }
    public GitHubRelease? LatestRelease { get; init; }
    public string CurrentVersion { get; init; } = string.Empty;
    public string? Error { get; init; }
}

/// <summary>
/// Event args for download progress.
/// </summary>
public sealed class UpdateProgressEventArgs : EventArgs
{
    public long BytesDownloaded { get; init; }
    public long TotalBytes { get; init; }
    public double Percent => TotalBytes > 0 ? (double)BytesDownloaded / TotalBytes * 100 : 0;
    public string Status { get; init; } = string.Empty;
}

/// <summary>
/// Service that checks GitHub Releases for new versions,
/// downloads updates, and triggers the installer.
/// </summary>
public sealed class UpdateService : IDisposable
{
    private const string GitHubOwner = "caos1codex-hash";
    private const string GitHubRepo = "Liz-coder-plus";
    private const string ReleasesApiUrl =
        $"https://api.github.com/repos/{GitHubOwner}/{GitHubRepo}/releases/latest";

    private readonly HttpClient _http;
    private readonly string _currentVersion;
    private CancellationTokenSource? _downloadCts;
    private bool _disposed;

    /// <summary>
    /// Creates a new UpdateService for the given current version.
    /// </summary>
    /// <param name="currentVersion">Semver string, e.g. "0.13.0"</param>
    public UpdateService(string currentVersion = "0.13.0")
    {
        _currentVersion = currentVersion;

        _http = new HttpClient();
        _http.DefaultRequestHeaders.Add("User-Agent", "LizCoderPlus-Desktop-Updater");
        _http.DefaultRequestHeaders.Add("Accept", "application/vnd.github+json");
        _http.Timeout = TimeSpan.FromSeconds(30);
    }

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------

    /// <summary>Raised when a version check finishes.</summary>
    public event EventHandler<UpdateCheckEventArgs>? UpdateCheckCompleted;

    /// <summary>Raised during download to report progress.</summary>
    public event EventHandler<UpdateProgressEventArgs>? DownloadProgressChanged;

    /// <summary>Raised when the download finishes.</summary>
    public event EventHandler<EventArgs>? DownloadCompleted;

    /// <summary>Raised when an error occurs during download.</summary>
    public event EventHandler<UpdateProgressEventArgs>? DownloadError;

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    /// <summary>
    /// Gets the current application version.
    /// </summary>
    public string CurrentVersion => _currentVersion;

    /// <summary>
    /// Asynchronously checks GitHub for a newer release version.
    /// </summary>
    public async Task CheckForUpdatesAsync(CancellationToken ct = default)
    {
        try
        {
            var response = await _http.GetStringAsync(ReleasesApiUrl, ct);

            using var doc = JsonDocument.Parse(response);
            var root = doc.RootElement;

            var release = new GitHubRelease
            {
                TagName = root.GetProperty("tag_name").GetString() ?? "",
                Name = root.GetProperty("name").GetString() ?? "",
                Body = root.GetProperty("body").GetString() ?? "",
                Prerelease = root.GetProperty("prerelease").GetBoolean(),
                Draft = root.GetProperty("draft").GetBoolean(),
                HtmlUrl = root.GetProperty("html_url").GetString() ?? "",
                PublishedAt = DateTimeOffset.Parse(
                    root.GetProperty("published_at").GetString() ?? DateTimeOffset.UtcNow.ToString()),
            };

            // Parse assets.
            var assets = root.GetProperty("assets");
            var assetList = new List<GitHubAsset>();
            foreach (var assetEl in assets.EnumerateArray())
            {
                assetList.Add(new GitHubAsset
                {
                    Name = assetEl.GetProperty("name").GetString() ?? "",
                    BrowserDownloadUrl = assetEl.GetProperty("browser_download_url").GetString() ?? "",
                    Size = assetEl.GetProperty("size").GetInt64(),
                    ContentType = assetEl.GetProperty("content_type").GetString() ?? "",
                });
            }
            release.Assets = assetList.ToArray();

            var latestVersion = NormalizeVersion(release.TagName);
            var currentNorm = NormalizeVersion(_currentVersion);
            var isUpdate = IsNewer(latestVersion, currentNorm);

            UpdateCheckCompleted?.Invoke(this, new UpdateCheckEventArgs
            {
                UpdateAvailable = isUpdate,
                LatestRelease = release,
                CurrentVersion = _currentVersion,
            });
        }
        catch (Exception ex)
        {
            UpdateCheckCompleted?.Invoke(this, new UpdateCheckEventArgs
            {
                UpdateAvailable = false,
                CurrentVersion = _currentVersion,
                Error = ex.Message,
            });
        }
    }

    /// <summary>
    /// Downloads the specified release asset to a temp directory.
    /// </summary>
    /// <param name="release">The release to download from.</param>
    /// <param name="assetName">Optional specific asset name. If null, picks first .zip.</param>
    /// <param name="ct">Cancellation token.</param>
    public async Task<string> DownloadUpdateAsync(
        GitHubRelease release,
        string? assetName = null,
        CancellationToken ct = default)
    {
        _downloadCts?.Cancel();
        _downloadCts = new CancellationTokenSource();
        var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(
            _downloadCts.Token, ct);

        try
        {
            // Find the right asset.
            var asset = string.IsNullOrEmpty(assetName)
                ? Array.Find(release.Assets, a =>
                    a.Name.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ||
                    a.Name.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
                : Array.Find(release.Assets, a =>
                    a.Name.Equals(assetName, StringComparison.OrdinalIgnoreCase));

            if (asset is null)
            {
                DownloadError?.Invoke(this, new UpdateProgressEventArgs
                {
                    Status = "No se encontro archivo de instalacion en el release.",
                });
                return string.Empty;
            }

            // Create temp file path.
            var tempDir = Path.Combine(
                Path.GetTempPath(), "LizCoderPlus_Updates");
            Directory.CreateDirectory(tempDir);
            var tempFile = Path.Combine(tempDir, asset.Name);

            // Report start.
            DownloadProgressChanged?.Invoke(this, new UpdateProgressEventArgs
            {
                Status = "Descargando actualizacion...",
                BytesDownloaded = 0,
                TotalBytes = asset.Size,
            });

            // Download with progress.
            using var response = await _http.GetAsync(
                asset.BrowserDownloadUrl, HttpCompletionOption.ResponseHeadersRead,
                linkedCts.Token);

            response.EnsureSuccessStatusCode();

            await using var contentStream = await response.Content.ReadAsStreamAsync(linkedCts.Token);
            await using var fileStream = new FileStream(tempFile, FileMode.Create, FileAccess.Write);

            var buffer = new byte[8192];
            long totalRead = 0;

            int bytesRead;
            while ((bytesRead = await contentStream.ReadAsync(buffer, linkedCts.Token)) > 0)
            {
                await fileStream.WriteAsync(buffer, 0, bytesRead, linkedCts.Token);
                totalRead += bytesRead;

                DownloadProgressChanged?.Invoke(this, new UpdateProgressEventArgs
                {
                    Status = $"Descargando... {totalRead / 1024.0:F0} KB / {asset.Size / 1024.0:F0} KB",
                    BytesDownloaded = totalRead,
                    TotalBytes = asset.Size,
                });
            }

            DownloadCompleted?.Invoke(this, EventArgs.Empty);
            return tempFile;
        }
        catch (OperationCanceledException)
        {
            DownloadError?.Invoke(this, new UpdateProgressEventArgs
            {
                Status = "Descarga cancelada.",
            });
            return string.Empty;
        }
        catch (Exception ex)
        {
            DownloadError?.Invoke(this, new UpdateProgressEventArgs
            {
                Status = $"Error en descarga: {ex.Message}",
            });
            return string.Empty;
        }
    }

    /// <summary>
    /// Cancels any in-progress download.
    /// </summary>
    public void CancelDownload()
    {
        _downloadCts?.Cancel();
    }

    /// <summary>
    /// Extracts a .zip update and returns the extracted directory path.
    /// </summary>
    public string? ExtractUpdate(string zipPath)
    {
        try
        {
            var extractDir = Path.Combine(
                Path.GetTempPath(), "LizCoderPlus_Extract", Guid.NewGuid().ToString("N"));

            DownloadProgressChanged?.Invoke(this, new UpdateProgressEventArgs
            {
                Status = "Extrayendo archivos...",
            });

            ZipFile.ExtractToDirectory(zipPath, extractDir, overwriteFiles: true);

            return extractDir;
        }
        catch (Exception ex)
        {
            DownloadError?.Invoke(this, new UpdateProgressEventArgs
            {
                Status = $"Error al extraer: {ex.Message}",
            });
            return null;
        }
    }

    /// <summary>
    /// Launches the installer .exe if found in the extracted directory.
    /// </summary>
    public bool LaunchInstaller(string extractDir)
    {
        var exe = Directory.GetFiles(extractDir, "*.exe", SearchOption.AllDirectories)
            .FirstOrDefault(f =>
                f.Contains("setup", StringComparison.OrdinalIgnoreCase) ||
                f.Contains("install", StringComparison.OrdinalIgnoreCase));

        if (exe is null)
        {
            // Fallback: pick any .exe.
            exe = Directory.GetFiles(extractDir, "*.exe", SearchOption.AllDirectories)
                .FirstOrDefault();
        }

        if (exe is null)
        {
            DownloadError?.Invoke(this, new UpdateProgressEventArgs
            {
                Status = "No se encontro ejecutable de instalacion.",
            });
            return false;
        }

        try
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = exe,
                UseShellExecute = true,
                Verb = "runas",
            });
            return true;
        }
        catch (Exception ex)
        {
            DownloadError?.Invoke(this, new UpdateProgressEventArgs
            {
                Status = $"Error al lanzar instalador: {ex.Message}",
            });
            return false;
        }
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private static string NormalizeVersion(string version)
    {
        // Strip 'v' prefix and keep only numeric parts.
        var cleaned = version.TrimStart('v', 'V');
        var parts = cleaned.Split('-')[0].Split('+')[0].Split('.');
        return string.Join('.', parts);
    }

    private static bool IsNewer(string latestStr, string currentStr)
    {
        try
        {
            var latest = Version.Parse(latestStr);
            var current = Version.Parse(currentStr);
            return latest > current;
        }
        catch
        {
            // Fallback: string comparison.
            return string.CompareOrdinal(latestStr, currentStr) > 0;
        }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _downloadCts?.Dispose();
        _http.Dispose();
    }
}
