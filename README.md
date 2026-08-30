# ytx

YouTube 영상 → 한국어 전사 · 검색 파이프라인.

Gemini 전사는 한국어 본문 품질이 높지만 코드스위칭된 영어 용어를 버린다.
YouTube 원어 자동자막(`ko-orig`)은 반대로 영어를 3배 더 보존하지만 한국어가 거칠다.
둘을 시각 기준으로 합쳐 양쪽 장점만 취한다.

## 빠른 시작

```powershell
$env:GEMINI_API_KEY = "..."            # https://aistudio.google.com/api-keys
python src/pipeline.py run "https://www.youtube.com/watch?v=jcBDSLSeud4"
```

산출물은 `data/<video_id>/derived/` 에 생긴다.

```powershell
python src/pipeline.py status jcBDSLSeud4                 # 진행도 + 사용량 추정
python src/pipeline.py run <url> --stages merge,render    # 일부 단계만 재실행
python src/pipeline.py purge jcBDSLSeud4 --scope derived  # derived 만 삭제
```

## 파이프라인

```
fetch       URL           -> raw/source.mp3, raw/captions.json      쿼터 0
plan        source.mp3    -> job.json, raw/audio/chunk-NNN.mp3      쿼터 0
transcribe  chunk-NNN.mp3 -> raw/transcripts/chunk-NNN.json         청크당 1콜
assemble    chunk 전사    -> derived/transcript.json                쿼터 0
merge       transcript+자막 -> derived/merged.json                  쿼터 0
render      merged.json   -> derived/output.srt, output.txt         쿼터 0
index       bundle        -> index.sqlite3                          쿼터 0
```

단계는 JSON 파일로만 이어진다. 각각 독립적으로 재실행할 수 있고, 완료된
청크는 `job.json` 의 fingerprint/config 가 일치하면 다시 호출하지 않는다.
중간에 실패해도 완료 청크는 보존되고 같은 명령으로 이어서 진행한다.

인터페이스는 `CONTRACT.md` 가 정의한다. 그 파일이 유일한 진실이다.

## MCP 서버

```powershell
python src/mcp_server.py --bundle-root data
```

stdio JSON-RPC. 외부 패키지 없이 stdlib 만 쓴다.

| 도구 | 하는 일 |
|---|---|
| `ytx_register` | 영상 등록/분석. 단계 선택 가능 |
| `ytx_status` | 진행도 + 로컬 Gemini 사용량 추정 |
| `ytx_outline` | 개요와 timestamp 목차, 복원 용어, 화자 상태 |
| `ytx_query` | 내용 질의. 근거 span/frame 과 timestamp 반환 |
| `ytx_excerpt` | 특정 시각 구간의 자막과 프레임 |
| `ytx_frames` | 화면 참조 시각의 프레임 추출 |
| `ytx_purge` | derived 재생성 및 명시적 삭제 |

## 왜 이 구조인가

측정된 사실 (영상 `jcBDSLSeud4`, 23분 27초, 한국어 강의):

| 소스 | 라틴 토큰 | `self supervised learning` |
|---|---|---|
| YouTube `ko-orig` | 91 | 있음 |
| Gemini `ko-KR` | 29 | 없음 |
| Gemini `ko-KR,en-US` | 25 | 없음 |
| Gemini auto | 28 | 없음 |

Gemini 는 4회 실행 모두 실패. YouTube 자막은 무료로 갖고 있다.
`merge.py` 가 시간 공백과 조사 잔존을 **함께** 근거로 삼아 그 구간에만 영어를
복원한다. Gemini 원본 단어는 삭제·재작성하지 않는다.

## 요구 사항

- Python 3.11+
- `yt-dlp` — 오디오·자막 취득
- `ffmpeg` / `ffprobe` — 청크 분할, 프레임 추출
- `google-genai` — `transcribe` 단계에만 필요. 다른 단계는 SDK 없이 돌아간다
- 선택 `pytesseract` + `Pillow` — 있으면 프레임 OCR, 없으면 `ocr_text: null`
- 선택 `tzdata` — 없으면 내장 US/Pacific 폴백을 쓴다

## 테스트

```powershell
python -m unittest discover -s tests
```

stdlib `unittest` 만 쓴다. 테스트 의존성이 없고 네트워크와 Gemini API 를
호출하지 않는다. `google-genai` 가 없으면 `test_transcribe` 는 skip 된다.

## 알려진 제한

- YouTube 자막의 표기 오류(`promots`, `retriever`, `RG`)가 그대로 들어온다.
  표기 정규화는 별도 단계이며 아직 없다.
- `merge.py` 의 임계값은 영상 한 편에서만 조정했다. 다른 영상 검증이 필요하다.
- 사용량 원장은 로컬 추정치다. AI Studio 의 서버 수치가 최종 권위다.
- `visual.py` 는 `raw/` 에 영상 파일이 있어야 프레임을 뽑는다. 오디오만 받은
  bundle 에서는 후보 시각만 계산한다.
- **전체 파이프라인의 실영상 end-to-end 검증은 아직 하지 않았다.** 다음 작업의
  첫 항목이다.
