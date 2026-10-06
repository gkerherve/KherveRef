; Inno Setup script for KherveRef — per-user install, no admin rights.
;
; Not run by hand: packaging/build_installer.py freezes the app and then calls
;
;   ISCC.exe /DAPP_VERSION=0.24.N /DSRC_DIR=...\dist\KherveRef /DOUT_DIR=...\dist
;            /DICON_FILE=...\build\KherveRef.ico KherveRef.iss
;
; Installs to %LOCALAPPDATA%\Programs\KherveRef, so there is no elevation
; prompt, and associates .kref libraries (per user, HKCU).
;
; Copyright (C) 2026 Gwilherm Kerherve.

#ifndef APP_VERSION
  #define APP_VERSION "0.0.0"
#endif
#ifndef SRC_DIR
  #define SRC_DIR "..\dist\KherveRef"
#endif
#ifndef OUT_DIR
  #define OUT_DIR "..\dist"
#endif
#ifndef ICON_FILE
  #define ICON_FILE "..\build\KherveRef.ico"
#endif

#define AppName "KherveRef"
#define AppPublisher "Gwilherm Kerherve"
#define AppURL "https://khervetools.com/tools/kherveref"
#define AppExe "KherveRef.exe"

[Setup]
AppId={{6C1E2D7A-4B3F-4E8A-9D21-7F5A0C3B8E64}
AppName={#AppName}
AppVersion={#APP_VERSION}
AppVerName={#AppName} {#APP_VERSION}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#APP_VERSION}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile={#ICON_FILE}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir={#OUT_DIR}
OutputBaseFilename={#AppName}-Setup-{#APP_VERSION}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[InstallDelete]
; Inno never removes a file a newer build dropped, and PySide6 / PyMuPDF /
; pygit2 are ABI-bound to the bundled Python: an upgrade must not leave the
; old _internal tree beside the new one. Nothing the user made lives here.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SRC_DIR}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Double-clicking a library's .kref opens it in KherveRef.
Root: HKCU; Subkey: "Software\Classes\.kref"; ValueType: string; ValueName: ""; ValueData: "KherveRef.Library"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\KherveRef.Library"; ValueType: string; ValueName: ""; ValueData: "KherveRef library"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\KherveRef.Library\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKCU; Subkey: "Software\Classes\KherveRef.Library\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
