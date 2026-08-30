"""Merge missing Latin terms from YouTube captions into a Gemini transcript.

Gemini words are immutable: this stage only annotates them with
``origin="gemini"`` and inserts evidence-backed YouTube words into grammatical
gaps. See CONTRACT.md section 2.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

MIN_MISSING_GAP = 1.5
CAPTION_LOOKBACK = 0.5
CAPTION_LOOKAHEAD = 4.0
KOREAN_PARTICLE_PREFIXES = (
    "이라는", "라는", "이라고", "라고", "의", "와", "과", "을", "를",
)
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}는 숫자여야 합니다: {value!r}")
    return float(value)


def _validate_words(words: Any) -> list[dict[str, Any]]:
    if not isinstance(words, list):
        raise ValueError("transcript.json에 words 배열이 없습니다.")
    validated: list[dict[str, Any]] = []
    previous_start = -1.0
    for index, word in enumerate(words):
        if not isinstance(word, dict) or not str(word.get("text", "")).strip():
            raise ValueError(f"words[{index}]가 올바른 단어가 아닙니다.")
        start = _number(word.get("start"), f"words[{index}].start")
        end = _number(word.get("end"), f"words[{index}].end")
        if start < previous_start or end < start:
            raise ValueError(f"words[{index}]의 timestamp 순서가 잘못되었습니다.")
        previous_start = start
        validated.append(word)
    return validated


def _validate_cues(cues: Any) -> list[dict[str, Any]]:
    if not isinstance(cues, list):
        raise ValueError("captions.json에 cues 배열이 없습니다.")
    validated: list[dict[str, Any]] = []
    previous_start = -1.0
    for index, cue in enumerate(cues):
        if not isinstance(cue, dict):
            raise ValueError(f"cues[{index}]가 객체가 아닙니다.")
        start = _number(cue.get("start"), f"cues[{index}].start")
        end = _number(cue.get("end"), f"cues[{index}].end")
        if start < previous_start or end < start:
            raise ValueError(f"cues[{index}]의 timestamp 순서가 잘못되었습니다.")
        previous_start = start
        validated.append(cue)
    return validated


def _is_particle_fragment(text: str) -> bool:
    compact = text.strip().lstrip(".,!?;:()[]{}\"'")
    return compact.startswith(KOREAN_PARTICLE_PREFIXES)


def _caption_latin_tokens(
    cues: list[dict[str, Any]],
    gap_start: float,
    gap_end: float,
    consumed: set[tuple[int, int]],
) -> list[tuple[int, int, str]]:
    tokens: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    window_start = max(0.0, gap_start - CAPTION_LOOKBACK)
    window_end = gap_end + CAPTION_LOOKAHEAD
    for cue_index, cue in enumerate(cues):
        start = float(cue["start"])
        end = float(cue["end"])
        if start > window_end:
            break
        if end < window_start:
            continue
        for token_index, token in enumerate(
            LATIN_TOKEN_RE.findall(str(cue.get("text", "")))
        ):
            if (cue_index, token_index) in consumed:
                continue
            key = token.casefold()
            if key not in seen:
                seen.add(key)
                tokens.append((cue_index, token_index, token))
    return tokens


def _nearby_gemini_latin(
    words: list[dict[str, Any]], start: float, end: float
) -> set[str]:
    present: set[str] = set()
    for word in words:
        if float(word["start"]) > end:
            break
        if float(word["end"]) < start:
            continue
        present.update(token.casefold() for token in LATIN_TOKEN_RE.findall(str(word["text"])))
    return present


def merge_payloads(
    transcript: dict[str, Any], captions: dict[str, Any]
) -> dict[str, Any]:
    words = _validate_words(transcript.get("words"))
    cues = _validate_cues(captions.get("cues"))
    transcript_id = transcript.get("video_id")
    captions_id = captions.get("video_id")
    if transcript_id and captions_id and transcript_id != captions_id:
        raise ValueError(f"video_id가 다릅니다: {transcript_id!r} != {captions_id!r}")

    output: list[dict[str, Any]] = []
    inserted_count = 0
    consumed_caption_tokens: set[tuple[int, int]] = set()
    for index, word in enumerate(words):
        output.append({**word, "origin": "gemini"})
        if index + 1 >= len(words):
            continue
        following = words[index + 1]
        gap_start = float(word["end"])
        gap_end = float(following["start"])
        if gap_end - gap_start <= MIN_MISSING_GAP:
            continue
        if not _is_particle_fragment(str(following["text"])):
            continue

        candidates = _caption_latin_tokens(
            cues, gap_start, gap_end, consumed_caption_tokens
        )
        present = _nearby_gemini_latin(words, gap_start - 1.0, gap_end + CAPTION_LOOKAHEAD)
        candidates = [
            candidate
            for candidate in candidates
            if candidate[2].casefold() not in present
        ]
        if not candidates:
            continue

        # YouTube rolling cues extend beyond the actual phrase. Keep restored
        # terms inside the missing Gemini interval and preserve source order.
        slot = (gap_end - gap_start) / len(candidates)
        speaker = word.get("speaker") if word.get("speaker") == following.get("speaker") else None
        for position, (cue_index, token_index, token) in enumerate(candidates):
            start = gap_start + position * slot
            end = gap_start + (position + 1) * slot
            output.append({
                "text": token,
                "start": start,
                "end": end,
                "speaker": speaker,
                "origin": "youtube",
            })
            consumed_caption_tokens.add((cue_index, token_index))
            inserted_count += 1

    return {
        "source": "merged",
        "model": transcript.get("model"),
        "language_codes": transcript.get("language_codes"),
        "video_id": transcript_id or captions_id,
        "words": output,
        "merge_stats": {
            "gemini_words": len(words),
            "youtube_words_inserted": inserted_count,
        },
    }


def merge_files(transcript_path: Path, captions_path: Path, output_path: Path) -> dict[str, Any]:
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    captions = json.loads(captions_path.read_text(encoding="utf-8"))
    result = merge_payloads(transcript, captions)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("captions", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("merged.json"))
    args = parser.parse_args()
    result = merge_files(args.transcript, args.captions, args.output)
    stats = result["merge_stats"]
    print(
        f"{args.output}: gemini={stats['gemini_words']} "
        f"youtube_inserted={stats['youtube_words_inserted']}"
    )


if __name__ == "__main__":
    main()
