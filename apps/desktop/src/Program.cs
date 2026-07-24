// ============================================================
// File: Program.cs
// Project: Liz Coder Plus - Desktop
// Description: Entry point for unpackaged WinUI 3 application.
// Self-contained mode: all runtime DLLs are in the app folder.
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
    [STAThread]
    static void Main(string[] args)
    {
        try
        {
            // Initialize COM wrappers for WinRT
            WinRT.ComWrappersSupport.InitializeComWrappers();

            // Start the WinUI Application directly
            Application.Start(p =>
            {
                _ = new App();
            });
        }
        catch (Exception ex)
        {
            var logDir = AppContext.BaseDirectory;
            try { Directory.CreateDirectory(logDir); } catch { }
            var msg = $"[{DateTime.Now}] CRASH:\n{ex.GetType().Name}: {ex.Message}\n\n{ex.StackTrace}";
            if (ex.InnerException != null)
                msg += $"\n\nInner: {ex.InnerException.GetType().Name}: {ex.InnerException.Message}\n{ex.InnerException.StackTrace}";
            File.WriteAllText(Path.Combine(logDir, "error.log"), msg);

            NativeMessageBox(0,
                $"Liz Coder Plus error:\n\n{ex.GetType().Name}: {ex.Message}\n\nSe creo error.log en la carpeta de la app.",
                "Liz Coder Plus - Error", 0x10);
        }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int NativeMessageBox(IntPtr hWnd, string text, string caption, uint type);
}
