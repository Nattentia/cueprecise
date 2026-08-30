"""transcript.json + captions.json -> merged.json (CONTRACT.md 2절 준수).

owner: claude

Gemini 전사를 골격으로 두고, 조사만 남고 앞 성분이 사라진 공백 구간에
YouTube 자막의 영어 용어를 시각 기준으로 끌어와 채운다.

사용법:
    python src/merge.py <transcript.json> <captions.json> <merged.json>

판정 근거는 둘을 함께 쓴다. 하나만으로는 삽입하지 않는다.
  1. Gemini 단어 사이 시간 공백이 MIN_GAP 초과
  2. 공백 직후 단어가 조사로 시작 (앞 명사가 사라진 문법적 흔적)

확신이 낮으면 넣지 않는다. 오탐보다 누락을 택한다.
영어 표기 정규화(retrievered, RG, EMR 등)는 이 단계에서 하지 않는다.

타임스탬프 배분: YouTube 롤링 자막은 cue 구간이 구조적으로 서로 겹친다.
따라서 cue 구간별로 균등 배분하면 인접 cue의 토큰끼리 시각이 뒤섞여
전역 순서가 깨진다. 대신 (cue 순서, cue 안 토큰 순서)로 정렬한 목록을
공백 구간 전체에 균등 배분한다. 순서 보존을 우선한 선택이다.
"""
from __future__ import annotations

import io
import json
import re
import sys
from typing import Any

# --- 내부 상수 (CONTRACT 미규정. 변경 근거는 DECISIONS/claude.md 에 남긴다) ---

MIN_GAP = 1.5
"""소실 후보로 볼 최소 공백(초). 강의 발화에서 이보다 짧은 쉼은 흔하다."""

DEDUPE_WINDOW = 5.0
"""공백 앞뒤 이 범위 안에 Gemini 가 이미 같은 라틴 토큰을 갖고 있으면 넣지 않는다."""

PARTICLE_EXACT = {
    "의", "와", "과", "은", "는", "이", "가", "를", "을",
    "라는", "이라는", "라고", "이라고", "란", "이란",
}
"""단독으로 나오면 앞 명사가 사라졌다는 신호가 되는 조사."""

PARTICLE_PREFIX = ("이라는", "라는", "이라고", "라고", "이란", "란")
"""이것으로 시작하는 어절도 같은 신호로 본다. 예: '라는데'."""

LATIN_RE = re.compile(r"[A-Za-z][A-Za-z\-']*")


def _is_dangling_particle(text: str) -> bool:
    t = text.strip()
    return t in PARTICLE_EXACT or t.startswith(PARTICLE_PREFIX)


def _latin(text: str) -> list[str]:
    return LATIN_RE.findall(text)


def find_gaps(words: list[dict]) -> list[dict]:
    """소실 후보 구간. 시간 공백과 조사 잔존을 모두 만족해야 한다."""
    gaps = []
    for i in range(1, len(words)):
        prev, cur = words[i - 1], words[i]
        gap = float(cur["start"]) - float(prev["end"])
        if gap <= MIN_GAP:
            continue
        if not _is_dangling_particle(cur["text"]):
            continue
        gaps.append({
            "start": float(prev["end"]),
            "end": float(cur["start"]),
            "after": cur["text"],
            "before": prev["text"],
            "index": i,
        })
    return gaps


def _nearby_latin(words: list[dict], start: float, end: float) -> set[str]:
    lo, hi = start - DEDUPE_WINDOW, end + DEDUPE_WINDOW
    seen: set[str] = set()
    for w in words:
        if lo <= float(w["start"]) <= hi:
            seen.update(t.lower() for t in _latin(w["text"]))
    return seen


def collect_inserts(gap: dict, cues: list[dict], words: list[dict],
                    consumed: set[tuple[int, int]]) -> list[dict]:
    """공백 구간에 넣을 YouTube 영어 토큰.

    cue 순서와 cue 안 토큰 순서를 유지한 채 공백 구간에 균등 배분한다.
    `consumed` 는 이미 다른 공백에서 소비한 (cue 인덱스, 토큰 위치) 집합이다.
    롤링 자막에서 한 cue 가 인접한 두 공백에 걸치면 같은 토큰이 두 번
    삽입되므로 전역으로 소비 여부를 추적한다.
    """
    already = _nearby_latin(words, gap["start"], gap["end"])
    picked: list[tuple[int, int, str]] = []
    for ci, cue in enumerate(cues):
        cs, ce = float(cue["start"]), float(cue["end"])
        if min(ce, gap["end"]) <= max(cs, gap["start"]):
            continue
        for ti, tok in enumerate(LATIN_RE.findall(cue["text"])):
            if (ci, ti) in consumed or tok.lower() in already:
                continue
            picked.append((ci, ti, tok))
            already.add(tok.lower())

    if not picked:
        return []

    span = gap["end"] - gap["start"]
    step = span / len(picked)
    out = []
    for k, (ci, ti, tok) in enumerate(picked):
        consumed.add((ci, ti))
        out.append({
            "text": tok,
            "start": round(gap["start"] + step * k, 3),
            "end": round(gap["start"] + step * (k + 1), 3),
            "origin": "youtube",
        })
    return out


def merge(transcript: dict, captions: dict) -> tuple[dict, dict]:
    words = [w for w in transcript["words"] if str(w.get("text", "")).strip()]
    cues = sorted(captions["cues"], key=lambda c: (float(c["start"]), float(c["end"])))

    base = [{
        "text": w["text"],
        "start": float(w["start"]),
        "end": float(w["end"]),
        "speaker": w.get("speaker"),
        "origin": "gemini",
    } for w in words]

    gaps = find_gaps(words)
    inserted: list[dict] = []
    per_gap = []
    consumed: set[tuple[int, int]] = set()
    for g in gaps:
        got = collect_inserts(g, cues, words, consumed)
        per_gap.append({
            "at": round(g["start"], 2),
            "context": f"{g['before']} ___ {g['after']}",
            "inserted": [x["text"] for x in got],
        })
        for x in got:
            # 공백 양쪽 Gemini 단어의 화자를 물려받는다. 다르면 비운다.
            lo_spk = words[g["index"] - 1].get("speaker")
            hi_spk = words[g["index"]].get("speaker")
            x["speaker"] = lo_spk if lo_spk == hi_spk else None
        inserted.extend(got)

    merged = sorted(base + inserted, key=lambda w: (w["start"], w["end"]))

    report = {
        "gemini_words_in": len(words),
        "gemini_words_out": sum(1 for w in merged if w["origin"] == "gemini"),
        "inserted": len(inserted),
        "gap_candidates": len(gaps),
        "gaps": per_gap,
    }
    out = {
        "source": "merged",
        "video_id": transcript.get("video_id") or captions.get("video_id"),
        "words": merged,
    }
    return out, report


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    tpath, cpath, opath = sys.argv[1], sys.argv[2], sys.argv[3]
    transcript = json.load(io.open(tpath, encoding="utf-8"))
    captions = json.load(io.open(cpath, encoding="utf-8"))

    merged, report = merge(transcript, captions)
    with io.open(opath, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)

    print(f"gemini {report['gemini_words_in']} -> {report['gemini_words_out']} "
          f"(보존율 {report['gemini_words_out'] / report['gemini_words_in']:.1%})")
    print(f"공백 후보 {report['gap_candidates']}건, 삽입 {report['inserted']}단어")
    for g in report["gaps"]:
        if g["inserted"]:
            print(f"  {g['at']:>8.2f}s  {g['context']}  <- {' '.join(g['inserted'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
