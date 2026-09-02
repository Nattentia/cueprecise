# 개인정보 및 네트워크 정책

CuePrecise는 별도의 운영 서버나 사용자 계정을 제공하지 않는다. 프로젝트 운영자는 사용자의
API 키, 영상, 대본 또는 Claude 대화를 수집하지 않는다.

## 외부로 전송되는 정보

CuePrecise는 사용자가 영상 분석을 요청하거나 설치 화면에서 관련 기능을 선택할 때 다음 외부
서비스와 통신한다.

- **YouTube**: 사용자가 지정한 영상의 오디오, 영상, 자막 및 공개 메타데이터를
  내려받는다. YouTube의 개인정보처리방침과 이용약관이 적용된다.
- **Google Gemini API**: 전사를 위해 영상에서 만든 오디오 청크와 사용자가 지정한
  언어 정보를 전송한다. 사용자가 발급한 API 키를 사용하며 Google의 개인정보처리방침과
  Gemini API 약관이 적용된다.
- **Microsoft WinGet 패키지 소스**: Windows 설치 화면이 FFmpeg를 찾지 못한 경우에만
  사용자의 선택에 따라 FFmpeg 설치에 필요한 패키지 정보를 요청한다.
- **Google AI Studio**: 설치 화면의 “API 키 만들기” 링크를 누른 경우 기본 브라우저로
  해당 웹사이트를 연다.

이 통신은 사용자가 설치 또는 분석을 요청한 경우에만 발생한다. CuePrecise는 광고, 분석 추적,
원격 측정(telemetry) 또는 자체 서버로의 정보 전송 기능을 포함하지 않는다.

## 컴퓨터에 저장되는 정보

- Gemini API 키는 Claude Desktop이 CuePrecise를 실행할 수 있도록 사용자의 로컬 Claude
  Desktop 설정 파일에 평문으로 저장된다. 저장소나 프로젝트 운영자에게 전송되지 않는다.
- 분석 결과와 내려받은 자료는 기본적으로 사용자 홈 폴더의 `.cueprecise/data` 아래에
  저장된다.
- API 사용량 원장은 원문 키가 아닌 키의 해시만 로컬에 저장한다.
- Claude 설정을 변경하기 전에 같은 폴더에 timestamp가 붙은 백업 파일을 만든다.

컴퓨터 또는 Claude Desktop 설정에 접근할 수 있는 다른 사람이나 프로그램은 저장된 API
키를 읽을 수 있다. 공용 컴퓨터에서는 사용하지 말고, 노출이 의심되면 Google AI Studio에서
키를 폐기한 뒤 새 키를 발급해야 한다.

## 설치와 제거

Windows 설치 프로그램은 사용자 계정의 `%LOCALAPPDATA%\Programs\CuePrecise`에 프로그램을
설치하고 시작 메뉴 바로가기를 만든다. 연결 버튼을 누르면 Claude Desktop 설정에
`cueprecise` 항목을 추가하고, 필요한 경우 WinGet으로 FFmpeg를 설치한다. 이미 연결해
둔 적이 있으면 저장돼 있던 API 키를 포함한 설정을 그대로 유지한다. 설정을 바꾸기
전에는 항상 백업 파일을 만든다.

Windows 설정의 **앱 > 설치된 앱 > CuePrecise > 제거** 또는 시작 메뉴의
**CuePrecise 제거**를 사용하면 프로그램과 Claude Desktop의 연결 항목을 제거한다. 이때
이 프로그램이 만든 `cueprecise` 항목만 지우고 다른 MCP 설정은
건드리지 않는다. FFmpeg, Claude 설정 백업 및 데이터 폴더의 분석 자료는 자동으로
삭제하지 않는다. 사용자는 보존 여부를 확인한 뒤 이 자료를 직접 삭제할 수 있다.

