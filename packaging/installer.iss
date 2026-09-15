#define MyAppName "航价守望"
#define MyAppVersion "0.6.0"
#define MyAppPublisher "AirfareMonitor"
#define MyAppExeName "AirfareMonitor.exe"
#ifndef BuildRoot
  #define BuildRoot "..\dist\AirfareMonitor"
#endif
#ifndef OutputRoot
  #define OutputRoot "..\release"
#endif

[Setup]
AppId={{B2D23D48-88C7-4C64-94C0-B827A397537E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\AirfareMonitor
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputBaseFilename=AirfareMonitorSetup-{#MyAppVersion}
OutputDir={#OutputRoot}
Compression=lzma
SolidCompression=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\resources\app.ico
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

[Files]
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if not UninstallSilent then
    MsgBox('卸载默认保留本地航程、历史、Excel、日志和独立浏览器 Profile。', mbInformation, MB_OK);
end;
