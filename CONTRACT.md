# CONTRACT

에이전트는 이 파일을 수정하지 않는다. 변경은 사람만 한다.
변경이 필요하면 `DECISIONS/<자기이름>.md`에 근거를 적고 사람의 판단을 기다린다.

## 1. 파일 소유권

| 파일 | 주인 | 다른 쪽 |
|---|---|---|
| `src/fetch_youtube.py` | codex | 읽기만 |
| `src/render.py` | codex | 읽기만 |
| `src/transcribe.py` | claude | 읽기만 |
| `src/merge.py` | claude | 읽기만 |
| `CONTRACT.md` | 사람 | 읽기만 |
| `DECISIONS/codex.md` | codex | 읽기만 |
| `DECISIONS/claude.md` | claude | 읽기만 |
| `tests/fixtures/**` | 사람 | 읽기만 |

남의 파일은 고치지 않는다. 문제를 발견하면 자기 `DECISIONS` 파일에 적는다.

## 2. 데이터 계약

모든 단계는 JSON 파일로만 이어진다. 함수 시그니처를 공유하지 않는다.
시각 단위는 **초(float)**, 영상 시작 기준 절대값.

### captions.json — `fetch_youtube.py` 출력

```json
{
  "source": "youtube-ko-orig",
  "video_id": "jcBDSLSeud4",
  "cues": [
    {"start": 207.68, "end": 209.42, "text": "했을까요? self"}
  ]
}
```

- `cues`는 `start` 오름차순.
- 롤링 중복이 제거된 상태여야 한다.
- 각 cue의 `start`는 **그 텍스트가 처음 등장한 블록의 start**를 쓴다.

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
