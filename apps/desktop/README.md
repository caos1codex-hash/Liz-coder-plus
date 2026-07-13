# Liz Coder Plus - Desktop

> Cliente Windows construido con **WinUI 3 / .NET 8** siguiendo el patrón **MVVM**.

## Estado

**Sprint 1 - Prompt 1 (Foundation)** — Esqueleto del proyecto, sin UI funcional todavía.

## Arquitectura

```
src/
├── App.xaml(.cs)          → Punto de entrada + DI container
├── Views/                 → Vistas XAML (UI)
├── ViewModels/            → View models (MVVM, CommunityToolkit.Mvvm)
├── Services/              → Servicios (backend client, configuración, etc.)
├── Models/                → Modelos de datos y configuración
└── Assets/                → Recursos gráficos
```

## Principios

- **MVVM estricto:** las vistas no contienen lógica de negocio.
- **Inyección de dependencias:** todos los servicios se resuelven por interfaz.
- **Configuración por entorno:** los ajustes se cargan desde `config/` según el entorno.
- **Tipado seguro:** `Nullable enable` y `LangVersion latest`.

## Requisitos

- Windows 10 1809 (17763) o superior.
- .NET 8 SDK.
- Windows App SDK 1.5+.

## Compilación

```bash
dotnet restore
dotnet build
dotnet run
```

## Próximos pasos (Sprint 4)

- Implementar la ventana principal con chat.
- Conectar al backend por WebSocket.
- Añadir panel de configuración.
- Integrar sistema de permisos.
