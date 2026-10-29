; Inno Setup script for Mesh Mix-S2C on Windows (built by desktop/build.py).
;
; Installs for the current user only (no administrator password), so the
; app can update itself silently: it runs a newer installer with
; /SILENT /relaunch=1 and quits; this installer waits for it to close,
; replaces the files and starts it again.

#define AppName "Mesh Mix-S2C"
#define AppExe "ManifoldMeshMix.exe"
#define AppMutex "ManifoldMeshMixRunning"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppNumVersion
  ; Windows' version field takes numbers only ("2.0.0" for "2.0.0-test.1")
  #define AppNumVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist-desktop\ManifoldMeshMix"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist-desktop"
#endif
#ifndef IconFile
  #define IconFile "app.ico"
#endif
#ifndef LicenseFile
  #define LicenseFile "..\..\..\LICENSE"
#endif

[Setup]
AppId={{6B7D3C2A-4E1F-4C8B-9A5E-2F0D8C1B7E43}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Tuna-The-First
AppPublisherURL=https://github.com/STL2CURVES-MMM/Mesh-Mix-S2C
AppSupportURL=https://github.com/STL2CURVES-MMM/Mesh-Mix-S2C
AppUpdatesURL=https://github.com/STL2CURVES-MMM/Mesh-Mix-S2C/releases
VersionInfoVersion={#AppNumVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=MeshMix-S2C-{#AppVersion}-Windows-x64-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
LicenseFile={#LicenseFile}
WizardStyle=modern
Compression=lzma2/normal
SolidCompression=yes
ChangesAssociations=yes
CloseApplications=no
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; an update replaces the program's libraries completely (no leftovers of the old version)
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\engine"
; the shortcuts of 1.x, from before the app was renamed (the new ones replace them)
Type: files; Name: "{autoprograms}\Manifold Mesh Mix.lnk"
Type: files; Name: "{autodesktop}\Manifold Mesh Mix.lnk"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; "Open with" for meshes (the app is offered, not made the default)
Root: HKA; Subkey: "Software\Classes\ManifoldMeshMix.Mesh"; ValueType: string; ValueName: ""; ValueData: "3D mesh"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\ManifoldMeshMix.Mesh\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\ManifoldMeshMix.Mesh\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".stl"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".3mf"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".obj"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".ply"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".off"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\.stl\OpenWithProgids"; ValueType: string; ValueName: "ManifoldMeshMix.Mesh"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.3mf\OpenWithProgids"; ValueType: string; ValueName: "ManifoldMeshMix.Mesh"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.obj\OpenWithProgids"; ValueType: string; ValueName: "ManifoldMeshMix.Mesh"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.ply\OpenWithProgids"; ValueType: string; ValueName: "ManifoldMeshMix.Mesh"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.off\OpenWithProgids"; ValueType: string; ValueName: "ManifoldMeshMix.Mesh"; ValueData: ""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; after an update started from inside the app
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: IsRelaunch

[Code]
function IsRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;

function InitializeSetup(): Boolean;
var
  Tries: Integer;
begin
  Result := True;
  if WizardSilent() then
  begin
    { started by the app's updater: give the app time to quit }
    Tries := 0;
    while CheckForMutexes('{#AppMutex}') and (Tries < 120) do
    begin
      Sleep(500);
      Tries := Tries + 1;
    end;
    if CheckForMutexes('{#AppMutex}') then
      Result := False;
  end
  else
  begin
    while CheckForMutexes('{#AppMutex}') do
    begin
      if MsgBox('{#AppName} is open. Close it, then click OK to continue.', mbError, MB_OKCANCEL) = IDCANCEL then
      begin
        Result := False;
        Exit;
      end;
    end;
  end;
end;
