"""Build deterministic video chapters and accept optional host-written titles.

Boundaries and evidence are always local.  An MCP host may replace only titles;
if it never does, the keyword/extractive fallback remains a complete result.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

TARGET_SECS = 300.0
MIN_SECS = 120.0
MAX_SECS = 480.0
SEARCH_SECS = 60.0
MAX_TITLE_CHARS = 100

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣_+.#-]*")
_END_RE = re.compile(r"[.!?。！？요다][\"'’”)]*$")
_STOP = {
    "the", "and", "for", "are", "was", "were", "has", "had", "not", "but", "can",
    "of", "to", "is", "we", "in", "on", "at", "by", "it", "as", "be", "or", "if",
    "you", "your", "our", "out", "all", "its", "it's", "their", "than", "also",
    "that", "this", "with", "from", "have", "will", "about", "there", "here",
    "what", "when", "where", "which", "they", "them", "then", "just", "into",
    "would", "could", "should", "really", "very", "some", "more", "most", "like",
    "how", "why", "who", "been", "being", "does", "did", "get", "got", "use",
    "one", "two", "way", "thing", "things", "think", "know", "want", "going",
    "yes", "yeah", "okay", "well", "right", "actually", "basically", "because",
    "so", "do", "an", "these", "those", "up", "us", "kind", "much", "many",
    "um", "uh", "he", "she", "see", "lots", "might", "mean", "said", "say",
    "그리고", "그런데", "그러면", "이것", "저것", "하는", "있는", "없는", "대해서",
    "합니다", "됩니다", "입니다", "제가", "우리가", "여러분", "지금", "이제",
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _transcript_path(bundle: Path) -> Path:
    for name in ("merged.json", "transcript.json"):
        path = bundle / "derived" / name
        if path.exists():
            return path
    raise FileNotFoundError("derived/merged.json 또는 derived/transcript.json이 없습니다.")


def _fingerprint(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def transcript_fingerprint(bundle: Path) -> str:
    return _fingerprint(_transcript_path(bundle))


def _youtube_metadata(bundle: Path, url: str | None) -> dict[str, Any]:
    path = bundle / "raw" / "youtube.json"
    if path.exists():
        try:
            return _read(path)
        except (OSError, json.JSONDecodeError):
            pass
    if not url:
        return {}
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-single-json", "--skip-download", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    # 거대한 포맷 목록 등은 버리고 chapter 생성에 필요한 불변 메타데이터만 둔다.
    metadata = {
        "video_id": raw.get("id"),
        "title": raw.get("title"),
        "duration": raw.get("duration"),
        "chapters": raw.get("chapters") or [],
    }
    _write(path, metadata)
    return metadata


def _native_groups(native: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Keep useful native chapters; group tiny runs and flag oversized spans."""
    valid = []
    for item in native:
        try:
            start = max(0.0, float(item["start_time"]))
            end = min(duration, float(item["end_time"]))
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            valid.append({"start": start, "end": end, "title": str(item.get("title") or "").strip()})
    valid.sort(key=lambda item: item["start"])
    if valid:
        # YouTube metadata가 intro/outro를 생략해도 전체 영상을 덮는다.
        valid[0]["start"] = 0.0
        for position in range(len(valid) - 1):
            valid[position]["end"] = valid[position + 1]["start"]
        valid[-1]["end"] = duration
    groups: list[dict[str, Any]] = []
    index = 0
    while index < len(valid):
        item = valid[index]
        span = item["end"] - item["start"]
        if MIN_SECS <= span <= MAX_SECS:
            groups.append({**item, "source_title": item["title"] or None})
            index += 1
            continue
        if span > MAX_SECS:
            groups.append({**item, "source_title": None})
            index += 1
            continue
        start, end = item["start"], item["end"]
        count = 1
        index += 1
        while index < len(valid) and end - start < MIN_SECS:
            candidate = valid[index]
            if candidate["end"] - start > MAX_SECS:
                break
            end = candidate["end"]
            count += 1
            index += 1
        groups.append({"start": start, "end": end,
                       "source_title": item["title"] if count == 1 and item["title"] else None})
    # 끝에 남은 짧은 chapter도 단독으로 두지 않는다. native title보다 탐색 가능한
    # 길이 하한이 우선이며, 병합한 구간은 host가 새 제목을 짓는다.
    normalized: list[dict[str, Any]] = []
    for group in groups:
        if normalized and group["end"] - group["start"] < MIN_SECS \
                and group["end"] - normalized[-1]["start"] <= MAX_SECS:
            normalized[-1]["end"] = group["end"]
            normalized[-1]["source_title"] = None
        else:
            normalized.append(group)
    if len(normalized) > 1 and normalized[0]["end"] - normalized[0]["start"] < MIN_SECS \
            and normalized[1]["end"] - normalized[0]["start"] <= MAX_SECS:
        normalized[1]["start"] = normalized[0]["start"]
        normalized[1]["source_title"] = None
        normalized.pop(0)
    return normalized


def _boundary(words: list[dict[str, Any]], target: float, lo: float, hi: float) -> float:
    candidates = []
    for index in range(1, len(words)):
        start = float(words[index]["start"])
        if start < max(lo + MIN_SECS, target - SEARCH_SECS):
            continue
        if start > min(hi - MIN_SECS, target + SEARCH_SECS):
            break
        previous = words[index - 1]
        gap = max(0.0, start - float(previous["end"]))
        punctuation = 1.0 if _END_RE.search(str(previous.get("text", ""))) else 0.0
        distance = abs(start - target) / SEARCH_SECS
        score = punctuation * 2.0 + min(gap, 3.0) - distance
        candidates.append((score, start))
    return max(candidates)[1] if candidates else min(hi, max(lo, target))


def _split_span(words: list[dict[str, Any]], start: float, end: float) -> list[tuple[float, float]]:
    if end - start <= MAX_SECS:
        return [(start, end)]
    count = max(2, math.ceil((end - start) / TARGET_SECS))
    pieces = [start]
    for position in range(1, count):
        ideal = start + (end - start) * position / count
        pieces.append(_boundary(words, ideal, pieces[-1], end))
    pieces.append(end)
    return [(pieces[i], pieces[i + 1]) for i in range(len(pieces) - 1)
            if pieces[i + 1] > pieces[i]]


def _sentences(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result, current = [], []
    for word in words:
        if current and float(word["start"]) - float(current[-1]["end"]) > 1.2:
            result.append(current)
            current = []
        current.append(word)
        if _END_RE.search(str(word.get("text", ""))) or len(current) >= 35:
            result.append(current)
            current = []
    if current:
        result.append(current)
    return [{"start": float(group[0]["start"]), "end": float(group[-1]["end"]),
             "text": " ".join(str(word["text"]).strip() for word in group)}
            for group in result if group]


def _tokens(text: str) -> list[str]:
    normalized = (token.strip("._+#-").casefold() for token in _TOKEN_RE.findall(text))
    return [token for token in normalized
            if len(token) >= 2 and token not in _STOP and not token.isdigit()]


def _evidence(words: list[dict[str, Any]], start: float, end: float, *,
              document_frequency: Counter[str], document_count: int) -> tuple[list[str], list[str]]:
    segment = [word for word in words if start <= float(word["start"]) < end]
    sentences = _sentences(segment)
    counts = Counter(token for sentence in sentences for token in _tokens(sentence["text"]))
    ranked_terms = sorted(
        counts,
        key=lambda token: (
            -counts[token] * (1.0 + math.log((document_count + 1)
                                             / (document_frequency[token] + 1))),
            token,
        ),
    )
    keywords = ranked_terms[:5]
    keyword_set = set(keywords)
    ranked = sorted(
        sentences,
        key=lambda sentence: (
            -sum(token in keyword_set for token in _tokens(sentence["text"])),
            abs((sentence["start"] + sentence["end"]) / 2 - (start + end) / 2),
        ),
    )
    excerpts = [sentence["text"][:360] for sentence in ranked[:2] if sentence["text"].strip()]
    return keywords, excerpts


def _fallback_title(keywords: list[str], excerpts: list[str], index: int) -> str:
    if keywords:
        return " · ".join(keywords[:4])[:MAX_TITLE_CHARS]
    if excerpts:
        return excerpts[0][:MAX_TITLE_CHARS].rstrip()
    return f"구간 {index + 1}"


def build(bundle: Path, *, url: str | None = None) -> dict[str, Any]:
    transcript_path = _transcript_path(bundle)
    transcript = _read(transcript_path)
    fingerprint = _fingerprint(transcript_path)
    words = transcript.get("words") or []
    if not words:
        raise ValueError("전사에 단어가 없습니다.")
    duration = float(words[-1]["end"])
    metadata = _youtube_metadata(bundle, url)
    native_groups = _native_groups(metadata.get("chapters") or [], duration)
    groups = native_groups or [{"start": float(words[0]["start"]), "end": duration,
                                "source_title": None}]

    spans: list[dict[str, Any]] = []
    for group in groups:
        pieces = _split_span(words, float(group["start"]), float(group["end"]))
        for start, end in pieces:
            exact = len(pieces) == 1 and group.get("source_title")
            spans.append({"start": start, "end": end,
                          "source_title": group.get("source_title") if exact else None})

    document_frequency: Counter[str] = Counter()
    for span in spans:
        text = " ".join(str(word["text"]) for word in words
                        if span["start"] <= float(word["start"]) < span["end"])
        document_frequency.update(set(_tokens(text)))

    chapters = []
    for index, span in enumerate(spans):
        keywords, excerpts = _evidence(
            words, span["start"], span["end"],
            document_frequency=document_frequency, document_count=len(spans),
        )
        source_title = span.get("source_title")
        chapters.append({
            "id": f"chapter-{index + 1:02d}",
            "start": round(span["start"], 3),
            "end": round(span["end"], 3),
            "title": source_title or _fallback_title(keywords, excerpts, index),
            "title_source": "youtube" if source_title else "local-keywords",
            "keywords": keywords,
            "excerpts": excerpts,
            "needs_title": not bool(source_title),
        })
    # 같은 전사와 같은 경계라면 이미 확정한 host title을 잃지 않는다.
    existing_path = bundle / "derived" / "chapters.json"
    if existing_path.exists():
        try:
            existing = _read(existing_path)
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("transcript_fingerprint") == fingerprint:
            previous = {item.get("id"): item for item in existing.get("chapters") or []}
            for item in chapters:
                old = previous.get(item["id"])
                if (old and old.get("title_source") == "host-llm"
                        and float(old.get("start", -1)) == item["start"]
                        and float(old.get("end", -1)) == item["end"]):
                    item["title"] = old["title"]
                    item["title_source"] = "host-llm"
                    item["needs_title"] = False
    payload = {
        "schema_version": 1,
        "video_id": transcript.get("video_id") or bundle.name,
        "transcript_fingerprint": fingerprint,
        "generation": {"boundaries": "youtube+local" if native_groups else "local",
                       "titles": "host-llm+fallback" if any(
                           item["title_source"] == "host-llm" for item in chapters
                       ) else "youtube+local-keywords",
                       "quality": "enhanced" if any(
                           item["title_source"] == "host-llm" for item in chapters
                       ) else "provisional"},
        "chapters": chapters,
    }
    _write(bundle / "derived" / "chapters.json", payload)
    return payload


def set_titles(bundle: Path, *, fingerprint: str,
               titles: list[dict[str, Any]]) -> dict[str, Any]:
    path = bundle / "derived" / "chapters.json"
    if not path.exists():
        raise FileNotFoundError("derived/chapters.json이 없습니다. 먼저 outline을 생성하세요.")
    payload = _read(path)
    if fingerprint != payload.get("transcript_fingerprint"):
        raise ValueError("전사가 변경되어 chapter 후보가 오래됐습니다. outline을 다시 생성하세요.")
    by_id = {chapter["id"]: chapter for chapter in payload["chapters"]}
    seen: set[str] = set()
    for item in titles:
        chapter_id = str(item.get("id") or "")
        title = str(item.get("title") or "").strip()
        if chapter_id not in by_id:
            raise ValueError(f"알 수 없는 chapter id: {chapter_id}")
        if chapter_id in seen:
            raise ValueError(f"중복 chapter id: {chapter_id}")
        if not title or len(title) > MAX_TITLE_CHARS or any(ord(char) < 32 for char in title):
            raise ValueError(f"유효하지 않은 title: {chapter_id}")
        seen.add(chapter_id)
        by_id[chapter_id]["title"] = title
        by_id[chapter_id]["title_source"] = "host-llm"
        by_id[chapter_id]["needs_title"] = False
    payload["generation"]["titles"] = "host-llm+fallback"
    payload["generation"]["quality"] = "enhanced" if seen else "provisional"
    _write(path, payload)
    return payload
