#define MyAppName "CuePrecise"
#define MyAppVersion "0.2.3"
#define MyAppPublisher "Nattentia"
#define MyAppURL "https://github.com/Nattentia/cueprecise"

[Setup]
; AppId 는 바꾸지 않는다. 이 값이 바뀌면 기존 설치가 지워지지 않고 별개의
; 프로그램으로 남는다.
AppId={{E5118050-5C8A-47D9-8A61-A4A94C6298ED}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={localappdata}\Programs\CuePrecise
DefaultGroupName=CuePrecise
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\windows
OutputBaseFilename=cueprecise-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayName=CuePrecise
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

; 덮어 설치할 때 남는 이전 버전의 시작 메뉴 항목을 지운다. 아래 [Run] 의
; --migrate 가 Claude 설정을 새 실행 파일로 먼저 옮기므로 연결은 끊기지 않는다.
[InstallDelete]
Type: filesandordirs; Name: "{autoprograms}\CuePrecise"

[Files]
Source: "..\dist\windows\cueprecise-mcp.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\windows\yt-dlp.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\windows\cueprecise-onboarding.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\CuePrecise 시작하기"; Filename: "{app}\cueprecise-onboarding.exe"
Name: "{group}\CuePrecise 제거"; Filename: "{uninstallexe}"

[Run]
; 이미 연결돼 있던 사용자를 조용히 새 이름으로 옮긴다. 연결된 적이 없으면
; 아무 일도 하지 않는다.
Filename: "{app}\cueprecise-onboarding.exe"; Parameters: "--migrate"; Flags: runhidden waituntilterminated
Filename: "{app}\cueprecise-onboarding.exe"; Description: "Gemini API 키를 입력하고 Claude Desktop에 연결"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\cueprecise-onboarding.exe"; Parameters: "--uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveClaudeConfig"
