"""Reconcile per-call speaker labels across overlapping transcript chunks."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

MAX_TIME_DELTA = 0.75
MIN_OVERLAP_VOTES = 2


def _normalized(text: str) -> str:
    return re.sub(r"\W+", "", text, flags=re.UNICODE).casefold()


def _same_word(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        bool(_normalized(str(left.get("text", ""))))
        and _normalized(str(left["text"])) == _normalized(str(right.get("text", "")))
        and abs(float(left["start"]) - float(right["start"])) <= MAX_TIME_DELTA
        and abs(float(left["end"]) - float(right["end"])) <= MAX_TIME_DELTA
    )


def _raw_label(word: dict[str, Any]) -> str | None:
    value = word.get("speaker_raw", word.get("speaker"))
    return str(value) if value is not None else None


def _first_mapping(words: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for word in words:
        raw = _raw_label(word)
        if raw is not None and raw not in mapping:
            mapping[raw] = {
                "global": f"speaker:{len(mapping)}",
                "status": "inferred",
                "evidence": None,
                "votes": 0,
            }
    return mapping


def _overlap_votes(
    existing: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, Counter[str]]:
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for word in current:
        raw = _raw_label(word)
        if raw is None:
            continue
        for previous in existing:
            global_label = previous.get("speaker_global")
            if global_label and _same_word(previous, word):
                votes[raw][str(global_label)] += 1
    return votes


def _reconcile_mapping(
    existing: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    votes = _overlap_votes(existing, current)
    raw_labels = list(dict.fromkeys(raw for word in current if (raw := _raw_label(word))))
    proposals: list[tuple[int, str, str, int]] = []
    mapping: dict[str, dict[str, Any]] = {}
    for raw in raw_labels:
        ranked = votes.get(raw, Counter()).most_common()
        top_votes = ranked[0][1] if ranked else 0
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if ranked and top_votes >= MIN_OVERLAP_VOTES and top_votes > runner_up:
            proposals.append((top_votes, raw, ranked[0][0], runner_up))
        else:
            mapping[raw] = {
                "global": None,
                "status": "unresolved",
                "evidence": None,
                "votes": top_votes,
            }

    # Enforce one-to-one mapping. If two current labels claim one previous
    # speaker, only the stronger unique proposal is accepted.
    claimed: set[str] = set()
    for top_votes, raw, global_label, _ in sorted(proposals, reverse=True):
        if global_label in claimed:
            mapping[raw] = {
                "global": None,
                "status": "unresolved",
                "evidence": None,
                "votes": top_votes,
            }
            continue
        claimed.add(global_label)
        mapping[raw] = {
            "global": global_label,
            "status": "inferred",
            "evidence": "overlap",
            "votes": top_votes,
        }
    return mapping


def _enrich(word: dict[str, Any], mapping: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw = _raw_label(word)
    match = mapping.get(raw) if raw is not None else None
    global_label = match.get("global") if match else None
    return {
        **word,
        "speaker": global_label or raw,
        "speaker_raw": raw,
        "speaker_global": global_label,
        "speaker_status": match.get("status", "unresolved") if match else "unresolved",
        "speaker_evidence": match.get("evidence") if match else None,
    }


def _overlap_bounds(
    previous: dict[str, Any], current: dict[str, Any], words: list[dict[str, Any]]
) -> tuple[float, float]:
    """직전 청크와 겹치는 시각 구간 [lo, hi]. 실 파이프라인은 chunk_start/
    chunk_end 를 넣어준다. 없으면(standalone/test) 관측값으로 폴백한다."""
    hi = previous.get("chunk_end")
    if hi is None:
        hi = max((float(w["end"]) for w in previous.get("words") or []), default=0.0)
    lo = current.get("chunk_start")
    if lo is None:
        lo = min((float(w["start"]) for w in words), default=0.0)
    return float(lo), float(hi)


def reconcile_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    if not chunks:
        raise ValueError("최소 한 개의 chunk transcript가 필요합니다.")
    ordered = sorted(chunks, key=lambda chunk: int(chunk.get("chunk_index", 0)))
    merged: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    duplicates_removed = 0

    for position, chunk in enumerate(ordered):
        words = chunk.get("words")
        if not isinstance(words, list):
            raise ValueError(f"chunk[{position}]에 words 배열이 없습니다.")
        mapping = _first_mapping(words) if position == 0 else _reconcile_mapping(merged, words)
        reports.append({
            "chunk_index": chunk.get("chunk_index", position),
            "labels": mapping,
        })

        # 중복 제거는 직전 청크와 겹치는 재전사 구간에서만 한다 (CONTRACT §8).
        # 첫 청크는 비교 대상이 없고, 인접 반복어(더듬음·열거)는 진짜 중복이
        # 아니므로 여기서 지우면 안 된다.
        if position == 0:
            lo = hi = None
        else:
            lo, hi = _overlap_bounds(ordered[position - 1], chunk, words)
            lo -= MAX_TIME_DELTA
            hi += MAX_TIME_DELTA

        for word in words:
            enriched = _enrich(word, mapping)
            in_overlap = position > 0 and lo <= float(word["start"]) < hi
            if in_overlap and any(
                _same_word(previous, enriched)
                for previous in merged
                if lo <= float(previous["start"]) < hi
            ):
                duplicates_removed += 1
                continue
            merged.append(enriched)
        merged.sort(key=lambda word: (float(word["start"]), float(word["end"])))

    first = ordered[0]
    result = {
        "source": "gemini-chunks-reconciled",
        "model": first.get("model"),
        "language_codes": first.get("language_codes"),
        "video_id": first.get("video_id"),
        "words": merged,
        "speaker_mapping": {
            "chunks": reports,
            "duplicates_removed": duplicates_removed,
        },
    }
    # 청크가 기록한 timestamp 보정 내역은 derived 까지 그대로 들고 간다
    # (CONTRACT §6: 부분 실패를 성공으로 숨기지 않는다).
    repairs = [
        {**item, "chunk_index": chunk.get("chunk_index", position)}
        for position, chunk in enumerate(ordered)
        for item in chunk.get("timestamp_repairs") or []
    ]
    if repairs:
        result["timestamp_repairs"] = repairs
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chunks", type=Path, nargs="+")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in args.chunks]
    result = reconcile_chunks(payloads)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"{args.output}: words={len(result['words'])} "
        f"overlap_removed={result['speaker_mapping']['duplicates_removed']}"
    )


if __name__ == "__main__":
    main()
