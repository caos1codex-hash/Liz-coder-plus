// ============================================================
// File: UpdateViewModel.cs
// Project: Liz Coder Plus - Desktop
// Description: MVVM ViewModel for the auto-update feature.
//              Checks GitHub Releases, downloads, and installs.
// ============================================================

using System;
using System.Threading.Tasks;
using System.Windows.Input;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using LizCoderPlus.Desktop.Services;

namespace LizCoderPlus.Desktop.ViewModels;

/// <summary>
/// View model that drives the update check / download / install flow.
/// Binds to UpdateDialog.xaml and is also accessible from MainWindow.
/// </summary>
public sealed partial class UpdateViewModel : ObservableObject, IDisposable
{
    private readonly UpdateService _updateService;
    private bool _disposed;

    // ------------------------------------------------------------------
    // Observable properties
    // ------------------------------------------------------------------

    [ObservableProperty]
    private string _currentVersion = "0.13.0";

    [ObservableProperty]
    private string _latestVersion = "---";

    [ObservableProperty]
    private string _updateStatus = "Sin verificar";

    [ObservableProperty]
    private bool _isChecking;

    [ObservableProperty]
    private bool _isDownloading;

    [ObservableProperty]
    private bool _updateAvailable;

    [ObservableProperty]
    private double _downloadPercent;

    [ObservableProperty]
    private string _downloadProgressText = string.Empty;

    [ObservableProperty]
    private string _releaseNotes = string.Empty;

    [ObservableProperty]
    private string _releaseUrl = string.Empty;

    [ObservableProperty]
    private bool _isUpdateReady;

    [ObservableProperty]
    private string _downloadedFilePath = string.Empty;

    [ObservableProperty]
    private string _errorMessage = string.Empty;

    [ObservableProperty]
    private bool _hasError;

    // ------------------------------------------------------------------
    // Commands
    // ------------------------------------------------------------------

    /// <summary>Check GitHub for a newer version.</summary>
    [RelayCommand]
    private async Task CheckForUpdates()
    {
        IsChecking = true;
        UpdateStatus = "Verificando actualizaciones...";
        HasError = false;
        ErrorMessage = string.Empty;
        UpdateAvailable = false;
        IsUpdateReady = false;

        await _updateService.CheckForUpdatesAsync();

        // Result comes through the event handler below.
    }

    /// <summary>Download the available update.</summary>
    [RelayCommand]
    private async Task DownloadUpdate()
    {
        IsDownloading = true;
        UpdateStatus = "Iniciando descarga...";
        HasError = false;
        ErrorMessage = string.Empty;

        if (_latestRelease is null)
        {
            HasError = true;
            ErrorMessage = "No hay release disponible para descargar.";
            IsDownloading = false;
            return;
        }

        var path = await _updateService.DownloadUpdateAsync(_latestRelease);
        DownloadedFilePath = path;

        if (!string.IsNullOrEmpty(path))
        {
            UpdateStatus = "Descarga completada. Listo para instalar.";
            IsUpdateReady = true;
        }
    }

    /// <summary>Cancel the in-progress download.</summary>
    [RelayCommand]
    private void CancelDownload()
    {
        _updateService.CancelDownload();
        UpdateStatus = "Descarga cancelada.";
        IsDownloading = false;
    }

    /// <summary>Install the downloaded update.</summary>
    [RelayCommand]
    private void InstallUpdate()
    {
        if (string.IsNullOrEmpty(DownloadedFilePath))
        {
            HasError = true;
            ErrorMessage = "No hay archivo descargado para instalar.";
            return;
        }

        // Check if it's a .exe installer or a .zip
        if (DownloadedFilePath.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
        {
            LaunchExe(DownloadedFilePath);
        }
        else if (DownloadedFilePath.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
        {
            var extractDir = _updateService.ExtractUpdate(DownloadedFilePath);
            if (extractDir is not null)
            {
                _updateService.LaunchInstaller(extractDir);
                UpdateStatus = "Instalador lanzado. Cierra Liz Coder Plus para completar.";
            }
        }
    }

    /// <summary>Open the release page in the default browser.</summary>
    [RelayCommand]
    private void OpenReleasePage()
    {
        if (!string.IsNullOrEmpty(ReleaseUrl))
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = ReleaseUrl,
                UseShellExecute = true,
            });
        }
    }

    /// <summary>Dismiss any error.</summary>
    [RelayCommand]
    private void ClearError()
    {
        HasError = false;
        ErrorMessage = string.Empty;
    }

    // ------------------------------------------------------------------
    // Internal state
    // ------------------------------------------------------------------

    private GitHubRelease? _latestRelease;

    // ------------------------------------------------------------------
    // Constructor
    // ------------------------------------------------------------------

    public UpdateViewModel(string currentVersion = "0.13.0")
    {
        _updateService = new UpdateService(currentVersion);
        _updateService.UpdateCheckCompleted += OnUpdateCheckCompleted;
        _updateService.DownloadProgressChanged += OnDownloadProgressChanged;
        _updateService.DownloadCompleted += OnDownloadCompleted;
        _updateService.DownloadError += OnDownloadError;

        CurrentVersion = currentVersion;
    }

    // ------------------------------------------------------------------
    // Event handlers
    // ------------------------------------------------------------------

    private void OnUpdateCheckCompleted(object? sender, UpdateCheckEventArgs e)
    {
        App.DispatcherQueue.TryEnqueue(() =>
        {
            IsChecking = false;

            if (e.Error is not null)
            {
                HasError = true;
                ErrorMessage = $"Error al verificar: {e.Error}";
                UpdateStatus = "Error al verificar actualizaciones";
                return;
            }

            if (e.LatestRelease is null)
            {
                UpdateStatus = "No se encontraron releases.";
                return;
            }

            _latestRelease = e.LatestRelease;
            LatestVersion = e.LatestRelease.TagName.TrimStart('v', 'V');
            ReleaseNotes = e.LatestRelease.Body;
            ReleaseUrl = e.LatestRelease.HtmlUrl;

            if (e.UpdateAvailable)
            {
                UpdateAvailable = true;
                UpdateStatus = $"Nueva version disponible: {LatestVersion}";
            }
            else
            {
                UpdateAvailable = false;
                UpdateStatus = "Estas actualizado.";
            }
        });
    }

    private void OnDownloadProgressChanged(object? sender, UpdateProgressEventArgs e)
    {
        App.DispatcherQueue.TryEnqueue(() =>
        {
            DownloadProgressText = e.Status;
            DownloadPercent = e.Percent;
            UpdateStatus = e.Status;
        });
    }

    private void OnDownloadCompleted(object? sender, EventArgs e)
    {
        App.DispatcherQueue.TryEnqueue(() =>
        {
            IsDownloading = false;
            DownloadPercent = 100;
            UpdateStatus = "Descarga completada.";
        });
    }

    private void OnDownloadError(object? sender, UpdateProgressEventArgs e)
    {
        App.DispatcherQueue.TryEnqueue(() =>
        {
            IsDownloading = false;
            HasError = true;
            ErrorMessage = e.Status;
            UpdateStatus = "Error en la descarga.";
        });
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private static void LaunchExe(string path)
    {
        try
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = path,
                UseShellExecute = true,
                Verb = "runas",
            });
        }
        catch (Exception)
        {
            // Fallback without elevation.
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = path,
                UseShellExecute = true,
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

        _updateService.UpdateCheckCompleted -= OnUpdateCheckCompleted;
        _updateService.DownloadProgressChanged -= OnDownloadProgressChanged;
        _updateService.DownloadCompleted -= OnDownloadCompleted;
        _updateService.DownloadError -= OnDownloadError;
        _updateService.Dispose();
    }
}
