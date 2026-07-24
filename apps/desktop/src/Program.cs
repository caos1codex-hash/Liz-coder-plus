// ============================================================
// File: Program.cs
// Project: Liz Coder Plus - Desktop
// Description: Entry point for unpackaged WinUI 3 application.
// Required when WindowsPackageType is None (no MSIX packaging).
// ============================================================

using System;
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
        WinRT.ComWrappersSupport.InitializeComWrappers();

        Application.Start(p => new App());
    }
}
