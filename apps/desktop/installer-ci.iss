; Minimal Inno Setup script for Liz Coder Plus
#define MyAppName "Liz Coder Plus"
#define MyAppVersion "0.18.0"
#define MyAppExeName "LizCoderPlus.Desktop.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
OutputDir=installer-output
OutputBaseFilename=LizCoderPlus-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin

[Languages]
Name: "spanish"; MessagesFile="compiler:Languages\Spanish.isl"

[Files]
Source: "publish-output\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pdb,*.xml"

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Liz Coder Plus"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Liz Coder Plus"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
