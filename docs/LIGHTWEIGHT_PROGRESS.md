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

상태: **대기**
대안:
증거:
막힌 것:
다음: `pipeline.py` 의 `STAGES` 사용처 전부 읽고, 유효 목록과 기본 실행 목록을 나눈다. `mcp_server.tool_register` 의 `stages or pipeline.STAGES` 도 함께.

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
