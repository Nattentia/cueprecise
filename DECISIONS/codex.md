# DECISIONS — codex

append-only. 최신이 아래.

작업을 마칠 때마다 append: 날짜 / 무엇을 / 왜.
남의 파일에서 문제를 발견했을 때도 여기에 적는다. 직접 고치지 않는다.

## 2026-08-30 · YouTube 롤링 자막을 줄 단위 연속 구간으로 접음

**무엇:** `ko-orig` SRT의 각 텍스트 줄을 독립적으로 추적해, 인접 블록에서
같은 줄이 반복되는 동안 하나의 cue로 합쳤다. 시작 시각은 최초 등장 블록,
종료 시각은 마지막 등장 블록에서 가져온다.

**왜:** 두 줄짜리 롤링 창과 10ms 전환 블록에서도 텍스트와 최초 시각을 모두
보존해야 한다. fixture와 실제 다운로드 양쪽에서 1,800줄 → 603 cue,
2,752단어를 확인했다.

## 2026-08-30 · 렌더 단계에서 초과 텍스트를 다음 cue로 넘김

**무엇:** 화자 변경, 0.65초 초과 무음, 7초 초과 길이, 2줄 초과 중 하나가
발생하기 전에 현재 cue를 닫는다. 같은 구현으로 SRT와 TXT를 생성한다.

**왜:** 완성된 긴 cue를 두 줄로 잘라내면 복구할 수 없는 텍스트 소실이 난다.
fixture의 Gemini 단어 2,856개가 출력에서도 동일 순서로 100% 보존됨을 확인했다.

## 2026-08-30 · 기존 JSON을 보존하며 장기 작업 계약을 확장

**무엇:** 기존 captions/transcript/merged 형식을 삭제하거나 바꾸지 않고,
job manifest, optional 청크·화자 필드, 로컬 사용량 원장, knowledge bundle,
frame 색인, pipeline/MCP 완료 조건을 추가했다.

**왜:** 장기 영상과 세션 간 컨텍스트를 구현하려면 파일 소유권과 단계 간
인터페이스가 먼저 고정돼야 한다. additive 필드와 schema_version으로 기존
reader를 계속 동작시키고, raw는 불변으로 두어 rollback과 재생성을 보장한다.

## 2026-08-30 · 컨텍스트는 bundle의 SQLite 근거 색인으로 복원

**무엇:** merged/transcript를 최대 30초 span으로 묶고 chapter와 frame OCR을
함께 `index.sqlite3`에 원자적으로 색인한다. 검색 결과는 video_id, 절대 시각,
원문, source path/kind, confidence와 speaker를 반환한다.

**왜:** 대화 기록에 전체 전사를 계속 싣지 않고도 새 세션에서 video_id로
근거를 다시 찾을 수 있어야 한다. 원본 derived JSON을 유지한 채 index를 통째로
재생성하므로 stale 자료를 섞지 않고 rollback도 단순하다.

## 2026-08-30 · 증거 없는 청크 화자도 고유 global ID를 갖는다

**무엇:** 첫 청크의 화자는 `confirmed`로 두고, 뒤 청크에서 overlap 증거가
없는 로컬 화자에는 청크마다 새로운 `speaker:N`을 발급한다. 다만 신원 연결은
검증되지 않았으므로 `speaker_status=unresolved`를 그대로 유지한다.

**왜:** Gemini의 `spk:1`은 호출별 로컬 라벨이다. 서로 다른 청크의 `spk:1`을
그대로 렌더·색인하면 다른 사람을 한 사람으로 합친다. 고유 표시 ID와 신원
확신도를 분리하면 잘못된 병합을 막으면서 raw 라벨도 보존할 수 있다.

## 2026-08-31 · 내용 근거와 화자 확신도를 분리

**무엇:** SQLite 근거 span에 `speaker_candidate`, `speaker_status`,
`speaker_confidence`를 저장한다. `unresolved` 화자는 후보 라벨만 보존하고
확정 `speaker`는 null로 색인한다. MCP 개요에서도 확정·추론 화자와 미해결
후보를 별도 필드로 반환한다.

**왜:** 겹친 발화, 짧은 맞장구, 청크별 독립 라벨에서는 화자 연결이 틀릴 수
있지만 발화 내용까지 버릴 이유는 없다. 내용은 100% 검색 가능하게 유지하면서
요약기가 불확실한 화자를 사실처럼 단정하지 않게 해야 한다. 실제 3청크
Stanford 번들에서 speaker:0은 confirmed/inferred로, 702단어의 speaker:1은
candidate+unresolved로 색인됨을 확인했다.

## 2026-08-31 · 챕터 경계는 로컬, 제목만 선택적으로 호스트가 작성

**무엇:** YouTube 원본 챕터를 2~8분 범위로 정규화하고 긴 구간은 문장·무음
경계에서 로컬 분할한다. 제목 없는 구간은 키워드 폴백으로 즉시 저장한다.
`ytx_outline`은 해당 구간의 키워드와 대표 문장만 반환하고, 임의 MCP 호스트가
처음부터 지은 제목은 `ytx_set_chapter_titles`가 fingerprint와 ID를 검증해
일괄 저장한다.

**왜:** 전체 전사를 호스트에 넘기거나 MCP sampling에 의존하지 않고도 모든
환경에서 완성된 목차가 필요하다. 경계와 원문은 결정적으로 유지하고 모델별
차이는 짧은 제목에만 제한한다. 호스트가 후속 호출을 못 하거나 잘못된 JSON을
보내도 로컬 제목이 남는다.

## 2026-08-31 · summary.md는 로컬 폴백과 선택적 호스트 개선의 2단 구조로 계획

**무엇:** `summary.md`를 챕터 지도와 원문 검색 사이의 영속 압축 문서로 두고,
`chapters.json`의 대표 문장으로 항상 로컬 추출본을 만든다. 지원하는 MCP 호스트는
전체 전사 대신 챕터별 짧은 패킷만 읽어 구조화된 요약을 한 번 작성하고, 서버가
fingerprint와 chapter ID를 검증해 Markdown으로 저장한다. 세부 계획과 대안 비교는
`SUMMARY_PLAN.md`에 기록했다.

**왜:** 호스트 종류나 MCP sampling 지원 여부와 무관하게 산출물이 있어야 하며,
Gemini 무료 호출과 매 세션 전체 전사 토큰을 추가로 쓰지 않아야 한다. 요약 자체를
검색 근거로 색인하지 않아 압축 과정의 오류가 원문 증거처럼 재인용되는 것도 막는다.

## 2026-08-31 · summary.md를 필수 단계가 아닌 온디맨드 캐시로 축소

**무엇:** 기본 분석과 특정 내용 검색은 summary 없이 수행한다. 사용자가 영상 전체
요약을 요청해 `ytx_summary`가 호출될 때만 로컬 추출본을 저장하고, 가능한 호스트가
같은 호출의 압축 패킷으로 이를 선택적으로 개선한다.

**왜:** 다중 영상의 특정 내용 검색은 원문 index가 담당하므로 모든 영상에 요약을
미리 만드는 것은 불필요하다. 온디맨드 생성은 요약 파일의 장기 컨텍스트 장점은
유지하면서 기본 처리 시간과 저장 작업을 없앤다.

## 2026-08-31 · 다음 작업은 선택 기능을 기본 경로에서 분리하는 경량화

**무엇:** 다음 세션의 구현 계획을 `LIGHTWEIGHT_HANDOFF.md`에 기록했다. 기본
경로는 audio/captions → transcribe → merge → chapters → index로 줄이고, 영상·
visual, diarization, render는 opt-in으로 전환한다. summary는 PR #22에서 이미
온디맨드로 분리했다.

**왜:** 영상 다운로드와 프레임 처리가 가장 큰 불필요 비용이고, 화자 구분과 자막
파일은 전체 요약·원문 검색의 필수 조건이 아니다. 반면 완료 청크 자동 삭제는 raw
삭제를 명시적으로만 허용한 계약에 어긋나므로 제외하고 수동 purge를 유지한다.

## 2026-09-04 · Claude 원클릭 후보는 Python형이 아닌 binary MCPB로 검증

**무엇:** `cueprecise-mcp.exe`와 manifest만 담는 Windows 전용 MCPB PoC를 만든다.
Gemini 키는 MCPB의 `sensitive` 사용자 설정으로 받고 환경 변수로만 서버에 전달한다.
FFmpeg, yt-dlp, 온보딩 프로그램과 기존 설치 프로그램은 이 PoC에 포함하지 않는다.

**확인된 것:** 공식 MCPB CLI의 manifest 검증과 bundle 정보 읽기를 통과했다. 서명되지
않은 PyInstaller 실행 파일은 Python 없이 MCP 초기화, 도구 목록, 기존 자료 상태 조회를
수행했다. 인터넷 영역 표시(ZoneId=3)를 붙인 MCPB를 Windows `Expand-Archive`로 풀면
내부 EXE에는 영역 표시가 전파되지 않았다. 이는 일반 ZIP 해제 동작에 대한 증거이며,
Claude Desktop 자체 해제기가 같은지에 대한 최종 증거는 아니다.

**Claude 실기 확인:** Claude Desktop 1.40609.1.0에서 인터넷 영역 표시가 붙은 MCPB를
설치·활성화했으며 SmartScreen은 나타나지 않았다. 설치된 미서명 EXE에도
`Zone.Identifier`가 없었다. `sensitive` canary 값은 Claude 데이터 폴더의 평문 검색에서
발견되지 않았고 설정 파일에는 `__encrypted__:` 값으로 저장됐다. 확장 제거 후 실행 파일
디렉터리와 설정 파일이 모두 삭제됐고, canary의 평문 흔적도 발견되지 않았다.

**남은 판정:** 서버 프로세스에 복호화된 키가 환경 변수로만 전달되는지를 별도로 확인한다.
새 영상 수집은 yt-dlp/FFmpeg 배포 결정을 하기 전까지 지원 완료로 간주하지 않는다. 이
항목들이 통과하기 전에는 MCPB를 공식 릴리스에 올리지 않는다.
