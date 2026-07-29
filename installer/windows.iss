#define MyAppName "Brightspace Pages Automator"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExeName "BrightspacePagesAutomator.exe"

[Setup]
AppId={{5A075E1F-F468-4591-9278-72C85EC912BB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\BrightspacePagesAutomator
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=BrightspacePagesAutomator-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes
; The running app holds a mutex with this name (see APP_MUTEX_NAME in
; src/config.py). It lets Setup detect a live instance instead of failing to
; overwrite a locked .exe. Both names must stay in sync.
AppMutex=BrightspacePagesAutomator.SingleInstance
CloseApplications=yes

[Files]
Source: "..\dist\BrightspacePagesAutomator\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "install_browsers.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\install_browsers.bat"; StatusMsg: "Installing Chromium browser (one-time, ~2 min)..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
; Reopens the app after a silent self-update. The entry above is skipifsilent,
; so without this one "Restart & Update" would only ever close the app. Gated on
; /RELAUNCH so a normal silent install by an admin stays silent.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait runasoriginaluser; Check: WantsRelaunch

[Code]
function WantsRelaunch(): Boolean;
begin
  Result := CompareText(ExpandConstant('{param:RELAUNCH|no}'), 'yes') = 0;
end;
