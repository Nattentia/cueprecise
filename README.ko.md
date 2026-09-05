<p align="right"><strong>한국어</strong> | <a href="./README.md">English</a></p>

# CuePrecise

> **YouTube 영상에서 원하는 대목을 찾고, 그 장면으로 바로 이동하세요.**

CuePrecise는 찾고 싶은 내용이나 화면이 나오는 시점을 찾아 그 자리부터 재생되는 링크로
돌려주는 로컬 MCP 서버입니다. Claude나 Codex에서 한국어로 질문하면 원문 대본과 화면
프레임도 함께 보여주므로 답이 나온 장면을 바로 확인할 수 있습니다.

외국어 영상도 한국어로 검색할 수 있습니다. 전사와 프레임, 검색 색인은 컴퓨터에 남아
다음 대화에서도 이어서 찾습니다.

## 할 수 있는 일

| 하고 싶은 일 | 결과 |
|---|---|
| 원하는 내용을 말로 검색 | 질문과 맞는 대본 구간, 타임스탬프, 근거 |
| 찾은 대목으로 바로 이동 | 해당 시간부터 재생되는 YouTube 하이퍼링크 |
| 특정 화면이 나온 시점 검색 | 조건과 일치하는 후보 프레임, 타임스탬프, 재생 링크 |
| 외국어 영상에 한국어로 질문 | 한국어 설명과 원어 대본·화면 근거 |
| 이름·기술 용어가 빠진 대본 보완 | Gemini 전사와 YouTube 자막을 맞춘 원문 |
| 나중에 다시 검색 | 컴퓨터에 저장된 전사, 프레임, 검색 색인 |

### 응답 예시

**질문**

> self-supervised learning 설명이 시작되는 곳과 그 문구가 적힌 화면을 찾아줘.

**CuePrecise 응답**

- [03:28부터 재생](https://www.youtube.com/watch?v=jcBDSLSeud4&t=208s)
- 원문 대본과 일치하는 화면 프레임
- 대본과 자막에서 가져온 단어별 출처

타임스탬프를 누르면 YouTube가 해당 시점에서 열립니다.

### 설치

[**CuePrecise 내려받기 →**](https://github.com/Nattentia/cueprecise/releases)

Windows용 Claude Desktop에서는 `cueprecise-windows.mcpb` 파일을 확장 프로그램으로
설치합니다. Codex, Claude Code, Cursor, Windsurf, VS Code, Gemini CLI에는
`cueprecise-setup.exe`를 사용하세요. 자세한 과정은 [빠른 시작](#빠른-시작)에 있습니다.

두 설치 파일에는 아직 디지털 서명되지 않은 실행 파일이 들어 있습니다. 이 저장소의
Releases 페이지에서 내려받고, 필요하면 `SHA256SUMS.txt`와 체크섬을 비교하세요.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml/badge.svg)](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-491%20passing-brightgreen.svg)](#테스트)

### 실제 사용 화면

```text
나: 폴란드어를 모르는데, 이 인터뷰는 무슨 내용이야?

AI 앱 + CuePrecise:
한국어로 설명하고 관련 구간의 재생 링크,
원문 대본, 화면 프레임을 근거로 보여줍니다.
```

https://github.com/user-attachments/assets/ce7d595b-871f-469a-bcb8-798713751ffd

<sub>데모 영상: Daniel Bartosiewicz | Content i Automatyzacja의 [「Czym jest prompt injection i jak chronić firmę przed złośliwą instrukcją dla AI? Gośc. Tomasz Bartel」](https://www.youtube.com/watch?v=W5C3FdUO0vs), CC BY 라이선스</sub>

**[전체 설치 방법](#빠른-시작)** · **[MCP 도구](#도구)** ·
**[처리 과정](#처리-과정)** · **[현재 제한](#현재-제한)**

---

## 답을 원본에서 확인합니다

영상의 내용을 다른 언어로 요약하면 그 답이 원본의 어느 말과 장면에서 나왔는지 확인하기
어렵습니다. YouTube 자막은 영상에 따라 거칠거나 비어 있으며 화면에만 나오는 정보는
담지 못합니다.

CuePrecise는 세 가지 자료를 같은 시간축에 묶습니다.

- Gemini가 원래 음성을 듣고 만든 전사
- 빠진 이름과 기술 용어를 보완하는 YouTube 자막
- 말로 설명되지 않은 화면 정보가 담긴 프레임

AI 앱은 이 근거를 읽고 질문한 언어로 답합니다. CuePrecise에는 원문과 시간 정보가
그대로 남고 자막에서 복원한 단어에는 출처도 따로 기록됩니다.

내용을 검색하면 대본에서 가장 가까운 시간대를 찾습니다. 화면을 검색하면 영상에서 맞는
프레임을 찾고 그 프레임이 나온 시각을 돌려줍니다. 두 검색 결과의 타임스탬프에는 원본
YouTube 영상의 해당 시점으로 가는 링크가 붙습니다.

### 실제 전사 사례

23분짜리 한국어 기술 강의 `jcBDSLSeud4`를 네 차례 전사했을 때 Gemini는
`self supervised learning`을 매번 빠뜨렸습니다. CuePrecise는 시간대가 맞는 YouTube
자막에서 이 표현을 가져와 보완했고, Gemini가 전사한 단어는 지우거나 고치지 않았습니다.

| 자료 | 라틴 문자 단어 수 | `self supervised learning` | 한국어 품질 |
|---|---:|---|---|
| YouTube `ko-orig` 자동 자막 | 91 | 있음 | 거침 |
| Gemini 전사 (`ko-KR`) | 29 | **없음** | 좋음 |
| Gemini 전사 (언어 자동 감지) | 28 | **없음** | 좋음 |
| **CuePrecise 병합본** | **38** | **있음** | **좋음** |

이 표는 한 영상에서 확인한 사례이며 전체 정확도를 뜻하지 않습니다. 병합 규칙과 검증
조건은 [`CONTRACT.md`](CONTRACT.md)에 적혀 있습니다.

---

## 빠른 시작

### Claude Desktop (Windows)

1. [Releases](https://github.com/Nattentia/cueprecise/releases)에서
   `cueprecise-windows.mcpb`를 내려받습니다.
2. Claude Desktop에서 **설정 → 확장 프로그램 → 고급 설정 → 확장 프로그램 설치**로
   이동합니다.
3. 파일을 고르고 Gemini API 키와 영상 자료를 저장할 폴더를 입력합니다.
4. CuePrecise를 켭니다. Claude가 요청하면 앱을 다시 시작하세요.

API 키 입력란은 민감 정보로 표시되며 Claude의 확장 프로그램 설정에서 관리됩니다.
영상 자료는 기본적으로 `~/.cueprecise/data`에 저장됩니다. 약 86MiB인 MCPB 파일 안에
실행에 필요한 영상 처리 도구가 모두 들어 있어 추가 다운로드가 없습니다.

### Windows의 다른 AI 앱

1. [Releases](https://github.com/Nattentia/cueprecise/releases)에서
   `cueprecise-setup.exe`를 내려받아 실행합니다.
2. 설치 뒤 열리는 화면에서 **API 키 만들기**를 누릅니다.
3. Google AI Studio에서 키를 만든 뒤 CuePrecise에 붙여넣습니다.
4. 컴퓨터에서 발견된 AI 앱 가운데 연결할 앱을 고르고 **연결하기**를 누릅니다.
5. 연결한 앱을 완전히 종료했다가 다시 실행합니다.

설치 프로그램은 필요한 영상 처리 도구를 준비하고, 기존 설정을 백업한 뒤 CuePrecise
항목만 추가합니다. Windows에서는 현재 사용자만 풀 수 있도록 API 키를 DPAPI로
암호화합니다. AI 앱의 설정 파일에는 암호화된 키 파일의 위치만 들어갑니다. 이전 버전이
평문으로 저장한 키도 업그레이드할 때 암호화 저장소로 옮깁니다.

> `v0.2.4`는 디지털 서명이 없는 시험판입니다. Windows에서 "알 수 없는 게시자" 경고가
> 나타날 수 있습니다. 이 저장소의 Releases 페이지에서 받은 파일만 사용하세요.

### macOS, Linux 또는 명령줄에서 설치

[`uv`](https://docs.astral.sh/uv/getting-started/installation/)를 사용하면 저장소를
복제하거나 Python 환경을 직접 만들지 않아도 됩니다.

```bash
uv tool install git+https://github.com/Nattentia/cueprecise
cueprecise setup
```

`ffmpeg`와 `ffprobe`를 설치한 뒤 환경을 확인합니다.

```bash
cueprecise doctor
```

[Google AI Studio](https://aistudio.google.com/api-keys)에서 API 키를 만들고 표준 입력으로
붙여넣습니다. 이 방법을 쓰면 명령어나 셸 기록에 키가 남지 않습니다.

```bash
cueprecise setup --api-key -         # 키를 붙여넣고 Enter
cueprecise run "https://www.youtube.com/watch?v=VIDEO_ID" --language pl-PL
cueprecise status VIDEO_ID
```

파일이나 비밀번호 관리 프로그램에서 키를 읽을 수도 있습니다.

```bash
cueprecise setup --api-key-file ~/.gemini-key
pass show gemini/api-key | cueprecise setup --api-key -
```

키가 노출됐다면 Google AI Studio에서 삭제하고 새로 만드세요. 자세한 절차는
[개인정보 및 네트워크 정책](PRIVACY.md#api-키를-폐기하는-방법)에 있습니다.

소스 코드를 직접 개발할 때는 저장소를 복제해 설치합니다.

```bash
git clone https://github.com/Nattentia/cueprecise.git
cd cueprecise
python -m pip install -r requirements.txt
python src/pipeline.py --help
```

---

## 다른 MCP 호스트에 연결하기

`cueprecise setup`은 컴퓨터에서 지원하는 AI 앱을 찾아 한꺼번에 연결합니다.

```bash
cueprecise setup                    # 발견한 앱 모두 연결
cueprecise setup --client codex     # Codex만 연결
cueprecise doctor                   # 앱별 설치 및 연결 상태 확인
```

| 앱 | 설정 파일 | 연결 방식 |
|---|---|---|
| Claude Desktop | `claude_desktop_config.json` | 설정 파일 수정 |
| Codex | `$CODEX_HOME/config.toml` (기본값 `~/.codex`) | `codex mcp add` |
| Claude Code | `~/.claude.json` | `claude mcp add -s user` |
| VS Code | `Code/User/mcp.json` (최상위 키 `servers`) | `code --add-mcp`, 제거할 때는 파일 수정 |
| Cursor | `~/.cursor/mcp.json` | 설정 파일 수정 |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | 설정 파일 수정 |
| Gemini CLI | `~/.gemini/settings.json` | `gemini mcp add -s user`, 실패하면 파일 수정 |

실행 파일이 `PATH`에 있는 앱만 자동으로 찾습니다. 설정 폴더만 남은 앱은 설치된 것으로
보지 않습니다. 자동 감지에 나오지 않은 앱은 `--client <이름>`으로 직접 지정할 수
있습니다.

같은 이름의 `cueprecise` 항목이 이미 있고 CuePrecise가 만든 설정이 아니라면 그 앱은
건너뜁니다. 기존 MCP 설정은 덮어쓰지 않으며 한 앱에서 연결이 실패해도 나머지는 계속
처리합니다.

macOS와 Linux에서는 MCP 클라이언트의 로컬 설정으로 API 키를 전달합니다. 해당 파일은
본인 계정만 읽을 수 있게 관리하세요. `--api-key 값`처럼 명령줄에 키를 직접 적는 방식은
모든 운영체제에서 거부됩니다.

ChatGPT 커넥터와 Claude.ai 웹은 HTTPS와 OAuth를 사용하는 원격 MCP 서버만 받습니다.
사용자 컴퓨터에서 실행되는 CuePrecise는 이 두 서비스에 연결할 수 없습니다.

아래 JSON은 복제한 소스 코드를 실행하거나 표에 없는 MCP 호스트를 직접 설정할 때
사용합니다. API 키가 평문으로 저장되므로 가능한 경우 `setup` 명령을 권합니다.

```json
{
  "mcpServers": {
    "cueprecise": {
      "command": "python",
      "args": [
        "C:/path/to/cueprecise/src/mcp_server.py",
        "--bundle-root",
        "C:/path/to/cueprecise/data"
      ],
      "env": { "GEMINI_API_KEY": "..." }
    }
  }
}
```

절대 경로를 입력하세요. `--bundle-root`는 전사와 검색 색인 등 영상별 자료를 보관할
폴더입니다. MCP 호스트를 다시 시작해도 이 폴더의 자료를 검색할 수 있습니다.

`GEMINI_API_KEY` 없이도 서버와 조회 도구는 작동합니다. 새 영상을 전사하려는 요청만
API 키가 필요하다는 안내와 함께 멈춥니다.

### 도구

| 도구 | 하는 일 | Gemini 호출 |
|---|---|---:|
| `cueprecise_register` | 영상 등록 및 분석 | 오디오 청크당 1회 |
| `cueprecise_status` | 진행 상태, 산출물, 예상 사용량 확인 | 없음 |
| `cueprecise_outline` | 타임스탬프 목차와 화자 상태 조회 | 없음 |
| `cueprecise_query` | 원하는 내용의 시간대 검색과 YouTube 재생 링크 생성 | 없음 |
| `cueprecise_excerpt` | 지정한 시간대의 대본, 프레임, 재생 링크 조회 | 없음 |
| `cueprecise_frames` | 특정 화면이 나온 시각 검색과 후보 프레임 추출 | 없음 |
| `cueprecise_summary` | 요청할 때 요약 생성 또는 저장된 요약 조회 | 없음 |
| `cueprecise_set_summary` | AI 앱이 근거로 다듬은 요약 검증 및 저장 | 없음 |
| `cueprecise_set_chapter_titles` | AI 앱이 쓴 챕터 제목 검증 및 저장 | 없음 |
| `cueprecise_purge` | 다시 만들 수 있는 산출물이나 지정한 자료 삭제 | 없음 |

Gemini는 음성을 전사할 때만 호출됩니다. 챕터 제목과 요약은 AI 앱이 검색된 근거를 읽고
작성하므로 CuePrecise가 Gemini를 따로 호출하지 않습니다.

---

## 영상별로 저장되는 자료

```text
data/<video_id>/
  job.json                  청크 계획과 진행 상태
  raw/
    source.<ext>            원본 오디오
    captions.json           YouTube 원어 자막
    metadata.json           언어 확인에 쓰는 영상 정보
    transcripts/            청크별 전사와 원본 응답
    frames/                 추출한 JPEG 프레임
  derived/
    transcript.json         하나로 합친 Gemini 전사
    merged.json             전사와 자막을 합친 근거
    chapters.json           타임스탬프 목차
    frames.json             프레임 색인
    output.srt, output.txt  선택해서 만드는 자막 파일
  index.sqlite3             검색 색인과 요약
```

`merged.json`의 각 단어에는 어디에서 가져왔는지와 화자 신뢰도가 붙습니다.

```json
{
  "text": "supervised", "start": 208.93, "end": 209.87,
  "speaker": "speaker:0", "speaker_status": "confirmed",
  "origin": "youtube"
}
```

---

## 처리 과정

```text
fetch       URL             → 오디오 + 360p 영상 + 자막 + 메타데이터
plan        오디오          → 청크 계획
transcribe  청크            → 타임스탬프가 있는 Gemini 전사   청크당 1회
assemble    청크 전사       → transcript.json
merge       전사 + 자막     → 출처가 기록된 merged.json
chapters    병합된 근거     → 타임스탬프 목차
visual      근거 + 영상     → 프레임과 프레임 색인
index       영상별 자료     → SQLite 검색 색인
```

각 단계는 JSON 파일로 결과를 넘기며 따로 다시 실행됩니다. 설정이 같으면 이미 끝난
청크를 재사용합니다. 전사 응답은 검증 전에 저장합니다. 파싱에 실패해도 이미 받은
응답에서 다시 시작하므로 Gemini 호출을 낭비하지 않습니다.

`--language`에는 영상이 실제로 사용하는 언어를 넣는 편이 안전합니다. `pl-PL`,
`ko-KR` 같은 BCP-47 코드를 쓸 수 있습니다. 언어를 지정하지 않으면 Gemini가 원문 대신
번역문을 돌려주는 경우가 있습니다. CuePrecise는 이를 감지하면 남은 청크에 호출을 쓰기
전에 작업을 멈춥니다.

---

## 요구 사항

- Python 3.11 이상
- 오디오 청크와 프레임을 만드는 `ffmpeg`, `ffprobe`
- 프레임 OCR에 쓸 `tesseract` (선택 사항)

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-optional.txt  # OCR과 시간대 지원
```

설치 패키지 이름은 `cueprecise-mcp`이며 실행 명령은 `cueprecise`와
`cueprecise-mcp`입니다. 아직 PyPI에는 배포되지 않았으므로 위에 적힌 GitHub URL에서
설치해야 합니다.

## 테스트

```bash
python -m unittest discover -s tests
```

테스트 491개는 Python 표준 라이브러리의 `unittest`로 실행됩니다. 테스트 도중
네트워크나 Gemini API를 호출하지 않습니다. `google-genai`가 없으면 해당 SDK가 필요한
테스트만 건너뜁니다.

## Gemini API 사용량

작업을 시작하기 전에 예상 전사 호출 수를 보여주고 설정된 일일 한도를 넘으면 멈춥니다.
로컬 사용 기록에는 API 키의 해시와 태평양 표준시 기준 날짜별 시도 횟수만 남으며 키
원문은 저장하지 않습니다. 정확한 서버 사용량은 Google AI Studio에서 확인하세요.

---

## 로드맵

- [ ] Gemini가 다음 청크를 처리하는 동안 앞 청크를 검사하는 파이프라이닝
- [ ] 여러 영상을 출처별 근거와 함께 검색하고 비교하기

---

## 현재 제한

- YouTube 자막에서 가져온 이름이나 용어에는 원래 자막의 오탈자가 남을 수 있습니다.
- 프레임 OCR에는 `pytesseract` 패키지와 Tesseract 실행 파일이 모두 필요합니다.
- 자막 병합 임계값은 한 영상으로 조정했으며 더 넓은 검증이 필요합니다.
- 청크가 세 개 이상일 때 겹치는 구간에 나오지 않은 화자는 `unresolved`로 남을 수
  있습니다. CuePrecise는 근거가 부족한 화자에게 임의의 신원을 붙이지 않습니다.
- 실제 영상을 이용한 검증은 현재 58분 길이까지 마쳤습니다. 실제 API 작업의 중단과
  재개는 처음부터 끝까지 검증하지 못했습니다.
- 대본에서 화면 참조 표현을 자동으로 찾아 프레임 후보를 고르는 기능은 한국어와 영어를
  지원합니다.

## 문서

- [`CODE_SIGNING_POLICY.md`](CODE_SIGNING_POLICY.md) — Windows 배포 파일의 빌드, 검토,
  서명 정책
- [`PRIVACY.md`](PRIVACY.md) — API 키와 로컬 자료, 외부 통신, 제거 과정에서 바뀌는 항목
- [`CONTRACT.md`](CONTRACT.md) — 단계 사이에서 오가는 자료의 형식과 검증 규칙
- [`DECISIONS/`](DECISIONS/) — 주요 설계 결정과 검토한 대안
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — 개발 환경, 변경 원칙, PR 절차
- [`SECURITY.md`](SECURITY.md) — 취약점을 비공개로 신고하는 방법

## 라이선스

CuePrecise는 MIT 라이선스로 배포됩니다. 자세한 내용은 [`LICENSE`](LICENSE)에 있습니다.

초기 전사 흐름은 MIT 라이선스의
[`gemini-transcribe-wrapper`](https://pypi.org/project/gemini-transcribe-wrapper/0.0.13/)
구현을 참고했습니다. CuePrecise의 코드는 별도로 작성됐습니다.

CuePrecise는 YouTube 또는 Google과 제휴한 제품이 아닙니다. YouTube는 CuePrecise가
지원하는 서비스입니다.
