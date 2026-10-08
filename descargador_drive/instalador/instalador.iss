; Instalador de Windows del Descargador de Drive (Inno Setup 6).
; Lo compila GitHub Actions: iscc /DAppVersion=1.0.N instalador.iss
; Antes hay que crear dist\DescargadorDrive con PyInstaller (modo carpeta).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Descargador de Drive"
#define AppExe "DescargadorDrive.exe"

[Setup]
AppId={{6C3B1E2A-8D4F-4F5B-9A61-2E7C0D9B4A17}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
; Se instala solo para el usuario: no pide permisos de administrador.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\..\dist
OutputBaseFilename=DescargadorDrive-Instalador
SetupIconFile=icono.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\DescargadorDrive\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
