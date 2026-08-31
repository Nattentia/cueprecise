#define MyAppName "ytx"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Nattentia"
#define MyAppURL "https://github.com/Nattentia/ytx"

[Setup]
AppId={{E5118050-5C8A-47D9-8A61-A4A94C6298ED}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={localappdata}\Programs\ytx
DefaultGroupName=ytx
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\windows
OutputBaseFilename=ytx-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayName=ytx
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\windows\ytx-mcp.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\windows\yt-dlp.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\windows\ytx-onboarding.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ytx 시작하기"; Filename: "{app}\ytx-onboarding.exe"
Name: "{group}\ytx 제거"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\ytx-onboarding.exe"; Description: "Gemini API 키를 입력하고 Claude Desktop에 연결"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\ytx-onboarding.exe"; Parameters: "--uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveClaudeConfig"
