"""화면 참조 시각의 프레임을 뽑아 frames.json 으로 색인한다 (CONTRACT.md 11절).

owner: claude

균일 전체 프레임 추출은 하지 않는다. 다음만 후보로 삼는다.

  1. transcript 의 화면 참조 표현 ("보시면", "이 그림", "표에서" 등)
  2. 라틴 문자 용어가 복원된 시각 (origin="youtube") — 슬라이드에 원문이 있을 확률이 높다
  3. 호출자가 지정한 시각

OCR 은 선택이다. 엔진을 차례로 시도한다.

  1. Windows 내장 OCR (`Windows.Media.Ocr`) — 설치할 것이 없다. PowerShell 을
     한 번 띄워 프레임 전부를 읽는다.
  2. `pytesseract` + tesseract 바이너리 — Windows 가 아닌 환경의 예비.
  3. 둘 다 없으면 프레임만 뽑고 `ocr_text` 는 null 로 둔다.

OCR 결과는 transcript 를 덮어쓰지 않고 독립 provenance 로만 저장한다. 어느
엔진이 어느 언어로 읽었는지 `ocr_engine` / `ocr_language` 에 남긴다.

사용법:
    python src/visual.py <bundle> [--at 208.0,912.5] [--max-frames 40]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
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
    """라틴 문자 용어가 복원된 시각. 슬라이드에 원문 표기가 있을 가능성이 높다."""
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


# Windows 내장 OCR 을 부르는 PowerShell. 별도 .ps1 파일로 두지 않는다 —
# 설치본은 PyInstaller 단일 exe 라 옆 파일이 따라가지 않고, 사용자 PC 의
# 실행 정책이 스크립트 파일을 막을 수 있다. -EncodedCommand 는 둘 다 피한다.
#
# 입출력은 콘솔이 아니라 UTF-8 파일로 주고받는다. 한국어 Windows 콘솔은
# cp949 라 `“` `—` 같은 글자가 깨진다 (DECISIONS 참고).
_WINDOWS_OCR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
  $null = $task.Wait(-1)
  $task.Result
}
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType=WindowsRuntime]

$utf8 = New-Object System.Text.UTF8Encoding($false)
$manifest = [IO.File]::ReadAllText('__MANIFEST__', $utf8) | ConvertFrom-Json

$engine = $null
$want = [string]$manifest.language
if ($want) {
  $primary = $want.Split('-')[0].ToLowerInvariant()
  foreach ($candidate in [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages) {
    $tag = $candidate.LanguageTag
    if ($tag -ieq $want -or $tag.Split('-')[0].ToLowerInvariant() -eq $primary) {
      $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($candidate)
      break
    }
  }
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }

$results = New-Object System.Collections.ArrayList
if ($engine) {
  foreach ($path in $manifest.paths) {
    $entry = [ordered]@{ path = $path; text = $null; error = $null }
    try {
      $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
      $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
      try {
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $ocr = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $entry.text = (@($ocr.Lines | ForEach-Object { $_.Text }) -join ' ')
      } finally {
        $stream.Dispose()
      }
    } catch {
      $entry.error = $_.Exception.Message
    }
    $null = $results.Add([pscustomobject]$entry)
  }
}

$language = $null
if ($engine) { $language = $engine.RecognizerLanguage.LanguageTag }
$payload = [ordered]@{ engine = 'windows'; language = $language; results = @($results) }
[IO.File]::WriteAllText('__OUTPUT__', (ConvertTo-Json $payload -Depth 5), $utf8)
"""

WINDOWS_OCR_BASE_TIMEOUT = 60.0
WINDOWS_OCR_PER_FRAME_TIMEOUT = 5.0


def _ps_literal(value: str) -> str:
    """PowerShell 작은따옴표 문자열 안에 넣을 수 있게 만든다."""
    return value.replace("'", "''")


def _powershell() -> str | None:
    found = shutil.which("powershell")
    if found:
        return found
    root = os.environ.get("SystemRoot")
    if root:
        candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _ocr_windows(paths: list[Path], language: str | None
                 ) -> tuple[dict[str, str | None], str | None] | None:
    """Windows 내장 OCR 로 여러 장을 한 번에 읽는다.

    돌려주는 것: ({절대경로: 글자 또는 None}, 실제로 쓴 인식 언어).
    쓸 수 없으면 None — 호출자가 다음 엔진으로 넘어간다.
    """
    if os.name != "nt" or not paths:
        return None
    shell = _powershell()
    if shell is None:
        return None

    with tempfile.TemporaryDirectory(prefix="cueprecise-ocr-") as workdir:
        manifest_path = Path(workdir) / "manifest.json"
        output_path = Path(workdir) / "result.json"
        manifest_path.write_text(json.dumps({
            "language": language,
            "paths": [str(path.resolve()) for path in paths],
        }, ensure_ascii=False), encoding="utf-8")

        script = (_WINDOWS_OCR_SCRIPT
                  .replace("__MANIFEST__", _ps_literal(str(manifest_path)))
                  .replace("__OUTPUT__", _ps_literal(str(output_path))))
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        timeout = WINDOWS_OCR_BASE_TIMEOUT + WINDOWS_OCR_PER_FRAME_TIMEOUT * len(paths)
        try:
            completed = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", encoded],
                capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0 or not output_path.exists():
            return None
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None

    raw_results = payload.get("results") or []
    if isinstance(raw_results, dict):
        # PowerShell 5.1 은 원소가 하나인 배열을 객체로 펴기도 한다.
        raw_results = [raw_results]
    texts: dict[str, str | None] = {}
    for item in raw_results:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        if item.get("error"):
            # 읽다 실패한 장이다. "Windows 가 읽었는데 글자가 없다" 와 다르다.
            # 결과에서 빼야 예비 엔진이 한 번 더 시도하고, 기록도 거짓이 되지 않는다.
            continue
        text = str(item.get("text") or "").strip()
        texts[str(Path(item["path"]).resolve())] = text or None
    if not texts:
        # 엔진을 만들지 못했거나 한 장도 읽지 못했다. 예비 엔진에 기회를 준다.
        return None
    return texts, payload.get("language")


def ocr_language(bundle: Path) -> str | None:
    """프레임 글자를 읽을 언어를 고른다. 모르면 None.

    말하는 언어를 쓴다. 자동 선택에 맡기면 Windows 표시 언어가 잡혀, 한국어
    Windows 에서 영어 슬라이드를 한국어 엔진으로 읽는다. 오류 없이 품질만
    떨어지므로 눈에 띄지 않는다.
    """
    captions = bundle / "raw" / "captions.json"
    if captions.exists():
        try:
            language = _read_json(captions).get("language")
        except (OSError, ValueError, AttributeError):
            language = None
        if isinstance(language, str) and language.strip():
            # YouTube 원어 트랙은 "en-orig" 처럼 온다.
            return language.strip().removesuffix("-orig")
    job = bundle / "job.json"
    if job.exists():
        try:
            codes = (_read_json(job).get("config") or {}).get("language_codes") or []
        except (OSError, ValueError, AttributeError):
            codes = []
        if codes and isinstance(codes[0], str) and codes[0].strip():
            return codes[0].strip()
    return None


def ocr_frames(paths: list[Path], language: str | None = None
               ) -> dict[str, dict[str, Any]]:
    """프레임들의 글자를 읽는다. 경로 -> {text, confidence, engine, language}.

    Windows 내장 OCR 이 점수를 주지 않으므로 그 결과의 confidence 는 None 이다.
    값이 없다는 사실을 그대로 남긴다. 색인 단계가 기본값을 채운다.
    """
    results: dict[str, dict[str, Any]] = {}
    windows = _ocr_windows(paths, language)
    if windows is not None:
        texts, used_language = windows
        for path in paths:
            key = str(path.resolve())
            if key in texts:
                results[key] = {"text": texts[key], "confidence": None,
                                "engine": "windows", "language": used_language}
    for path in paths:
        key = str(path.resolve())
        if key in results:
            continue
        text, confidence = _ocr(path)
        results[key] = {"text": text, "confidence": confidence,
                        "engine": "tesseract" if text is not None else None,
                        "language": None}
    return results


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
                   candidates: list[dict[str, Any]],
                   language: str | None = None) -> list[dict[str, Any]]:
    """후보 시각의 프레임을 뽑는다. ffmpeg 가 없으면 빈 목록을 돌려준다.

    OCR 은 다 뽑은 뒤 한 번에 한다. Windows OCR 은 PowerShell 을 띄우는 데
    1초 가까이 걸려, 장마다 띄우면 40장에 30초가 넘는다.
    """
    out_dir = bundle / "raw" / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[tuple[dict[str, Any], float, str, Path]] = []
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
        extracted.append((candidate, timestamp, name, target))

    readings = ocr_frames([target for _, _, _, target in extracted], language)
    frames: list[dict[str, Any]] = []
    for candidate, timestamp, name, target in extracted:
        reading = readings.get(str(target.resolve()), {})
        frames.append({
            "timestamp": round(timestamp, 3),
            "path": "raw/frames/" + name,
            "reason": candidate["reason"],
            "ocr_text": reading.get("text"),
            "confidence": reading.get("confidence"),
            "ocr_engine": reading.get("engine"),
            "ocr_language": reading.get("language"),
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
    frames = (extract_frames(video, bundle, candidates, language=ocr_language(bundle))
              if video is not None else [])
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
