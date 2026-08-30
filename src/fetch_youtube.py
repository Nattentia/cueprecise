"""Fetch and de-roll YouTube's Korean original automatic captions."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

TIMING_RE = re.compile(r"^(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2},\d{3})")


@dataclass(frozen=True)
class Block:
    start: float
    end: float
    lines: tuple[str, ...]


def _seconds(value: str) -> float:
    hours, minutes, tail = value.split(":")
    seconds, millis = tail.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_srt(path: Path) -> list[Block]:
    blocks: list[Block] = []
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    for chunk in re.split(r"\n\s*\n", raw.strip()):
        rows = chunk.splitlines()
        timing_index = next((i for i, row in enumerate(rows) if TIMING_RE.match(row.strip())), None)
        if timing_index is None:
            continue
        match = TIMING_RE.match(rows[timing_index].strip())
        assert match is not None
        lines = tuple(line.strip() for line in rows[timing_index + 1 :] if line.strip())
        blocks.append(Block(_seconds(match["start"]), _seconds(match["end"]), lines))
    return blocks


def collapse_rolling_lines(blocks: list[Block]) -> list[dict[str, object]]:
    """Collapse each consecutive run of an identical caption line."""
    active: dict[str, tuple[float, float]] = {}
    cues: list[dict[str, object]] = []
    for block in blocks:
        current = set(block.lines)
        for caption in list(active):
            if caption not in current:
                start, end = active.pop(caption)
                cues.append({"start": start, "end": end, "text": caption})
        for caption in dict.fromkeys(block.lines):
            if caption in active:
                active[caption] = (active[caption][0], block.end)
            else:
                active[caption] = (block.start, block.end)
    for caption, (start, end) in active.items():
        cues.append({"start": start, "end": end, "text": caption})
    cues.sort(key=lambda cue: (float(cue["start"]), float(cue["end"])))
    return cues


def fetch(url: str, output: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ytx-captions-") as directory:
        target = Path(directory) / "%(id)s.%(ext)s"
        command = ["yt-dlp", "--write-auto-sub", "--sub-lang", "ko-orig", "--skip-download", "--convert-subs", "srt", "-o", str(target), url]
        subprocess.run(command, check=True)
        candidates = list(Path(directory).glob("*.ko-orig*.srt"))
        if not candidates:
            raise FileNotFoundError("ko-orig 자동자막을 내려받지 못했습니다.")
        if len(candidates) != 1:
            raise RuntimeError(f"예상하지 못한 자막 파일 수: {len(candidates)}")
        video_id = candidates[0].name.split(".ko-orig", 1)[0]
        cues = collapse_rolling_lines(parse_srt(candidates[0]))
    result: dict[str, object] = {"source": "youtube-ko-orig", "video_id": video_id, "cues": cues}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument("-o", "--output", type=Path, default=Path("captions.json"))
    args = parser.parse_args()
    result = fetch(args.url, args.output)
    print(f"{len(result['cues'])} cues -> {args.output}")


if __name__ == "__main__":
    main()
