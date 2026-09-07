<p align="right"><strong>한국어</strong> | <a href="./README.md">English</a></p>

# CuePrecise

> **1시간이 넘는 YouTube 영상을 약 3분 30초에 분석하고, 필요한 말과 화면이 나온 순간을 근거와 함께 찾아줍니다.**

CuePrecise는 Claude Desktop·Codex·Cursor 같은 AI 앱에 연결하는 오픈소스 MCP 서버입니다.
영상의 음성, 자막, 화자, 화면을 하나의 시간축에 묶어 검색 가능한 자료로 만들고,
AI가 그 자료를 바탕으로 답하게 합니다. 외국어 영상도 한국어로 질문할 수 있습니다.

요약만 보여주는 도구가 아닙니다. 답변에 사용된 원문, 화자, 화면, YouTube 타임스탬프를
함께 돌려줘서 답이 나온 순간을 직접 확인할 수 있습니다.

AI 앱에 영상을 통째로 넘겨 매번 인식시키는 대신, 원음 중심으로 음성을 청크 단위로
전사하고 필요한 화면과 근거만 색인합니다. 인식 품질은 원음 전사로 확보하고, YouTube
자막은 빠진 이름과 기술 용어를 보완하는 데만 사용합니다. 실제 시간은 영상 길이,
네트워크, API 응답 속도에 따라 달라집니다.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml/badge.svg)](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml)

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

가장 간단한 경로는 Windows용 Claude Desktop 확장 프로그램입니다.

1. Releases에서 cueprecise-windows.mcpb를 내려받습니다.
2. Claude Desktop에서 **설정 → 확장 프로그램 → 고급 설정 → 확장 프로그램 설치**를
   차례로 누릅니다.
3. 파일을 선택하고, Claude가 물으면 [Gemini API 키](https://aistudio.google.com/api-keys)와
   영상 자료를 저장할 폴더를 지정합니다.
4. CuePrecise를 켠 뒤 YouTube 링크에 관해 질문합니다.

이 파일 하나에 CuePrecise 서버, yt-dlp, FFmpeg, FFprobe가 함께 들어 있습니다.
약 86MiB이며 Python, Git, 별도 명령어 입력이 필요 없습니다. 현재 이 확장 프로그램은
Windows용 Claude Desktop에서 사용할 수 있습니다. 다른 지원 앱에는 같은 Releases의
cueprecise-setup.exe를 사용합니다.

두 Windows 배포 파일에는 아직 디지털 서명이 없습니다. 반드시 이 저장소의 Releases에서
내려받고, 필요하면 SHA256SUMS.txt로 파일을 확인하세요.

## 요약에서 멈추지 않고, 근거까지 찾아줍니다

### 말과 화면을 같은 근거로 찾습니다

CuePrecise는 다음 자료를 같은 시간축에 놓습니다.

1. Gemini가 원래 음성을 듣고 만든 단어 단위 전사
2. 전사에서 빠진 이름과 기술 용어를 보완하는 YouTube 원어 자막
3. 말로 설명되거나 화면 참조가 나타난 시점의 프레임

질문과 관련된 대목을 찾으면 원문, 타임스탬프, 가까운 화면 프레임을 함께 돌려줍니다.
근거가 없으면 답을 꾸미지 않고 찾지 못했다고 알립니다. 분석 결과는 컴퓨터에 남으므로
호스트 AI를 다시 켠 뒤에도 같은 영상을 이어서 질문할 수 있습니다.

### 전사에서 빠진 영어 용어를 되찾습니다

한국어 강의나 대담에서는 문장 속 영어 이름과 기술 용어가 전사에서 빠질 수 있습니다.
Gemini 전사는 자연스러운 한국어 문장을 만들지만, 실제 발화의 용어를 놓치면 검색과
화면 찾기 모두 어긋납니다.

~~~text
Gemini 전사:
그러면 어떻게 이 능력을 학습을 했을까요? 라는 방식으로 학습을 합니다.

CuePrecise 병합 결과:
그러면 어떻게 이 능력을 학습을 했을까요? self supervised learning 라는 방식으로 학습을 합니다.
~~~

CuePrecise는 같은 시간대의 YouTube 원어 자막을 대조해 빈 구간에만 빠진 표현을
보완합니다. Gemini가 전사한 단어는 지우거나 고치지 않고, 자막에서 가져온 단어에는
origin을 남깁니다. 예시 영상에서 확인한 결과는 다음과 같습니다.

- YouTube 원어 자동자막에는 라틴 단어 91개와 self supervised learning이 있었습니다.
- Gemini 전사에는 라틴 단어 28~29개가 있었지만 해당 용어가 네 차례 모두 빠졌습니다.
- 병합 결과에는 라틴 단어 38개와 해당 용어가 남으면서 한국어 문장 품질도 유지됐습니다.

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

ChatGPT 커넥터와 Claude.ai 웹에는 연결할 수 없습니다. 이 둘은 HTTPS와 OAuth를
사용하는 원격 MCP를 요구하지만, CuePrecise는 사용자의 컴퓨터에서 실행되는 로컬
stdio MCP 서버이기 때문입니다.

## 자세한 설치

### Windows의 다른 AI 앱

1. [Releases](https://github.com/Nattentia/cueprecise/releases)에서 cueprecise-setup.exe를
   내려받아 실행합니다.
2. 설치 화면에서 **API 키 만들기**를 누르고 Google AI Studio에서 만든 키를 붙여넣습니다.
3. 연결할 AI 앱을 선택하고 **연결하기**를 누릅니다.
4. 연결이 끝나면 선택한 AI 앱을 완전히 껐다가 다시 켭니다.

Python이나 Git을 설치할 필요가 없습니다. Windows에서는 API 키를 현재 사용자만
풀 수 있도록 DPAPI로 암호화합니다. 기존 설정은 백업한 뒤 CuePrecise 항목만
추가하며, 예전 버전이 평문으로 저장했던 키도 업그레이드할 때 암호화 저장소로
옮깁니다.

현재 `v0.2.5` 배포 파일은 서명되지 않은 시험판입니다. Windows에서 알 수 없는 게시자
경고가 나타날 수 있으므로 이 저장소의 Releases에서 받은 파일만 사용하세요.

### macOS·Linux 또는 명령어 설치

uv가 있으면 [uv 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를
따라 설치한 뒤 다음 명령을 실행합니다.

~~~bash
uv tool install git+https://github.com/Nattentia/cueprecise
cueprecise setup
~~~

setup은 Claude Desktop 설정과 기본 데이터 디렉터리 ~/.cueprecise/data를 만들고,
기존 설정은 timestamp가 붙은 .bak 파일로 보존합니다.

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
  있으며, 전사 청크마다 Gemini를 한 번 호출합니다.
- cueprecise_status — 진행도, 산출물, 로컬 사용량 추정치를 확인합니다.

검색과 근거 확인:

- cueprecise_outline — 영상 개요, 타임스탬프 챕터, 복원된 용어와 화자 상태를 확인합니다.
- cueprecise_query — 질문과 관련된 전사 구간, 화자, 프레임, 타임스탬프를 찾습니다.
- cueprecise_excerpt — 특정 시각 구간의 자막과 프레임을 확인합니다.
- cueprecise_frames — 화면 참조 시점의 프레임을 추출합니다. 필요한 경우 영상만
  다시 내려받습니다.

결과물과 정리:

- cueprecise_summary — 요약을 생성하거나 저장된 요약을 조회합니다.
- cueprecise_set_summary — 호스트 AI가 근거를 바탕으로 개선한 요약을 검증하고 저장합니다.
- cueprecise_set_chapter_titles — 호스트 AI가 지은 챕터 제목을 검증하고 저장합니다.
- cueprecise_purge — 청크 오디오, 원본 영상, 파생 결과물을 명시적으로 삭제합니다.

전사를 제외한 모든 단계는 로컬에서 실행됩니다. 요약과 챕터 제목은 호스트 AI가
검색된 근거를 보고 작성하며, 별도의 Gemini 호출을 만들지 않습니다.

## 동작 방식

CuePrecise는 긴 영상을 한 번에 이해시키는 대신, 다시 찾을 수 있는 지식 번들을
만듭니다.

1. YouTube에서 오디오, 필요한 저해상도 영상, 원어 자막, 메타데이터를 가져옵니다.
2. 오디오를 청크로 나누고 Gemini의 단어 단위 전사와 화자 정보를 받습니다.
3. 완료된 청크를 이어 붙이고, 같은 시간대의 자막에서 빠진 용어만 보완합니다.
4. 말에서 화면 참조가 나타난 시점과 사용자가 지정한 시점의 프레임을 추출합니다.
5. 전사, 챕터, 화자, 프레임을 SQLite 검색 색인에 저장합니다.
6. AI 앱은 필요한 대목만 검색한 뒤 근거와 타임스탬프가 있는 답변을 작성합니다.

분석 응답 원문은 검증 전에 저장합니다. 파싱이 실패해도 이미 사용한 호출의 결과가
남으므로, 재실행할 때 같은 응답을 다시 받지 않고 이어서 처리할 수 있습니다.

render 단계는 선택 사항입니다. 필요할 때만 SRT와 TXT를 만들며, 넘치는 텍스트를
잘라 버리지 않고 다음 자막 큐로 넘겨 단어 보존율 100%를 지킵니다.

### 언어 지정과 번역문 방지

가능하면 run 명령에 --language ko-KR처럼 원어를 지정하세요. Gemini가 전사 대신
번역문을 반환하는 경우가 있기 때문입니다. CuePrecise는 청크마다 원어 자막,
요청 언어, 영상 메타데이터를 차례로 대조해 번역문으로 판단하면 그 자리에서
멈춥니다. 추가 API 호출 없이 이미 받은 자료로 판정하며, 남은 청크의 호출도
아낍니다.

## 저장되는 결과

기본 데이터 디렉터리는 ~/.cueprecise/data입니다. 영상별로 다음 자료가 저장됩니다.

~~~text
data/<video_id>/
  job.json                  작업 계획과 청크별 진행 상태
  raw/
    captions.json           YouTube 원어 자막
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

각 단어에는 시각, 화자, 신뢰도, 출처가 남습니다. cueprecise_query의 결과에는
최소한 시작·종료 시각, 텍스트, 출처, 신뢰도가 포함됩니다. 근거가 없거나 약하면
추측하지 않고 evidence 부족을 반환합니다.

프레임은 영상 전체를 일정한 간격으로 모두 저장하지 않습니다. 전사의 화면 참조,
코드·표·도식 후보, 사용자가 요청한 시점을 우선합니다. OCR을 설치한 경우 인식된
문자도 전사와 별도의 출처로 보관합니다.

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
- --daily-limit, --rpm-limit — Gemini 호출 추정 한도
- --width — 자막 줄 폭. 한국어 기본 20, 영어 기본 42

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

실측 번들 크기는 23분 영상 약 55.8MB, 58분 영상 약 110.3MB였습니다. 오디오는
전사 후 정리할 수 있고, 프레임을 추출한 뒤에는 영상을 삭제할 수 있습니다.
영상까지 보관하려면 --keep-video를 사용하세요.

로컬 사용량 원장은 API 키 자체가 아닌 키의 해시와 Pacific 날짜별 시도 횟수를
기록합니다. 작업 전 예상 호출 수를 보여주며, 설정한 한도를 넘으면 시작 전에
멈춥니다. 로컬 수치는 추정치이고 AI Studio의 서버 수치가 최종 기준입니다.

## 요구 사항

- Python 3.11+
- ffmpeg / ffprobe — 청크 분할과 프레임 추출
- 선택 사항: tesseract — 프레임 OCR

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
- OCR은 pytesseract와 tesseract 바이너리가 있을 때만 동작합니다. 없으면 프레임은
  시각과 위치로만 조회됩니다.
- 자막 병합 임계값은 제한된 실제 영상으로 조정되어 다른 영상의 추가 검증이 필요합니다.
- 청크가 3개 이상이고 겹치는 구간에 근거가 없으면 서로 다른 청크의 화자를 같은
  사람인지 확정하지 못하고 unresolved로 남길 수 있습니다.
- 실영상 검증은 23분과 58분 영상까지 완료했습니다. 실제 API 경로의 중단·재개는
  추가 검증이 필요합니다.
- 화면 후보를 찾는 표현은 현재 한국어와 영어를 중심으로 지원합니다.
- 화면 검색은 전체 프레임을 무작위로 읽는 방식이 아니라, 전사의 화면 참조와 요청
  시점을 우선하는 후보 기반 방식입니다.

## 문서

- [Code signing policy](CODE_SIGNING_POLICY.md) — Windows 배포 파일의 빌드·검토·서명 정책
- [PRIVACY.md](PRIVACY.md) — API 키, 로컬 데이터, 외부 서비스 통신
- [CONTRACT.md](CONTRACT.md) — 단계 사이 JSON 구조와 검증 기준
- [DECISIONS/](DECISIONS/) — 설계 결정 기록
- [CONTRIBUTING.md](CONTRIBUTING.md) — 개발 환경과 PR 절차
- [SECURITY.md](SECURITY.md) — 비공개 취약점 신고 절차

## 로드맵

- [ ] 호스트별 타임스탬프 링크 통일
- [ ] 청크 전사 파이프라이닝
- [ ] 여러 영상 통합 분석

## 라이선스

MIT. [LICENSE](LICENSE)를 참고하세요.

CuePrecise는 YouTube 및 Google과 제휴 관계가 없고 두 회사의 공식 제품도 아닙니다.
YouTube는 지원 대상 서비스일 뿐입니다.
