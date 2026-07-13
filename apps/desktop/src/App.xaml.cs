// ============================================================
// File: App.xaml.cs
// Project: Liz Coder Plus - Desktop
// Description: Application entry point and DI container setup.
// Sprint: 1 - Prompt 2 (chat wiring)
// ============================================================

using LizCoderPlus.Desktop.Views;
using Microsoft.UI.Xaml;

namespace LizCoderPlus.Desktop;

/// <summary>
/// Main application class. Wires up dependency injection,
/// host environment, and the main window lifecycle.
/// </summary>
public partial class App : Application
{
    private MainWindow? _mainWindow;

    /// <summary>
    /// Initializes the application and configures the DI container.
    /// </summary>
    public App()
    {
        this.InitializeComponent();
    }

    /// <summary>
    /// Launches the main window when the application starts.
    /// </summary>
    /// <param name="args">Event arguments.</param>
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _mainWindow = new MainWindow();
        _mainWindow.Activate();
    }
}
