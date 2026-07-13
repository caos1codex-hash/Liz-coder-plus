// ============================================================
// File: MainWindow.xaml.cs
// Project: Liz Coder Plus - Desktop
// Description: Code-behind for the main application window.
// Sprint: 1 - Prompt 1 (Foundation)
// ============================================================

using Microsoft.UI.Xaml;

namespace LizCoderPlus.Desktop.Views;

/// <summary>
/// Main shell window of the desktop assistant.
/// </summary>
public sealed partial class MainWindow : Window
{
    /// <summary>
    /// Initializes a new instance of the <see cref="MainWindow"/> class.
    /// </summary>
    public MainWindow()
    {
        this.InitializeComponent();
        Title = "Liz Coder Plus";
    }

    /// <summary>
    /// Activates and brings the window to the foreground.
    /// </summary>
    public void Activate()
    {
        // TODO (Sprint 4): position window and apply saved settings.
        this.Activate();
    }
}
