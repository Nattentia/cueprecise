<p align="right"><strong>한국어</strong> | <a href="./README.md">English</a></p>

# CuePrecise

> **긴 YouTube 영상을 넣고, 몇 분 뒤부터 질문하세요.**

CuePrecise는 영상에서 사람들이 한 말을 먼저 읽습니다. 그중 필요한 부분을 찾고, 해당
시간의 화면을 가져와 말로 나온 내용과 화면에 나온 정보를 함께 AI에 전달합니다. 실제
촬영에서는 68분 영상이 약 3분 만에 질문할 준비를 마쳤으며, 영상과 사용 가능한 자막에
따라 시간은 달라집니다. 작업 뒤에는 AI가 다음 대화에서도 참고할 수 있는 자료가 내
컴퓨터에 남기 때문에 처음부터 다시 시작할 필요가 없습니다.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml/badge.svg)](https://github.com/Nattentia/cueprecise/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-432%20passing-brightgreen.svg)](#테스트)

CuePrecise(큐프리사이스)는 Claude Desktop·Codex 등 AI 앱에 연결하는 오픈소스 도구입니다.
YouTube 자막만 가져오는 것이 아니라 Gemini로 영상의 음성을 직접 전사합니다. 여기에
YouTube 자막을 맞춰 놓친 이름과 영어 기술 용어를 보완하고, 질문에 필요한 장면까지
함께 찾아줍니다.

### 이렇게 사용합니다

```text
나: 이 영상에서 self-supervised learning을 설명하는 부분을 찾아줘.

Claude + CuePrecise:
관련 구간을 타임스탬프와 함께 찾고,
그 시점의 정확한 대본과 화면 근거를 보여줍니다.
```

https://github.com/user-attachments/assets/20499d73-e24f-4a40-aa30-77e42a34a9ef

<sub>데모 원본: 고려대학교 Korea University의 [「ChatGPT와 함께하는 슬기로운 대학생활 - [1부] ChatGPT 너는 누구냐?」](https://www.youtube.com/watch?v=Y7iCGhXHJNQ)</sub>

| | |
|---|---|
| 🔎 **정확한 장면 찾기** | 질문과 관련된 구간을 타임스탬프로 반환합니다. |
| 🧩 **영어 용어 보존** | 한국어 발화 속 영어 기술 용어가 사라지는 문제를 줄입니다. |
| 💾 **대화가 끝나도 유지** | 분석 결과가 내 컴퓨터에 남아 다음 대화에서도 검색됩니다. |
| 🔐 **운영자 서버 없음** | 계정·광고·추적 없이 사용자의 컴퓨터에서 동작합니다. |

### Windows에서 시작하기

[**Windows 설치 파일 내려받기 →**](https://github.com/Nattentia/cueprecise/releases)

1. `cueprecise-setup.exe`를 실행합니다.
2. 안내 화면에서 **API 키 만들기**를 누르고, Google에서 만든 키를 붙여넣습니다.
3. 컴퓨터에서 발견된 AI 앱을 선택하고 **연결하기**를 누릅니다.
4. 연결이 끝나면 선택한 AI 앱을 다시 시작합니다.

Python이나 Git을 설치할 필요가 없습니다. 현재 공개 파일은 SignPath 심사용 미서명
시험판이므로 Windows 경고가 나타날 수 있습니다. 서명 완료 전에는 반드시 이 저장소의
Releases에서 받은 파일만 사용하세요.

**[자세한 설치 방법](#빠른-시작)** · **[지원하는 도구](#도구)** ·
**[작동 원리](#동작-방식)** · **[알려진 제한](#알려진-제한)**

---

## 무엇을 해결하나

한국어 강의·대담을 전사하면 둘 중 하나를 포기하게 된다.

**❌ Gemini 전사만 쓰면** — 한국어 문장은 매끄럽지만 코드스위칭된 영어 용어를 버린다.

```
그러면 어떻게 이 능력을 학습을 했을까요?  라는 방식으로 학습을 합니다.
                                       ↑ 영어 용어가 통째로 사라졌다
```

**❌ YouTube 자동자막만 쓰면** — 영어는 3배 더 살아남지만 한국어가 거칠고,
문장·화자·검색이 없다.

**✅ CuePrecise는 둘을 합친다.**

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

Gemini는 4회 실행에서 모두 그 용어를 놓쳤다. CuePrecise는 시간 공백과 한국어 조사 잔존을
**함께** 근거로 삼아 그 구간에만 영어를 끼워 넣는다. Gemini 원본 단어는 지우거나
고쳐 쓰지 않는다.

---

## 빠른 시작

### Windows — 설치 파일로 시작하기

1. [Releases](https://github.com/Nattentia/cueprecise/releases)에서 `cueprecise-setup.exe`를 내려받아 실행한다.
2. 설치가 끝나면 자동으로 열리는 화면에서 **API 키 만들기**를 누른다.
3. Google AI Studio에서 만든 키를 복사해 붙여넣는다.
4. 컴퓨터에서 발견된 AI 앱을 선택하고 **연결하기**를 누른다.
5. “연결 완료”가 보이면 선택한 AI 앱을 완전히 껐다가 다시 켠다.

Python이나 Git을 설치하거나 명령어를 입력할 필요가 없다. 필요한 영상 처리 도구도
연결 과정에서 자동으로 준비한다. API 키는 선택한 AI 앱의 내 컴퓨터 설정에만 저장되며,
기존 설정은 백업한 뒤 CuePrecise 항목만 추가한다. 이미 연결해 둔 적이 있으면
API 키를 포함한 설정을 그대로 물려받는다.

> `v0.2.0`은 SignPath Foundation 코드 서명 심사를 위한 시험판이다. 아직 디지털
> 서명이 없어 Windows에서 "알 수 없는 게시자" 경고가 나타날 수 있다. 반드시 이
> 저장소의 Releases에서 받은 파일만 사용한다.

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

[API 키](https://aistudio.google.com/api-keys)를 먼저 환경변수로 넣으면 `setup`이 MCP
설정에 함께 등록한다. 키 없이 설치해도 조회 도구는 동작하며 나중에 다시 실행할 수 있다.

```bash
export GEMINI_API_KEY="..."          # Windows PowerShell: $env:GEMINI_API_KEY="..."
cueprecise setup
cueprecise run "https://www.youtube.com/watch?v=VIDEO_ID" --language ko-KR
cueprecise status VIDEO_ID
```

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
`server/discover`로 물으면 지원 판을 알리고, `initialize`로 걸어오면 악수로 받는다.

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
남의 설정을 덮어쓰지 않으며, 한 앱이 실패해도 나머지는 계속 붙인다.

**ChatGPT 커넥터와 Claude.ai 웹에는 붙지 않는다.** 둘은 HTTPS 주소와 OAuth로 접속하는
원격 MCP만 받는다. CuePrecise는 이 PC에서 도는 로컬 서버라 그 자리에 넣을 수 없다.

다음 JSON은 소스 clone을 쓰거나 위 목록에 없는 MCP 호스트에 손으로 붙일 때만 필요하다.

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

`GEMINI_API_KEY`가 없어도 서버는 뜬다. 조회 도구는 그대로 돌고, 전사가 필요한
도구만 그 사실을 알리며 멈춘다.

### 도구

| 도구 | 하는 일 | Gemini 호출 |
|---|---|---|
| `cueprecise_register` | 영상 등록·분석. 단계 선택 가능 | 청크당 1회 |
| `cueprecise_status` | 진행도, 산출물, 사용량 추정 | 없음 |
| `cueprecise_outline` | 개요와 timestamp 목차, 복원 용어, 화자 상태 | 없음 |
| `cueprecise_query` | 내용 질의. 근거 span·frame을 timestamp와 함께 반환 | 없음 |
| `cueprecise_excerpt` | 특정 시각 구간의 자막과 프레임 | 없음 |
| `cueprecise_frames` | 화면 참조 시각의 프레임 추출. 영상이 없으면 그때 받는다 | 없음 |
| `cueprecise_summary` | 요청할 때만 요약 생성·조회 | 없음 |
| `cueprecise_set_summary` | 호스트가 근거로 개선한 요약을 검증·저장 | 없음 |
| `cueprecise_set_chapter_titles` | 호스트가 지은 챕터 제목을 검증·저장 | 없음 |
| `cueprecise_purge` | derived 재생성 및 명시적 삭제 | 없음 |

전사를 뺀 모든 단계는 로컬에서 돈다. 챕터 제목과 요약은 호스트 LLM이 근거를 보고
직접 쓰며, 그것도 별도 호출을 만들지 않는다.

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

단계는 JSON 파일로만 이어진다. 각각 독립적으로 재실행할 수 있고, 완료된 청크는
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
오디오는 `purge --scope chunks`로 지울 수 있고, 원본이 남아 있으면 필요할 때 다시 뽑는다.

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
모듈을 설치해 최상위 이름 충돌을 피하며, `python src/*.py` 실행 경로도 계속 지원한다.

## 테스트

```bash
python -m unittest discover -s tests
```

432개. stdlib `unittest`만 쓴다. 테스트 의존성이 없고 네트워크와 Gemini API를
호출하지 않는다. `google-genai`가 없으면 `test_transcribe`는 skip된다.

## API 사용량

로컬 원장이 API 키 해시별·Pacific 날짜별 시도 횟수를 센다. **원문 API 키는 저장하지
않는다.** 작업 전에 예상 호출 수를 보여주고, 한도를 넘으면 시작하기 전에 멈춘다.
로컬 수치는 추정치이며 AI Studio의 서버 수치가 최종 권위다.

---

## 로드맵

- [ ] **클릭 가능한 타임스탬프** — 원본 YouTube 영상의 정확한 시점으로 바로 이동한다.
- [ ] **청크 전사 파이프라이닝** — Gemini가 다음 청크를 처리하는 동안 이전 청크를 검사한다.
- [ ] **여러 영상 통합 분석** — 관련 영상들을 출처별 근거와 함께 검색하고 비교한다.

---

## 알려진 제한

- YouTube 자막의 표기 오류(`promots`, `retriever`)가 그대로 들어온다. 표기 정규화
  단계는 아직 없다.
- OCR은 `pytesseract`와 tesseract 바이너리가 있을 때만 동작한다. 없으면 프레임은
  시각으로만 조회된다.
- `merge.py`의 임계값은 영상 한 편에서 조정했다. 다른 영상 검증이 필요하다.
- 청크가 3개 이상일 때, 앞 청크와 겹치지 않는 새 화자는 global 라벨을 받지 못하고
  `unresolved`로 남는다. 틀린 이름을 붙이지는 않지만 같은 사람인지 알 수 없다.
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

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

CuePrecise는 YouTube 및 Google과 아무런 제휴 관계가 없고 두 회사의 공식 제품도
아니다. YouTube는 지원 대상 서비스일 뿐이다.
