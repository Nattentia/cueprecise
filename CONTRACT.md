# CONTRACT

에이전트는 이 파일을 수정하지 않는다. 변경은 사람만 한다.
변경이 필요하면 `DECISIONS/<자기이름>.md`에 근거를 적고 사람의 판단을 기다린다.

**2026-08-30 개정.** codex 가 토큰 한도로 중단됐다. 프로젝트 소유자가 claude
에게 전권을 위임했고, 이 파일의 개정 권한도 함께 위임했다. 아래 소유권 표를
그에 맞춰 고친다. codex 가 복귀하면 소유자가 다시 나눈다.

## 1. 파일 소유권

| 파일 | 주인 | 다른 쪽 |
|---|---|---|
| `src/fetch_youtube.py` | claude | 읽기만 |
| `src/render.py` | claude | 읽기만 |
| `src/transcribe.py` | claude | 읽기만 |
| `src/merge.py` | claude | 읽기만 |
| `src/audio.py` | claude | 읽기만 |
| `src/usage.py` | claude | 읽기만 |
| `src/speakers.py` | claude | 읽기만 |
| `CONTRACT.md` | 사람 | 읽기만 |
| `DECISIONS/codex.md` | codex | 읽기만 (codex 복귀 전까지 동결) |
| `DECISIONS/claude.md` | claude | 읽기만 |
| `tests/fixtures/**` | 사람 | 읽기만 |
| `tests/test_transcribe.py`, `tests/test_merge.py`, `tests/test_audio.py`, `tests/test_usage.py`, `tests/test_speakers.py` | claude | 읽기만 |
| `src/pipeline.py` | claude | 읽기만 |
| `src/context.py` | claude | 읽기만 |
| `src/visual.py` | claude | 읽기만 |
| `src/mcp_server.py` | claude | 읽기만 |
| `tests/test_fetch_youtube.py`, `tests/test_render.py`, `tests/test_pipeline.py`, `tests/test_context.py`, `tests/test_visual.py` | claude | 읽기만 |

남의 파일은 고치지 않는다. 문제를 발견하면 자기 `DECISIONS` 파일에 적는다.

## 2. 데이터 계약

모든 단계는 JSON 파일로만 이어진다. 함수 시그니처를 공유하지 않는다.
시각 단위는 **초(float)**, 영상 시작 기준 절대값.

### captions.json — `fetch_youtube.py` 출력

```json
{
  "source": "youtube-ko-orig",
  "language": "ko-orig",
  "original": true,
  "video_id": "jcBDSLSeud4",
  "cues": [
    {"start": 207.68, "end": 209.42, "text": "했을까요? self"}
  ]
}
```

- `cues`는 `start` 오름차순.
- 롤링 중복이 제거된 상태여야 한다.
- 각 cue의 `start`는 **그 텍스트가 처음 등장한 블록의 start**를 쓴다.
- 자막 언어를 고정하지 않는다. 원어 자동자막(`.*-orig`)을 받으므로 한국어
  영상은 `ko-orig`, 영어 영상은 `en-orig`가 온다.
- `original`은 그 트랙이 **영상의 원어**인지를 뜻한다. `-orig`만 참이다.
  YouTube는 자동자막을 임의 언어로 기계 번역해 주므로, 영어 영상에 `ko`를
  요청하면 한국어 번역 자막이 내려온다. 번역된 트랙을 "원문이 무슨 언어인가"의
  근거로 쓰면 멀쩡한 전사를 번역문으로 오판한다.
- 원어 트랙이 없어 일반 자막으로 폴백할 때는 자동자막을 요청하지 않는다.
  사람이 올린 자막만 받고 `original`은 거짓이다.
- 여러 트랙이 받아지면 원어 트랙, 그다음 요청한 언어 순서로 고른다.
  파일 이름 순서로 고르면 번역 트랙이 뽑혀 `merge`가 없는 근거를 만들어낸다.

### transcript.json — `transcribe.py` 출력

```json
{
  "source": "gemini",
  "model": "gemini-3.5-transcribe",
  "language_codes": ["ko-KR"],
  "video_id": "jcBDSLSeud4",
  "words": [
    {"text": "했을까요?", "start": 207.6, "end": 208.1, "speaker": "spk:0"}
  ]
}
```

- `language_codes`가 `null`이면 자동 감지로 호출했다는 뜻.
- `speaker`는 `null` 가능.

### merged.json — `merge.py` 출력

`transcript.json`과 동일한 `words` 구조에 `origin` 필드를 더한다.

```json
{
  "source": "merged",
  "words": [
    {"text": "self", "start": 208.3, "end": 208.6,
     "speaker": "spk:0", "origin": "youtube"}
  ]
}
```

- `origin`은 `"gemini"` 또는 `"youtube"`.
- 기본 골격은 gemini. youtube는 빈 구간을 메우는 용도로만 들어간다.

## 3. render.py 입력 규약

`words` 배열을 가진 JSON이면 무엇이든 받는다. `transcript.json`과
`merged.json` 둘 다 동일하게 처리된다. `origin` 필드는 무시해도 된다.

출력 규칙:

- 큐 분할 조건 (하나라도 걸리면 자른다)
  - 화자 변경
  - 앞 단어 `end`와 다음 단어 `start` 간격 > 0.65초
  - 큐 길이 > 7.0초
  - 줄바꿈했을 때 최대 줄 수 초과
- 줄 폭: 인자로 받는다. 한국어 20, 영어 42.
- 최대 2줄.
- **넘치는 텍스트는 다음 큐로 넘긴다. 잘라 버리지 않는다.**

마지막 항목이 핵심이다. 기존 `gemini-transcribe-wrapper` 0.0.13은
`format.py:108`에서 `return lines[:MAX_LINES_PER_CUE]`로 초과분을 버려
23분 영상 텍스트의 97%를 소실시킨다. 같은 실수를 반복하지 않는다.

## 4. 검증 기준

`tests/fixtures/`의 샘플로 확인한다. 영상 `jcBDSLSeud4` 기준:

| 항목 | 기대값 |
|---|---|
| YouTube srt 원본 자막 줄 | 1800 |
| 롤링 중복 제거 후 | 603 |
| 제거 후 단어 수 | 2752 |
| 반복 길이 3회인 구간 | 597건 |
| Gemini 단어 수 | 2856 ~ 2898 |
| 렌더 결과 단어 보존율 | 100% (소실 0) |
| 정상 전사의 한글 비율 | 99% 이상 |

### 원본 오디오 음질을 낮추지 않는다

`AUDIO_FORMAT`은 최고 음질을 받는다. 청크를 16kHz 모노 64k로 다시 인코딩하므로
원본 음질이 무관해 보이지만, 실측 결과 **원본 음질이 모델의 응답 안정성 자체를
바꾼다.** `jcBDSLSeud4` A/B (2026-08-31):

| 음질 | 실행 | 한국어 전사 성공 |
|---|---|---|
| opus 103k (16.8MB) | 3회 | 3/3 |
| opus 65k (10.95MB) | 1회 | 0/1 — 영어 번역문 |
| opus 46k (7.76MB) | 3회 | 0/3 — 번역문 1, 빈 응답 2 |

절감액(58분 기준 42.6MB → 20.3MB)이 커도 채택하지 않는다. 전사가 실패하면
남는 것이 없다.

### 번역문을 결과로 받아들이지 않는다

Gemini는 조건에 따라 받아적기 대신 번역문을 돌려준다. 번역문은 단어 수도 많고
문장도 자연스러워 사람 눈에 정상으로 보이지만 **원문이 아니므로 근거로 쓸 수
없다.** `transcribe` 단계는 청크마다 이것을 판정하고, 걸리면 그 자리에서
멈춘다 — 남은 청크의 호출을 아낀다.

판정은 이미 받아둔 자료로만 한다. 추가 호출을 쓰지 않는다.

- 같은 시간대의 **원어 자막**(`original: true`)이 한글 30% 이상인데 전사가
  한글 5% 미만이면 번역문으로 본다.
- 자막이 없어도 `ko*`를 요청했는데 전사가 한글 5% 미만이면 번역문으로 본다.
- 대조는 **청크의 시간 범위와 겹치는 자막**으로 한다. 영상 전체 자막과
  대조하면 한국어 강의 중간의 영어 발표 구간이 통째로 걸린다.
- 임계값은 영어 용어가 섞인 한국어 강의를 오탐하지 않게 잡는다. 정상 전사는
  한글 99%대, 번역문 사례는 0%였다.

멈춘 뒤의 복구는 `--language`로 원어를 지정한 재실행이다. **언어를 바꾸면
config가 달라져 그 영상의 청크를 전부 다시 부른다.** 언어가 뒤섞인 결과나
어긋난 타임스탬프를 조용히 만드는 것보다 낫다는 판단이다. 설정을 바꾸지 않은
재실행은 저장된 응답을 다시 읽어 같은 판정을 내며 호출을 쓰지 않는다.

## 5. 협업 규약

원격: `https://github.com/Nattentia/ytx` (private)

### 작업 공간 분리

같은 클론에서 둘이 동시에 작업하면 브랜치를 나눠도 워킹 트리에서
서로 덮어쓴다. 각자 자기 클론을 쓴다.

| 에이전트 | 디렉터리 |
|---|---|
| claude | `C:\dev\ytx` |
| codex | `C:\dev\ytx-codex` |

codex 최초 설정:

```
git clone https://github.com/Nattentia/ytx.git C:\dev\ytx-codex
```

### 브랜치

- `main` 에 직접 커밋하지 않는다.
- 자기 접두사 브랜치를 판다: `claude/<주제>`, `codex/<주제>`
- 푸시 후 PR 을 연다. 사람이 검토하고 머지한다.

```
git switch -c codex/fetch-youtube
git add src/fetch_youtube.py
git commit -m "feat: youtube ko-orig 자막 취득 및 롤링 중복 제거"
git push -u origin codex/fetch-youtube
gh pr create --fill
```

### 커밋

- 자기 소유 파일만 담는다.
- 한 커밋에 한 가지 변경.
- 작업을 마치면 `DECISIONS/<자기이름>.md`에 append: 날짜 / 무엇을 / 왜.

### 상대 작업 확인

머지된 결과는 `git pull` 로 받는다. 진행 중인 것은 PR 에서 본다.

```
git pull origin main
gh pr list
```

## 6. 확장 호환성 규칙

이 절 이후는 장기 영상과 영속 컨텍스트를 위한 **추가 계약**이다. 2~4절의
기존 JSON 필드와 단일 파일 실행 경로는 그대로 유지한다.

- 기존 필드를 삭제하거나 의미를 바꾸지 않는다. 새 필드는 optional로 추가한다.
- 모든 시간은 영상 시작 기준 절대 초(float)다.
- raw 산출물은 수정하지 않는다. 보정·병합·요약은 derived 산출물로 쓴다.
- 각 단계는 같은 입력 fingerprint와 설정이면 재실행해도 같은 checkpoint를
  안전하게 재사용해야 한다.
- 부분 실패를 성공으로 숨기지 않는다. 완료된 청크와 실패 원인을 manifest에 남긴다.
- 새 writer가 만든 파일은 구 reader가 모르는 필드를 무시해도 기존 기능이 동작해야 한다.
- 호환 불가능한 변경은 `schema_version`을 올리고 별도 migration을 제공하기 전에는 금지한다.

## 7. job.json — 장기 작업 manifest

`audio.py`가 계획을 만들고 `pipeline.py`가 상태를 갱신한다.

```json
{
  "schema_version": 1,
  "video_id": "jcBDSLSeud4",
  "input": {
    "source": "https://www.youtube.com/watch?v=jcBDSLSeud4",
    "fingerprint": "sha256:..."
  },
  "config": {
    "chunk_max_secs": 1790.0,
    "overlap_secs": 10.0,
    "language_codes": null,
    "diarization": true
  },
  "status": "partial",
  "chunks": [
    {
      "index": 0,
      "start": 0.0,
      "end": 1410.0,
      "path": "raw/audio/chunk-000.mp3",
      "status": "complete",
      "attempts": 1,
      "transcript_path": "raw/transcripts/chunk-000.json",
      "error": null
    }
  ]
}
```

- `status`: `planned | running | partial | complete | failed`.
- 청크 수는 `ceil(total_secs / chunk_max_secs)`로 정하고 전체 길이에 균등 분배한다.
- 각 청크는 검증 후에도 1790초를 넘으면 안 된다.
- 첫 청크 외에는 앞 청크와 `overlap_secs`만큼 겹칠 수 있다.
- `path`와 `transcript_path`는 knowledge bundle 루트 기준 상대 경로다.
- 실제 API 요청 직전에 `attempts`를 증가시킨다.
- 완료 checkpoint가 fingerprint/config와 일치하면 API를 다시 호출하지 않는다.

## 8. transcript 확장 — 청크와 화자

2절의 `transcript.json`/`merged.json` word 구조에 아래 필드를 optional로 추가한다.

```json
{
  "text": "안녕하세요",
  "start": 0.9,
  "end": 1.6,
  "speaker": "spk:0",
  "speaker_raw": "spk:0",
  "speaker_global": "speaker:0",
  "speaker_status": "inferred",
  "speaker_evidence": "overlap"
}
```

- `speaker`는 기존 reader 호환을 위해 유지하며, global이 확정되면 그 표시값을 쓴다.
- `speaker_raw`는 API가 해당 호출에서 준 값을 절대 덮어쓰지 않는다.
- `speaker_status`: `confirmed | inferred | unresolved`.
- `speaker_evidence`: `overlap | source-name | voice-embedding | manual | null`.
- 청크 transcript 최상위에는 `chunk_index`, `chunk_start`, `chunk_end`를 optional로 둔다.
- 저장되는 word timestamp는 언제나 절대 시각이다.
- overlap 병합은 원문과 raw speaker를 보존하고, 중복 제거 내역을 derived metadata에 남긴다.
- 근거가 약하면 화자를 임의 확정하지 않고 `unresolved`로 둔다.

## 9. 로컬 Gemini 사용량 원장

`usage.py`가 API 키 hash별·Pacific 날짜별 요청 시도 횟수를 로컬에 저장한다.
원문 API 키는 절대 저장하지 않는다.

작업 전과 실제 요청 후 다음을 표시한다.

```text
오늘 로컬 기록: 7회
이번 작업 예상: 3회
작업 후 예상: 10회
설정된 일일 한도: 25회
예상 잔여: 15회
초기화: Pacific midnight
정확도: local estimate
```

- 일일/RPM 한도는 설정값이며 모델 한도로 하드코딩하지 않는다.
- 카운터는 실제 요청 직전에 증가한다. API 실패와 429도 시도 횟수에 포함한다.
- checkpoint 재사용, render, merge, YouTube 작업은 Gemini 호출로 세지 않는다.
- 원장은 file lock과 atomic replace로 갱신한다.
- 예상 잔여를 넘으면 무료 모드에서는 batch 시작 전에 중단한다.
- 429를 받으면 새 청크 호출을 중단하고 manifest에 원인을 기록한다.
- 로컬 수치는 추정치이며 AI Studio의 서버 수치가 최종 권위다.

## 10. knowledge bundle과 검색 계약

영상 하나의 영속 자료는 다음 구조를 기본으로 한다.

```text
data/<video_id>/
  job.json
  raw/
    captions.json
    audio/
    transcripts/
    frames/
  derived/
    transcript.json
    merged.json
    chapters.json
    frames.json
    output.srt        선택 (render)
    output.txt        선택 (render)
    summary.md        선택 (요청 시 생성)
  index.sqlite3
```

- 위 구조는 **가능한 산출물의 전체 목록**이다. "선택"으로 표시한 것은 기본
  실행에서 만들지 않고, 요청이 있을 때 해당 단계만 돌려 만든다. 기능과 검증
  기준은 그대로다 — SRT/TXT의 단어 보존율 100%는 언제 만들든 지켜야 한다.
- 기본 실행은 **요약과 후속 원문 검색에 필요한 자료를 모두 만든다.** 그 판단
  기준은 "이 파일을 읽는 코드가 있는가"다. `output.srt`/`output.txt`를 읽는
  코드는 없다. 근거 검색은 `merged.json`과 `index.sqlite3`가 한다.

- `context.py`는 SQLite에 transcript span, chapter, entity/term, speaker, frame을 색인한다.
- 각 검색 결과는 최소 `video_id`, `start`, `end`, `text`, `source_path`,
  `source_kind`, `confidence`를 반환한다.
- 답변은 검색된 transcript/frame 근거와 timestamp를 포함한다.
- 새 대화에서는 대화 기록이 아니라 `video_id`의 bundle과 index에서 복원한다.
- 근거가 없거나 약하면 추측하지 않고 evidence 부족을 반환한다.
- 입력/config fingerprint가 달라지면 잘못된 cache를 재사용하지 않는다.
- raw 삭제는 명시적 별도 명령으로만 가능하다. derived는 raw에서 재생성 가능해야 한다.

## 11. frames.json — 시각 자료 색인

```json
{
  "schema_version": 1,
  "video_id": "jcBDSLSeud4",
  "frames": [
    {
      "timestamp": 208.0,
      "path": "raw/frames/000208000.jpg",
      "reason": "screen-reference",
      "ocr_text": "self supervised learning",
      "confidence": 0.82
    }
  ]
}
```

- 균일 전체 프레임 추출을 기본값으로 하지 않는다.
- transcript의 화면 참조, 코드/표/도식 후보, 사용자 요청 시각을 우선한다.
- OCR 텍스트는 transcript를 조용히 덮어쓰지 않고 독립 provenance로 저장한다.

## 12. pipeline과 MCP 완료 조건

`pipeline.py`는 각 단계를 독립적으로 재실행할 수 있게 조정하고 JSON 파일로만
연결한다. `mcp_server.py`는 최소 다음 동작을 제공한다.

- 영상 등록/분석 시작
- 작업 상태와 로컬 Gemini 사용량 추정 조회
- 영상 개요와 timestamp 목차 조회
- 내용 질의 및 근거 span/frame 반환
- 특정 내용/시각의 자막과 프레임 조회
- derived 재생성 및 명시적 영상 자료 삭제

최종 acceptance:

- 30분 이하와 초과 영상 모두 처리한다.
- 청크 실패 후 완료 청크를 재호출하지 않고 재개한다.
- 영어 용어 provenance와 화자 raw/global/unresolved를 확인할 수 있다.
- SRT/TXT 단어 보존율 100%다. 기본 실행이 그 파일을 만들지 않아도 `render`를
  돌리면 보존율은 지켜져야 한다.
- 프로세스와 대화가 끝난 뒤에도 bundle+SQLite로 후속 질의가 가능하다.
- 필수 답변에는 source와 timestamp가 있다.
- 전사가 번역문이면 성공으로 처리하지 않는다 (4절).

기본 실행과 선택 단계:

- 단계 목록은 "실행할 수 있는 단계"(`STAGES`)와 "생략하면 도는
  단계"(`DEFAULT_STAGES`)를 구분한다. 둘을 한 상수로 겸하면 기본을 바꿀 때마다
  호출부마다 예외가 생긴다.
- 명시적 단계 선택은 기본 목록이 아니라 항상 전체 목록에서 검증한다. 기본에서
  빠진 단계도 이름을 대면 돌아야 한다.
- `status`는 선택 산출물의 부재를 실패로 보이게 하지 않는다.
- 사용자가 영상 취득을 껐으면(`--skip-video`) 뒤의 단계가 그것을 되돌려
  받아오지 않는다. 프레임이 필요한데 영상이 없으면 그때 받아오되, 끄라는
  지시가 있었으면 받지 않는다.
- 네트워크를 쓰기 전에 그 결과를 쓸 수 있는지 먼저 확인한다. 전사가 없는
  bundle에서 프레임을 요청하면 영상을 받기 전에 실패해야 한다.
