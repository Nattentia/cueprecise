"""Plan and extract overlap-safe MP3 chunks for long transcription jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_CHUNK_MAX_SECS = 1790.0
DEFAULT_OVERLAP_SECS = 10.0


def probe_duration(path: Path) -> float:
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    try:
        duration = float(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError(f"ffprobe duration을 읽지 못했습니다: {result.stdout!r}") from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"오디오 길이는 양수여야 합니다: {duration}")
    return duration


def plan_chunks(
    total_secs: float,
    chunk_max_secs: float = DEFAULT_CHUNK_MAX_SECS,
    overlap_secs: float = DEFAULT_OVERLAP_SECS,
) -> list[dict[str, Any]]:
    if not math.isfinite(total_secs) or total_secs <= 0:
        raise ValueError("total_secs는 양수여야 합니다.")
    if not math.isfinite(chunk_max_secs) or chunk_max_secs <= 0:
        raise ValueError("chunk_max_secs는 양수여야 합니다.")
    if not math.isfinite(overlap_secs) or overlap_secs < 0:
        raise ValueError("overlap_secs는 0 이상이어야 합니다.")
    if overlap_secs >= chunk_max_secs:
        raise ValueError("overlap_secs는 chunk_max_secs보다 작아야 합니다.")

    count = max(1, math.ceil(total_secs / chunk_max_secs))
    # ceil(total/max) can still exceed max after overlap is added. Increase the
    # count until the real extracted duration satisfies the hard ceiling.
    while count > 1 and total_secs / count + overlap_secs > chunk_max_secs:
        count += 1

    core_secs = total_secs / count
    chunks: list[dict[str, Any]] = []
    for index in range(count):
        core_start = index * core_secs
        start = core_start if index == 0 else max(0.0, core_start - overlap_secs)
        end = total_secs if index == count - 1 else (index + 1) * core_secs
        if end - start > chunk_max_secs + 1e-6:
            raise AssertionError("계획된 청크가 chunk_max_secs를 초과했습니다.")
        chunks.append({
            "index": index,
            "start": round(start, 6),
            "end": round(end, 6),
            "path": f"raw/audio/chunk-{index:03d}.mp3",
            "status": "planned",
            "attempts": 0,
            "transcript_path": f"raw/transcripts/chunk-{index:03d}.json",
            "error": None,
        })
    return chunks


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def extract_chunks(input_path: Path, bundle_root: Path, chunks: list[dict[str, Any]]) -> None:
    for chunk in chunks:
        output = bundle_root / str(chunk["path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        duration = float(chunk["end"]) - float(chunk["start"])
        command = [
            "ffmpeg", "-v", "error", "-y", "-ss", str(chunk["start"]),
            "-i", str(input_path), "-t", str(duration), "-vn", "-ac", "1",
            "-ar", "16000", "-b:a", "64k", str(output),
        ]
        subprocess.run(command, check=True)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def create_job(
    input_path: Path,
    bundle_root: Path,
    video_id: str,
    source: str,
    *,
    chunk_max_secs: float = DEFAULT_CHUNK_MAX_SECS,
    overlap_secs: float = DEFAULT_OVERLAP_SECS,
    language_codes: list[str] | None = None,
    diarization: bool = True,
) -> dict[str, Any]:
    duration = probe_duration(input_path)
    chunks = plan_chunks(duration, chunk_max_secs, overlap_secs)
    extract_chunks(input_path, bundle_root, chunks)
    job = {
        "schema_version": 1,
        "video_id": video_id,
        "input": {"source": source, "fingerprint": file_fingerprint(input_path)},
        "config": {
            "chunk_max_secs": chunk_max_secs,
            "overlap_secs": overlap_secs,
            "language_codes": language_codes,
            "diarization": diarization,
        },
        "status": "planned",
        "chunks": chunks,
    }
    _atomic_json(bundle_root / "job.json", job)
    return job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--source")
    parser.add_argument("--chunk-max-secs", type=float, default=DEFAULT_CHUNK_MAX_SECS)
    parser.add_argument("--overlap-secs", type=float, default=DEFAULT_OVERLAP_SECS)
    args = parser.parse_args()
    job = create_job(
        args.input, args.bundle, args.video_id, args.source or str(args.input),
        chunk_max_secs=args.chunk_max_secs, overlap_secs=args.overlap_secs,
    )
    print(f"{args.bundle / 'job.json'}: {len(job['chunks'])} chunks")


if __name__ == "__main__":
    main()
