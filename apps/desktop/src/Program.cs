// ============================================================
// File: Program.cs
// Project: Liz Coder Plus - Desktop
// Description: Entry point for unpackaged WinUI 3 application.
// Initializes WindowsAppSDK Bootstrap for unpackaged deployment.
// ============================================================

using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;
using Microsoft.Windows.ApplicationModel.DynamicDependency;

namespace LizCoderPlus.Desktop;

/// <summary>
/// Bootstrapper for the unpackaged WinUI 3 desktop application.
/// Initializes the WindowsAppSDK runtime via Bootstrap before
/// starting the WinUI Application.
/// </summary>
public static class Program
{
    /// <summary>
    /// Application entry point.
    /// </summary>
    [STAThread]
    static void Main(string[] args)
    {
        // --- STEP 1: Bootstrap the WindowsAppSDK runtime ---
        // This is REQUIRED for unpackaged (non-MSIX) WinUI 3 apps.
        // Without it, the runtime DLLs are not found and the app
        // crashes silently before any managed code runs.
        try
        {
            Bootstrap.Initialize(0);
        }
        catch (Exception ex)
        {
            // Bootstrap failed - write log and show error
            var logDir = AppContext.BaseDirectory;
            try { Directory.CreateDirectory(logDir); } catch { }
            File.WriteAllText(Path.Combine(logDir, "bootstrap-error.log"),
                $"[{DateTime.Now}] Bootstrap Initialize FAILED:\n{ex}");
            NativeMessageBox(0,
                "Liz Coder Plus no puede inicializar el runtime de Windows.\n\n" +
                $"Error: {ex.Message}\n\n" +
                "Asegurate de tener Windows 10 version 1809 o superior.\n" +
                "Tambien puedes intentar instalar el Windows App Runtime desde:\n" +
                "https://aka.ms/windowsappsdk/latest",
                "Liz Coder Plus - Error Critico", 0x10);
            return;
        }

        // --- STEP 2: Initialize COM wrappers ---
        WinRT.ComWrappersSupport.InitializeComWrappers();

        // --- STEP 3: Start WinUI Application ---
        Application.Start(p =>
        {
            _ = new App();
        });
    }

    /// <summary>
    /// Native Win32 MessageBox - no dependency on System.Windows.Forms.
    /// </summary>
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int NativeMessageBox(IntPtr hWnd, string text, string caption, uint type);
}
