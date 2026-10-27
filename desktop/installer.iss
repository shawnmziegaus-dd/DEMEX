; Inno Setup script for DEMEX. build_desktop.bat runs this automatically when
; Inno Setup 6 is installed (free: https://jrsoftware.org/isinfo.php).
; Per-user install into %LOCALAPPDATA%\DEMEX -- no admin rights needed.
#define AppName "DEMEX"
#define AppVersion GetEnv("DEMEX_VERSION")
#if AppVersion == ""
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B3F0C5A2-6D1E-4C7B-9A8F-2E4D5C6B7A81}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Disassemble Dynamics, Inc.
DefaultDirName={localappdata}\DEMEX
DefaultGroupName=DEMEX
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=DEMEX-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\DEMEX.exe
CloseApplications=yes

[Files]
Source: "..\dist\DEMEX\*"; DestDir: "{app}"; Excludes: "demex_records.db,demex_records.db-wal,demex_records.db-journal,.env,demex_rate_card.json,projects\*"; Flags: ignoreversion recursesubdirs createallsubdirs
; User data: installed only on a fresh install, never overwritten or uninstalled.
Source: "..\dist\DEMEX\demex_records.db"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall skipifsourcedoesntexist
Source: "..\dist\DEMEX\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall skipifsourcedoesntexist
Source: "..\dist\DEMEX\projects\*"; DestDir: "{app}\projects"; Flags: onlyifdoesntexist uninsneveruninstall skipifsourcedoesntexist recursesubdirs createallsubdirs

[Icons]
Name: "{group}\DEMEX"; Filename: "{app}\DEMEX.exe"
Name: "{autodesktop}\DEMEX"; Filename: "{app}\DEMEX.exe"

[Run]
Filename: "{app}\DEMEX.exe"; Description: "Launch DEMEX"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Program files only -- demex_records.db, .env, projects\ stay put.
Type: filesandordirs; Name: "{app}\_internal"
