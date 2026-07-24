; ============================================================
; Liz Coder Plus - Inno Setup Installer Script
; Auto-generated for CI pipeline
; ============================================================

#define MyAppName "Liz Coder Plus"
#define MyAppVersion "0.18.0"
#define MyAppPublisher "Liz Coder Plus"
#define MyAppExeName "LizCoderPlus.Desktop.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer-output
OutputBaseFilename=LizCoderPlus-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; Flags: checked

[Files]
Source: "publish-output\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pdb,*.xml"

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Liz Coder Plus - AI Desktop Assistant"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Liz Coder Plus despues de instalar"; Flags: nowait postinstall shellexec skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
