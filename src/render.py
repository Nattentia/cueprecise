"""Render any JSON containing a words array as lossless SRT and text."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_GAP = 0.65
MAX_DURATION = 7.0
MAX_LINES = 2


@dataclass
class Cue:
    words: list[dict[str, Any]]
    lines: list[str]

    @property
    def start(self) -> float:
        return float(self.words[0]["start"])

    @property
    def end(self) -> float:
        return float(self.words[-1]["end"])


def wrap_words(words: list[dict[str, Any]], width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in words:
        text = str(word["text"]).strip()
        candidate = f"{line} {text}".strip()
        if line and len(candidate) > width:
            lines.append(line)
            line = text
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def build_cues(words: list[dict[str, Any]], width: int) -> list[Cue]:
    if width < 1:
        raise ValueError("width는 1 이상이어야 합니다.")
    cues: list[Cue] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if current:
            cues.append(Cue(current, wrap_words(current, width)))
            current = []

    for word in words:
        if not str(word.get("text", "")).strip():
            continue
        if current:
            previous = current[-1]
            if (
                previous.get("speaker") != word.get("speaker")
                or float(word["start"]) - float(previous["end"]) > MAX_GAP
                or float(word["end"]) - float(current[0]["start"]) > MAX_DURATION
                or len(wrap_words(current + [word], width)) > MAX_LINES
            ):
                flush()
        current.append(word)
        # Preserve an unusually long single token intact; never truncate it.
        if len(wrap_words(current, width)) > MAX_LINES:
            flush()
    flush()
    return cues


def _timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render(input_path: Path, output_prefix: Path, width: int) -> tuple[Path, Path]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    words = payload.get("words")
    if not isinstance(words, list):
        raise ValueError("입력 JSON에 words 배열이 없습니다.")
    cues = build_cues(words, width)
    srt_path = output_prefix.with_suffix(".srt")
    txt_path = output_prefix.with_suffix(".txt")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_parts = [f"{index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n" + "\n".join(cue.lines) for index, cue in enumerate(cues, 1)]
    srt_path.write_text("\n\n".join(srt_parts) + ("\n" if srt_parts else ""), encoding="utf-8")
    txt_path.write_text("\n".join(" ".join(str(word["text"]).strip() for word in cue.words) for cue in cues) + ("\n" if cues else ""), encoding="utf-8")
    return srt_path, txt_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="transcript.json 또는 merged.json")
    parser.add_argument("-o", "--output-prefix", type=Path)
    parser.add_argument("--width", type=int, default=20, help="한 줄 최대 폭 (한국어 20, 영어 42)")
    args = parser.parse_args()
    prefix = args.output_prefix or args.input.with_suffix("")
    srt_path, txt_path = render(args.input, prefix, args.width)
    print(f"{srt_path}\n{txt_path}")


if __name__ == "__main__":
    main()
