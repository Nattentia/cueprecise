# 경량화 작업 상태

브랜치: `claude/lightweight-defaults` (base `891bcf6` = main)
규칙: 커밋만 한다. **푸시 금지, PR 생성·병합 금지.** `CONTRACT.md` 수정 금지.
계획 원본: `LIGHTWEIGHT_HANDOFF.md`

## 기준선

- 2026-08-31 시작, `python -m unittest discover -s tests` → **179 tests OK**
- 실측 검증용 3청크 번들: `C:\dev\ytx-codex\data\vRTcE19M-KE` (93MB, `.raw.json` 꼬리표 있음)
  - **원본을 직접 건드리지 않는다.** 복사본을 만들어 `--bundle-root` 로 쓴다.
- `C:\dev\sandbox\ytx\data\jcBDSLSeud4` 는 빈 디렉터리다. 쓸 수 없다.

## 정지 조건

1. 같은 작업에서 테스트 2회 연속 실패 → 그 작업 `막힘` 으로 적고 다음 작업으로 넘어간다. 무한 재시도 금지.
2. `CONTRACT.md` 와 충돌 → 멈추고 `사람 결정 필요` 에 적는다. 문서를 고치지 않는다.
3. 작업 B 는 손대지 않는다 (아래 이유).
4. 네 작업이 모두 `완료` 또는 `막힘` 이면 루프를 끝낸다.
5. Gemini 실호출이 필요해지는 경로는 실행하지 않는다. 캐시 재사용 검증만 한다.

## 작업 순서 (계획서의 A→B→C→D 를 D→A→C 로 바꿨다)

D 가 A·C 의 토대라서 먼저 간다. B 는 제외한다.

---

### D — `STAGES` / `DEFAULT_STAGES` 분리

상태: **완료** (`8a48161` 다음 커밋)

대안:
1. 계획서안 — `DEFAULT_STAGES` 를 손으로 적은 별도 튜플. 두 상수가 따로 놀아
   단계를 추가할 때 한쪽만 고치는 사고가 난다. 버림.
2. `--skip-stages` 플래그만 추가 — 상수는 하나로 두고 빼기만 한다. MCP 호스트가
   뺄 이름을 알아야 해서 설명이 길어지고, 기본과 전체의 구분이 도구 스키마에
   안 드러난다. 버림.
3. **채택** — `OPTIONAL_STAGES` 를 유일한 손편집 지점으로 두고
   `DEFAULT_STAGES = STAGES - OPTIONAL_STAGES` 로 유도한다. 계획서와 다르지만
   위험이 없고(순서·검증은 `STAGES` 가 그대로 쥔다) 뒤 작업 A·C 가 상수 한 줄만
   고치면 끝난다. `STAGE_ARTIFACTS` 도 함께 둬서 status 가 선택 산출물을 알 수 있다.

계획서와 달리한 것 하나 더: 이 커밋에서 `OPTIONAL_STAGES` 는 **빈 튜플**이다.
D 는 배관만 놓고 기본 동작을 바꾸지 않는다. 실제로 단계를 빼는 것은 A(visual)와
C(render)가 각각 한다. 그래야 커밋마다 동작 변화가 하나씩만 들어가 되돌리기 쉽다.

부수 이득: `--stages all` / `stages: ["all"]` 키워드를 넣었다. 기본이 전체가
아니게 된 뒤 "전부 돌려라" 를 표현할 방법이 없으면 호스트가 아홉 개를 손으로
나열하게 된다.

증거:
- `pipeline.resolve_stages()` 가 CLI·MCP·`run()` 의 유일한 정규화 지점. 검증은
  항상 `STAGES` 전체 기준이라 기본에서 빠진 단계도 이름을 대면 돈다.
- `status()` 가 `optional_artifacts` 를 함께 낸다. 지금은 빈 목록.
- 테스트 179 → **186 통과** (신규 7건). `DEFAULT_STAGES == STAGES` 라 기존 동작
  변화 0. `python src/pipeline.py run --help` 정상.

막힌 것: 없음.
다음: 없음.

---

### A — 영상 다운로드·visual 기본 off + 온디맨드 프레임

상태: **대기**
대안:
증거:
막힌 것:
다음: `visual.build` 에 영상 취득 경로가 없다. `job.json.input.source` 를 읽어 `stage_fetch(video=True)` 를 부르는 계층을 어디에 둘지 정한다. `job.json` 이 없는 번들 분기도 필요. `tool_frames` 가 `max_frames` 를 안 넘기는 것도 같이 고친다.

---

### C — `render` 기본 stages 제외

상태: **대기**
대안:
증거:
막힌 것:
다음: D 가 끝난 뒤 `DEFAULT_STAGES` 에서 `render` 만 빼면 된다. `render.py` 는 건드리지 않는다. 단어 보존 100% 테스트 유지.

---

### B — diarization opt-in

상태: **제외 (사람 결정 필요)**
이유: `config.diarization` 플래그가 Gemini 호출까지 닿지 않는다. `transcribe.py:207` 이
`"diarization_mode": "speaker"` 를 무조건 박는다. 기본값만 바꾸면 절감은 0 이고,
`pipeline.py:253` 의 `job["config"] == config` 전체 비교 때문에 기존 번들이 재계획된다.
`.raw.json` 꼬리표가 없던 시절 번들은 실제로 Gemini 를 다시 부른다.
선행 조건 둘이 필요하다: (1) 플래그를 `transcription_config` 에 실제 연결, (2) config
비교에서 diarization 제외 또는 job.json 마이그레이션. 소유자 결정 전까지 손대지 않는다.

---

## 사람 결정 필요

- **작업 B 진행 여부.** 위 참조.
- **CONTRACT §10 과 §12.** §10 기본 번들 구조에 `output.srt`/`output.txt` 가 있고,
  §12 acceptance 가 "화자 raw/global/unresolved 확인" 을 요구한다. C 와 B 가 이 문장과
  부딪친다. "기본 구조 = 가능한 산출물 목록", "acceptance = opt-in 경로에서 확인" 으로
  읽으면 넘어가지만 소유자가 정해야 한다.
- **PR #22 병합.** 자리 비운 사이 병합하지 않는다. 이 브랜치는 PR #22 없이 main 위에 선다.

## 발견한 별건

- `mcp_server.tool_frames` 가 `visual.build` 에 `max_frames` 를 넘기지 않는다. A 에서 함께 고친다.
- `render.py` 는 화자 변경을 cue 종료 조건으로 쓴다. B 를 하면 SRT 줄 나눔이 바뀐다
  (단어 보존율은 유지). 계획서의 회귀 목록 6건에 없던 항목이다.
