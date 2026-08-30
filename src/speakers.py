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
        for word in words:
            enriched = _enrich(word, mapping)
            if any(_same_word(previous, enriched) for previous in merged):
                duplicates_removed += 1
                continue
            merged.append(enriched)
        merged.sort(key=lambda word: (float(word["start"]), float(word["end"])))

    first = ordered[0]
    return {
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
