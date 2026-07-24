// ============================================================
// File: App.xaml.cs
// Project: Liz Coder Plus - Desktop
// Description: Application entry point and DI container setup.
// Sprint: 1 - Prompt 2 (chat wiring)
// Version: 0.17.0 - Fixed window lifecycle for unpackaged mode
// ============================================================

using System;
using System.IO;
using LizCoderPlus.Desktop.Views;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Windowing;
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
    /// Provides thread-safe access to the UI DispatcherQueue
    /// for ViewModels and services that need to update the UI.
    /// </summary>
    public static DispatcherQueue DispatcherQueue { get; private set; } = null!;

    /// <summary>
    /// Current application version string.
    /// </summary>
    public static string Version { get; } = "0.17.0";

    /// <summary>
    /// Initializes the application and configures the DI container.
    /// </summary>
    public App()
    {
        this.InitializeComponent();

        // Log app construction
        try
        {
            var logPath = Environment.GetEnvironmentVariable("LIZ_DIAG_LOG")
                ?? Path.Combine(AppContext.BaseDirectory, "startup.log");
            File.AppendAllText(logPath, $"  App() constructor called\n");
        }
        catch { }
    }

    /// <summary>
    /// Launches the main window when the application starts.
    /// </summary>
    /// <param name="args">Event arguments.</param>
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        var logPath = Environment.GetEnvironmentVariable("LIZ_DIAG_LOG")
            ?? Path.Combine(AppContext.BaseDirectory, "startup.log");

        try
        {
            File.AppendAllText(logPath, $"  OnLaunched called\n");
        }
        catch { }

        // Get the DispatcherQueue for this thread
        DispatcherQueue = DispatcherQueue.GetForCurrentThread()!;

        try
        {
            // Create the main window
            _mainWindow = new MainWindow();
            File.AppendAllText(logPath, $"  MainWindow created\n");

            // IMPORTANT: For unpackaged WinUI 3 apps, we MUST set the window
            // to the AppWindow and configure its presenter explicitly
            var appWindow = _mainWindow.GetAppWindow();
            if (appWindow != null)
            {
                File.AppendAllText(logPath, $"  Got AppWindow, setting size...\n");

                // Set window size: 1200x800
                appWindow.Resize(new Windows.Graphics.SizeInt32(1200, 800));

                // Center the window on screen
                var displayArea = DisplayArea.GetFromWindowId(
                    appWindow.Id, DisplayAreaFallback.Primary);
                if (displayArea != null)
                {
                    int x = (displayArea.WorkArea.Width - 1200) / 2;
                    int y = (displayArea.WorkArea.Height - 800) / 2;
                    appWindow.Move(new Windows.Graphics.PointInt32(
                        Math.Max(0, x), Math.Max(0, y)));
                }

                File.AppendAllText(logPath, $"  Window positioned\n");
            }
            else
            {
                File.AppendAllText(logPath, $"  WARNING: AppWindow was null, using fallback\n");
                // Fallback: just set the WinUI Window properties
                _mainWindow.AppWindow.Title = "Liz Coder Plus — AI Desktop Assistant";
            }

            // Activate the window (brings it to foreground and shows it)
            _mainWindow.Activate();
            File.AppendAllText(logPath, $"  Window.Activate() called\n");
        }
        catch (Exception ex)
        {
            try
            {
                File.AppendAllText(logPath, $"  OnLaunched ERROR: {ex.GetType().Name}: {ex.Message}\n{ex.StackTrace}\n");
            }
            catch { }

            // Show error even if logging fails
            _ = NativeMethods.MessageBox(
                IntPtr.Zero,
                $"Error al crear la ventana:\n\n{ex.GetType().Name}: {ex.Message}\n\nRevisa startup.log para mas detalles.",
                "Liz Coder Plus - Error",
                0x10);
        }
    }

    private static class NativeMethods
    {
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern int MessageBox(IntPtr hWnd, string text, string caption, uint type);
    }
}
