// ============================================================
// File: App.xaml.cs
// Project: Liz Coder Plus - Desktop
// Description: Application entry point and DI container setup.
// Sprint: 1 - Prompt 1 (Foundation)
// ============================================================

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.UI.Xaml;

namespace LizCoderPlus.Desktop;

/// <summary>
/// Main application class. Wires up dependency injection,
/// host environment, and the main window lifecycle.
/// </summary>
public partial class App : Application
{
    private readonly IHost _host;

    /// <summary>
    /// Initializes the application and configures the DI container.
    /// </summary>
    public App()
    {
        this.InitializeComponent();

        _host = Host.CreateDefaultBuilder()
            .ConfigureServices(ConfigureServices)
            .Build();
    }

    /// <summary>
    /// Registers all services required by the desktop application.
    /// </summary>
    private static void ConfigureServices(IServiceCollection services)
    {
        // TODO (Sprint 1 - Prompt 2+): register views, view models, and services.
        // services.AddSingleton<MainWindow>();
        // services.AddTransient<ChatViewModel>();
        // services.AddSingleton<IBackendClient, WebSocketBackendClient>();
    }

    /// <summary>
    /// Launches the main window when the application starts.
    /// </summary>
    /// <param name="args">Event arguments.</param>
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        // TODO (Sprint 4): show the main window with the chat interface.
        // _host.Services.GetRequiredService<MainWindow>().Activate();
    }
}
