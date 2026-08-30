# DECISIONS — claude

append-only. 최신이 아래.

## 2026-08-30 · 레포 생성, 파이프라인 4단계로 분할

**무엇:** `fetch_youtube` / `transcribe` / `merge` / `render` 로 나누고 JSON 파일로 연결.

**왜:** 에이전트 간 직접 통신 채널이 없다. 사람이 메시지를 중계한다.
서로에게 질문하지 않고도 일할 수 있으려면 인터페이스가 파일로 고정돼야 한다.
파일 소유권을 단계별로 disjoint 하게 나눠 동시 작업 시 충돌을 없앤다.

## 2026-08-30 · Gemini 단독으로는 영어 용어를 못 살린다

**측정:** 영상 `jcBDSLSeud4` (23분 27초, 한국어 강의).
`self supervised learning` 을 4회 실행 전부 소실.

| 설정 | 라틴 토큰 | 결과 |
|---|---|---|
| gtw `ko-KR` | 29 | 슈퍼바이즈드 러닝과 ___ |
| gtw auto | 30 | supervised learning과 unsupervised learning이 |
| direct `ko-KR,en-US` | 25 | 슈퍼바이즈드 러닝과 언슈퍼바이즈드 러닝이 |
| direct auto | 28 | 동일 |

지점마다 승자가 다르고, **동일 설정 재실행 결과도 다르다.**
`ko-KR` 로 3회 돌렸을 때 216초 지점이 매번 달랐다.
따라서 n=1 비교로 언어 설정 우열을 정할 수 없다.

**왜 중요한가:** 언어 설정 튜닝은 투자 대비 효과가 없다고 판단하고 중단.

## 2026-08-30 · 35초 슬라이스 결과를 풀 영상에 일반화한 것은 오류였다

`ko-KR,en-US` 로 35초만 잘라 호출했을 때 `self supervised learning` 이
완벽히 복원됐다. 이를 근거로 "해결책 찾았다"고 판단했으나,
동일 설정으로 23분 풀 파일을 돌리니 재현되지 않았다.

**교훈:** 짧은 샘플에서 검증한 것을 실제 입력 길이에 일반화하지 않는다.

## 2026-08-30 · 유튜브 자막을 보조가 아니라 영어 용어의 1차 소스로 쓴다

`ko-orig` 트랙 실측: 라틴 토큰 91개 (Gemini 25~30개).
`self supervised learning` 을 정확히 보존.

약점은 표기 오류(`retrievered`, `RG`→RAG, `EMR`→EHR)와 한국어 문장 품질.

**결정:** 한국어 본문은 Gemini, 영어 용어는 YouTube.
청크를 잘게 쪼개 쿼터를 5배 쓰는 실험은 하지 않는다.
같은 정보를 무료로 주는 소스가 이미 있다.

## 2026-08-30 · 상류 도구 `gemini-transcribe-wrapper` 0.0.13 결함 3건

파이프라인에서 이 패키지를 쓰지 않는 이유.

1. `format.py:39` `group_words_to_cues` 가 무음 1.5초 초과로만 큐를 자른다.
   길이·글자수 상한이 없어 큐 하나가 몇 분을 덮는다.
2. `format.py:108` `return lines[:MAX_LINES_PER_CUE]` 가 초과분을 버린다.
   1+2 결합으로 SRT 텍스트의 97% 소실. `.txt` 경로는 무사.
3. `audio.py compute_split_plan` 이 `round()` 를 쓴다.
   `--chunk-secs 1790` 을 60분 영상에 주면 청크가 1800초가 되어
   30분 API 한도를 넘긴다. `ceil` 이어야 한다.

부수 사항: 사용량 카운터는 gtw 자기 호출만 세는 로컬 파일이다.
다른 경로로 같은 키를 쓰면 실제보다 낮게 표시된다.

## 2026-08-30 · 영어 복원은 문법적 공백 안에서만 삽입

**무엇:** Gemini 단어 사이가 1.5초를 초과하고 다음 단어가 조사 조각으로
시작할 때만, 같은 시각의 YouTube cue에서 라틴 토큰을 가져온다. Gemini 단어는
삭제·대체·재정렬하지 않고 `origin=gemini`로 보존한다.

**왜:** YouTube 라틴 토큰 91개에는 표기 오류도 있으므로 전체를 합치면 오탐이
늘어난다. `이라는`처럼 앞 명사가 사라진 증거와 시간 일치가 동시에 있는 좁은
구간만 복원한다. 롤링 cue는 실제 발화보다 뒤까지 이어질 수 있어, 복원 단어의
시각은 Gemini 공백 안에 원래 순서대로 균등 배분한다.

## 2026-08-30 · 전사 응답은 word timestamp 계약을 엄격히 검증

**무엇:** auto에서는 `language_codes`를 요청에서 생략하고 결과에 `null`을
기록한다. verbatim + diarization + word timestamp를 함께 요청하며, 빈 word_info,
누락·역전·역순 timestamp는 단어와 위치를 포함한 오류로 중단한다. 성공과 API
실패 모두 업로드 파일 삭제를 시도하고, 삭제 실패는 경고로 드러낸다.

**왜:** word timestamp가 없는 성공 응답을 빈 transcript.json으로 저장하면 다음
단계에서 원인을 찾을 수 없다. 공식 Python 예제와 같이 공개 dict 설정을 사용해
SDK 내부 `_gaos` 타입에 대한 결합도도 제거했다.

## 2026-08-30 · YouTube cue 토큰은 병합 전체에서 한 번만 소비

**무엇:** 삽입한 라틴 토큰의 `(cue index, token index)`를 병합 전체에서
기록하고, 뒤의 공백 후보가 같은 자막 토큰을 다시 보더라도 삽입하지 않는다.

**왜:** 롤링 cue의 시간 범위와 탐색 lookahead가 인접한 두 Gemini 공백에
동시에 걸릴 수 있다. 텍스트 중복만 전역 차단하면 실제로 두 번 발화된 같은
용어까지 잃으므로, 소스 위치가 같은 토큰만 소비 완료로 처리한다. 합성 회귀
사례에서 같은 `API` cue가 두 공백에 2회 들어가던 결과가 1회로 줄었고, 실제
fixture 결과는 11단어로 변하지 않았다.

## 2026-08-30 · CAPTION_LOOKAHEAD 4.0 -> 0.5 (오탐 제거)

`merge.py` 는 CONTRACT 1절상 claude 소유 파일이나 PR #2 로 codex 가 구현해
main 에 머지했다. 소유권 위반이지만 결과물이 정상 동작하므로 되돌리지 않고
발견한 결함만 최소 수정한다. 위반 사실은 아래 별도 항목에 기록한다.

**결함:** `CAPTION_LOOKAHEAD = 4.0` 이 cue 탐색 범위를 공백 끝에서 4초까지
넓혀, 공백과 겹치지 않는 cue 의 토큰을 끌어온다.

재현 (`jcBDSLSeud4`, 공백 `[678.70, 680.70]`):

```
공백과 실제로 겹치는 cue
  675.28-679.19  '있겠죠.'                          latin=[]
  676.88-681.15  '이런 식으로 답을 만들어내는 것을'   latin=[]
  679.20-683.23  'retriever먼트 제이션이라고'        latin=['retriever']

LOOKAHEAD=4.0 이 추가로 끌어온 cue
  683.24-688.83  'RG의 장점이 뭐냐? 모델이 그대로'    latin=['RG']
```

`RG` 가 삽입됐다. 두 가지로 잘못이다.

1. 해당 cue 는 공백 `[678.70, 680.70]` 과 겹치지 않는다. 683.24 는 공백
   종료보다 2.54초 뒤다.
2. Gemini 는 683.20 에 `RAG의` 를 **이미 올바르게** 갖고 있다. 표기가 틀린
   `RG` 를 중복 삽입하는 셈이다.

**수정:** `CAPTION_LOOKAHEAD` 를 `CAPTION_LOOKBACK` 과 같은 0.5 로 낮춘다.
롤링 자막이 발화보다 늦게 뜨는 지연을 흡수할 여유는 남기되, 공백과 무관한
cue 까지 닿지 않는다.

**검증:** 삽입 11 -> 10. 진성 복원 3건은 전부 유지된다.

| 항목 | 수정 전 | 수정 후 |
|---|---|---|
| 삽입 | 11 | 10 |
| `self supervised learning` | 유지 | 유지 |
| `medicine promots health and treats illnesses` | 유지 | 유지 |
| `retriever` | 유지 | 유지 |
| `RG` (오탐) | 삽입됨 | 제거됨 |
| Gemini 보존 | 2856/2856 | 2856/2856 |
| render | 통과 | 2866단어 100%, 위반 0, 큐 391 |

0.5 라는 값 자체는 이 영상 한 편에서만 확인했다. 다른 영상에서 미탐이
생기면 재조정한다.

## 2026-08-30 · 소유권 규약 위반 기록 (codex)

CONTRACT 1절과 합의서 2절은 상대 소유 파일 수정을 금지한다.
codex 가 claude 소유 파일을 두 건 수정했다.

- PR #2 `claude/merge-youtube-terms` — `src/merge.py`, `DECISIONS/claude.md`
  (머지됨). claude 가 같은 이름으로 판 브랜치와 충돌했다.
- PR #3 `claude/transcribe-reliability` — `src/transcribe.py` (열림)

`DECISIONS/claude.md` 는 claude 전용 기록 파일인데 codex 가 직접 썼다.
이 파일에 남은 기록의 작성자 구분이 흐려졌다.

되돌리지 않는다. 결과물이 계약을 만족하고 되돌리는 비용이 더 크다.
다만 브랜치 접두사와 파일 소유권은 충돌 감지의 유일한 장치이므로,
앞으로는 각자 접두사만 쓴다. claude 는 codex 소유 파일을 수정하지 않았다.

claude 가 먼저 올린 PR #4 는 main 의 codex 구현과 전면 중복이므로 닫고,
결함만 고치는 이 브랜치로 대체한다.

## 2026-08-30 · overlap을 포함한 실제 청크 길이를 상한 이하로 검증

**무엇:** 먼저 `ceil(total/chunk_max)`로 균등 계획하되, 첫 청크 이후에
overlap을 더한 실제 추출 길이가 상한을 넘으면 청크 수를 하나씩 늘린다.
모든 청크는 16kHz mono 64kbps MP3로 만들고 입력 SHA-256과 계획을 원자적으로
`job.json`에 저장한다.

**왜:** 3,570초 입력을 1,790초 상한으로 둘로 나누면 core는 1,785초지만
두 번째 청크에 overlap 10초가 더해져 1,795초가 된다. 단순 ceil만 믿으면 API
상한을 다시 넘으므로 실제 start/end를 만든 뒤 상한을 재검증해야 한다.

## 2026-08-30 · PR #2/#3 소유권 기록 정정

앞의 "소유권 규약 위반 기록"은 GitHub 계정과 실행 주체를 혼동한 것이다.
PR #2와 #3은 root orchestrator가 Claude 트랙 worker에 위임했고, 그 worker가
Claude 소유 파일을 구현했다. Codex는 읽기 전용 검증과 merge를 맡았다.

두 트랙이 같은 GitHub 계정 `Nattentia`를 사용하고 root가 작업을 배정하면서
작성자 표시만으로 실제 트랙을 구분할 수 없어 생긴 귀속 오류다. 자율 실행 중
Codex가 Claude 소유 source를 수정한 사실은 없다. append-only 기록이므로 앞
항목은 삭제하지 않고 이 정정을 최신 사실로 남긴다. PR #7의 lookahead 0.5
결정과 검증 결과는 유효하다.

## 2026-08-30 · transcribe.py 계약 적합성 점검 — 코드 변경 없음

합의서 단계 3 점검 항목을 현재 main(`7ec4cd3`)에서 확인했다. PR #3(codex)이
검증 계층을 이미 추가해 두어 추가 수정이 필요하지 않았다.

**실호출 검증이 필요했던 이유:** PR #3 이 `generation_config` 를 타입 객체
(`GI.GenerationConfig(transcription_config=GI.TranscriptionConfig(**cfg))`)
에서 평문 dict(`{"transcription_config": cfg}`)로 바꿨다. SDK 가 dict 를
그대로 받아 `verbatim`/`diarization`/word timestamp 를 실제 요청에
적용하는지는 코드만으로 알 수 없다. 합의서가 "실제 요청에 적용되는지"를
요구하므로 짧은 슬라이스로 실호출했다.

**결과** (`jcBDSLSeud4` 195~230초, 35초 슬라이스, `auto`, Gemini 1콜):

| 점검 항목 | 결과 |
|---|---|
| auto 입력 시 `language_codes` | `null` |
| word timestamp 전부 존재 | True |
| start 단조증가 | True |
| diarization 적용 | `spk:0` 라벨 부여됨 |
| `video_id` | `null` 유지 (합의서 지시대로) |
| 단어 키 | `text`, `start`, `end`, `speaker` |
| 추출 단어 | 56 / 34.9초 |

dict 형태 `generation_config` 는 정상 동작한다. 되돌릴 이유가 없다.

**업로드 정리:** `finally` 가 `interactions.create` 를 감싸고 있어 성공·실패
양쪽에서 `files.delete` 를 호출한다. 삭제 실패는 `RuntimeWarning` 으로
알리고 전사 결과는 살린다. 계약 요구를 만족한다.

**빈 응답·비정상 timestamp:** `_extract_words` 가 `TranscriptionResultError`
로 설명 가능한 오류를 낸다. word_info 부재 시 "verbatim/word timestamp 설정과
..." 라는 원인 안내를 포함한다.

**미해결 — 계약 판단 필요:** `video_id` 를 호출자가 지정할 경로가 없다.
현재는 항상 `null` 이고 `merge.py` 가 `captions.json` 쪽 값으로 채운다.
단일 파일 경로에서는 문제없지만, `pipeline.py` 가 여러 단계를 잇게 되면
transcript 단독으로 영상을 식별하지 못한다. CLI 인자 추가는 호출 규약
변경이므로 계약 PR 이 필요하다. 지금은 합의서 지시대로 `null` 을 유지한다.

**Gemini API 호출 1회.**

## 2026-08-30 · 호출량은 서버 quota가 아닌 보수적 로컬 추정치

**무엇:** API key의 SHA-256별, Pacific 날짜별로 실제 요청 직전 시도 횟수와
최근 1분 시각을 file lock 안에서 원자적으로 기록한다. 일일/RPM 한도는 호출자가
반드시 설정하며 코드에 특정 무료 한도를 기본값으로 넣지 않는다.

**왜:** 실패와 429도 서버 quota를 소비할 수 있고, 다른 도구가 같은 key를 쓴
호출은 이 원장에서 볼 수 없다. 따라서 성공 후가 아니라 요청 직전에 올리고,
화면에는 항상 `local estimate`라고 표시해야 한다.

## 2026-08-30 · 청크 화자는 overlap 다수 증거로만 연결

**무엇:** 첫 청크의 raw label은 등장 순서대로 내부 global label을 부여한다.
다음 청크는 같은 절대 시각·같은 단어가 최소 2개 겹치고, 1위 표가 단독일
때만 local→global을 `inferred/overlap`으로 연결한다. 두 local label이 같은
global을 요구하면 강한 한쪽만 받아들이고 나머지는 `unresolved`로 둔다.

**왜:** 호출마다 `spk:0`과 `spk:1`이 바뀔 수 있다. raw 번호를 직접 합치면
청크 전체 화자가 뒤집히므로, `speaker_raw`는 불변 보존하고 증거 부족 시 기존
reader용 `speaker`만 raw로 유지한다. overlap 중복 단어는 derived 결과에서만
제거하고 제거 수를 metadata에 남긴다.

## 2026-08-30 · 전권 위임 인수, 남은 전체 구현

codex 가 토큰 한도로 PR #13 을 열어둔 채 중단했다. 프로젝트 소유자가 claude
에게 전권과 `CONTRACT.md` 개정 권한을 위임했다. 이슈 #12 의 소유권 질문은
"누가 중복하나"에서 "누가 전부 맡나"로 바뀌었고, 답은 claude 다.

`CONTRACT.md` 1절 소유권 표를 그에 맞춰 고쳤다. codex 복귀 시 소유자가 다시
나눈다. `DECISIONS/codex.md` 는 동결한다.

**추가한 것**

| 파일 | 내용 |
|---|---|
| `src/pipeline.py` | 7단계 오케스트레이터, checkpoint/resume, purge |
| `src/visual.py` | 화면 참조·복원 용어 시각의 프레임 후보와 OCR |
| `src/mcp_server.py` | stdio JSON-RPC MCP 서버, 도구 7종 |
| `tests/test_pipeline.py` | 15건 |
| `tests/test_merge.py` | 14건 |
| `tests/test_render.py` | 12건 |
| `tests/test_fetch_youtube.py` | 13건 |
| `tests/test_visual.py` | 14건 |
| `tests/test_transcribe.py` | 10건 |
| `tests/test_mcp_server.py` | 16건 |

전체 115건 통과. 네트워크와 Gemini API 를 호출하지 않는다.

## 2026-08-30 · 구현 중 내린 판단

**transcribe 임포트를 지연시켰다.** `pipeline.py` 가 최상단에서
`import transcribe` 하면 `google-genai` 없이는 `merge`/`render`/`index`/
`status`/`purge` 도 못 돈다. 실제 전사 시점에만 불러온다. 덕분에 SDK 없는
환경에서도 나머지 단계와 테스트가 전부 동작한다.

**`stage_transcribe` 에 `transcriber` 주입점을 뒀다.** 재개 로직과 실패 처리를
API 호출 없이 검증하려면 대역이 필요하다. 기본값은 실제 함수이므로 운영
경로는 바뀌지 않는다. 이 주입점 덕에 다음을 API 0콜로 확인했다.

- 청크 로컬 타임스탬프가 절대 시각으로 보정되는지
- 완료 청크를 재호출하지 않는지
- 실패 후 재개 시 실패한 청크만 호출하는지
- 실패한 시도도 원장에 계상되는지
- preflight 가 한도 초과 시 호출 전에 막는지

**tzdata 폴백을 넣었다.** `usage.py` 의 `ZoneInfo("America/Los_Angeles")` 가
Windows venv 에서 `ZoneInfoNotFoundError` 로 죽는다. 실제로 SDK venv 에서
테스트 3건이 이 이유로 터졌다. 합의서 7절이 새 외부 의존성 도입을 금지하므로
`tzdata` 를 추가하는 대신 2007년 이후 미국 DST 규칙(3월 둘째 일요일 ~ 11월
첫째 일요일)을 직접 구현한 폴백을 뒀다. `ZoneInfo` 가 되면 그쪽을 쓴다.
하루 경계가 어긋나면 사용량 집계가 하루 밀리므로 고정 오프셋으로 때우지
않았다.

**`_log` 를 인코딩 안전하게 만들었다.** Windows cp949 콘솔에서 em-dash 가
`UnicodeEncodeError` 를 낸다. 진행 로그가 파이프라인을 죽이면 안 된다.

**프레임 후보는 균일 추출하지 않는다.** 계약 11절대로 화면 참조 표현
(`보시면`, `이 그림` 등), 복원된 영어 용어 시각, 사용자 지정 시각만 잡고
8초 이내 후보는 합친다. OCR 은 `pytesseract` 가 있을 때만 하고 결과를
transcript 에 덮어쓰지 않는다.

**MCP 서버를 stdlib 로 짰다.** MCP SDK 를 새로 들이는 것은 합의서 7절의
"새 외부 의존성" 에 해당한다. stdio JSON-RPC 는 직접 구현해도 충분히 짧다.

## 2026-08-30 · 아직 하지 않은 것 (다음 작업의 시작점)

**실영상 end-to-end 검증을 하지 않았다.** 소유자 지시대로 검증은 다음
작업으로 미뤘다. 각 모듈과 단계 연결은 단위 테스트로 확인했지만, 실제 URL
하나로 `fetch → index` 를 통과시킨 적이 없다. 다음 작업의 첫 항목이다.

확인해야 할 것:

1. 짧은 영상(`jcBDSLSeud4`, 23분) 전체 실행 — 청크 1개, 약 1콜
2. 30분 초과 영상(`vRTcE19M-KE`, 58분) 전체 실행 — 청크 2개, 약 2콜
3. 청크 실패 후 재개가 실제 API 경로에서도 동작하는지
4. `assemble` 이 실제 다청크 화자 라벨을 정합하는지 (지금은 합성 데이터로만 검증)
5. overlap 구간 중복 제거가 실제 전사에서 올바른지
6. `ytx_query` 가 실제 bundle 에서 근거 timestamp 를 돌려주는지

**미해결 계약 사항:** `transcribe.py` 의 `video_id` 는 여전히 `null` 이고
`pipeline.py` 가 청크 저장 시 채운다. 단일 파일 경로에서 `transcribe.py` 를
직접 쓰면 transcript 만으로 영상을 식별하지 못한다. CLI 인자 추가는 호출
규약 변경이라 계약 판단이 필요하다.

**Gemini API 호출:** 이 작업에서 0회. 단계 3 점검의 1회가 전부다.

## 2026-08-30 · 실영상 검증 1번 — assemble 이 인접 반복어를 지우던 버그

**실행:** `jcBDSLSeud4` (23분, 한국어) 전체 파이프라인 `fetch → index` 첫
실행. Gemini 실호출 1회 성공 (`gemini-3.5-transcribe`, `client.interactions`,
google-genai 2.20.0). exit 0, 7단계 완료.

**정상 확인:** 롤링 중복 제거 cues 603 (CONTRACT §4 일치), `self supervised
learning` 208~210초 복원 (origin=youtube), `RG` 오탐 재발 없음 (PR #7 유지),
srt 391 큐, render 100% 보존, SQLite 색인 48 span 전 구간 커버, `ytx_query`
근거 timestamp 반환.

**버그:** `assemble` 이 `transcribe` 2829단어를 2822단어로 줄였다. 7단어
조용히 소실. `render` 는 그 뒤라 "100% 보존" 으로 보고했다. 손실 수치는
`transcript.json` 의 `speaker_mapping.duplicates_removed` 에만 있고 콘솔에
안 떴다.

원인은 `speakers.reconcile_chunks` 의 `_same_word` 중복 필터가 **모든 청크
(첫 청크 포함)** 에 무조건 돌면서, 같은 청크에서 만들어지는 `merged` 리스트에
자기 형제 단어를 비교한 것이다. `_same_word` 는 정규화 텍스트 일치 + start
0.75초 이내 + end 0.75초 이내면 참이므로, 더듬음·열거로 생기는 정상 인접
반복어가 삭제됐다.

소실된 7건 중 3건은 의미가 바뀌었다.

| 시각 | 원문 | 삭제 후 |
|---|---|---|
| 1018초 | `메드 팜, 메드 팜 2` | `메드 팜, 팜 2` (MedPalm ↔ MedPalm 2) |
| 1153초 | `의사 A와 의사 B가` | `의사 A와 B가` |
| 1194초 | `몇 년, 몇 월, 며칠` | `몇 년, 월, 며칠` |

CONTRACT §8 은 중복 제거를 overlap 구간으로만 한정한다. 코드가 계약을
어기고 있었다.

**수정:**

1. `speakers.py` — 중복 제거를 `position > 0` 이면서 단어 시각이 직전 청크
   overlap 구간 `[chunk_start, 직전 chunk_end]` (양끝 `MAX_TIME_DELTA` 여유)
   안에 들 때만 적용. 첫/유일 청크는 스킵. `chunk_start`/`chunk_end` 가 없는
   standalone·test 경로는 관측 min start / max end 로 폴백. 비교 대상
   `merged` 도 같은 창으로 필터해 기존 O(n²) → O(n·k) 로 줄었다.
2. `pipeline.py` — `stage_assemble` 로그에 `overlap 중복 제거 N` 추가.
3. `tests/test_speakers.py` — 단일 청크 인접 반복어 보존, N>1 에서 overlap
   중복은 지우되 고유 구간 반복어는 보존, 두 케이스 추가. 전체 117건 통과.

**재검증** (`jcBDSLSeud4`, `--stages assemble,merge,render,index`, Gemini 0콜):
assemble 2829단어 (7단어 복원), `overlap 중복 제거 0`, 위 3구절 원문 복원,
merge 삽입 11 유지, render 2840단어 100% 보존.

**완전성 영향 없음:** 진성 overlap 중복은 정의상 시각이 직전 청크 오디오
구간 안에 있으므로 새 게이트가 정확히 덮는다. 게이트 밖 진성 중복은
물리적으로 생길 수 없다. 잡던 것 중 놓치는 것은 없고, 가짜 중복(인접
반복어)만 이제 보존한다.

**쿼터/시간:** 수정은 전부 `assemble` (쿼터 0). 새 API 호출 없음. 첫 청크는
중복 제거를 통째로 건너뛰므로 오히려 빨라진다.

**남은 검증** (이 PR 범위 밖):

1. 30분 초과 영상(`vRTcE19M-KE`, 58분) 2청크 실행 — 실제 overlap 중복 제거,
   청크 경계 문장, 다청크 화자 정합, 절대 타임스탬프
2. 청크 실패 후 재개가 실제 API 경로에서 동작하는지
3. `stage_fetch` 가 영상 파일을 안 받아 `visual.py` 프레임 추출 불가 — 구현 남음
4. CONTRACT §4 Gemini 단어 밴드(2856~2898) 재보정 — 이번 실측 2829, 청크
   재인코딩(16kHz mono 64kbps) + API 비결정성 영향으로 추정
5. 첫 청크 `speaker_status` 가 `inferred`/`evidence:null` — `confirmed` 가 맞음
6. Windows 콘솔 cp949 mojibake — `_log` 폴백이 안 죽지만 여전히 깨져 보임

**Gemini API 호출:** 이 작업에서 1회 (검증 1번 전체 실행).

## 2026-08-30 · 단어 하나가 깨졌다고 청크 전체를 버리지 않는다

**실행:** `vRTcE19M-KE` (58분 05초, 영어 강의) 2청크 실행. chunk 0 성공
(5309단어). chunk 1 실패.

```
TranscriptionResultError: word_info[3341] 'language'의 timestamp가 비정상입니다: start=1119.8, end=120.3
```

**원인:** Gemini 가 긴 오디오에서 비결정적으로 단어 하나의 offset 을 손상시킨다.
같은 `chunk-001.mp3` 를 같은 설정으로 다시 요청하니 5288단어 전부 정상이었고
(`end<start` 0건, 단조증가 위반 0건), 그 단어는 `1119.8-1120.3` 이었다. 즉
원본은 `1120.3` 인데 앞자리 `1` 이 누락돼 `120.3` 으로 왔다. 코드 결함이 아니라
상류 API 의 산발적 결함이다.

**설계 문제:** `2026-08-30 · 전사 응답은 word timestamp 계약을 엄격히 검증`
항목이 첫 이상 단어에서 청크 전체를 중단시킨다. 그 판단의 근거("빈 응답을
조용히 저장하면 다음 단계에서 원인을 못 찾는다")는 지금도 맞다. 다만
**5,300단어 중 1건**에도 같은 잣대를 댄 것이 과했다. 게다가 응답 원문을
어디에도 남기지 않아, 이미 소모한 호출 결과가 통째로 사라졌다. 실패 1건이
호출 2회(실패 + 재실행)를 먹고, 재실행도 같은 확률로 또 깨진다.

**수정 — 세 가지. 호출을 늘리지 않는 것이 제약이었다.**

1. **응답 원문을 파싱 전에 저장한다.** `transcribe.request_raw` / `parse_raw`
   로 호출과 파싱을 분리하고, `transcribe()` 가 `raw_path` 를 받으면 검증 전에
   `chunk-NNN.raw.json` 을 쓴다. 파싱이 실패해도 소모한 호출이 남는다.
   29분 청크 기준 0.9MB (source.mp3 64MB 대비 무시할 수준). 덤으로 지금
   버리던 Gemini 부가 정보가 보존돼, 나중에 필요해지면 호출 없이 꺼낸다.
2. **깨진 timestamp 를 이웃으로 복구한다.** `end < start` 면 다음 단어의
   start 를, 그게 없으면 `start + cap` 을 쓴다. `cap` 은 그 응답 안 정상
   단어들의 p99 길이라 응답마다 자체 보정된다. `start` 역행·누락·음수도
   앞 단어의 end 로 되돌린다. **단어는 지우지 않는다.**
   실측 근거: 이 응답에서 앞 단어 end == 다음 단어 start 가 89.7%(4742/5287),
   단어 길이 중간값 0.20초·최대 1.50초. 관측 사례에 적용하면
   `min(다음 단어 start 1120.3, 1119.8+cap)` = **1120.3** 으로 정답과 일치한다.
3. **무더기 손상은 여전히 중단한다.** 보정이 `max(3, 단어수의 0.5%)` 를
   넘으면 `TranscriptionResultError`. 산발적 잡음과 체계적 손상을 가른다.
   이때도 원문은 저장돼 있어 원인 조사에 호출을 다시 쓰지 않는다.

**저장된 응답 재사용 경로.** 위 1번만으로는 "복구 가능"에 그친다. 호출 0회로
실제 되살리려면 재파싱 경로가 있어야 한다. `transcribe.from_raw()` 와
`pipeline._reusable_raw()` 를 넣어, 미완료 청크에 쓸 수 있는 원문이 있으면
API 대신 그것을 파싱한다. 쿼터 preflight 도 실제 호출할 청크 수만 센다.
요청 언어가 다르면(`requested_langs` 비교) 재사용하지 않고, `--force` 는
무시한다. 재파싱마저 실패하면 임의로 호출하지 않고 "원문 파일을 삭제하고
재실행하라"고 안내한다. 쿼터 소비는 언제나 사람이 정한다.

`google-genai` 임포트를 `request_raw` 안으로 옮겼다. 파싱·복구·재사용은
SDK 없이 돌아가고, `pipeline.py` 의 지연 임포트도 필요 없어졌다.
`transcribe.py` 단독 실행도 `--from-raw` 를 받는다.

**보정 흔적은 숨기지 않는다** (CONTRACT §6). 보정된 단어에
`timestamp_repaired: ["end"]`, 페이로드에 `timestamp_repairs` 목록(index/
text/field/from/to), `speakers.reconcile_chunks` 가 이를 `chunk_index` 를
붙여 derived 까지 옮긴다. `pipeline` 은 `청크 N: M단어, timestamp 보정 K` 를
찍는다. 전부 optional 필드라 CONTRACT 개정은 필요 없다(§6).

**검증 A 재개 결과** (저장해 둔 원문으로 재파싱, **Gemini 호출 0회**):

| 항목 | 결과 |
|---|---|
| chunk 0 / chunk 1 | 5309 / 5288 단어 |
| assemble | 10566단어, overlap 중복 제거 **31** |
| 제거된 31단어 시각 | 1732.6~1742.2 — overlap 창 [1732.6, 1742.6] **안에만** |
| 창 밖 제거 | **0건** |
| 청크 간 화자 정합 | chunk1 `spk:0` → `speaker:0`, evidence `overlap`, 표 31 |
| 절대 timestamp | 단조증가, 3484.0/3485.2초 커버, 경계 1738초 문장 안 끊김 |
| merge | 삽입 0 (영어 영상이라 `ko-orig` 자막 없음. 설계대로 무해 통과) |
| render | 803큐, 10566단어 100% 보존 |
| index | evidence 124 span |

`output.txt` 를 공백으로 쪼개면 10567 토큰이라 1개 많은데, Gemini 가
`"1 It's"` 처럼 내부에 공백이 있는 토큰을 하나 준 탓이다. 보존율의 정본은
단어 객체 수이고 그 기준으로 100% 다.

**PR #16 의 dedup 수정이 실 2청크 데이터로 처음 검증됐다.** 수정 전 코드였다면
이 영상에서 **153단어**를 지웠을 것이고, 그중 122단어는 overlap 과 무관한
진짜 발화다. 수정 후 31단어, 전부 진성 overlap.

**쿼터 영향:** 정상 흐름은 그대로 1콜. 단어 1건 손상 시 기존 2콜 → **1콜**.
저장된 원문이 있으면 재개가 **0콜**. 늘어나는 경로가 없다.

**테스트:** 132건 통과 (117 + 15). 복구 정확성, 마지막 단어 폴백, start 역행,
offset 누락, 무더기 손상 중단, 재파싱 왕복, 저장 원문 재사용 시 호출 0회,
언어 불일치 시 재사용 안 함, `--force` 무시, 재파싱 실패 시 임의 호출 금지.

**Gemini API 호출:** 이 작업에서 3회 (A chunk0 1, A chunk1 실패 1, 원인 규명용
재요청 1). 재개와 재검증은 0회.

## 2026-08-30 · 새 화자에게 global 라벨을 주지 않는다 (미해결, 별건)

검증 A 에서 드러났다. `vRTcE19M-KE` chunk 1 에만 두 번째 화자가 등장한다
(질의응답 구간). `_reconcile_mapping` 은 overlap 표가 없는 raw 라벨을 전부
`unresolved` 로 두고 `global` 을 `null` 로 남긴다. 새 라벨에 새 global 을
발급하는 분기가 없다. 결과로 710단어가 `speaker: "spk:1"` 이라는 **호출별
로컬 라벨**을 그대로 달고 derived 까지 나간다.

CONTRACT §8 의 "근거가 약하면 임의 확정하지 않는다" 를 어기지는 않는다.
다만 청크가 3개 이상이면 서로 다른 사람이 각각 `spk:1` 로 나올 수 있고,
`render.py` 의 큐 분할과 `context.py` 의 span 묶기가 `speaker` 값 동일성으로
판단하므로 **다른 사람이 한 사람으로 합쳐진다.**

새 라벨에 새 global(`speaker:N`)을 발급하고 `speaker_status` 를
`unresolved` 로 유지하는 방향이 맞아 보인다. "이 사람이 누구인지 모른다" 와
"이 사람은 speaker:0 이 아니다" 는 다른 정보이고, 후자는 확실히 안다.
2청크에서는 피해가 없어 이번 PR 에 섞지 않는다. 별도 판단이 필요하다.
