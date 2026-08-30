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

상태: **완료**

대안 (영상 취득을 어디에 두는가):
1. `visual.build` 에 `ensure_video` 콜백을 넘긴다 — visual.py 가 네트워크를 모르는
   상태로 남아 깔끔하지만, 호출부 셋(CLI·MCP·run)이 각자 콜백을 만들어야 한다. 버림.
2. **채택** — `pipeline.ensure_video()` 를 만들고 `stage_visual` 이 먼저 부른다.
   다운로드는 이미 pipeline 소유(`_download`, `VIDEO_FORMAT`)이라 새 책임이 아니다.
   MCP `tool_frames` 를 `visual.build` 대신 `pipeline.stage_visual` 로 돌리면
   CLI(`--stages visual`)와 MCP 가 같은 경로를 쓴다.
3. MCP 계층에서만 받아온다 — CLI 로 `--stages visual` 을 돌리면 여전히 빈 결과가
   나온다. 같은 로직이 두 벌 된다. 버림.

계획서와 달리한 것: `tool_frames` 를 `visual.build` 직접 호출에서 떼어냈다.
계획서는 "영상이 없으면 그때 받아야 한다" 고만 적었는데, 그 자리를 MCP 로 두면
CLI 가 같은 구멍을 그대로 갖는다. 부수로 `max_frames` 미전달 버그도 사라졌다.

CLI: `--skip-video` 가 기본이 됐으므로 `--with-video`(미리 받기)를 추가했다.
`--skip-video` 는 남겨두고 `--with-video` 를 이기게 했다 — 기존 스크립트가
깨지지 않는다.

회귀 대비 (계획서 §6 의 1·6번):
- URL 은 `job.json.input.source` 에서 읽는다. 새 영속 설정 없음.
- `job.json` 이 없거나 URL 이 없으면 받지 않고 그 사실을 로그로 남긴다.
- 영상을 못 구했는데 프레임이 0장이면 note 를 "영상을 확보하지 못했다" 로 바꾼다.
  후보 시각만 계산된 경우와 구분된다. 조용한 빈 결과가 없어졌다.
- 이미 있는 영상은 재사용하고 지우지 않는다.

증거:
- 테스트 186 → **197 통과** (신규 11건: pipeline 8, mcp 3).
- 실제 3청크 번들(`vRTcE19M-KE`, 58분)을 스크래치패드에 복사해 derived 를 지우고
  `--stages assemble,merge,chapters,index` 재생성 → **10,557단어**, 챕터 15개,
  index 생성. 기록된 3청크 결과와 정확히 일치. Gemini 호출 0.
- 같은 번들 `status` 가 `optional_artifacts: ["frames"]` 를 낸다.
- `ensure_video` 가 이미 있는 영상을 재다운로드 없이 재사용하는 것을 실번들에서 확인.

막힌 것: 없음. 단 **실제 영상 다운로드 경로는 네트워크가 필요해 실행하지 않았다**
(가짜 `_download` 로만 검증). `_download` 자체는 이번에 손대지 않았고 fetch 가
쓰던 것과 같은 함수·같은 포맷 문자열이다.
다음: 없음.

---

### C — `render` 기본 stages 제외

상태: **완료**

대안:
1. 그대로 두기 — 렌더는 빠르고 산출물도 작다(58분 기준 수백 KB). 용량만 보면
   할 이유가 없다. 다만 근거 검색은 `merged.json` 과 `index.sqlite3` 로 하고
   `output.srt`/`txt` 를 읽는 코드가 **하나도 없다**(`context.py`, `mcp_server.py`,
   `chapters.py` 확인). 기본 산출물에 있을 이유가 없어 버림.
2. **채택** — `OPTIONAL_STAGES` 에 `render` 추가. D 덕에 상수 한 줄이다.
   `render.py` 는 손대지 않았다.
3. `ytx_render` MCP 도구 신설 — `ytx_register(stages=["render"])` 로 이미 되므로
   도구 표면만 넓힌다. 버림.

증거:
- 테스트 197 → **201 통과** (신규 4건).
- 실번들 `vRTcE19M-KE` 로 기본 경로 재생성 → derived 에 `transcript/merged/chapters`
  만 남고 SRT/TXT 없음. 이어서 `--stages render` 한 번으로 생성,
  **merged 10,557단어 = output.txt 10,557단어**, 보존율 100%.

막힌 것: 없음.
다음: 없음.

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

---

## 마무리 (2026-08-31)

D → A → C 완료. B 는 손대지 않았다 (소유자 결정 대기).

커밋 4개, 전부 로컬. **푸시하지 않았다.**

```
b4571c6 feat: SRT/TXT 를 요청할 때만 만든다
a72014a feat: 영상을 기본으로 받지 않고 프레임이 필요할 때 받는다
117799b refactor: 유효 단계와 기본 실행 단계를 분리한다
8a48161 docs: 경량화 작업 상태 파일과 정지 조건
```

테스트 179 → **201 통과** (신규 22건).

기본 파이프라인:

```
fetch(오디오+자막) → plan → transcribe → assemble → merge → chapters → index
선택: render(SRT/TXT), visual(프레임, 영상을 그때 받음)
```

절감: 영상 다운로드가 기본에서 사라져 bundle 용량 **13~28% 감소**. 나머지
(render 제외)는 용량이 아니라 기본 산출물 단순화가 목적이다.

돌아와서 할 것:

1. 작업 B 진행 여부 결정 (아래 `사람 결정 필요`).
2. CONTRACT §10/§12 문구 정리 여부 결정.
3. 이 브랜치 검토 후 푸시·PR 은 사람이.
4. PR #22(요약) 병합. 이 브랜치는 그것 없이 main 위에 서 있다. 충돌 예상 지점은
   `mcp_server.py` 의 도구 목록과 `README.md` 뿐이다.


---

## 추가 · 오디오 화질 낮추기 (기각, 2026-08-31)

소유자 지시로 검증했다. **결과: 채택 불가.**

저음질(opus 46k) 오디오에 `--language ko-KR` 을 주면 Gemini 가 빈 응답을
돌려준다. 두 번 연속 재현했다. 정지 조건 1번(2회 연속 실패)에 따라 멈췄다.

절감액은 컸다 — 58분 기준 42.6MB → 20.3MB. 그러나 전사 자체가 실패하면
의미가 없다. `AUDIO_FORMAT` 은 손대지 않았다.

근거 표는 `DECISIONS/claude.md` 의 같은 날짜 항목에 있다.

**미검증으로 남긴 것:** 중간 음질(opus 65k, 23분 기준 10.9MB)은 시험하지
않았다. 저음질이 실패한 이상 "음질이 응답 안정성을 바꾼다" 가 성립하므로,
중간값을 채택하려면 영상 여러 편에서 반복 검증이 필요하다. 절감 6MB 를 위해
그만한 호출을 쓸 값이 있는지는 소유자 판단이다.

**부수 발견:** 자동 언어 감지가 불안정하다. 같은 한국어 영상이 한 번은 한국어
전사, 한 번은 영어 번역문으로 나왔다. README 에 언어 지정 권고를 추가했다.
