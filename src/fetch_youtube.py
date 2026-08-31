"""YouTube 원어 자동자막을 받아 롤링 중복을 편다.

언어를 고정하지 않는다. `*-orig` 는 그 영상이 실제로 촬영된 언어의 자동자막을
뜻하므로 한국어 영상이면 `ko-orig`, 영어 영상이면 `en-orig` 가 온다. 원어
자동자막이 없는 영상은 일반 자막(ko/en)으로 한 번 더 시도한다.
"""

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


ORIGINAL_LANGS = (".*-orig",)
"""영상이 실제로 촬영된 언어의 자동자막. 언어를 몰라도 이것만 받으면 된다."""

FALLBACK_LANGS = ("ko", "en")
"""원어 자동자막이 없는 영상용. 있는 것만 받아지고 없으면 그냥 안 받아진다.

폴백은 **자동자막을 요청하지 않는다.** YouTube 는 자동자막을 아무 언어로나
기계 번역해 주기 때문에, 영어 영상에 `ko` 를 요청하면 한국어 번역 자막이
내려온다 (실측 확인). 그것을 원어 근거로 쓰면 멀쩡한 영어 전사를 번역문으로
오판한다. 여기서는 사람이 올린 자막만 받는다.
"""


def _download_subs(url: str, directory: Path, langs: tuple[str, ...], *,
                   auto: bool) -> list[Path]:
    target = directory / "%(id)s.%(ext)s"
    command = ["yt-dlp"]
    if auto:
        command.append("--write-auto-sub")
    command += ["--write-subs", "--sub-langs", ",".join(langs),
                "--skip-download", "--convert-subs", "srt", "-o", str(target), url]
    # 출력을 삼킨다. MCP 서버는 stdout 을 JSON-RPC 통로로 쓰므로 자식
    # 프로세스가 거기에 쓰면 프로토콜이 깨진다.
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("yt-dlp 자막 취득 실패: " + (result.stderr or "")[-300:])
    return sorted(directory.glob("*.srt"))


def _pick(candidates: list[Path]) -> Path:
    """원어 자동자막을 먼저 고른다. 여러 언어가 받아졌을 때만 의미가 있다."""
    for path in candidates:
        if "-orig." in path.name:
            return path
    return candidates[0]


def _split_name(path: Path) -> tuple[str, str]:
    """`<video_id>.<lang>.srt` 를 나눈다."""
    parts = path.name.rsplit(".", 2)
    if len(parts) != 3:
        return path.stem, "unknown"
    return parts[0], parts[1]


def fetch(url: str, output: Path, *, langs: list[str] | None = None) -> dict[str, object]:
    # (요청 언어, 자동자막까지 받을지)
    attempts = ([(tuple(langs), True)] if langs
                else [(ORIGINAL_LANGS, True), (FALLBACK_LANGS, False)])
    with tempfile.TemporaryDirectory(prefix="ytx-captions-") as directory:
        root = Path(directory)
        candidates: list[Path] = []
        for attempt, auto in attempts:
            candidates = _download_subs(url, root, attempt, auto=auto)
            if candidates:
                break
        if not candidates:
            raise FileNotFoundError(
                "자막을 내려받지 못했습니다 (시도: %s)."
                % "; ".join(",".join(a) for a, _ in attempts))
        chosen = _pick(candidates)
        video_id, language = _split_name(chosen)
        cues = collapse_rolling_lines(parse_srt(chosen))
    payload: dict[str, object] = {"source": "youtube-" + language, "language": language,
                                  # 원어 트랙만 "원문이 무슨 언어인가" 의 근거가
                                  # 된다. 나머지는 번역일 수 있다.
                                  "original": language.endswith("-orig"),
                                  "video_id": video_id, "cues": cues}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10),
                      encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument("-o", "--output", type=Path, default=Path("captions.json"))
    parser.add_argument("--sub-langs", default=None,
                        help="쉼표 구분. 생략하면 원어 자동자막을 찾는다")
    args = parser.parse_args()
    langs = ([s.strip() for s in args.sub_langs.split(",") if s.strip()]
             if args.sub_langs else None)
    result = fetch(args.url, args.output, langs=langs)
    print(f"{result['language']}: {len(result['cues'])} cues -> {args.output}")


if __name__ == "__main__":
    main()
