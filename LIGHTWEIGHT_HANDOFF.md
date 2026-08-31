# ytx 경량화 작업 인계서

대상: 다음 Claude Code 세션  
상태: 계획만 확정, 구현하지 않음  
기준 브랜치: `main`에 PR #22 병합 후 새 브랜치에서 시작  
규칙: `CONTRACT.md`가 최우선이며 직접 수정하지 않는다.

## 1. 목적

ytx의 본래 목적은 **YouTube 링크를 한 번 분석해 내용을 요약하고, 이후 세션에서
필요한 원문을 timestamp와 함께 다시 찾는 것**이다. 기본 실행은 이 목적에 필요한
자료만 만들고, 자막 파일·화자 구분·영상 프레임은 요청이 있을 때만 추가한다.

경량화는 전사 정확도를 낮추는 작업이 아니다. Gemini 전사, YouTube 자막 병합,
챕터, 원문 색인은 유지한다.

## 2. 현재 상태

현재 기본 `pipeline.STAGES`:

```text
fetch → plan → transcribe → assemble → merge → chapters → render → visual → index
```

현재 기본 동작:

- `diarization=True`
- 영상도 기본 다운로드하며 `--skip-video`로만 끌 수 있음
- SRT/TXT와 프레임을 매번 생성
- 전사 청크는 완료 후에도 보존하고 수동 `purge --scope chunks` 제공
- `summary.md`는 PR #22부터 온디맨드 생성이므로 기본 비용 없음

실측 bundle 용량:

- 23분 영상: 55.8MB
- 58분 영상: 110.3MB
- 오디오: 전체의 72~80%
- 360p 영상: 13~28%
- 완료 청크: 전체의 약 20~25%

`render`, `merge`, `chapters`, `index`는 Gemini 호출을 쓰지 않는다. 따라서
경량화의 큰 효과는 호출 수보다 **영상 다운로드·프레임 처리·저장 공간·불필요한
화자 메타데이터 감소**에 있다.

## 3. 권장 기본 경로

```text
fetch(audio + ko-orig captions)
→ plan
→ transcribe(diarization off)
→ assemble
→ merge
→ chapters
→ index
```

선택 기능:

```text
render  사용자가 SRT/TXT를 원할 때
visual  사용자가 화면·도식·코드 캡처를 원할 때
summary 사용자가 전체 요약을 원할 때(PR #22)
speaker 화자별 분석이 중요한 인터뷰·대담일 때
```

## 4. 구현 우선순위

### 작업 A — 기본 영상 다운로드와 visual 비활성화

가장 먼저 한다. 저장 공간과 네트워크 절감 효과가 가장 명확하다.

- 새 bundle의 기본 fetch는 오디오와 `ko-orig` 자막만 받는다.
- `visual`은 기본 stages에서 제외한다.
- `ytx_frames` 또는 명시적 visual 요청에서 영상이 없으면 해당 YouTube URL로
  360p 영상을 그때 받아 프레임을 추출해야 한다.
- 원본 URL은 이미 `job.json.input.source`에 있으므로 새 영속 설정을 만들지 않는다.
- 기존 bundle에 영상이 있으면 그대로 재사용하고 자동 삭제하지 않는다.

Acceptance:

- 기본 `ytx_register` 후 `raw/source_video.*`가 없음
- captions, transcript, merged, chapters, index는 정상 생성
- 이후 `ytx_frames` 호출 한 번으로 영상 취득과 프레임 추출이 완료
- Gemini 추가 호출 0

### 작업 B — diarization을 새 영상에서 opt-in으로 변경

요약과 내용 검색은 화자 ID 없이 가능하다. 짧은 맞장구와 겹친 발화를 정교하게
맞추는 것은 기본 목적에 비해 과하다.

- 새 bundle 기본값은 `diarization=False`.
- MCP `ytx_register`와 CLI에 명시적 opt-in을 제공한다.
- 인터뷰·대담에서만 사용하도록 설명한다.
- **기존 완료 bundle의 job config를 기본값 변경만으로 다시 plan/transcribe하지
  않는다.** 기존 `diarization=True` 결과는 유효한 캐시로 보존한다.
- 명시적으로 설정을 바꾼 경우에만 config fingerprint가 달라지고 재전사 필요를
  사용자에게 알린다.

Acceptance:

- 새 기본 job은 `diarization=false`
- 명시적 opt-in은 기존 화자 reconciliation 경로를 그대로 통과
- 기존 3청크 Stanford bundle을 조회/재실행해도 Gemini 재호출 없음
- 화자 필드가 없어도 assemble, merge, chapters, index, summary가 정상 동작

### 작업 C — render를 기본 stages에서 제외

SRT/TXT 생성은 빠르고 용량도 작으므로 효과는 A/B보다 작다. 기능은 삭제하지 않고
명시적 stage로 유지한다.

- 기본 분석에서는 `output.srt`, `output.txt`를 만들지 않는다.
- 사용자가 “자막 파일을 만들어줘”라고 요청할 때 `render`만 실행한다.
- 기존 `render.py`와 100% 단어 보존 검증은 변경하지 않는다.

Acceptance:

- 기본 분석은 SRT/TXT 없이 완료
- `--stages render`와 이에 대응하는 MCP 경로로 나중에 생성 가능
- 렌더 결과 단어 보존율 100%

### 작업 D — 기본/전체 stage 상수 분리

현재 `STAGES`가 유효 stage 목록과 기본 실행 목록을 동시에 뜻한다. 다음처럼 역할을
분리하면 조건문과 호스트별 예외가 줄어든다.

```python
STAGES = (...모든 지원 stage...)
DEFAULT_STAGES = ("fetch", "plan", "transcribe", "assemble",
                  "merge", "chapters", "index")
```

- CLI와 `ytx_register`의 생략값만 `DEFAULT_STAGES` 사용
- 명시적 `stages` 입력은 계속 `STAGES` 전체에서 검증
- status에는 optional artifact가 없어도 실패로 표시하지 않는다.

## 5. 하지 말 것

### 완료 청크 자동 삭제

현재는 구현하지 않는다. `CONTRACT.md` 10절이 raw 삭제를 명시적 별도 명령으로만
허용한다. 자동 삭제는 복구·감사 자료를 없애며 계약 위반이다.

대신 전사 완료 응답에 다음 안내만 유지하거나 추가한다.

```text
원본 오디오가 있으므로 purge --scope chunks로 약 20~25%를 회수할 수 있음
```

### `merged.json` 생략

YouTube 삽입이 0개일 때 transcript와 중복될 수 있으나 텍스트 JSON의 용량은 오디오·
영상보다 작다. reader마다 조건 분기를 추가하는 것보다 현재 단일 계약을 유지하는 편이
안전하다.

### 화자 reconciliation 삭제

기능 자체는 유지한다. 기본 off로만 바꾸고, 인터뷰/대담 사용자가 opt-in하면 기존
검증된 경로를 사용한다.

### 벡터 DB·별도 로컬 LLM·요약 모델 추가

경량화 범위가 아니다. 외부 의존성과 설치 부담을 늘리지 않는다.

## 6. 실제 사용에서 조심할 회귀

1. `visual`을 나중에 요청할 때 source URL을 잃어 영상 다운로드가 불가능한 경우
2. 기본 diarization 변경으로 기존 job fingerprint가 달라져 무료 호출이 다시 나가는 경우
3. 기본 stages 변경 뒤 status가 optional 파일 부재를 실패로 오인하는 경우
4. MCP tool 설명이 예전 기본 동작을 안내해 호스트가 불필요한 stage를 요청하는 경우
5. `ytx_register(stages=[...])`의 명시적 사용자 선택을 새 기본값이 덮는 경우
6. 영상 없이 시작한 bundle에서 `ytx_frames`가 조용히 빈 결과만 반환하는 경우

위 여섯 항목은 각각 회귀 테스트가 필요하다.

## 7. 권장 테스트 순서

1. 단위 테스트: default stage 선택, 새/기존 diarization config
2. MCP 테스트: register 기본값, render/frames 온디맨드 경로
3. 전체 테스트
4. 기존 58분 Stanford bundle 캐시 재사용 검증(Gemini 0콜)
5. 새 짧은 영상 dry/fake transcriber 실행으로 기본 artifact 목록 확인
6. 같은 bundle에 visual과 render를 나중에 추가해 결과 생성 확인

## 8. 완료 조건

- 기본 분석으로 전체 요약과 후속 원문 검색에 필요한 자료가 모두 남는다.
- 기본 실행은 영상, frames, SRT/TXT, 화자 분리를 만들지 않는다.
- 요청하면 네 선택 기능을 독립적으로 나중에 추가할 수 있다.
- 기존 bundle과 명시적 stages 호출은 깨지지 않는다.
- 기존 캐시를 잘못 무효화해 Gemini를 재호출하지 않는다.
- 전체 테스트와 실제 캐시 재사용 검증을 통과한다.

여기서 멈춘다. 다음 별도 작업은 여러 bundle을 한 번에 조회하는 **다중 영상 통합
원문 검색**이다. 경량화 PR에 섞지 않는다.

## 9. Git 작업 제안

```text
PR #22 병합
git switch main
git pull --ff-only origin main
git switch -c claude/lightweight-defaults
```

한 PR에서 A→B→C→D 순서로 구현하되 각 단계마다 관련 테스트를 먼저 통과시킨다.
예상보다 기존 캐시 호환 처리가 커지면 B를 별도 PR로 분리한다.
