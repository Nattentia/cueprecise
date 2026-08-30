# ytx

YouTube 영상 → 한국어 전사 파이프라인.

Gemini 전사는 한국어 본문 품질이 높지만 코드스위칭된 영어 용어를 버린다.
YouTube 원어 자동자막(`ko-orig`)은 반대로 영어를 3배 더 보존하지만 한국어가 거칠다.
둘을 시각 기준으로 합쳐 양쪽 장점만 취한다.

```
fetch_youtube.py  →  captions.json   (영어 용어 강함, 쿼터 0)
transcribe.py     →  transcript.json (한국어 본문 강함, 1콜)
merge.py          →  merged.json     (공백을 자막으로 메움, 쿼터 0)
render.py         →  .srt / .txt
```

인터페이스는 `CONTRACT.md`가 정의한다. 그 파일이 유일한 진실이다.

## 왜 이 구조인가

측정된 사실 (영상 `jcBDSLSeud4`, 23분 27초, 한국어 강의):

| 소스 | 라틴 토큰 | `self supervised learning` |
|---|---|---|
| YouTube `ko-orig` | 91 | 있음 |
| Gemini `ko-KR` | 29 | 없음 |
| Gemini `ko-KR,en-US` | 25 | 없음 |
| Gemini auto | 28 | 없음 |

Gemini는 4회 실행 모두 실패. YouTube 자막은 무료로 갖고 있다.
