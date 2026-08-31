"""Build and query a persistent SQLite evidence index for one video bundle."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable

SPAN_MAX_SECS = 30.0
SPAN_MAX_GAP = 2.5

SPEAKER_CONFIDENCE = {
    "confirmed": 1.0,
    "inferred": 0.75,
    "unresolved": 0.0,
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _transcript_spans(payload: dict[str, Any], source_path: str) -> Iterable[dict[str, Any]]:
    words = payload.get("words")
    if not isinstance(words, list):
        raise ValueError(f"{source_path}에 words 배열이 없습니다.")
    current: list[dict[str, Any]] = []

    def finish() -> dict[str, Any] | None:
        if not current:
            return None
        candidate = current[0].get("speaker_global") or current[0].get("speaker")
        statuses = {str(word.get("speaker_status") or "unresolved") for word in current}
        if "unresolved" in statuses:
            speaker_status = "unresolved"
        elif "inferred" in statuses:
            speaker_status = "inferred"
        else:
            speaker_status = "confirmed"
        return {
            "start": float(current[0]["start"]),
            "end": float(current[-1]["end"]),
            "text": " ".join(str(word["text"]).strip() for word in current),
            # 불확실한 라벨은 내용 검색에서 확정 화자로 주장하지 않는다.
            # 원래 라벨은 candidate 로 보존해 디버깅과 수동 확인에 쓴다.
            "speaker": candidate if speaker_status != "unresolved" else None,
            "speaker_candidate": candidate,
            "speaker_status": speaker_status,
            "speaker_confidence": SPEAKER_CONFIDENCE[speaker_status],
            "source_path": source_path,
            "source_kind": "transcript",
            "confidence": 1.0,
        }

    for word in words:
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        start = float(word["start"])
        end = float(word["end"])
        if end < start:
            raise ValueError(f"역전된 word timestamp: {text!r}")
        if current:
            previous = current[-1]
            previous_speaker = previous.get("speaker_global") or previous.get("speaker")
            speaker = word.get("speaker_global") or word.get("speaker")
            if (
                start - float(previous["end"]) > SPAN_MAX_GAP
                or end - float(current[0]["start"]) > SPAN_MAX_SECS
                or speaker != previous_speaker
            ):
                span = finish()
                if span:
                    yield span
                current = []
        current.append(word)
    span = finish()
    if span:
        yield span


def _optional_records(bundle: Path, filename: str, key: str, kind: str) -> Iterable[dict[str, Any]]:
    path = bundle / "derived" / filename
    if not path.exists():
        return
    payload = _read_json(path)
    records = payload.get(key, []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"{path}의 {key}가 배열이 아닙니다.")
    for record in records:
        text = str(record.get("text") or record.get("title") or record.get("ocr_text") or "").strip()
        if not text:
            continue
        start = float(record.get("start", record.get("timestamp", 0.0)))
        end = float(record.get("end", start))
        yield {
            "start": start,
            "end": end,
            "text": text,
            "speaker": record.get("speaker"),
            "speaker_candidate": record.get("speaker"),
            "speaker_status": None,
            "speaker_confidence": None,
            "source_path": str(path.relative_to(bundle)).replace("\\", "/"),
            "source_kind": kind,
            "confidence": float(record.get("confidence", 1.0)),
        }


SUMMARY_KEY = "summary"


def read_summary(bundle: Path) -> str | None:
    """색인에 보관한 요약. 없으면 None."""
    index_path = bundle / "index.sqlite3"
    if not index_path.exists():
        return None
    try:
        connection = sqlite3.connect(index_path)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (SUMMARY_KEY,)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return row[0] if row else None


def write_summary(bundle: Path, payload: str) -> None:
    """요약을 색인 metadata 에 넣는다. 파일을 따로 만들지 않는다."""
    index_path = bundle / "index.sqlite3"
    if not index_path.exists():
        raise FileNotFoundError("index.sqlite3 가 없습니다. index 단계를 먼저 실행하세요.")
    connection = sqlite3.connect(index_path)
    try:
        connection.execute(
            "INSERT INTO metadata VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SUMMARY_KEY, payload),
        )
        connection.commit()
    finally:
        connection.close()


def build_index(bundle: Path) -> Path:
    transcript_path = bundle / "derived" / "merged.json"
    if not transcript_path.exists():
        transcript_path = bundle / "derived" / "transcript.json"
    if not transcript_path.exists():
        raise FileNotFoundError("derived/merged.json 또는 derived/transcript.json이 없습니다.")
    transcript = _read_json(transcript_path)
    video_id = str(transcript.get("video_id") or bundle.name)
    relative_transcript = str(transcript_path.relative_to(bundle)).replace("\\", "/")
    records = list(_transcript_spans(transcript, relative_transcript))
    records.extend(_optional_records(bundle, "chapters.json", "chapters", "chapter"))
    records.extend(_optional_records(bundle, "frames.json", "frames", "frame"))

    index_path = bundle / "index.sqlite3"
    # 색인은 임시 파일에 새로 만들어 통째로 교체한다. 요약은 전사에서 다시
    # 만들어낼 수 없는 자료(호스트가 쓴 문장)라, 교체 전에 읽어 두지 않으면
    # 재색인 한 번에 조용히 사라진다.
    carried_summary = read_summary(bundle)
    bundle.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".index.", suffix=".sqlite3", dir=bundle)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(
                """
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE evidence (
                    id INTEGER PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    start REAL NOT NULL,
                    end REAL NOT NULL,
                    text TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    speaker TEXT,
                    speaker_candidate TEXT,
                    speaker_status TEXT,
                    speaker_confidence REAL,
                    confidence REAL NOT NULL
                );
                CREATE INDEX evidence_time ON evidence(video_id, start);
                CREATE INDEX evidence_kind ON evidence(video_id, source_kind);
                """
            )
            connection.execute("INSERT INTO metadata VALUES (?, ?)", ("schema_version", "2"))
            connection.execute("INSERT INTO metadata VALUES (?, ?)", ("video_id", video_id))
            if carried_summary is not None:
                connection.execute("INSERT INTO metadata VALUES (?, ?)",
                                   (SUMMARY_KEY, carried_summary))
            connection.executemany(
                """INSERT INTO evidence
                (video_id, start, end, text, source_path, source_kind, speaker,
                 speaker_candidate, speaker_status, speaker_confidence, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        video_id, record["start"], record["end"], record["text"],
                        record["source_path"], record["source_kind"], record["speaker"],
                        record["speaker_candidate"], record["speaker_status"],
                        record["speaker_confidence"],
                        record["confidence"],
                    )
                    for record in records
                ],
            )
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, index_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return index_path


def search(index_path: Path, query: str, limit: int = 8) -> list[dict[str, Any]]:
    terms = list(dict.fromkeys(re.findall(r"[0-9A-Za-z가-힣_-]+", query.casefold())))
    if not terms or limit < 1:
        return []
    clauses = " OR ".join("lower(text) LIKE ?" for _ in terms)
    parameters = [f"%{term}%" for term in terms]
    connection = sqlite3.connect(index_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"SELECT * FROM evidence WHERE {clauses}", parameters  # noqa: S608 - fixed clauses
        ).fetchall()
    finally:
        connection.close()
    ranked = sorted(
        rows,
        key=lambda row: (
            -sum(row["text"].casefold().count(term) for term in terms),
            -float(row["confidence"]),
            float(row["start"]),
        ),
    )[:limit]
    return [
        {
            "video_id": row["video_id"],
            "start": row["start"],
            "end": row["end"],
            "text": row["text"],
            "source_path": row["source_path"],
            "source_kind": row["source_kind"],
            "confidence": row["confidence"],
            "speaker": row["speaker"],
            "speaker_candidate": row["speaker_candidate"] if "speaker_candidate" in row.keys() else row["speaker"],
            "speaker_status": row["speaker_status"] if "speaker_status" in row.keys() else None,
            "speaker_confidence": row["speaker_confidence"] if "speaker_confidence" in row.keys() else None,
        }
        for row in ranked
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("bundle", type=Path)
    find = subparsers.add_parser("search")
    find.add_argument("index", type=Path)
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    if args.command == "build":
        print(build_index(args.bundle))
    else:
        print(json.dumps(search(args.index, args.query, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
