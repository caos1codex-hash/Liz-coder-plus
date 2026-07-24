// ============================================================
// File: Program.cs
// Project: Liz Coder Plus - Desktop
// Description: Entry point for unpackaged WinUI 3 application.
// Includes crash diagnostics (writes error.log on failure).
// ============================================================

using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;

namespace LizCoderPlus.Desktop;

/// <summary>
/// Bootstrapper for the unpackaged WinUI 3 desktop application.
/// This is required because unpackaged WinUI 3 apps do not
/// auto-generate a Main method from the XAML compiler.
/// </summary>
public static class Program
{
    /// <summary>
    /// Application entry point. Creates and starts the WinUI Application.
    /// </summary>
    [STAThread]
    static void Main(string[] args)
    {
        var logPath = Path.Combine(AppContext.BaseDirectory, "startup.log");
        void Log(string msg)
        {
            try { File.AppendAllText(logPath, $"[{DateTime.Now:HH:mm:ss}] {msg}\n"); } catch { }
        }

        try
        {
            Log("=== Liz Coder Plus Starting ===");
            Log($"OS: {Environment.OSVersion}");
            Log($".NET: {Environment.Version}");
            Log($"Directory: {AppContext.BaseDirectory}");
            Log($"Architecture: {(Environment.Is64BitProcess ? "x64" : "x86")}");

            // Check critical WinUI 3 runtime DLLs
            var criticalDlls = new[]
            {
                "Microsoft.Interop.WindowsRuntime.dll",
                "WinRT.Runtime.dll",
                "Microsoft.WindowsAppSDK.dll",
                "Microsoft.UI.Xaml.dll"
            };
            foreach (var dll in criticalDlls)
            {
                var exists = File.Exists(Path.Combine(AppContext.BaseDirectory, dll));
                Log($"DLL {dll}: {(exists ? "FOUND" : "!! MISSING !!")}");
            }

            // Check if WindowsAppSDK native runtime exists
            var nativeDirs = new[] { "", "runtimes\\win-x64\\native\\" };
            foreach (var dir in nativeDirs)
            {
                var fullPath = Path.Combine(AppContext.BaseDirectory, dir);
                if (Directory.Exists(fullPath))
                {
                    Log($"Native dir {dir}: exists");
                }
            }

            Log("Initializing ComWrappers...");
            WinRT.ComWrappersSupport.InitializeComWrappers();

            Log("Starting WinUI Application...");
            Application.Start(p =>
            {
                Log("Creating App instance...");
                return new App();
            });

            Log("Application exited normally.");
        }
        catch (Exception ex)
        {
            var errorText = $"CRASH: {ex.GetType().Name}\n\nMessage: {ex.Message}\n\nStack:\n{ex.StackTrace}";
            if (ex.InnerException != null)
            {
                errorText += $"\n\nInner: {ex.InnerException.GetType().Name}: {ex.InnerException.Message}\n{ex.InnerException.StackTrace}";
            }
            Log(errorText);
            File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "error.log"), errorText);

            // Show native Windows message box (no external dependencies needed)
            MessageBox(IntPtr.Zero, 
                $"Liz Coder Plus crashed on startup:\n\n{ex.GetType().Name}: {ex.Message}\n\nA file called error.log was created in the app folder.",
                "Liz Coder Plus - Error", 
                0x10);
        }
    }

    // P/Invoke for MessageBox - no dependency on System.Windows.Forms
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBox(IntPtr hWnd, string text, string caption, uint type);
}
