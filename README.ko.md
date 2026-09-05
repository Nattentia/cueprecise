<p align="right"><strong>한국어</strong> | <a href="./README.md">English</a></p>

# CuePrecise

> **YouTube 영상에 관해 물어보세요. CuePrecise는 누가 언제 무엇을 말했고 그때 화면에 무엇이 있었는지 찾아줍니다.**

CuePrecise는 Claude Desktop·Codex 같은 AI 앱에 연결하는 오픈소스 MCP 서버다. 영상의
음성·자막·화자·화면을 같은 시간축에 묶어 검색 가능한 자료로 저장한다. 외국어 영상도
한국어로 질문할 수 있다. 답이 나온 대목은 원문과 장면으로 확인할 수 있다.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml/badge.svg)](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-491%20passing-brightgreen.svg)](#테스트)

## 영상으로 할 수 있는 일

| 하고 싶은 일 | 돌아오는 결과 |
|---|---|
| 영상 내용 묻기 | 관련 대본과 화면을 근거로 한 답변 |
| 원하는 내용이 나온 때 찾기 | 원문과 타임스탬프 |
| 특정 그림이나 화면 찾기 | 조건에 맞는 프레임과 해당 시점 |
| 인물별 주장 비교하기 | 화자마다 묶은 발언과 관점 |
| 영상 전체 파악하기 | 요약과 타임스탬프 목차 |
| 새 쟁점으로 가상 토론 열기 | 영상 속 인물들의 실제 발언을 근거로 구성한 토론 |
| 나중에 다시 질문하기 | 컴퓨터에 저장된 전사·프레임·검색 색인 |

Claude에서는 응답의 타임스탬프를 누르면 해당 시점부터 YouTube 영상이 재생된다. 링크를
표시하는 방식은 AI 앱마다 다르다.

### 이렇게 써볼 수 있다

```text
이 영상에서 self-supervised learning을 설명하는 부분을 찾아줘.
그 문구가 적힌 화면은 언제 나와?

출연자별로 기본소득에 관한 주장을 정리해줘.

이 토크쇼의 출연자들이 최근 이슈를 두고 다시 토론한다면 어떤 이야기를 할까?
영상에서 실제로 했던 발언을 근거로 구성해줘.
```

### Windows용 Claude Desktop에서 시작하기

[**CuePrecise 내려받기 →**](https://github.com/Nattentia/cueprecise/releases)

1. 최신 릴리스에서 `cueprecise-windows.mcpb`를 내려받습니다.
2. Claude Desktop에서 **설정 → 확장 프로그램 → 고급 설정 → 확장 프로그램 설치**를
   차례로 누릅니다.
3. 내려받은 파일을 선택하고, Claude가 물으면
   [Gemini API 키](https://aistudio.google.com/api-keys)를 붙여넣습니다.
4. CuePrecise를 켜고 Claude에게 YouTube 링크에 관해 질문합니다.

API 키를 다른 사람에게 공개하지 마세요. 필요하면
[Google AI Studio](https://aistudio.google.com/api-keys)에서 언제든 삭제할 수 있습니다.

파일 하나에 CuePrecise와 영상 처리 도구가 모두 들어 있습니다. 약 86MiB이며 Python,
Git, FFmpeg, 명령어 입력, 설정 파일 수정이 필요 없습니다. 이 확장 프로그램은 현재
Windows용 Claude Desktop에서 사용할 수 있습니다. Codex·Claude Code·Cursor·Windsurf·
VS Code·Gemini CLI에는 같은 릴리스의 `cueprecise-setup.exe`를 사용합니다.

두 파일 모두 아직 디지털 서명되지 않은 실행 파일을 포함합니다. 반드시 이 저장소의
Releases에서 내려받으세요. 필요하면 `SHA256SUMS.txt`로 파일을 확인하세요.

**[자세한 설치 방법](#빠른-시작)** · **[지원하는 도구](#도구)** ·
**[작동 원리](#동작-방식)** · **[알려진 제한](#알려진-제한)**

---

## 말과 화면을 같은 근거로 쓴다

CuePrecise는 세 가지 자료를 같은 시간축에 놓는다.

- Gemini가 원래 음성을 듣고 만든 전사
- 빠진 이름과 기술 용어를 보완하는 YouTube 자막
- 말로 설명되지 않은 정보가 담긴 화면 프레임

질문과 관련된 대목을 찾으면 원문·타임스탬프·가까운 화면 프레임을 함께 돌려준다.
근거가 없으면 답을 꾸미지 않고 찾지 못했다고 알린다. 분석 결과는 컴퓨터에 남으므로
다음 대화에서도 같은 영상을 이어서 검색할 수 있다.

### 전사에서 빠진 영어 용어를 되찾은 사례

한국어 강의나 대담에서는 문장 속 영어 이름과 기술 용어가 전사에서 빠질 때가 있다.
Gemini 전사는 자연스러운 한국어 문장을 만들지만 아래 예시에서는
`self supervised learning`을 놓쳤다.

```
그러면 어떻게 이 능력을 학습을 했을까요?  라는 방식으로 학습을 합니다.
                                       ↑ 영어 용어가 통째로 사라졌다
```

CuePrecise는 같은 시간대의 YouTube 자막에서 빠진 표현을 찾아 보완한다.

```
그러면 어떻게 이 능력을 학습을 했을까요? self supervised learning 라는 방식으로 학습을 합니다.
                                       └─ origin: "youtube" 로 출처가 남는다
```

영상 `jcBDSLSeud4`(23분 한국어 강의) 실측:

| 소스 | 라틴 단어 | `self supervised learning` | 한국어 품질 |
|---|---:|---|---|
| YouTube `ko-orig` 자동자막 | 91 | 있음 | 거칠다 |
| Gemini 전사 (`ko-KR`) | 29 | **없음** | 좋다 |
| Gemini 전사 (자동 감지) | 28 | **없음** | 좋다 |
| **CuePrecise (병합)** | **38** | **있음** | **좋다** |

Gemini는 네 차례 실행에서 모두 그 용어를 놓쳤다. CuePrecise는 시간 공백과 한국어 조사를
함께 확인해 해당 구간에만 영어를 넣는다. Gemini가 전사한 단어는 지우거나 고치지 않으며
자막에서 가져온 단어에는 출처를 남긴다.

### 화자별로 다시 읽는다

CuePrecise는 발화를 화자별로 나누고 긴 영상을 여러 청크로 처리할 때도 같은 화자를
이어 붙인다. 확인된 화자와 추정한 화자를 구분하고 근거가 부족하면 `unresolved`로
남긴다.

이 정보로 인물별 주장과 논거를 모아 비교할 수 있다. 영상에 없던 새 쟁점을 던지고
각 인물의 기존 발언을 근거로 가상 토론을 구성하는 것도 가능하다. 가상 토론은 AI가 만든
시뮬레이션이며 영상 속 인물이 실제로 새 주제에 관해 한 발언은 아니다.

---

## 빠른 시작

### Windows용 Claude Desktop — 파일 하나로 설치

1. [Releases](https://github.com/Nattentia/cueprecise/releases)에서
   `cueprecise-windows.mcpb`를 내려받는다.
2. Claude Desktop에서 **설정 → 확장 프로그램 → 고급 설정 → 확장 프로그램 설치**를
   차례로 누른다.
3. 파일을 선택한다. Claude가 Gemini API 키와 영상 자료를 둘 폴더를 물어본다.
4. CuePrecise를 켠다. Claude가 요청하면 앱을 다시 시작한다.

API 키 입력란은 민감 정보로 표시되며 Claude의 확장 프로그램 설정에서 관리된다. 영상
자료 폴더의 기본값은 `~/.cueprecise/data`다. 약 86MiB인 파일 안에 CuePrecise 서버,
`yt-dlp`, FFmpeg, FFprobe가 모두 들어 있어 다른 프로그램을 먼저 설치하거나 추가로
내려받을 필요가 없다.

### Windows의 다른 AI 앱

1. [Releases](https://github.com/Nattentia/cueprecise/releases)에서 `cueprecise-setup.exe`를 내려받아 실행한다.
2. 설치가 끝나면 자동으로 열리는 화면에서 **API 키 만들기**를 누른다.
3. Google AI Studio에서 만든 키를 복사해 붙여넣는다.
4. 컴퓨터에서 발견된 AI 앱을 선택하고 **연결하기**를 누른다.
5. “연결 완료”가 보이면 선택한 AI 앱을 완전히 껐다가 다시 켠다.

Python이나 Git을 설치하거나 명령어를 입력할 필요가 없다. 필요한 영상 처리 도구도
연결 과정에서 자동으로 준비한다. Windows에서는 API 키를 현재 사용자만 풀 수 있도록
Windows DPAPI로 암호화한다. AI 앱 설정에는 암호화 파일의 위치만 남긴다. 기존 설정은
백업한 뒤 CuePrecise 항목만 추가한다. 예전 버전이 설정에 평문으로 저장했던 키도
업그레이드할 때 자동으로 암호화 저장소로 옮긴다.

> `v0.2.4`는 아직 디지털 서명되지 않은 시험판이다. Windows에서 "알 수 없는 게시자"
> 경고가 나타날 수 있으므로 반드시 이 저장소의 Releases에서 받은 파일만 사용한다.

### macOS·Linux 또는 명령어 설치

[`uv`](https://docs.astral.sh/uv/getting-started/installation/)가 있으면 저장소를 직접
받거나 Python 환경을 만들 필요가 없다.

```bash
uv tool install git+https://github.com/Nattentia/cueprecise
cueprecise setup
```

`setup`은 Claude Desktop 설정과 기본 데이터 디렉터리 `~/.cueprecise/data`를 만들고
기존 설정은 timestamp가 붙은 `.bak` 파일로 보존한다. Claude Desktop을 다시
시작하면 된다.

영상 분석에는 `ffmpeg`와 `ffprobe`가 PATH에 있어야 한다. 환경을 확인하려면:

```bash
cueprecise doctor
```

[API 키](https://aistudio.google.com/api-keys)는 명령이나 셸 기록에 들어가지 않도록
표준 입력으로 붙여넣는다. 키 없이 설치해도 조회 도구는 동작하며 나중에 다시 실행할 수 있다.

```bash
cueprecise setup --api-key -         # 키를 붙여넣고 Enter
cueprecise run "https://www.youtube.com/watch?v=VIDEO_ID" --language ko-KR
cueprecise status VIDEO_ID
```

키를 파일이나 표준 입력으로 줄 수도 있다. 셸 기록 파일에 키를 남기지 않는 경로다.

```bash
cueprecise setup --api-key-file ~/.gemini-key      # 파일에서 읽는다
pass show gemini/api-key | cueprecise setup --api-key -   # 표준 입력에서 읽는다
```

키가 노출됐다고 판단되면 [Google AI Studio](https://aistudio.google.com/api-keys)에서
지우고 새로 만든다. 자세한 절차는 [개인정보 정책](PRIVACY.md#api-키를-폐기하는-방법)에
있다.

CLI에서 `--bundle-root`를 생략하면 현재 디렉터리의 `data/<video_id>/`에 쌓인다. MCP는
`setup`이 정한 데이터 디렉터리를 쓴다.

소스에서 개발하거나 기존 실행 경로가 필요한 경우에만 clone 방식으로 설치한다.

```bash
git clone https://github.com/Nattentia/cueprecise.git
cd cueprecise
python -m pip install -r requirements.txt
python src/pipeline.py --help
```

---

## MCP 호스트에 붙이기

MCP 스펙은 `2026-07-28`판에서 `initialize` 악수를 없애고 요청마다 판을 싣는
방식으로 바뀌었다. CuePrecise는 **새 판과 그 이전 판 양쪽 모두에서 동작한다.**
`server/discover`로 물으면 지원 판을 알린다. `initialize`로 걸어오면 악수로 받는다.

`cueprecise setup`은 이 PC에 설치된 AI 앱을 찾아 전부에 붙인다. 앱 이름을 몰라도 된다.

```bash
cueprecise setup                    # 찾은 앱 전부
cueprecise setup --client codex     # 하나만
cueprecise doctor                   # 앱별 설치·연결 상태
```

| 앱 | 설정 파일 | 붙이는 방법 |
|---|---|---|
| Claude Desktop | `claude_desktop_config.json` | 파일 |
| Codex | `$CODEX_HOME/config.toml` (기본 `~/.codex`) | `codex mcp add` |
| Claude Code | `~/.claude.json` | `claude mcp add -s user` |
| VS Code | `Code/User/mcp.json` (최상위 키가 `servers`) | `code --add-mcp`, 제거는 파일 |
| Cursor | `~/.cursor/mcp.json` | 파일 |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | 파일 |
| Gemini CLI | `~/.gemini/settings.json` | `gemini mcp add -s user`, 실패하면 파일 |

설치 여부는 실행 파일이 `PATH`에 있는지로 판정한다. 남아 있는 설정 폴더를 앱이 있다는
증거로 삼지 않는다. 감지되지 않은 앱도 `--client <이름>`으로 이름을 대면 붙는다.

`cueprecise`라는 항목이 이미 있는데 CuePrecise가 만든 것이 아니면 그 앱은 건너뛴다.
남의 설정을 덮어쓰지 않으며 한 앱이 실패해도 나머지는 계속 붙인다.

Windows의 `cueprecise setup`도 설치 화면과 같은 DPAPI 암호화 저장소를 쓴다. macOS와
Linux에서는 현재 MCP 클라이언트의 로컬 설정으로 키를 전달하므로 해당 파일을 본인 계정만
읽을 수 있게 관리해야 한다. 모든 운영체제에서 `--api-key 값`처럼 키를 명령줄에 직접
적는 방식은 프로세스 목록과 셸 기록 노출을 막기 위해 거절한다.

**ChatGPT 커넥터와 Claude.ai 웹에는 붙지 않는다.** 둘은 HTTPS 주소와 OAuth로 접속하는
원격 MCP만 받는다. CuePrecise는 이 PC에서 도는 로컬 서버라 그 자리에 넣을 수 없다.

다음 JSON은 소스 clone을 쓰거나 위 목록에 없는 MCP 호스트에 손으로 붙일 때만 필요하다.
키가 평문으로 남으므로 가능하면 `setup` 명령을 사용한다.

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
      "env": {
        "GEMINI_API_KEY": "..."
      }
    }
  }
}
```

절대 경로로 적는다. Windows에서도 `/`를 쓰면 JSON에서 역슬래시를 이스케이프할 일이
없다. macOS·Linux는 `/home/you/cueprecise/src/mcp_server.py` 꼴이다.

`--bundle-root`는 영상 번들을 쌓아둘 디렉터리다. 호스트를 껐다 켜도 그 안의 번들과
`index.sqlite3`로 이전 대화의 근거를 다시 찾는다.

`GEMINI_API_KEY`가 없어도 서버는 뜬다. 조회 도구는 그대로 돈다. 전사가 필요한
도구만 그 사실을 알리며 멈춘다.

### 도구

| 도구 | 하는 일 | Gemini 호출 |
|---|---|---|
| `cueprecise_register` | 영상 등록·분석. 단계 선택 가능 | 청크당 1회 |
| `cueprecise_status` | 진행도, 산출물, 사용량 추정 | 없음 |
| `cueprecise_outline` | 개요와 타임스탬프 목차, 복원 용어, 화자 상태 | 없음 |
| `cueprecise_query` | 내용 질의. 근거 구간·프레임·타임스탬프 반환 | 없음 |
| `cueprecise_excerpt` | 특정 시각 구간의 자막과 프레임 | 없음 |
| `cueprecise_frames` | 화면 참조 시각의 프레임 추출. 영상이 없으면 그때 받는다 | 없음 |
| `cueprecise_summary` | 요청할 때만 요약 생성·조회 | 없음 |
| `cueprecise_set_summary` | 호스트가 근거로 개선한 요약을 검증·저장 | 없음 |
| `cueprecise_set_chapter_titles` | 호스트가 지은 챕터 제목을 검증·저장 | 없음 |
| `cueprecise_purge` | derived 재생성 및 명시적 삭제 | 없음 |

전사를 뺀 모든 단계는 로컬에서 돈다. 챕터 제목과 요약은 호스트 LLM이 근거를 보고
직접 쓰며 그것도 별도 호출을 만들지 않는다.

그림이나 화면을 설명해 장면을 찾는 일은 호스트 AI가 내용 검색과 프레임 조회를 함께
사용해 처리한다. 화자별 분석과 가상 토론도 검색 결과에 담긴 화자 정보와 원문 발화를
호스트 AI가 읽어 구성한다.

---

## 결과물

```
data/<video_id>/
  job.json                  작업 manifest — 청크 계획과 진행 상태
  raw/
    source.<ext>            원본 오디오
    captions.json           YouTube 원어 자동자막
    metadata.json           언어 판정에 쓰는 영상 정보 (200바이트대)
    transcripts/            청크별 전사 + 응답 원문
    frames/                 추출한 프레임 jpg
  derived/
    transcript.json         Gemini 전사 (청크 이어붙임)
    merged.json             전사 + 자막 병합  ← 근거 검색의 기준
    chapters.json           timestamp 목차
    frames.json             프레임 색인
    output.srt, output.txt  선택 (render 단계)
  index.sqlite3             검색 색인 + 요약
```

**`merged.json`** — 단어마다 출처와 화자 신뢰도가 붙는다.

```json
{
  "text": "supervised", "start": 208.93, "end": 209.87,
  "speaker": "speaker:0", "speaker_status": "confirmed",
  "origin": "youtube"
}
```

**`cueprecise_query` 응답** — 근거 없이 답하지 않는다.

```json
{
  "start": 1728.4, "end": 1740.2, "timecode": "00:28:48",
  "text": "그래서 이게 임상 실험을 중단을 했습니다. 아홉 명에게 적용했는데 한 명 빼고 여덟 명이 발작을 했습니다.",
  "source_path": "derived/merged.json", "source_kind": "transcript",
  "speaker": "speaker:3", "speaker_status": "inferred", "speaker_confidence": 0.75
}
```

**`output.srt`** — 단어 보존율 100%. 넘치는 텍스트를 잘라 버리지 않고 다음 큐로 넘긴다.

```srt
1
00:00:00,200 --> 00:00:02,400
저희 공부를 안해도 머리로 다 집어넣을 수 있습니까?
```

---

## 명령줄

```bash
python src/pipeline.py run <url> [옵션]
python src/pipeline.py status <video_id>
python src/pipeline.py purge <video_id> --scope <범위>
```

| `run` 옵션 | 기본값 | 설명 |
|---|---|---|
| `--language` | 자동 감지 | 쉼표 구분 BCP-47 (`ko-KR`). **지정을 권한다** |
| `--stages` | 기본 단계 | 돌릴 단계. `all`이면 선택 단계까지 전부 |
| `--bundle-root` | `data` | 번들을 쌓을 디렉터리 |
| `--force` | 꺼짐 | 캐시를 무시하고 다시 만든다 |
| `--skip-video` | 꺼짐 | 영상을 받지 않는다. 프레임이 필요하면 그때 받는다 |
| `--keep-video` | 꺼짐 | 프레임을 뽑은 뒤에도 영상을 지우지 않는다 |
| `--at` | — | 프레임을 뽑을 시각(초). 쉼표 구분 |
| `--max-frames` | 40 | 프레임 최대 개수 |
| `--chunk-max-secs` | 1790 | 청크 최대 길이 |
| `--overlap-secs` | 10 | 청크 겹침 |
| `--daily-limit` | 25 | 하루 호출 상한 |
| `--rpm-limit` | 2 | 분당 호출 상한 |
| `--width` | 20 | 자막 줄 폭 (한국어 20, 영어 42) |

`--scope`는 `chunks` · `video` · `derived` · `raw` · `all` 중 하나다. `chunks`와
`video`는 원본에서 다시 만들 수 있는 것만 지운다.

```bash
python src/pipeline.py run <url> --stages render   # 나중에 SRT/TXT만
python src/pipeline.py run <url> --stages visual   # 나중에 프레임만
python src/pipeline.py purge <id> --scope chunks   # 청크 오디오 정리
```

---

## 동작 방식

```
fetch       URL             →  오디오 + 360p 영상 + 자막 + 메타데이터   yt-dlp 한 번
plan        오디오          →  job.json, 청크 분할                      쿼터 0
transcribe  청크            →  청크별 전사                              청크당 1회
assemble    청크 전사       →  transcript.json                          쿼터 0
merge       전사 + 자막     →  merged.json                              쿼터 0
chapters    merged.json     →  chapters.json                            쿼터 0
render      merged.json     →  output.srt, output.txt         선택      쿼터 0
visual      merged + 영상   →  frames/, frames.json                     쿼터 0
index       번들            →  index.sqlite3                            쿼터 0
```

단계는 JSON 파일로만 이어진다. 각각 독립적으로 재실행할 수 있고 완료된 청크는
설정이 같으면 다시 호출하지 않는다. 중간에 실패해도 완료분은 보존되고 같은 명령으로
이어서 진행한다.

전사 응답 원문은 검증 **전에** 저장한다. 파싱이 실패해도 이미 쓴 호출이 남아,
재실행하면 Gemini를 다시 부르지 않고 그 원문으로 이어간다.

### 언어는 지정하는 편이 낫다

`--language`를 생략하면 Gemini가 알아서 고른다. 같은 한국어 영상에서 한 번은 한국어
전사가, 한 번은 **영어 번역문**이 나왔다. 번역문은 원문이 아니므로 근거로 쓸 수 없다.

CuePrecise는 청크마다 번역문 여부를 판정하고 걸리면 그 자리에서 멈춘다 — 남은 청크의 호출을
아끼기 위해서다. 판정은 이미 받아둔 자료(원어 자막 → 요청 언어 → 영상 메타데이터)로만
하며 추가 호출을 쓰지 않는다.

### 저장 공간

실측 번들 용량은 23분 55.8MB, 58분 110.3MB다. 오디오가 72~80%, 영상이 13~28%를
차지한다. 프레임을 뽑고 나면 영상은 지운다(`--keep-video`로 끈다). 전사가 끝난 청크
오디오는 `purge --scope chunks`로 지울 수 있고 원본이 남아 있으면 필요할 때 다시 뽑는다.

---

## 요구 사항

- Python 3.11+
- `ffmpeg` / `ffprobe` — 청크 분할, 프레임 추출. 파이썬 패키지가 아니라 따로 설치한다
- 선택 `tesseract` — 프레임 OCR. 없으면 `ocr_text`가 `null`이다

```bash
python -m pip install -r requirements.txt              # yt-dlp, google-genai
python -m pip install -r requirements-optional.txt     # OCR, 시간대 (선택)
```

`google-genai`는 `transcribe` 단계에만 필요하다. 나머지 단계와 MCP 서버는 SDK 없이
돌아간다.

설치형 배포 이름은 `cueprecise-mcp`이고 실행 명령은 `cueprecise`, `cueprecise-mcp`다.
아직 PyPI 릴리스 전이므로 위의 GitHub URL로 설치한다. `uv tool`의 격리 환경 안에 기존
모듈을 설치해 최상위 이름 충돌을 피하며 `python src/*.py` 실행 경로도 계속 지원한다.

## 테스트

```bash
python -m unittest discover -s tests
```

491개. stdlib `unittest`만 쓴다. 테스트 의존성이 없고 네트워크와 Gemini API를
호출하지 않는다. `google-genai`가 없으면 `test_transcribe`는 skip된다.

## API 사용량

로컬 원장이 API 키 해시별·Pacific 날짜별 시도 횟수를 센다. **원문 API 키는 저장하지
않는다.** 작업 전에 예상 호출 수를 보여준다. 한도를 넘으면 시작하기 전에 멈춘다.
로컬 수치는 추정치이며 AI Studio의 서버 수치가 최종 권위다.

---

## 로드맵

- [ ] **호스트별 타임스탬프 링크 통일** — Claude에서 확인된 재생 링크를 다른 MCP
  호스트에서도 같은 방식으로 제공한다.
- [ ] **청크 전사 파이프라이닝** — Gemini가 다음 청크를 처리하는 동안 이전 청크를 검사한다.
- [ ] **여러 영상 통합 분석** — 관련 영상들을 출처별 근거와 함께 검색하고 비교한다.

---

## 알려진 제한

- YouTube 자막의 표기 오류(`promots`, `retriever`)가 그대로 들어온다. 표기 정규화
  단계는 아직 없다.
- OCR은 `pytesseract`와 tesseract 바이너리가 있을 때만 동작한다. 없으면 프레임은
  시각으로만 조회된다.
- `merge.py`의 임계값은 영상 한 편에서 조정했다. 다른 영상 검증이 필요하다.
- 청크가 3개 이상일 때 앞 청크와 겹치지 않는 새 화자는 후보 라벨을 받지만
  `unresolved`로 남는다. 서로 떨어진 두 발화가 같은 사람의 말인지 확인하지 못할 수 있다.
- 실영상 검증은 23분(단일 청크)과 58분(3청크)까지 마쳤다. 실 API 경로의 중단·재개는
  아직이다.
- 프레임 후보를 찾는 화면 참조 표현은 한국어와 영어만 있다.

## 문서

- [Code signing policy](CODE_SIGNING_POLICY.md) — 공식 Windows 배포 파일의 빌드,
  검토 및 서명 정책.
- [`PRIVACY.md`](PRIVACY.md) — API 키와 로컬 데이터 보관, 외부 서비스 통신, 설치 및
  제거 시 변경사항.
- [`CONTRACT.md`](CONTRACT.md) — 데이터 계약. 단계 사이 JSON 구조와 검증 기준을
  정의한다. 인터페이스에 대해서는 이 파일이 유일한 진실이다.
- [`DECISIONS/`](DECISIONS/) — 설계 결정 기록. 무엇을 왜 그렇게 정했고 무엇을
  기각했는지 남아 있다.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — 개발 환경, 변경 원칙, PR 절차.
- [`SECURITY.md`](SECURITY.md) — 비공개 취약점 신고 절차.

## 라이선스

MIT. [`LICENSE`](LICENSE) 참고.

초기 전사 흐름을 설계할 때 MIT 라이선스의
[`gemini-transcribe-wrapper`](https://pypi.org/project/gemini-transcribe-wrapper/0.0.13/)
구현을 참고했다. CuePrecise는 별도로 작성된 프로젝트다.

CuePrecise는 YouTube 및 Google과 아무런 제휴 관계가 없고 두 회사의 공식 제품도
아니다. YouTube는 지원 대상 서비스일 뿐이다.
