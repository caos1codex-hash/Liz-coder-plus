; ============================================================
; Liz Coder Plus - Inno Setup Installer Script
; Generates a professional Windows installer EXE
; ============================================================

#define MyAppName "Liz Coder Plus"
#define MyAppVersion "0.18.0"
#define MyAppPublisher "Liz Coder Plus"
#define MyAppURL "https://github.com/caos1codex-hash/Liz-coder-plus"
#define MyAppExeName "LizCoderPlus.Desktop.exe"
#define MyAppCopyright "MIT License - 2024"

[Setup]
; App identity
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
AppCopyright={#MyAppCopyright}

; Default installation folder
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Output
OutputDir=installer-output
OutputBaseFilename=LizCoderPlus-Setup-{#MyAppVersion}
SetupIconFile=..\assets\app.ico
Compression=lzma2/ultra64
SolidCompression=yes

; Installer UI
WizardStyle=modern
WizardSizePercent=120
PrivilegesRequired=admin

; Architecture
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

; Language
ShowLanguageDialog=no

; Misc
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; Flags: checked
Name: "startmenu"; Description: "Crear entrada en el menu inicio"; Flags: checked

[Files]
; Include ALL files from the publish folder
Source: "publish-output\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pdb,*.xml"

[Icons]
; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"; Comment: "Liz Coder Plus - AI Desktop Assistant"
; Start Menu
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startmenu; IconFilename: "{app}\{#MyAppExeName}"; Comment: "Liz Coder Plus - AI Desktop Assistant"

[Run]
; Option to launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Liz Coder Plus despues de instalar"; Flags: nowait postinstall shellexec skipifsilent

[UninstallDelete]
; Clean up all files on uninstall
Type: filesandordirs; Name: "{app}"

[Code]
// Show a custom info page during installation
procedure InitializeWizard();
begin
  // Modern wizard header
  WizardForm.Caption := '{#MyAppName} {#MyAppVersion} - Instalador';
end;
