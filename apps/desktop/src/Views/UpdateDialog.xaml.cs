// ============================================================
// File: UpdateDialog.xaml.cs
// Project: Liz Coder Plus - Desktop
// Description: Code-behind for the update dialog.
//              Wires XAML controls to UpdateViewModel.
// ============================================================

using System;
using LizCoderPlus.Desktop.ViewModels;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LizCoderPlus.Desktop.Views;

/// <summary>
/// Dialog that lets the user check for updates, download, and install.
/// Fully driven by <see cref="UpdateViewModel"/>.
/// </summary>
public sealed partial class UpdateDialog : ContentDialog
{
    private readonly UpdateViewModel _vm;

    /// <summary>
    /// Creates a new UpdateDialog.
    /// </summary>
    /// <param name="currentVersion">Current app version string.</param>
    public UpdateDialog(string currentVersion = "0.13.0")
    {
        _vm = new UpdateViewModel(currentVersion);

        this.InitializeComponent();

        // Bind version labels.
        CurrentVersionText.Text = currentVersion;

        // Wire VM property changes to UI.
        _vm.PropertyChanged += OnViewModelPropertyChanged;
    }

    // ------------------------------------------------------------------
    // Event handlers
    // ------------------------------------------------------------------

    private void OnDialogOpened(ContentDialog sender, ContentDialogOpenedEventArgs args)
    {
        // Optionally auto-check on open.
        // _ = _vm.CheckForUpdatesCommand.ExecuteAsync(null);
    }

    private void OnCheckClick(object sender, RoutedEventArgs e)
    {
        _ = _vm.CheckForUpdatesCommand.ExecuteAsync(null);
    }

    private void OnDownloadClick(object sender, RoutedEventArgs e)
    {
        _ = _vm.DownloadUpdateCommand.ExecuteAsync(null);
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        _vm.CancelDownloadCommand.Execute(null);
    }

    private void OnInstallClick(object sender, RoutedEventArgs e)
    {
        _vm.InstallUpdateCommand.Execute(null);
    }

    private void OnClearErrorClick(object sender, RoutedEventArgs e)
    {
        _vm.ClearErrorCommand.Execute(null);
    }

    private void OnOpenReleaseClick(object sender, RoutedEventArgs e)
    {
        _vm.OpenReleasePageCommand.Execute(null);
    }

    // ------------------------------------------------------------------
    // VM -> UI binding
    // ------------------------------------------------------------------

    private void OnViewModelPropertyChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        // Use DispatcherQueue to marshal to UI thread.
        App.DispatcherQueue.TryEnqueue(() =>
        {
            switch (e.PropertyName)
            {
                case nameof(_vm.LatestVersion):
                    LatestVersionText.Text = _vm.LatestVersion;
                    break;

                case nameof(_vm.UpdateStatus):
                    StatusText.Text = _vm.UpdateStatus;
                    break;

                case nameof(_vm.IsChecking):
                    CheckButton.IsEnabled = !_vm.IsChecking;
                    CheckButton.Content = _vm.IsChecking
                        ? "Verificando..."
                        : "Buscar Actualizaciones";
                    break;

                case nameof(_vm.IsDownloading):
                    DownloadButton.IsEnabled = !_vm.IsDownloading && _vm.UpdateAvailable;
                    CancelButton.Visibility = _vm.IsDownloading
                        ? Visibility.Visible
                        : Visibility.Collapsed;
                    DownloadProgress.Visibility = _vm.IsDownloading
                        ? Visibility.Visible
                        : Visibility.Collapsed;
                    ProgressText.Visibility = _vm.IsDownloading
                        ? Visibility.Visible
                        : Visibility.Collapsed;
                    break;

                case nameof(_vm.DownloadPercent):
                    DownloadProgress.Value = _vm.DownloadPercent;
                    break;

                case nameof(_vm.DownloadProgressText):
                    ProgressText.Text = _vm.DownloadProgressText;
                    break;

                case nameof(_vm.UpdateAvailable):
                    DownloadButton.IsEnabled = _vm.UpdateAvailable && !_vm.IsDownloading;
                    break;

                case nameof(_vm.IsUpdateReady):
                    InstallButton.IsEnabled = _vm.IsUpdateReady;
                    InstallButton.Visibility = _vm.IsUpdateReady
                        ? Visibility.Visible
                        : Visibility.Collapsed;
                    DownloadButton.IsEnabled = false;
                    break;

                case nameof(_vm.ReleaseNotes):
                    ReleaseNotesText.Text = string.IsNullOrEmpty(_vm.ReleaseNotes)
                        ? "Sin notas de version."
                        : _vm.ReleaseNotes;
                    break;

                case nameof(_vm.ReleaseUrl):
                    OpenReleaseLink.Visibility = !string.IsNullOrEmpty(_vm.ReleaseUrl)
                        ? Visibility.Visible
                        : Visibility.Collapsed;
                    break;

                case nameof(_vm.HasError):
                    ErrorBorder.Visibility = _vm.HasError
                        ? Visibility.Visible
                        : Visibility.Collapsed;
                    break;

                case nameof(_vm.ErrorMessage):
                    ErrorText.Text = _vm.ErrorMessage;
                    break;
            }
        });
    }
}
