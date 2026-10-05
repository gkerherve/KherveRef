; Inno Setup script for KherveRef (Windows installer).
;
;   python packaging/generate_icon.py
;   pyinstaller KherveRef.spec --noconfirm
;   ISCC.exe /DMyAppVersion=0.4 KherveRef_setup.iss
;
; Output: installer\Setup_KherveRef_<version>.exe

#define MyAppName      "KherveRef"
#define MyAppPublisher "Gwilherm Kerherve"
#define MyAppExeName   "KherveRef.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.0"
#endif

[Setup]
AppId={{6C1E2D7A-4B3F-4E8A-9D21-7F5A0C3B8E64}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer
OutputBaseFilename=Setup_KherveRef_{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=LICENSE
SetupIconFile=build\KherveRef.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\KherveRef\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Double-clicking a library's .kref opens it in KherveRef.
Root: HKA; Subkey: "Software\Classes\.kref"; ValueType: string; ValueName: ""; ValueData: "KherveRef.Library"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\KherveRef.Library"; ValueType: string; ValueName: ""; ValueData: "KherveRef library"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\KherveRef.Library\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\KherveRef.Library\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
