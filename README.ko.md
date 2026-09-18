<p align="right"><strong>한국어</strong> | <a href="./README.md">English</a></p>

# CuePrecise

> **1시간이 넘는 YouTube 영상을 약 3분에 분석하고, 필요한 말과 화면이 나온 순간을 근거와 함께 찾아줍니다.**

CuePrecise는 Claude Desktop·Codex·Cursor 같은 AI 앱에 연결하는 오픈소스 MCP 서버입니다.
영상의 음성, 자막, 화자, 화면을 하나의 시간축에 묶어 검색 가능한 자료로 만들고,
AI가 그 자료를 바탕으로 답하게 합니다. 외국어 영상도 한국어로 질문할 수 있지만,
정확한 검색에는 영상 원어의 표현이 필요할 수 있습니다.

요약만 보여주는 도구가 아닙니다. 답변에 사용된 원문, 화자, 화면, YouTube 타임스탬프를
함께 돌려줘서 답이 나온 순간을 직접 확인할 수 있습니다.

AI 앱에 영상을 통째로 넘겨 매번 인식시키는 대신, 원음 중심으로 음성을 청크 단위로
전사하고 필요한 화면과 근거만 색인합니다. 인식 품질은 원음 전사로 확보하고, 원어
YouTube 자막이 있을 때만 같은 시간대의 라틴 문자 표현을 보완하는 데 사용합니다.
실제 시간은 영상 길이, 네트워크, API 응답 속도에 따라 달라집니다.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml/badge.svg)](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml)
[![Listed on mcpservers.org](https://mcpservers.org/badge.svg)](https://mcpservers.org/servers/nattentia/cueprecise)
[![Glama](https://glama.ai/mcp/servers/Nattentia/cueprecise/badges/score.svg)](https://glama.ai/mcp/servers/Nattentia/cueprecise)

## 영상으로 먼저 확인하기

아래 영상은 1시간 48분짜리 강의를 실제로 분석한 장면입니다. 관점별 요약, 화자별
발언 비교, 화면 검색, 타임스탬프 이동이 차례로 등장합니다.

https://github.com/user-attachments/assets/aeec6d01-aff2-477c-9e91-2edcc6b31183

<sub><a href="https://www.youtube.com/watch?v=F9I7llmuhAk">데모에 사용한 원본 영상</a></sub>

[**직접 써보기: GitHub Releases에서 내려받기 →**](https://github.com/Nattentia/cueprecise/releases)

- **답이 나온 순간으로 이동합니다.** 요약의 모든 항목에 시작 시각을 붙입니다.
- **누가 무엇을 말했는지 분리합니다.** 특정 인물의 발언을 모아 관점과 근거를 비교합니다.
- **기억나는 화면을 다시 찾습니다.** 그림이나 화면이 나온 프레임을 당시 설명과 연결합니다.
- **원본으로 확인합니다.** 답변에 사용한 대목에서 바로 재생되는 YouTube 타임스탬프를 제공합니다.
- **한국어 자막을 입혀서 봅니다.** 분석에 쓴 근거 그대로 번역한 자막을 영상 위에 올려 재생합니다.

다음처럼 물어볼 수 있습니다.

~~~text
이 영상의 핵심 주장을 타임스탬프와 함께 정리해줘.

self-supervised learning을 설명하는 부분과 그 문구가 적힌 화면을 찾아줘.

출연자별로 기본소득에 관한 주장과 근거를 비교해줘.

이 토크쇼의 출연자들이 영상에서 실제로 한 발언을 근거로
새 쟁점에 관해 토론한다면 어떤 입장과 반론이 나올까?
~~~

Claude에서는 응답의 타임스탬프를 누르면 해당 시점부터 YouTube가 재생됩니다.
타임스탬프를 표시하는 방식은 AI 앱마다 다를 수 있습니다.

## 설치 방법

> **Windows 스마트 앱 컨트롤:** v0.2.5는 스마트 앱 컨트롤이 켜진 PC에서
> `[WinError 4551] 애플리케이션 제어 정책에서 이 파일을 차단했습니다` 오류로 막혔습니다.
> v0.2.6부터 Claude Desktop 확장 프로그램은 널리 쓰이는 실행 파일(공식 Python 임베디드
> 패키지와 수정하지 않은 FFmpeg 정식 빌드)만 담아 스마트 앱 컨트롤이 켜진 상태에서도
> 동작합니다. `v0.2.10`부터는 다른 AI 앱용 설치 파일도 서명되지 않은 실행 파일 대신 같은
> 방식의 압축 파일(zip)로 배포해 같은 문제를 해결했습니다. 현재 알려진 스마트 앱 컨트롤
> 문제는 없습니다.

가장 간단한 경로는 Windows용 Claude Desktop 확장 프로그램입니다.

1. Releases에서 cueprecise-windows.mcpb를 내려받습니다.
2. Claude Desktop에서 **설정 → 확장 프로그램 → 고급 설정 → 확장 프로그램 설치**를
   차례로 누릅니다.
3. 파일을 선택하고, Claude가 물으면 [Gemini API 키](https://aistudio.google.com/api-keys)와
   영상 자료를 저장할 폴더를 지정합니다.
4. CuePrecise를 켠 뒤 YouTube 링크에 관해 질문합니다.

이 파일 하나에 CuePrecise 서버, 내장 Python, yt-dlp, FFmpeg, FFprobe가 함께 들어 있습니다.
약 95MiB이며 Python, Git, 별도 명령어 입력이 필요 없습니다. 현재 이 확장 프로그램은
Windows용 Claude Desktop에서 사용할 수 있습니다. 다른 지원 앱에는 같은 Releases의
cueprecise-windows.zip을 사용합니다.

두 Windows 배포 파일에는 아직 디지털 서명이 없습니다. 반드시 이 저장소의 Releases에서
내려받고, 필요하면 SHA256SUMS.txt로 파일을 확인하세요.

## 요약에서 멈추지 않고, 근거까지 찾아줍니다

### 말과 화면을 같은 근거로 찾습니다

CuePrecise는 다음 자료를 같은 시간축에 놓습니다.

1. Gemini가 원래 음성을 듣고 만든 단어 단위 전사
2. 원어 자막이 있을 때 전사에서 빠진 라틴 문자 표현과 용어를 보완하는 YouTube 자막
3. 전사의 화면 참조 표현·복원된 용어·사용자가 지정한 시점의 프레임

질문과 관련된 대목을 찾으면 원문, 타임스탬프, 가까운 화면 프레임을 함께 돌려줍니다.
근거가 없거나 약하면 답을 꾸미지 않고 evidence 부족을 반환합니다. 분석 결과 번들은
컴퓨터에 남지만, 전사에 사용할 오디오 청크는 Gemini로 전송됩니다. 자세한 내용은
[PRIVACY.md](PRIVACY.md)를 참고하세요.

### 전사에서 빠진 라틴 문자 용어를 되찾습니다

한국어 강의나 대담에서는 문장 속 이름과 기술 용어가 전사에서 빠질 수 있습니다.
Gemini 전사는 자연스러운 문장을 만들지만, 실제 발화의 라틴 문자 표현을 놓치면
검색과 화면 찾기 모두 어긋납니다.

~~~text
Gemini 전사:
그러면 어떻게 이 능력을 학습을 했을까요? 라는 방식으로 학습을 합니다.

CuePrecise 병합 결과:
그러면 어떻게 이 능력을 학습을 했을까요? self supervised learning 라는 방식으로 학습을 합니다.
~~~

CuePrecise는 원어 자막이 있을 때 같은 시간대의 YouTube 자막을 대조해 빈 구간에만 빠진 표현을
보완합니다. Gemini가 전사한 단어는 지우거나 고치지 않고, 자막에서 가져온 단어에는
origin을 남깁니다. 예시 영상에서 확인한 결과는 다음과 같습니다.

- YouTube 원어 자동자막에는 라틴 단어 91개와 self supervised learning이 있었습니다.
- Gemini 전사에는 라틴 단어 28~29개가 있었지만 해당 용어가 네 차례 모두 빠졌습니다.
- 병합 결과에는 라틴 단어 38개와 해당 용어가 남으면서 한국어 문장 품질도 유지됐습니다.

이는 한 영상에서 얻은 단일 측정 사례이며, 일반적인 정확도를 보장하는 수치가 아닙니다.
병합 규칙과 검증 기준은 [CONTRACT.md](CONTRACT.md)에 기록되어 있습니다.

### 화자별로 다시 읽습니다

긴 영상을 여러 청크로 나눠 처리해도 화자 정보를 이어 붙입니다. 화자는 실제 이름이
아니라 분석 결과의 식별자이며, 확인된 화자와 추정한 화자를 구분합니다. 근거가
부족하면 임의로 확정하지 않고 unresolved로 남깁니다.

이 정보로 다음 작업을 할 수 있습니다.

- 출연자별 주장과 논거를 모아 비교하기
- 특정 화자가 특정 주제에 관해 말한 대목만 모으기
- 영상 속 인물의 실제 발언을 근거로 새로운 쟁점에 대한 가상 토론 구성하기

가상 토론은 영상 속 인물이 새 주제에 관해 실제로 한 발언이 아니라, 기존 발언을
바탕으로 AI가 구성한 시뮬레이션입니다.

### 같은 근거로 만든 한국어 자막을 영상에 입힙니다

자동 번역이 부정확한 영어 강연에 한국어 자막을 붙여 볼 수 있습니다. 자막을 새로
만드는 것이 아니라 이미 분석해 둔 근거를 그대로 씁니다.

**전문용어를 먼저 확정합니다.** Gemini 전사를 YouTube 자막·화면에 뜬 글자와 대조해
`by torch → PyTorch`, `combine JS → ConvNetJS` 같은 표기를 정리하고 목록으로 고정합니다.
용어가 정해진 뒤에 문장 번역을 시작하므로, 같은 용어가 묶음마다 다르게 번역되는 일이
없습니다.

**번역은 사용 중인 AI 앱이 합니다.** 강연을 작은 묶음으로 나눠 넘기고, 돌아온 번역은
숫자·용어·읽기 속도·빠진 줄·말투를 자동으로 검사합니다. 걸린 줄만 다시 보여주고
나머지는 통과시킵니다. Gemini를 추가로 호출하지 않으므로 번역에는 API 비용이 들지
않습니다.

68분 강연 934문장으로 측정한 결과 용어 확정에 약 2분, 번역 19묶음에 약 14분이 걸렸고
재검토는 6묶음이었습니다. 재생 속도의 약 5배라 번역을 걸어둔 뒤 바로 영상을 틀어도
자막이 앞서갑니다.

완성된 자막은 브라우저의 로컬 뷰어에서 봅니다.

- YouTube 플레이어 위에 자막이 겹쳐 나오고, 전체화면에서도 그대로 유지됩니다.
- 자막은 한국어, 한국어+원문 병기, 원문 중에서 고릅니다.
- 글자 크기, 자막 위치, 그림자와 상자 스타일을 조절하고 다음에 열 때도 유지합니다.
- 짧은 용어 해설을 켜면 해당 용어가 나오는 순간에 화면에 함께 뜹니다.
- 옆 패널에서 스크립트를 검색하고, 용어 목록과 품질 보고를 봅니다. 문장을 누르면 그
  시각부터 재생됩니다.
- 단축키: `Space` 재생·정지, `←` `→` 5초 이동, `C` 자막 언어, `T` 용어 해설,
  `F` 전체화면.

번역 대상 언어는 현재 한국어입니다. 다른 언어는 로드맵에 있습니다.

### 한 번 분석한 영상은 다시 쓸 수 있습니다

전사, 병합 결과, 챕터, 프레임, 검색 색인을 로컬에 저장합니다. 대화가 끝난 뒤에도
같은 영상에 새 질문을 할 수 있고, 완료된 청크는 설정이 같으면 다시 Gemini에 보내지
않습니다. 중간에 작업이 멈춰도 완료된 부분과 실패 원인을 보존해 이어서 진행합니다.

## 지원 환경

자동 설치를 지원하는 앱은 다음과 같습니다.

- Claude Desktop
- Codex
- Claude Code
- VS Code
- Cursor
- Windsurf
- Gemini CLI

Cursor, Windsurf, Gemini CLI 설정 경로는 코드에 포함되어 있지만 현재 개발 PC에서
전체 흐름을 끝까지 검증한 것은 아닙니다.

ChatGPT 커넥터와 Claude.ai 웹에는 연결할 수 없습니다. 이 둘은 HTTPS와 OAuth를
사용하는 원격 MCP를 요구하지만, CuePrecise는 사용자의 컴퓨터에서 실행되는 로컬
stdio MCP 서버이기 때문입니다.

## 자세한 설치

### Windows의 다른 AI 앱

1. [Releases](https://github.com/Nattentia/cueprecise/releases)에서
   cueprecise-windows.zip을 내려받습니다.
2. 압축을 풀고 `CuePrecise 설치.cmd`를 더블클릭합니다. Windows가 실행을 확인하는
   창을 띄울 수 있습니다.
3. Google AI Studio에서 만든 [Gemini API 키](https://aistudio.google.com/api-keys)를
   붙여넣고, 연결할 AI 앱을 고릅니다.
4. 연결이 끝나면 선택한 AI 앱을 완전히 껐다가 다시 켭니다.

AI 앱이 직접 대화형 입력 없이 스크립트를 실행할 수도 있습니다. 예:
`CuePrecise 설치.cmd --api-key-stdin --targets claude-desktop,codex`.

Python이나 Git을 설치할 필요가 없습니다. Windows에서는 API 키를 현재 사용자만
풀 수 있도록 DPAPI로 암호화합니다. 기존 설정은 백업한 뒤 CuePrecise 항목만
추가하며, 예전 버전이 평문으로 저장했던 키도 업그레이드할 때 암호화 저장소로
옮깁니다.

현재 `v0.2.10` 배포 파일은 서명되지 않은 시험판입니다. Windows에서 알 수 없는 게시자
경고가 나타날 수 있으므로 이 저장소의 Releases에서 받은 파일만 사용하세요.

### macOS·Linux 또는 명령어 설치

uv가 있으면 [uv 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를
따라 설치한 뒤 다음 명령을 실행합니다.

~~~bash
uv tool install git+https://github.com/Nattentia/cueprecise
cueprecise setup
~~~

setup은 감지된 지원 AI 앱의 설정(Claude Desktop 포함)과 기본 데이터 디렉터리
~/.cueprecise/data를 만들고, 기존 설정은 안전하게 백업할 수 있을 때만 timestamp가
붙은 .bak 파일로 보존합니다. 비밀이 포함된 설정을 해석할 수 없는 경우에는 안전하지
않은 백업을 만들지 않습니다.

영상 분석에는 ffmpeg와 ffprobe가 PATH에 있어야 합니다.

~~~bash
cueprecise doctor
~~~

API 키가 셸 기록에 남지 않도록 표준 입력이나 파일로 전달하세요.

~~~bash
cueprecise setup --api-key -                  # 키를 붙여넣고 Enter
cueprecise setup --api-key-file ~/.gemini-key # 파일에서 읽기
pass show gemini/api-key | cueprecise setup --api-key -

cueprecise run "https://www.youtube.com/watch?v=VIDEO_ID" --language ko-KR
cueprecise status VIDEO_ID
~~~

키가 노출됐다고 판단되면 [Google AI Studio](https://aistudio.google.com/api-keys)에서
삭제하고 새로 만드세요. 자세한 내용은 [개인정보 정책](PRIVACY.md#api-키를-폐기하는-방법)을
확인하세요.

소스에서 개발하거나 기존 실행 경로가 필요한 경우에만 clone 방식으로 설치합니다.

~~~bash
git clone https://github.com/Nattentia/cueprecise.git
cd cueprecise
python -m pip install -r requirements.txt
python src/pipeline.py --help
~~~

### 수동으로 MCP 호스트에 등록하기

cueprecise setup은 설치된 앱을 찾아 설정을 자동으로 추가합니다.

~~~bash
cueprecise setup                    # 찾은 앱 전부
cueprecise setup --client codex     # 하나만
cueprecise doctor                   # 설치·연결 상태
~~~

앱의 실행 파일이 PATH에 없거나 자동 감지 목록에 없는 경우에도
cueprecise setup --client <이름>으로 지정할 수 있습니다. 이미 있는 cueprecise 항목이
CuePrecise가 만든 것이 아니면 건너뛰며, 다른 MCP 설정을 덮어쓰지 않습니다.

수동 등록이 필요한 MCP 호스트에서는 다음 JSON을 사용합니다. API 키가 평문으로
남으므로 가능하면 setup 명령을 사용하세요.

~~~json
{
  "mcpServers": {
    "cueprecise": {
      "command": "python",
      "args": [
        "C:/path/to/cueprecise/src/mcp_server.py",
        "--bundle-root",
        "C:/path/to/cueprecise/data"
      ],
      "env": {
        "GEMINI_API_KEY": "..."
      }
    }
  }
}
~~~

절대 경로를 사용하세요. --bundle-root는 영상 자료를 쌓아둘 디렉터리입니다.
GEMINI_API_KEY 없이도 서버는 시작되며, 이미 분석한 영상의 조회 도구는 사용할 수
있습니다. 새 전사가 필요한 도구만 API 키가 없다는 사실을 알리고 멈춥니다.

## MCP 도구

도구는 역할별로 나뉩니다.

분석과 상태 확인:

- cueprecise_register — YouTube 영상을 등록하고 분석합니다. 필요한 단계만 선택할 수
  있으며, 새로 전사하는 청크에 Gemini 전사 호출이 필요합니다. 이미 저장된 응답은
  설정이 같으면 재사용합니다.
- cueprecise_status — 진행도, 산출물, 로컬 사용량 추정치를 확인합니다.

검색과 근거 확인:

- cueprecise_outline — 영상 개요, 타임스탬프 챕터, 복원된 용어와 화자 상태를 확인합니다.
- cueprecise_query — 질문과 관련된 전사 구간, 화자, 프레임, 타임스탬프를 찾습니다.
- cueprecise_excerpt — 특정 시각 구간의 자막과 프레임을 확인합니다.
- cueprecise_frames — 화면 참조 시점의 프레임을 추출합니다. 필요한 경우 영상만
  다시 내려받습니다.

자막:

- cueprecise_subtitle — 용어 확인·번역·검사 중 다음 자막 작업 묶음과 로컬 뷰어 주소를 돌려줍니다.
- cueprecise_set_subtitle — AI 앱의 응답을 검증해 저장하고 다음 묶음을 돌려줍니다.

결과물과 정리:

- cueprecise_summary — 요약을 생성하거나 저장된 요약을 조회합니다.
- cueprecise_set_summary — 호스트 AI가 근거를 바탕으로 개선한 요약을 검증하고 저장합니다.
- cueprecise_set_chapter_titles — 호스트 AI가 지은 챕터 제목을 검증하고 저장합니다.
- cueprecise_purge — 청크 오디오, 원본 영상, 파생 결과물, raw 자료를 명시적으로 삭제합니다.

YouTube 취득과 Gemini 전사 이후의 조립·자막 병합·챕터·렌더링·프레임 추출·검색 색인은
로컬에서 실행됩니다. 오디오 청크는 Gemini로 전송되며, 요약과 챕터 제목은 호스트 AI가
검색된 근거를 보고 작성합니다.

## 동작 방식

CuePrecise는 긴 영상을 한 번에 이해시키는 대신, 다시 찾을 수 있는 지식 번들을
만듭니다.

1. YouTube에서 오디오, 원어 자막(있는 경우), 메타데이터와 화면 단계에 사용할 저해상도
   영상을 가져옵니다. --skip-video를 사용하면 프레임을 요청할 때까지 영상 다운로드를
   미룹니다.
2. 오디오를 청크로 나누고 Gemini의 단어 단위 전사와 화자 정보를 받습니다.
3. 완료된 청크를 이어 붙이고, 원어 자막이 있을 때 같은 시간대의 라틴 문자 표현으로
   전사의 빈 구간을 보완합니다.
4. 전사의 화면 참조 표현, 복원된 용어와 사용자가 지정한 시점의 프레임을 추출합니다.
5. 전사, 챕터, 화자, 프레임을 SQLite 검색 색인에 저장합니다.
6. AI 앱은 필요한 대목만 검색한 뒤 근거와 타임스탬프가 있는 답변을 작성합니다.

분석 응답 원문은 검증 전에 저장합니다. 파싱이 실패해도 이미 사용한 호출의 결과가
남으므로, 재실행할 때 같은 응답을 다시 받지 않고 이어서 처리할 수 있습니다.

render 단계는 선택 사항입니다. 필요할 때만 SRT와 TXT를 만들며, 넘치는 텍스트를
잘라 버리지 않고 다음 자막 큐로 넘겨 단어 보존율 100%를 지킵니다.

### 언어 지정과 번역문 방지

가능하면 run 명령에 --language ko-KR처럼 원어를 지정하세요. Gemini가 전사 대신
번역문을 반환하는 경우가 있기 때문입니다. CuePrecise는 청크마다 이용 가능한 원어
자막, 요청 언어, 영상 메타데이터를 대조해 번역문으로 판단하면 그 자리에서 멈춥니다.
판단 근거가 없으면 검사를 건너뛰었다고 기록합니다. 추가 API 호출 없이 이미 받은
자료로 판정하며, 남은 청크의 호출도 아낍니다.

## 저장되는 결과

기본 데이터 디렉터리는 ~/.cueprecise/data입니다. 영상별로 다음 자료가 저장됩니다.

~~~text
data/<video_id>/
  job.json                  작업 계획과 청크별 진행 상태
  raw/
    captions.json           YouTube 자막(원어 우선, 없을 수 있음)
    metadata.json           언어 판정에 쓰는 영상 정보
    audio/                  전사에 사용한 청크 오디오
    transcripts/            청크별 전사와 원본 응답
    frames/                 추출한 프레임
  derived/
    transcript.json         청크 전사를 이어 붙인 결과
    merged.json             전사와 자막을 병합한 근거
    chapters.json           타임스탬프 목차
    frames.json             프레임 색인
    output.srt, output.txt  render 단계에서 선택적으로 생성
  index.sqlite3             전사·챕터·프레임 검색 색인과 요약
~~~

색인된 근거 구간에는 시각, 화자 상태, 근거 신뢰도, 출처가 남습니다. 병합된 단어
자료에는 단어별 시각과 출처가 남고, cueprecise_query의 결과에는 최소한 시작·종료
시각, 텍스트, 출처, 신뢰도가 포함됩니다. 근거가 없거나 약하면 추측하지 않고
evidence 부족을 반환합니다.

프레임은 영상 전체를 일정한 간격으로 모두 저장하지 않습니다. 전사의 화면 참조,
복원된 용어, 사용자가 요청한 시점을 우선합니다. 모든 코드·표·도식을 의미적으로
분류하는 기능은 아닙니다. 프레임에서 인식한 문자는 전사와 별도의 출처로 보관합니다.

## 명령줄 참고

~~~bash
python src/pipeline.py run <url> [옵션]
python src/pipeline.py status <video_id>
python src/pipeline.py purge <video_id> --scope <범위>
~~~

자주 쓰는 run 옵션:

- --language — 쉼표로 구분한 BCP-47 언어 코드. 한국어 영상은 ko-KR 지정 권장
- --stages — 실행할 단계. all이면 선택 단계까지 모두 실행
- --bundle-root — 번들을 저장할 디렉터리
- --force — 캐시를 무시하고 다시 생성
- --skip-video — 영상 다운로드 생략
- --keep-video — 프레임 추출 뒤에도 영상 보관
- --at — 프레임을 추출할 시각(초)
- --max-frames — 프레임 최대 개수. 기본 40
- --chunk-max-secs — 청크 최대 길이. 기본 1790초
- --overlap-secs — 청크 겹침 구간. 기본 10초
- --daily-limit, --rpm-limit, --request-interval — Gemini 호출 한도와 호출 사이 최소 간격
- --width — 자막 줄 폭. 기본 20. 영어 자막은 42를 권장

파생 결과만 다시 만들거나 자료를 정리할 수 있습니다.

~~~bash
python src/pipeline.py run <url> --stages render
python src/pipeline.py run <url> --stages visual
python src/pipeline.py purge <id> --scope chunks
~~~

--scope에는 chunks, video, derived, raw, all 중 하나를 지정합니다. 삭제 범위는
명시적으로 지정한 경우에만 적용됩니다.

## 성능과 사용량

분석은 여러 단계를 독립적으로 재실행할 수 있습니다. 완료된 청크는 같은 입력과
설정이면 재사용하고, render·merge·visual·index 같은 후처리는 Gemini 호출 없이
로컬에서 수행합니다.

번들 크기는 영상의 오디오, 전사 결과와 선택한 프레임 수에 따라 달라집니다. 원본
영상은 프레임 추출 뒤 기본적으로 삭제되며, --keep-video를 사용하면 보관합니다.
전사가 모두 끝나면 청크 오디오도 지우고 원본 오디오는 남깁니다. 두 앱이 같은 영상을
동시에 요청하면 나중 요청은 사용 중이라고 알리고 멈춥니다.

로컬 사용량 원장은 API 키 자체가 아닌 키의 해시와 Pacific 날짜별 시도 횟수를
기록합니다. 작업 전 예상 호출 수를 보여주며, 설정한 한도를 넘으면 시작 전에
멈춥니다. 로컬 수치는 추정치이고 AI Studio의 서버 수치가 최종 기준입니다.

## 요구 사항

- Python 3.11+
- ffmpeg / ffprobe — 청크 분할과 프레임 추출
- 프레임 OCR — Windows에는 내장돼 있습니다. macOS·Linux는 pytesseract, Pillow와 tesseract
  바이너리를 설치합니다 (선택)

~~~bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-optional.txt
~~~

google-genai는 transcribe 단계에만 필요합니다. 설치형 배포 이름은 cueprecise-mcp이고
실행 명령은 cueprecise와 cueprecise-mcp입니다. 아직 PyPI 릴리스 전이므로 GitHub
URL 설치를 사용하세요.

## 테스트

~~~bash
python -m unittest discover -s tests
~~~

표준 라이브러리 unittest를 사용하며 네트워크와 Gemini API를 호출하지 않습니다.
google-genai가 없으면 전사 관련 테스트는 건너뜁니다.

## 알려진 제한

- YouTube 자막의 표기 오류는 그대로 들어올 수 있습니다. 별도의 표기 정규화 단계는
  아직 없습니다.
- 자막 병합 임계값은 제한된 실제 영상으로 조정되어 다른 영상의 추가 검증이 필요합니다.
- 청크가 3개 이상이고 겹치는 구간에 근거가 없으면 서로 다른 청크의 화자를 같은
  사람인지 확정하지 못하고 unresolved로 남길 수 있습니다.
- 실제 API 경로에서 장시간 작업을 중단한 뒤 재개하는 시나리오는 추가 검증이 필요합니다.
- 화면 후보를 찾는 표현은 현재 한국어와 영어를 중심으로 지원합니다.
- 화면 검색은 전체 프레임을 무작위로 읽는 방식이 아니라, 전사의 화면 참조와 요청
  시점을 우선하는 후보 기반 방식입니다.
- 자막 번역 대상 언어는 현재 한국어뿐입니다. 뷰어 화면도 한국어입니다.
- 자막 뷰어는 MCP 서버가 실행 중일 때 이 컴퓨터(`127.0.0.1`)에서만 열리며, 데스크톱
  브라우저용입니다.
- 품질 보고의 교정 목록은 MinGPT → minGPT 같은 대소문자 차이도 교정 한 건으로 셉니다.

## 문서

- [Code signing policy](CODE_SIGNING_POLICY.md) — Windows 배포 파일의 빌드·검토·서명 정책
- [PRIVACY.md](PRIVACY.md) — API 키, 로컬 데이터, 외부 서비스 통신
- [CONTRACT.md](CONTRACT.md) — 단계 사이 JSON 구조와 검증 기준
- [DECISIONS/](DECISIONS/) — 설계 결정 기록
- [CONTRIBUTING.md](CONTRIBUTING.md) — 개발 환경과 PR 절차
- [SECURITY.md](SECURITY.md) — 비공개 취약점 신고 절차

## 로드맵

- [x] 분석한 근거로 만든 한국어 자막을 YouTube 영상에 입혀 재생
- [ ] 한국어 외 언어 자막
- [ ] 호스트별 타임스탬프 링크 통일
- [ ] 청크 전사 파이프라이닝
- [ ] 여러 영상 통합 분석

## 감사의 말

초기 전사 흐름은 MIT 라이선스로 공개된
[`gemini-transcribe-wrapper`](https://pypi.org/project/gemini-transcribe-wrapper/0.0.13/)를
참고했습니다. CuePrecise는 별도로 작성한 프로젝트입니다.

## 라이선스

MIT. [LICENSE](LICENSE)를 참고하세요.

CuePrecise는 YouTube 및 Google과 제휴 관계가 없고 두 회사의 공식 제품도 아닙니다.
YouTube는 지원 대상 서비스일 뿐입니다.
