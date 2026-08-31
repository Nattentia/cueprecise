"""화면 참조 시각의 프레임을 뽑아 frames.json 으로 색인한다 (CONTRACT.md 11절).

owner: claude

균일 전체 프레임 추출은 하지 않는다. 다음만 후보로 삼는다.

  1. transcript 의 화면 참조 표현 ("보시면", "이 그림", "표에서" 등)
  2. 영어 용어가 복원된 시각 (origin="youtube") — 슬라이드에 원문이 있을 확률이 높다
  3. 호출자가 지정한 시각

OCR 은 선택이다. `pytesseract` 가 없으면 프레임만 뽑고 `ocr_text` 는 null 로
둔다. OCR 결과는 transcript 를 덮어쓰지 않고 독립 provenance 로만 저장한다.

사용법:
    python src/visual.py <bundle> [--at 208.0,912.5] [--max-frames 40]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import runtime

DEFAULT_MAX_FRAMES = 40
MIN_SEPARATION_SECS = 8.0
"""이보다 가까운 후보는 하나로 합친다. 같은 슬라이드를 여러 장 뽑지 않는다."""

VIDEO_NAMES = ("source_video.mp4", "source_video.webm", "source_video.mkv",
               "source.mp4")
"""프레임을 뽑을 수 있는 파일.

영상은 `source_video.*` 로 받는다. 오디오도 받은 형식을 그대로 두므로, 둘 다
`source.*` 를 쓰면 webm 끼리 이름이 부딪힐 수 있다. `source.mp4` 는 이 규칙
이전에 받은 bundle 을 위해 남긴다.
"""

SCREEN_REFERENCE_PATTERNS = (
    # 한국어 — 네 갈래: 보다 / 이 그림 / 여기·좌우 / 그림에서
    r"보시면", r"보시다시피", r"보면", r"보겠습니다",
    r"이\s*그림", r"이\s*표", r"이\s*그래프", r"이\s*슬라이드", r"이\s*화면",
    r"여기\s*보", r"왼쪽", r"오른쪽", r"위\s*쪽", r"아래\s*쪽",
    r"그림에서", r"표에서", r"그래프에서", r"화면에",
    # 영어 — 같은 네 갈래를 그대로 옮겼다.
    r"\byou can see\b", r"\byou['’]?ll see\b", r"\bas you see\b",
    r"\blet['’]?s look at\b", r"\blook at th(?:is|e)\b",
    r"\bif you look\b", r"\btake a look\b",
    r"\bthis (?:figure|table|graph|chart|slide|diagram|plot|image|picture)\b",
    r"\bup here\b", r"\bdown here\b", r"\bover here\b", r"\bright here\b",
    r"\bon the left\b", r"\bon the right\b",
    r"\bat the top\b", r"\bat the bottom\b",
    r"\bin the (?:figure|table|graph|chart|diagram|plot)\b",
    r"\bon the (?:slide|screen)\b",
)
_SCREEN_RE = re.compile("|".join(SCREEN_REFERENCE_PATTERNS), re.IGNORECASE)
"""대소문자를 가리지 않는다. 한국어 패턴에는 영향이 없다."""

_WINDOW_SECS = 6.0
"""화면 참조 표현을 찾을 때 묶어서 볼 문맥 길이."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def screen_reference_times(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """화면 참조 표현이 나오는 시각. 표현 시점보다 약간 앞을 잡는다."""
    hits: list[dict[str, Any]] = []
    index = 0
    while index < len(words):
        start = float(words[index]["start"])
        window: list[dict[str, Any]] = []
        cursor = index
        while cursor < len(words) and float(words[cursor]["start"]) - start <= _WINDOW_SECS:
            window.append(words[cursor])
            cursor += 1
        text = " ".join(str(w["text"]) for w in window)
        if _SCREEN_RE.search(text):
            # 말하는 시점엔 이미 화면이 바뀐 뒤다. 1초 앞을 잡는다.
            hits.append({"timestamp": max(0.0, round(start - 1.0, 3)),
                         "reason": "screen-reference"})
            index = cursor
        else:
            index += 1
    return hits


def restored_term_times(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """영어 용어가 복원된 시각. 슬라이드에 원문 표기가 있을 가능성이 높다."""
    return [
        {"timestamp": max(0.0, round(float(w["start"]) - 0.5, 3)),
         "reason": "restored-term"}
        for w in words if w.get("origin") == "youtube"
    ]


def _spread(candidates: list[dict[str, Any]], room: int) -> list[dict[str, Any]]:
    """`room` 개만 남기되 시간축에 고르게 편다.

    앞에서부터 자르면 긴 영상의 뒷부분에 프레임이 한 장도 안 남는다. 후보는
    이미 시각 순이므로 균등 간격 색인을 고른다.
    """
    if room >= len(candidates):
        return list(candidates)
    if room <= 1:
        return candidates[:room]
    last = len(candidates) - 1
    picked = {round(position * last / (room - 1)) for position in range(room)}
    return [candidates[index] for index in sorted(picked)]


def dedupe_candidates(candidates: list[dict[str, Any]], *,
                      min_separation: float = MIN_SEPARATION_SECS,
                      max_frames: int = DEFAULT_MAX_FRAMES) -> list[dict[str, Any]]:
    """사람이 지정한 시각을 먼저 확보하고, 남은 자리를 자동 후보로 채운다.

    `requested` 는 호출자가 콕 집어 달라고 한 시각이다. 자동 후보에 밀려
    사라지면 안 된다.
    """
    ordered = sorted(candidates, key=lambda c: float(c["timestamp"]))
    requested = [c for c in ordered if c.get("reason") == "requested"]
    automatic = [c for c in ordered if c.get("reason") != "requested"]

    def _far_enough(candidate: dict[str, Any], chosen: list[dict[str, Any]]) -> bool:
        return all(
            abs(float(candidate["timestamp"]) - float(other["timestamp"])) >= min_separation
            for other in chosen
        )

    kept: list[dict[str, Any]] = []
    for candidate in requested:
        if len(kept) >= max_frames:
            break
        if _far_enough(candidate, kept):
            kept.append(candidate)

    eligible: list[dict[str, Any]] = []
    for candidate in automatic:
        if _far_enough(candidate, kept) and _far_enough(candidate, eligible):
            eligible.append(candidate)
    kept.extend(_spread(eligible, max_frames - len(kept)))
    return sorted(kept, key=lambda c: float(c["timestamp"]))


def source_video(bundle: Path) -> Path | None:
    """bundle 의 영상 파일. 오디오만 받은 bundle 이면 None.

    오디오를 영상 대신 넘기면 ffmpeg 가 후보마다 실패하므로, 없는 것은
    없다고 답한다.
    """
    for name in VIDEO_NAMES:
        candidate = bundle / "raw" / name
        if candidate.exists():
            return candidate
    return None


def _ocr(path: Path) -> tuple[str | None, float | None]:
    """pytesseract 가 있으면 OCR 한다. 없으면 (None, None)."""
    try:
        import pytesseract  # noqa: PLC0415 - 선택 의존성
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None, None
    try:
        data = pytesseract.image_to_data(Image.open(path),
                                         output_type=pytesseract.Output.DICT)
    except Exception:
        return None, None
    words, scores = [], []
    for text, score in zip(data.get("text", []), data.get("conf", [])):
        text = str(text).strip()
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if text and score >= 0:
            words.append(text)
            scores.append(score / 100.0)
    if not words:
        return None, None
    return " ".join(words), round(sum(scores) / len(scores), 3)


def extract_frames(source_video: Path, bundle: Path,
                   candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """후보 시각의 프레임을 뽑는다. ffmpeg 가 없으면 빈 목록을 돌려준다."""
    out_dir = bundle / "raw" / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for candidate in candidates:
        timestamp = float(candidate["timestamp"])
        name = "%09d.jpg" % round(timestamp * 1000)
        target = out_dir / name
        if not target.exists():
            command = [runtime.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                       "-ss", "%.3f" % timestamp, "-i", str(source_video),
                       "-frames:v", "1", "-q:v", "3", str(target)]
            try:
                result = subprocess.run(command, capture_output=True, text=True)
            except FileNotFoundError:
                return []
            if result.returncode != 0 or not target.exists():
                continue
        ocr_text, confidence = _ocr(target)
        frames.append({
            "timestamp": round(timestamp, 3),
            "path": "raw/frames/" + name,
            "reason": candidate["reason"],
            "ocr_text": ocr_text,
            "confidence": confidence,
        })
    return frames


def build(bundle: Path, *, at: list[float] | None = None,
          max_frames: int = DEFAULT_MAX_FRAMES) -> dict[str, Any]:
    source = bundle / "derived" / "merged.json"
    if not source.exists():
        source = bundle / "derived" / "transcript.json"
    if not source.exists():
        raise FileNotFoundError("derived 전사가 없습니다. pipeline 을 먼저 실행하세요.")
    payload = _read_json(source)
    words = payload["words"]

    candidates = screen_reference_times(words) + restored_term_times(words)
    candidates += [{"timestamp": float(t), "reason": "requested"} for t in (at or [])]
    found = len(candidates)
    candidates = dedupe_candidates(candidates, max_frames=max_frames)

    video = source_video(bundle)
    frames = extract_frames(video, bundle, candidates) if video is not None else []
    result = {
        "schema_version": 1,
        "video_id": payload.get("video_id") or bundle.name,
        "frames": frames,
        "candidates_considered": len(candidates),
        # 찾았지만 근접·상한으로 떨어진 수. 몇 장을 안 뽑았는지 숨기지 않는다.
        "candidates_dropped": found - len(candidates),
        "note": None if frames else
        "프레임을 뽑지 못했다. raw/source.mp4 등 영상 파일이 필요하다 "
        "(오디오만 받은 bundle 에서는 후보 시각만 계산된다).",
    }
    _write_json(bundle / "derived" / "frames.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--at", default=None, help="쉼표 구분 초 단위 시각")
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    args = parser.parse_args()

    at = [float(s) for s in args.at.split(",") if s.strip()] if args.at else None
    result = build(args.bundle, at=at, max_frames=args.max_frames)
    print("frames=%d candidates=%d" % (len(result["frames"]), result["candidates_considered"]))
    if result["note"]:
        print(result["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
