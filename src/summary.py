"""Create an on-demand persistent summary with an optional host-written upgrade.

The local extractive summary is always complete enough to return immediately.  A
host may replace its prose through structured fields, but cannot provide timestamps
or alter chapter boundaries.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import chapters

SCHEMA_VERSION = 1
MAX_OVERVIEW_CHARS = 1600
MAX_POINT_CHARS = 600
MAX_BULLET_CHARS = 600
MAX_TERM_CHARS = 100
MAX_MEANING_CHARS = 600
MAX_POINTS = 12
MAX_BULLETS_PER_CHAPTER = 3
MAX_TERMS = 20
META_PREFIX = "<!-- ytx-summary:"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _chapter_fingerprint(payload: dict[str, Any]) -> str:
    stable = {
        "transcript_fingerprint": payload.get("transcript_fingerprint"),
        "chapters": [
            {"id": item.get("id"), "start": item.get("start"), "end": item.get("end"),
             "title": item.get("title"), "keywords": item.get("keywords"),
             "excerpts": item.get("excerpts")}
            for item in payload.get("chapters") or []
        ],
    }
    return _hash_json(stable)


def _fmt(seconds: float) -> str:
    total = int(round(seconds))
    return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)


def _title(bundle: Path, video_id: str) -> str:
    metadata = bundle / "raw" / "youtube.json"
    if metadata.exists():
        try:
            value = str(_read(metadata).get("title") or "").strip()
            if value:
                return value
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return video_id


def _metadata_line(metadata: dict[str, Any]) -> str:
    return META_PREFIX + " " + json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + " -->"


def read_metadata(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0].strip()
        if not first.startswith(META_PREFIX) or not first.endswith("-->"):
            return None
        return json.loads(first[len(META_PREFIX):-3].strip())
    except (OSError, IndexError, json.JSONDecodeError):
        return None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".summary.", suffix=".md",
                                                   dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _clean(value: Any, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise ValueError(f"유효하지 않은 {field}")
    return text


def _render(*, title: str, metadata: dict[str, Any], overview: str,
            key_points: list[dict[str, Any]],
            chapter_summaries: list[dict[str, Any]],
            terms: list[dict[str, Any]], chapters_by_id: dict[str, dict[str, Any]]) -> str:
    lines = [_metadata_line(metadata), "", f"# {title}", "", "## 한눈에 보기", "", overview,
             "", "## 핵심 내용", ""]
    for point in key_points:
        first = chapters_by_id[point["chapter_ids"][0]]
        lines.append(f"- {point['text']} [{_fmt(float(first['start']))}]")
    lines += ["", "## 챕터별 정리", ""]
    for position, item in enumerate(chapter_summaries, 1):
        chapter = chapters_by_id[item["id"]]
        lines.append(f"### {position}. {chapter['title']} [{_fmt(float(chapter['start']))}]")
        lines.append("")
        lines.extend(f"- {bullet}" for bullet in item["bullets"])
        lines.append("")
    if terms:
        lines += ["## 주요 용어", ""]
        for item in terms:
            first = chapters_by_id[item["chapter_ids"][0]]
            lines.append(f"- **{item['term']}**: {item['meaning']} "
                         f"[{_fmt(float(first['start']))}]")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _local_content(chapter_items: list[dict[str, Any]]) -> dict[str, Any]:
    excerpts = [(item, str(text).strip()) for item in chapter_items
                for text in (item.get("excerpts") or []) if str(text).strip()]
    overview_parts = [text for _, text in excerpts[:2]]
    overview = " ".join(overview_parts) or "이 영상의 내용을 시간순 챕터로 정리했다."
    overview = overview[:MAX_OVERVIEW_CHARS].rstrip()

    if len(chapter_items) <= MAX_POINTS:
        selected = chapter_items
    else:
        selected = [chapter_items[round(i * (len(chapter_items) - 1) / (MAX_POINTS - 1))]
                    for i in range(MAX_POINTS)]
    key_points = []
    for item in selected:
        text = next((str(value).strip() for value in item.get("excerpts") or []
                     if str(value).strip()), str(item["title"]))
        key_points.append({"text": text[:MAX_POINT_CHARS].rstrip(),
                           "chapter_ids": [item["id"]]})

    chapter_summaries = []
    for item in chapter_items:
        bullets = [str(value).strip()[:MAX_BULLET_CHARS].rstrip()
                   for value in (item.get("excerpts") or [])[:2] if str(value).strip()]
        chapter_summaries.append({"id": item["id"],
                                  "bullets": bullets or [str(item["title"])]})

    occurrences = Counter(keyword for item in chapter_items
                          for keyword in set(item.get("keywords") or []))
    terms = []
    for term, count in occurrences.most_common(MAX_TERMS):
        if count < 2:
            continue
        related = [item["id"] for item in chapter_items
                   if term in (item.get("keywords") or [])]
        terms.append({"term": term, "meaning": "영상에서 반복해서 다루는 핵심 용어",
                      "chapter_ids": related})
    return {"overview": overview, "key_points": key_points,
            "chapter_summaries": chapter_summaries, "terms": terms}


def _validate_content(content: dict[str, Any], chapter_items: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["id"]: item for item in chapter_items}

    def ids(raw: Any, field: str) -> list[str]:
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{field}의 chapter_ids가 비었습니다.")
        result = [str(value) for value in raw]
        if len(result) != len(set(result)) or any(value not in by_id for value in result):
            raise ValueError(f"{field}의 chapter_ids가 유효하지 않습니다.")
        return result

    overview = _clean(content.get("overview"), field="overview", maximum=MAX_OVERVIEW_CHARS)
    raw_points = content.get("key_points")
    if not isinstance(raw_points, list) or not 1 <= len(raw_points) <= MAX_POINTS:
        raise ValueError("key_points 개수가 유효하지 않습니다.")
    points = [{"text": _clean(item.get("text"), field="key point", maximum=MAX_POINT_CHARS),
               "chapter_ids": ids(item.get("chapter_ids"), "key point")}
              for item in raw_points if isinstance(item, dict)]
    if len(points) != len(raw_points):
        raise ValueError("key_points 형식이 유효하지 않습니다.")

    raw_chapters = content.get("chapter_summaries")
    if not isinstance(raw_chapters, list):
        raise ValueError("chapter_summaries가 배열이 아닙니다.")
    received = [str(item.get("id")) for item in raw_chapters if isinstance(item, dict)]
    expected = [item["id"] for item in chapter_items]
    if received != expected:
        raise ValueError("chapter_summaries는 모든 chapter id를 원래 순서대로 포함해야 합니다.")
    summaries = []
    for item in raw_chapters:
        bullets = item.get("bullets")
        if not isinstance(bullets, list) or not 1 <= len(bullets) <= MAX_BULLETS_PER_CHAPTER:
            raise ValueError(f"{item['id']}의 bullets 개수가 유효하지 않습니다.")
        summaries.append({"id": item["id"],
                          "bullets": [_clean(value, field="bullet", maximum=MAX_BULLET_CHARS)
                                      for value in bullets]})

    raw_terms = content.get("terms") or []
    if not isinstance(raw_terms, list) or len(raw_terms) > MAX_TERMS:
        raise ValueError("terms 개수가 유효하지 않습니다.")
    terms = []
    for item in raw_terms:
        if not isinstance(item, dict):
            raise ValueError("term 형식이 유효하지 않습니다.")
        terms.append({"term": _clean(item.get("term"), field="term", maximum=MAX_TERM_CHARS),
                      "meaning": _clean(item.get("meaning"), field="meaning",
                                        maximum=MAX_MEANING_CHARS),
                      "chapter_ids": ids(item.get("chapter_ids"), "term")})
    return {"overview": overview, "key_points": points,
            "chapter_summaries": summaries, "terms": terms}


def _ensure_chapters(bundle: Path) -> dict[str, Any]:
    path = bundle / "derived" / "chapters.json"
    if path.exists():
        payload = _read(path)
        if payload.get("transcript_fingerprint") == chapters.transcript_fingerprint(bundle):
            return payload
    return chapters.build(bundle)


def build(bundle: Path) -> dict[str, Any]:
    """Return a current summary, creating a local fallback only on demand."""
    chapter_payload = _ensure_chapters(bundle)
    chapter_items = chapter_payload.get("chapters") or []
    if not chapter_items:
        raise ValueError("요약할 chapter가 없습니다.")
    video_id = str(chapter_payload.get("video_id") or bundle.name)
    chapter_fingerprint = _chapter_fingerprint(chapter_payload)
    path = bundle / "derived" / "summary.md"
    stored = read_metadata(path)
    if (stored and stored.get("transcript_fingerprint") ==
            chapter_payload.get("transcript_fingerprint")
            and stored.get("chapters_fingerprint") == chapter_fingerprint):
        generation = stored.get("generation") or "local-extractive"
        return {"video_id": video_id, "path": str(path), "generation": generation,
                "fingerprint": chapter_fingerprint, "summary": path.read_text(encoding="utf-8"),
                "needs_host_summary": generation != "host-llm",
                "packet": packet(chapter_payload) if generation != "host-llm" else None}

    content = _local_content(chapter_items)
    metadata = {"schema_version": SCHEMA_VERSION, "video_id": video_id,
                "transcript_fingerprint": chapter_payload["transcript_fingerprint"],
                "chapters_fingerprint": chapter_fingerprint,
                "generation": "local-extractive"}
    rendered = _render(title=_title(bundle, video_id), metadata=metadata, **content,
                       chapters_by_id={item["id"]: item for item in chapter_items})
    _atomic_write(path, rendered)
    return {"video_id": video_id, "path": str(path), "generation": "local-extractive",
            "fingerprint": chapter_fingerprint, "summary": rendered,
            "needs_host_summary": True, "packet": packet(chapter_payload)}


def packet(chapter_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": item["id"], "start": item["start"], "end": item["end"],
             "title": item["title"], "keywords": item.get("keywords") or [],
             "excerpts": item.get("excerpts") or []}
            for item in chapter_payload.get("chapters") or []]


def set_host_summary(bundle: Path, *, fingerprint: str, content: dict[str, Any]) -> dict[str, Any]:
    chapter_payload = _ensure_chapters(bundle)
    current = _chapter_fingerprint(chapter_payload)
    if fingerprint != current:
        raise ValueError("전사 또는 chapter가 변경됐습니다. summary를 다시 요청하세요.")
    chapter_items = chapter_payload.get("chapters") or []
    clean = _validate_content(content, chapter_items)
    video_id = str(chapter_payload.get("video_id") or bundle.name)
    metadata = {"schema_version": SCHEMA_VERSION, "video_id": video_id,
                "transcript_fingerprint": chapter_payload["transcript_fingerprint"],
                "chapters_fingerprint": current, "generation": "host-llm"}
    rendered = _render(title=_title(bundle, video_id), metadata=metadata, **clean,
                       chapters_by_id={item["id"]: item for item in chapter_items})
    path = bundle / "derived" / "summary.md"
    _atomic_write(path, rendered)
    return {"video_id": video_id, "path": str(path), "generation": "host-llm",
            "fingerprint": current, "summary": rendered, "needs_host_summary": False}
