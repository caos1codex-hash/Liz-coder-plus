// ============================================================
// File: Program.cs
// Project: Liz Coder Plus - Desktop
// Description: Entry point for unpackaged WinUI 3 application.
// Self-contained mode: all runtime DLLs are in the app folder.
// Version: 0.17.0 - Added extensive diagnostic logging
// ============================================================

using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;

namespace LizCoderPlus.Desktop;

/// <summary>
/// Bootstrapper for the unpackaged WinUI 3 desktop application.
/// Self-contained deployment: no Bootstrap needed since all
/// WindowsAppSDK runtime DLLs are in the publish folder.
/// </summary>
public static class Program
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int NativeMessageBox(IntPtr hWnd, string text, string caption, uint type);

    [STAThread]
    static void Main(string[] args)
    {
        // Write a log IMMEDIATELY before anything else - proves .NET is running
        var logPath = Path.Combine(AppContext.BaseDirectory, "startup.log");
        try
        {
            File.WriteAllText(logPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] Liz Coder Plus v0.17.0 starting...\n");
            File.AppendAllText(logPath, $"  BaseDirectory: {AppContext.BaseDirectory}\n");
            File.AppendAllText(logPath, $"  Runtime: {RuntimeInformation.FrameworkDescription}\n");
            File.AppendAllText(logPath, $"  OS: {RuntimeInformation.OSDescription}\n");
            File.AppendAllText(logPath, $"  Args: {string.Join(", ", args)}\n");
        }
        catch { }

        try
        {
            // Initialize COM wrappers for WinRT
            WinRT.ComWrappersSupport.InitializeComWrappers();
            File.AppendAllText(logPath, $"  COM Wrappers initialized OK\n");

            // Set the main window size via environment variable (read by App.xaml.cs)
            Environment.SetEnvironmentVariable("LIZ_DIAG_LOG", logPath);

            // Start the WinUI Application directly
            File.AppendAllText(logPath, $"  Calling Application.Start...\n");
            Application.Start(p =>
            {
                File.AppendAllText(logPath, $"  Inside Application.Start callback, creating App...\n");
                _ = new App();
                File.AppendAllText(logPath, $"  App created OK\n");
            });

            File.AppendAllText(logPath, $"  Application.Start returned (app exiting normally)\n");
        }
        catch (Exception ex)
        {
            var msg = $"[{DateTime.Now}] CRASH:\n{ex.GetType().Name}: {ex.Message}\n\n{ex.StackTrace}";
            if (ex.InnerException != null)
                msg += $"\n\nInner: {ex.InnerException.GetType().Name}: {ex.InnerException.Message}\n{ex.InnerException.StackTrace}";
            File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "error.log"), msg);

            try { File.AppendAllText(logPath, $"  CRASH: {ex.GetType().Name}: {ex.Message}\n"); } catch { }

            NativeMessageBox(0,
                $"Liz Coder Plus error:\n\n{ex.GetType().Name}: {ex.Message}\n\nSe creo error.log en la carpeta de la app.",
                "Liz Coder Plus - Error", 0x10);
        }
    }
}
