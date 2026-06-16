#define MyAppName "Brightspace Pages Automator"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExeName "BrightspacePagesAutomator.exe"

[Setup]
AppId={{5A075E1F-F468-4591-9278-72C85EC912BB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=BrightspacePagesAutomator-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes

[Files]
Source: "..\dist\BrightspacePagesAutomator\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
