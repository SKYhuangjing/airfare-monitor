#define MyAppName "航价守望"
#define MyAppVersion "0.5.1"
#define MyAppPublisher "AirfareMonitor"
#define MyAppExeName "AirfareMonitor.exe"

[Setup]
AppId={{B2D23D48-88C7-4C64-94C0-B827A397537E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\AirfareMonitor
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputBaseFilename=AirfareMonitorSetup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
UninstallDisplayName={#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

[Files]
Source: "..\dist\AirfareMonitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
  MsgBox('卸载默认保留本地航程、历史、Excel、日志和独立浏览器 Profile。', mbInformation, MB_OK);
end;
