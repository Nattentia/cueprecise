"""YouTube URL 하나로 전체 파이프라인을 실행한다 (CONTRACT.md 7·9·10·12절).

owner: claude

단계는 JSON 파일로만 이어지고 각각 독립적으로 재실행할 수 있다.

    fetch      URL -> raw/source.<ext>, raw/source_video.<ext>, raw/captions.json
    plan       source 오디오 -> job.json, raw/audio/chunk-NNN.mp3
    transcribe chunk-NNN.mp3 -> raw/transcripts/chunk-NNN.json   (Gemini 호출)
    assemble   chunk transcripts -> derived/transcript.json      (화자 정합)
    merge      transcript + captions -> derived/merged.json
    render     merged.json -> derived/output.srt, output.txt
    visual     merged.json + source_video -> raw/frames/, derived/frames.json
    index      bundle -> index.sqlite3

사용법:
    python src/pipeline.py run <url> [--bundle-root data] [--stages a,b,c]
    python src/pipeline.py status <video_id>
    python src/pipeline.py purge <video_id> [--scope chunks|derived|raw|all]

완료된 청크는 job.json 의 fingerprint/config 가 일치하면 다시 호출하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from bisect import bisect_right
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audio
import chapters
import context
import fetch_youtube
import merge as merge_mod
import render as render_mod
import speakers
import transcribe as transcribe_mod
import usage
import visual
import runtime

# transcribe 임포트는 SDK 를 요구하지 않는다. google-genai 는 실제 호출 경로
# (transcribe.request_raw) 안에서만 불러오므로 fetch/merge/render/index/status
# 와 저장된 응답 재파싱은 SDK 없이 동작한다.

# STAGES 는 "실행할 수 있는 단계" 이고 DEFAULT_STAGES 는 "생략했을 때 도는
# 단계" 다. 둘을 한 상수로 겸하면 기본을 줄일 때마다 호출부마다 예외를 심게
# 된다. OPTIONAL_STAGES 만 고치면 CLI·MCP·status 가 함께 따라온다.
STAGES = ("fetch", "plan", "transcribe", "assemble", "merge", "chapters",
          "render", "visual", "index")

# 기본에서 빠지는 단계. 요청이 있을 때만 돈다.
#
# render — SRT/TXT 는 사람이 자막 파일을 원할 때 쓴다. 근거 검색은
# merged.json 과 index 로 하므로 기본 산출물에 있을 이유가 없다. 기능은
# 그대로다. `--stages render` / `cueprecise_register(stages=["render"])` 로 언제든
# 만들 수 있고 단어 보존율 100% 도 그대로다.
#
# visual 은 기본에 남긴다. 이 도구의 목적이 "오디오에 안 잡히는 화면 정보를
# 캡처로 가져오는 것" 이라 프레임이 없으면 목적의 절반이 빠진다. 실측(58분):
# 360p 영상 14.5MB < 지금 받는 소리 42.6MB. 줄일 곳은 영상이 아니라 소리다.
OPTIONAL_STAGES: tuple[str, ...] = ("render",)

DEFAULT_STAGES = tuple(s for s in STAGES if s not in OPTIONAL_STAGES)

# 단계가 만드는 산출물. status 가 선택 산출물의 부재를 실패로 보이지 않게
# 하는 데 쓴다.
STAGE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "fetch": ("captions",),
    "assemble": ("transcript",),
    "merge": ("merged",),
    "chapters": ("chapters",),
    "render": ("srt", "txt"),
    "visual": ("frames",),
    "index": ("index",),
}

ALL_STAGES_KEYWORD = "all"


def resolve_stages(value: object = None) -> tuple[str, ...]:
    """단계 선택을 정규화한다.

    생략(`None` 또는 빈 값)은 `DEFAULT_STAGES`, `"all"` 은 `STAGES` 전체다.
    명시적 선택은 기본 목록이 아니라 항상 `STAGES` 전체에서 검증한다 —
    기본에서 빠진 단계도 이름을 대면 돌아가야 한다.
    """
    if value is None:
        return DEFAULT_STAGES
    if isinstance(value, str):
        items = [s.strip() for s in value.split(",") if s.strip()]
    else:
        items = [str(s).strip() for s in value if str(s).strip()]
    if not items:
        return DEFAULT_STAGES
    if len(items) == 1 and items[0] == ALL_STAGES_KEYWORD:
        return STAGES
    for stage in items:
        if stage not in STAGES:
            raise ValueError("알 수 없는 단계: %s (가능: %s, %s)"
                             % (stage, ", ".join(STAGES), ALL_STAGES_KEYWORD))
    return tuple(items)

DEFAULT_DAILY_LIMIT = 25
DEFAULT_RPM_LIMIT = 2
DEFAULT_REQUEST_INTERVAL = 30.0
DEFAULT_WIDTH = 20

# 프레임용 영상. 슬라이드 글자를 읽을 만한 최소 화질이면 된다. 360p mp4 는
# 23분 영상 기준 16MB 로, 이미 받는 오디오(58분 61MB)보다 작다. m3u8 은
# 구간 추출이 느려 직접 https 포맷을 먼저 고른다.
VIDEO_FORMAT = ("bv*[height<=480][ext=mp4][protocol^=http]/"
                "bv*[height<=480][protocol^=http]/bv*[height<=480]/wv*")

# 오디오는 받은 형식 그대로 둔다. mp3 로 변환하면 파일이 오히려 커지고
# (m4a 129k 21.7MB -> mp3 160k 26.8MB) 재압축이라 음질도 떨어진다. 어차피
# 청크를 만들 때 16kHz 모노로 낮추므로 원본을 고음질로 보관할 이유가 없다.
AUDIO_FORMAT = "ba[protocol^=http]/ba/bestaudio"

_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([0-9A-Za-z_-]{11})")


class StageError(RuntimeError):
    """단계 실패. 부분 결과는 bundle 에 남는다."""


# --------------------------------------------------------------------------- util

def video_id_from_url(url: str) -> str:
    match = _ID_RE.search(url)
    if match:
        return match.group(1)
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url.strip()):
        return url.strip()
    raise ValueError(f"URL 에서 video_id 를 찾지 못했습니다: {url}")


def bundle_path(bundle_root: Path, video_id: str) -> Path:
    return Path(bundle_root) / video_id


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_job(bundle: Path, job: dict[str, Any]) -> None:
    _write_json(bundle / "job.json", job)


def _load_job(bundle: Path) -> dict[str, Any]:
    """job.json 을 읽는다. 없으면 무엇을 해야 하는지 알려준다."""
    path = bundle / "job.json"
    if not path.exists():
        raise StageError(
            "%s 가 없습니다. plan 단계를 먼저 실행하세요 "
            "(--stages plan,... 또는 단계 지정 없이 run)." % path)
    return _read_json(path)


def _log(message: str) -> None:
    """진행 로그는 stderr 로 보낸다.

    MCP 서버가 stdout 을 JSON-RPC 통신 통로로 쓴다. 진행 로그가 같은 곳으로
    나가면 프로토콜이 깨져 클라이언트가 응답을 파싱하지 못한다. CLI 사용자는
    터미널에서 그대로 보이므로 달라지는 것이 없다.

    Windows cp949 등 좁은 콘솔 인코딩에서도 죽지 않게 감싼다.
    """
    try:
        print(message, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
        print(message.encode(encoding, "replace").decode(encoding),
              file=sys.stderr, flush=True)


# -------------------------------------------------------------------------- stages

def _remove_stale_sources(bundle: Path, names: tuple[str, ...],
                          keep: str | None = None) -> list[str]:
    """이전 원본을 지운다. `keep` 은 방금 받은 파일이라 남긴다.

    형식이 바뀌면(mp3 -> webm, source.mp4 -> source_video.mp4) 옛 파일이 그대로
    남아 용량만 두 배가 된다. 반드시 **다운로드가 성공한 뒤에** 부른다.
    """
    removed: list[str] = []
    for name in names:
        if name == keep:
            continue
        path = bundle / "raw" / name
        if path.exists():
            path.unlink()
            removed.append(name)
    return removed


def _download(url: str, fmt: str, raw: Path, stem: str,
              names: tuple[str, ...]) -> tuple[Path | None, str]:
    """받아서 성공했을 때만 자리를 바꾼다. 실패하면 기존 원본이 그대로 남는다.

    예전에는 다시 받기 전에 옛 파일을 먼저 지웠다. 다운로드가 실패하면(연결
    끊김, 포맷 없음, 연령 제한) 옛 것도 새 것도 없는 상태가 됐다. 임시
    디렉터리에 받고, 성공한 뒤에 옛 것을 치우고 옮긴다.
    """
    staging = raw / ".download"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [runtime.tool("yt-dlp"), "--no-playlist", "-f", fmt,
             "-o", str(staging / stem) + ".%(ext)s", url],
            capture_output=True, text=True,
        )
        files = [path for path in staging.iterdir() if path.is_file()]
        if result.returncode != 0 or not files:
            return None, (result.stderr or "")
        # 여러 개가 남으면 가장 큰 것이 본편이다 (.part 등 부산물 배제).
        downloaded = max(files, key=lambda path: path.stat().st_size)
        _remove_stale_sources(raw.parent, names, keep=downloaded.name)
        target = raw / downloaded.name
        if target.exists():
            target.unlink()
        downloaded.replace(target)
        return target, ""
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _has_video_stream(path: Path) -> bool:
    """영상 트랙이 있는 파일인가. 소리·영상을 한 번에 받은 뒤 가려낸다."""
    try:
        result = subprocess.run(
            [runtime.tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        # ffprobe 가 없으면 확장자로 짐작한다. 정확도가 낮지만 여기서 멈추는
        # 것보다 낫다 — ffprobe 는 어차피 청크 분할에 필요하다.
        return path.suffix.lower() in (".mp4", ".mkv")
    return "video" in (result.stdout or "")


METADATA_NAME = "metadata.json"
"""yt-dlp 가 알려주는 영상 정보. 언어 근거로만 쓴다."""


def _trim_metadata(info: dict[str, Any]) -> dict[str, Any]:
    """info.json 에서 언어 판정에 쓸 것만 남긴다.

    통째로 두면 수백 KB 에 썸네일 URL 과 포맷 목록이 딸려 온다. 우리가 보는
    것은 넷뿐이다. 제목과 채널은 사람이 번들을 알아보라고 남긴다.
    """
    automatic = info.get("automatic_captions") or {}
    subtitles = info.get("subtitles") or {}
    return {
        "video_id": info.get("id"),
        "title": (info.get("title") or "")[:200],
        "channel": info.get("channel") or info.get("uploader"),
        "language": info.get("language"),
        "auto_caption_langs": sorted(k for k in automatic if k.endswith("-orig")),
        "subtitle_langs": sorted(subtitles)[:20],
    }


def _write_metadata_from_info(info_path: Path, raw: Path) -> Path | None:
    """staging 의 info.json 을 추려 raw/metadata.json 으로 남긴다."""
    try:
        info = _read_json(info_path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict):
        return None
    target = raw / METADATA_NAME
    _write_json(target, _trim_metadata(info))
    return target


def _fetch_metadata(url: str, raw: Path) -> Path | None:
    """메타데이터만 따로 받는다. 미디어를 다시 받지 않으므로 몇 초로 끝난다.

    소리·영상이 이미 있어 통합 호출이 돌지 않은 번들에서만 쓴다. 실패해도
    파이프라인은 진행한다 — 언어 근거가 하나 없을 뿐이다.
    """
    try:
        result = subprocess.run(
            [runtime.tool("yt-dlp"), "--no-playlist", "--skip-download", "--dump-json", url],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(info, dict):
        return None
    target = raw / METADATA_NAME
    _write_json(target, _trim_metadata(info))
    return target


def _fetch_sources(url: str, raw: Path, *, want_video: bool,
                   want_captions: bool) -> tuple[dict[str, Path | None], str]:
    """yt-dlp 한 번으로 소리·영상·자막을 받는다.

    셋을 따로 받으면 같은 페이지를 세 번 해석한다 (실측 12.3초, 회당 약 4초).
    한 번에 요청하면 해석도 한 번이고 다운로드도 이어서 돈다 (실측 5.4초).

    성공 여부는 종료 코드가 아니라 **실제로 받아진 파일**로 판단한다. 자막
    하나가 실패해도 소리까지 실패로 처리하면 안 된다.
    """
    staging = raw / ".download"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path | None] = {"audio": None, "video": None, "captions": None}
    try:
        fmt = AUDIO_FORMAT + ("," + VIDEO_FORMAT if want_video else "")
        command = [runtime.tool("yt-dlp"), "--no-playlist", "-f", fmt]
        if want_captions:
            command += ["--write-auto-sub", "--sub-langs",
                        ",".join(fetch_youtube.ORIGINAL_LANGS),
                        "--convert-subs", "srt"]
        # 메타데이터는 같은 호출에 얹는다. 왕복이 늘지 않는다.
        command += ["--write-info-json"]
        command += ["-o", str(staging / "media") + ".%(format_id)s.%(ext)s", url]
        result = subprocess.run(command, capture_output=True, text=True)

        media: list[Path] = []
        info: Path | None = None
        for path in sorted(staging.iterdir()):
            if not path.is_file():
                continue
            name = path.name.lower()
            if name.endswith(".info.json"):
                # 포맷마다 한 벌씩 나올 수 있다. 내용이 같으므로 하나만 쓴다.
                # media 로 새면 가장 큰 파일 규칙에 걸려 오디오로 오인된다.
                if info is None:
                    info = path
            elif path.suffix.lower() == ".srt":
                if found["captions"] is None:
                    # 포맷마다 한 벌씩 나온다. 내용이 같으므로 하나만 쓴다.
                    found["captions"] = path
            elif path.suffix.lower() != ".part":
                media.append(path)
        if info is not None:
            _write_metadata_from_info(info, raw)

        for path in sorted(media, key=lambda item: item.stat().st_size, reverse=True):
            kind = "video" if want_video and _has_video_stream(path) else "audio"
            if found[kind] is None:
                found[kind] = path

        moved: dict[str, Path | None] = {"audio": None, "video": None,
                                         "captions": found["captions"]}
        for kind, stem, names in (("audio", "source", audio.AUDIO_NAMES),
                                  ("video", "source_video", visual.VIDEO_NAMES)):
            source = found[kind]
            if source is None:
                continue
            target = raw / (stem + source.suffix)
            _remove_stale_sources(raw.parent, names, keep=target.name)
            if target.exists():
                target.unlink()
            source.replace(target)
            moved[kind] = target
        if moved["captions"] is not None:
            # staging 은 곧 지워지므로 자막은 여기서 읽어 둔다.
            moved["captions"] = _stage_captions(moved["captions"], raw)
        return moved, (result.stderr or "")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _stage_captions(srt: Path, raw: Path) -> Path:
    """받아둔 srt 를 captions.json 으로 옮긴다.

    `video_id` 는 **번들 이름**으로 덮어쓴다. `payload_from_srt` 는 파일 이름
    (`<video_id>.<lang>.srt`)에서 뽑는데, 통합 취득은 포맷 id 로 이름을 짓기
    때문에(`media.251-2.ko-orig.srt`) 그대로 두면 `media.251-2` 가 들어가고
    `merge` 가 "video_id 가 다릅니다" 로 멈춘다.
    """
    payload = fetch_youtube.payload_from_srt(srt)
    payload["video_id"] = raw.parent.name
    target = raw / "captions.json"
    fetch_youtube.write_payload(payload, target)
    return target


def _pin_captions_video_id(captions: Path, video_id: str) -> None:
    """자막의 video_id 를 번들 이름에 맞춘다. 어긋나면 merge 가 멈춘다."""
    try:
        payload = _read_json(captions)
    except (OSError, json.JSONDecodeError):
        return
    if payload.get("video_id") != video_id:
        payload["video_id"] = video_id
        _write_json(captions, payload)


def stage_fetch(bundle: Path, url: str, *, force: bool = False,
                video: bool = True) -> dict[str, Any]:
    """오디오·영상·자막을 **한 번의 yt-dlp 호출로** 받는다. Gemini 호출 없음.

    영상과 자막은 실패해도 치명이 아니다. 영상은 나중에 `ensure_video` 가,
    자막은 `fetch_youtube.fetch` 의 폴백이 다시 시도한다.
    """
    raw = bundle / "raw"
    captions = raw / "captions.json"
    raw.mkdir(parents=True, exist_ok=True)

    source = audio.source_audio(bundle)
    existing_video = visual.source_video(bundle)
    need_audio = force or source is None
    need_video = video and (force or existing_video is None)
    need_captions = force or not captions.exists()

    if need_audio or need_video:
        got, error = _fetch_sources(url, raw, want_video=need_video,
                                    want_captions=need_captions)
        if need_audio and got["audio"] is None:
            # 기존 원본이 있었다면 그대로 살아 있다.
            raise StageError("오디오 다운로드 실패: " + error[-500:])
        if got["audio"] is not None:
            source = audio.source_audio(bundle)
            _log("  오디오 %.1fMB (%s)" % (source.stat().st_size / 1048576, source.name))
        else:
            _log("  오디오 재사용 (%s)" % source.name)
        if need_video:
            if got["video"] is None:
                tail = error.strip().splitlines()
                _log("  경고: 영상 취득 실패, 프레임 추출을 건너뛴다 (%s)"
                     % (tail[-1][:200] if tail else "원인 불명"))
            else:
                _log("  영상 %.1fMB (%s)"
                     % (got["video"].stat().st_size / 1048576, got["video"].name))
                if force:
                    # 옛 영상에서 뽑아둔 프레임은 새 영상과 무관하다.
                    shutil.rmtree(raw / "frames", ignore_errors=True)
        if need_captions and got["captions"] is not None:
            need_captions = False
    else:
        _log("  오디오 재사용 (%s)" % source.name)
        if existing_video is not None:
            _log("  영상 재사용 (%s)" % existing_video.name)

    if need_captions:
        # 자막이 없는 경우가 번역문 검사의 사각지대다. 이 갈래는 어차피
        # yt-dlp 를 부르므로, 메타데이터가 없으면 여기서 같이 받아 둔다.
        # 소리·영상을 재사용한 갈래에서는 부르지 않는다 (호출 0 보장).
        if not (raw / METADATA_NAME).exists():
            _fetch_metadata(url, raw)
        # 원어 자동자막이 같이 안 왔을 때만 한 번 더 시도한다 (사람이 올린 자막).
        try:
            fetch_youtube.fetch(url, captions)
            _pin_captions_video_id(captions, bundle.name)
        except Exception as error:  # 자막은 선택 자료다. 없어도 파이프라인은 진행한다.
            _log("  경고: 자막 취득 실패, 영어 용어 복원을 건너뛴다 (%s)" % error)
            # 자막이 원래 없는 영상과 취득이 고장난 것은 사람이 할 일이 다르다.
            # 0 cue 만 남기면 구분할 수 없으므로 이유를 함께 적는다.
            _write_json(captions, {"source": "youtube", "language": None,
                                   "original": False, "error": str(error)[:300],
                                   "video_id": bundle.name, "cues": []})

    payload = _read_json(captions) if captions.exists() else {"cues": []}
    cue_count = len(payload.get("cues", []))
    _log("  자막 %d cues (%s)" % (cue_count, payload.get("language") or "없음"))
    script = _metadata_script(bundle)
    _log("  영상 원어(메타데이터) %s" % (script or "불명"))
    found = visual.source_video(bundle)
    return {"source_audio": str(source), "captions": str(captions), "cues": cue_count,
            "captions_language": payload.get("language"),
            "metadata_script": script,
            "video": str(found) if found else None}


def _plan_config(job: dict[str, Any]) -> dict[str, Any]:
    """저장된 config 에서 계획에 영향을 주는 항목만 남긴다.

    `diarization` 은 Gemini 요청에 닿은 적이 없는 죽은 값이었고 지금은 계획에서
    빠졌다. 옛 job.json 에는 아직 남아 있으므로 비교 전에 떼어낸다. 그러지
    않으면 키 하나 때문에 계획을 다시 세워 완료된 청크를 전부 다시 부른다.
    """
    config = dict(job.get("config", {}))
    config.pop("diarization", None)
    return config


def stage_plan(bundle: Path, url: str, *, chunk_max_secs: float, overlap_secs: float,
               language_codes: list[str] | None,
               force: bool = False) -> dict[str, Any]:
    """청크 계획과 분할. Gemini 호출 없음."""
    job_path = bundle / "job.json"
    source = audio.source_audio(bundle)
    if source is None:
        raise StageError("raw 에 원본 오디오가 없습니다. fetch 단계를 먼저 실행하세요.")

    config = {"chunk_max_secs": chunk_max_secs, "overlap_secs": overlap_secs,
              "language_codes": language_codes}

    if job_path.exists() and not force:
        job = _read_json(job_path)
        if (job.get("input", {}).get("fingerprint") == audio.file_fingerprint(source)
                and _plan_config(job) == config):
            missing = [c for c in job["chunks"] if not (bundle / c["path"]).exists()]
            if missing:
                audio.extract_chunks(source, bundle, missing)
            _log("  기존 계획 재사용: 청크 %d개" % len(job["chunks"]))
            return job
        _log("  fingerprint/config 변경 감지, 계획을 다시 세운다")
        previous = job
    else:
        previous = None

    job = audio.create_job(
        source, bundle, bundle.name, url,
        chunk_max_secs=chunk_max_secs, overlap_secs=overlap_secs,
        language_codes=language_codes,
    )
    carried = _carry_chunk_progress(previous, job, bundle)
    if carried:
        _save_job(bundle, job)
        _log("  청크 %d개 (완료 기록 %d개 보존)" % (len(job["chunks"]), carried))
    else:
        _log("  청크 %d개" % len(job["chunks"]))
    return job


def _carry_chunk_progress(previous: dict[str, Any] | None, job: dict[str, Any],
                          bundle: Path) -> int:
    """경계가 그대로인 청크의 완료 기록을 새 계획으로 옮긴다.

    재계획은 설정이 바뀌었을 때만 돈다. 그런데 바뀐 설정이 청크 경계와 무관한
    것이면(예: 나중에 추가된 필드) 이미 끝낸 전사가 그대로 유효한데도 장부만
    `planned` 로 초기화돼 왔다. 그러면 `status` 가 0/N 을 보고하고, 사람은
    분석이 안 된 줄 알고 다시 돌린다.

    경계(start/end)와 전사 파일이 모두 그대로일 때만 옮긴다. 경계가 옮겨갔으면
    옛 전사는 시각이 어긋나므로 넘기지 않는다. 실제로 그 응답을 다시 써도
    되는지는 transcribe 단계의 `_reusable_raw` 가 응답 원문의 꼬리표로 다시
    판정하므로, 여기서 옮긴 기록이 잘못된 재사용을 만들지는 않는다.
    """
    if not previous:
        return 0
    by_bounds = {(float(c["start"]), float(c["end"])): c
                 for c in previous.get("chunks") or []}
    carried = 0
    for chunk in job["chunks"]:
        old = by_bounds.get((float(chunk["start"]), float(chunk["end"])))
        if not old or old.get("status") != "complete":
            continue
        if not (bundle / chunk["transcript_path"]).exists():
            continue
        chunk["status"] = "complete"
        chunk["attempts"] = old.get("attempts", chunk.get("attempts", 0))
        carried += 1
    if carried and carried == len(job["chunks"]):
        job["status"] = "complete"
    elif carried:
        job["status"] = "partial"
    return carried


def _pending_chunks(job: dict[str, Any], bundle: Path) -> list[dict[str, Any]]:
    pending = []
    for chunk in job["chunks"]:
        done = chunk["status"] == "complete" and (bundle / chunk["transcript_path"]).exists()
        if not done:
            pending.append(chunk)
    return pending


def _raw_path(bundle: Path, chunk: dict[str, Any]) -> Path:
    """청크 응답 원문 경로. transcript_path 와 같은 자리에 .raw.json 으로 둔다."""
    transcript = str(chunk["transcript_path"])
    stem = transcript[: -len(".json")] if transcript.endswith(".json") else transcript
    return bundle / (stem + ".raw.json")


def _raw_meta(job: dict[str, Any], chunk: dict[str, Any], langs: str) -> dict[str, Any]:
    """응답 원문에 함께 저장할 꼬리표. 나중에 재사용해도 되는지 판단할 근거다."""
    return {
        "requested_langs": langs,
        "fingerprint": job.get("input", {}).get("fingerprint"),
        "chunk_index": chunk["index"],
        "chunk_start": chunk["start"],
        "chunk_end": chunk["end"],
    }


# 번역문 가드 ------------------------------------------------------------------
#
# Gemini 는 소리가 흐릿하면 "받아적기" 대신 "알아듣고 다른 언어로 다시 쓰기" 로
# 미끄러진다 (실측: DECISIONS/claude.md 2026-08-31). 번역문은 단어 수도 많고
# 문장도 자연스러워 사람 눈에는 정상으로 보이지만 원문 근거로 쓸 수 없다.
#
# 판정은 **문자 종류**로 한다. 한국어를 특별 취급하지 않는다 — 일본어 영상이
# 영어로 번역돼 와도 같은 규칙으로 잡힌다. 근거 둘 다 이미 공짜로 가진 자료다.
#   1. 같은 시간대 원어 자막의 문자 종류와 전사의 문자 종류가 다르다
#   2. 자막이 없어도, 요청한 언어의 문자 종류와 전사가 다르다
# 임계값은 영어 용어가 섞인 한국어 강의를 오탐하지 않게 잡는다.

_TRANSLATION_MIN_CHARS = 40
"""이보다 짧은 표본은 근거로 쓰지 않는다."""

_TRANSLATION_CAPTION_MIN_CHARS = 20
"""자막 표본의 최소 길이. 실제 자막 파일은 수천 자라 넉넉히 통과한다."""

_TRANSLATION_DOMINANT_SHARE = 0.3
"""이 비율을 넘는 문자 종류가 그 글의 문자 체계다."""

_TRANSLATION_TRACE_SHARE = 0.05
"""기대한 문자가 이보다 적으면 그 언어를 받아적은 것이 아니다."""

# 언어 코드 앞자리 -> 그 언어가 쓰는 문자 종류. 여기 없는 언어는 요청 언어만
# 으로는 판정하지 않는다 (자막 근거가 있으면 그것으로 판정한다).
_LANGUAGE_SCRIPTS: dict[str, str] = {
    "ko": "hangul", "ja": "japanese", "zh": "han", "yue": "han",
    "ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic", "sr": "cyrillic",
    "be": "cyrillic", "mk": "cyrillic", "kk": "cyrillic", "ky": "cyrillic",
    "tg": "cyrillic", "mn": "cyrillic",
    "el": "greek", "he": "hebrew", "yi": "hebrew",
    "ar": "arabic", "fa": "arabic", "ur": "arabic", "ps": "arabic",
    "sd": "arabic", "ug": "arabic", "ku": "arabic",
    "hi": "devanagari", "mr": "devanagari", "ne": "devanagari",
    "sa": "devanagari",
    "bn": "bengali", "as": "bengali",
    "pa": "gurmukhi", "gu": "gujarati", "or": "oriya",
    "ta": "tamil", "te": "telugu", "kn": "kannada", "ml": "malayalam",
    "si": "sinhala", "dv": "thaana",
    "th": "thai", "lo": "lao", "bo": "tibetan", "my": "myanmar",
    "km": "khmer", "am": "ethiopic", "ti": "ethiopic",
    "ka": "georgian", "hy": "armenian",
    "en": "latin", "de": "latin", "fr": "latin", "es": "latin",
    "pt": "latin", "it": "latin", "nl": "latin", "pl": "latin",
    "tr": "latin", "id": "latin", "vi": "latin", "sv": "latin",
    "cs": "latin", "sk": "latin", "sl": "latin", "hr": "latin",
    "bs": "latin", "ro": "latin", "hu": "latin", "fi": "latin",
    "et": "latin", "lv": "latin", "lt": "latin", "da": "latin",
    "no": "latin", "nb": "latin", "nn": "latin", "is": "latin",
    "ga": "latin", "cy": "latin", "ca": "latin", "eu": "latin",
    "gl": "latin", "sq": "latin", "az": "latin", "uz": "latin",
    "tk": "latin", "ms": "latin", "tl": "latin", "fil": "latin",
    "jv": "latin", "su": "latin", "sw": "latin", "af": "latin",
    "zu": "latin", "xh": "latin", "yo": "latin", "ig": "latin",
    "ha": "latin", "so": "latin", "mt": "latin", "haw": "latin",
    "mi": "latin", "la": "latin", "eo": "latin",
}

# 유니코드 블록 -> 문자 종류. 시작 코드 오름차순으로 두고 이분 탐색한다.
# 언어를 늘려도 글자당 비교 횟수는 로그로만 는다 (긴 elif 사슬을 대신한다).
_SCRIPT_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0370, 0x03FF, "greek"),
    (0x0400, 0x04FF, "cyrillic"),
    (0x0530, 0x058F, "armenian"),
    (0x0590, 0x05FF, "hebrew"),
    (0x0600, 0x06FF, "arabic"),
    (0x0700, 0x074F, "syriac"),
    (0x0750, 0x077F, "arabic"),
    (0x0780, 0x07BF, "thaana"),
    (0x0900, 0x097F, "devanagari"),
    (0x0980, 0x09FF, "bengali"),
    (0x0A00, 0x0A7F, "gurmukhi"),
    (0x0A80, 0x0AFF, "gujarati"),
    (0x0B00, 0x0B7F, "oriya"),
    (0x0B80, 0x0BFF, "tamil"),
    (0x0C00, 0x0C7F, "telugu"),
    (0x0C80, 0x0CFF, "kannada"),
    (0x0D00, 0x0D7F, "malayalam"),
    (0x0D80, 0x0DFF, "sinhala"),
    (0x0E00, 0x0E7F, "thai"),
    (0x0E80, 0x0EFF, "lao"),
    (0x0F00, 0x0FFF, "tibetan"),
    (0x1000, 0x109F, "myanmar"),
    (0x10A0, 0x10FF, "georgian"),
    (0x1200, 0x139F, "ethiopic"),
    (0x1780, 0x17FF, "khmer"),
    (0x3040, 0x30FF, "japanese"),      # 히라가나·가타카나
    (0x3131, 0x318E, "hangul"),        # 호환 자모 (가나 범위와 겹치지 않는다)
    (0x4E00, 0x9FFF, "han"),           # 한자
    (0xAC00, 0xD7A3, "hangul"),        # 완성형 한글
    (0xFB50, 0xFDFF, "arabic"),
    (0xFE70, 0xFEFF, "arabic"),
)

_SCRIPT_STARTS: tuple[int, ...] = tuple(start for start, _, _ in _SCRIPT_RANGES)


def _script_counts(text: str) -> dict[str, int]:
    """글자를 문자 종류별로 센다. 숫자·기호·공백은 세지 않는다."""
    counts: dict[str, int] = {}
    for char in text:
        code = ord(char)
        if code < 0x80:                       # ASCII 는 표를 뒤질 것도 없다
            name = "latin" if char.isalpha() else None
        else:
            index = bisect_right(_SCRIPT_STARTS, code) - 1
            name = None
            if index >= 0:
                start, end, found = _SCRIPT_RANGES[index]
                if code <= end:
                    name = found
        if name is not None:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _dominant_script(text: str) -> str | None:
    """그 글의 문자 체계. 어느 것도 뚜렷하지 않으면 None.

    일본어는 한자를 섞어 쓰므로 가나가 조금이라도 뚜렷하면 japanese 로 본다.
    그러지 않으면 한자 비중이 큰 일본어 문장이 중국어로 잡힌다.
    """
    counts = _script_counts(text)
    total = sum(counts.values())
    if not total:
        return None
    if counts.get("japanese", 0) / total >= _TRANSLATION_TRACE_SHARE:
        return "japanese"
    name, count = max(counts.items(), key=lambda item: item[1])
    return name if count / total >= _TRANSLATION_DOMINANT_SHARE else None


def _script_share(text: str, script: str) -> float:
    counts = _script_counts(text)
    total = sum(counts.values())
    if not total:
        return 0.0
    share = counts.get(script, 0)
    if script == "japanese":
        # 가나 없이 한자만 나온 구간도 일본어일 수 있다.
        share += counts.get("han", 0)
    return share / total


def _requested_script(langs: str) -> str | None:
    """요청한 언어들이 한 문자 체계로 모이면 그 이름."""
    scripts = {_LANGUAGE_SCRIPTS.get(code.strip().lower().split("-")[0])
               for code in langs.split(",") if code.strip()}
    scripts.discard(None)
    return scripts.pop() if len(scripts) == 1 else None


def _captions_are_original(language: str | None) -> bool:
    """그 자막이 영상의 원어 트랙인가.

    `-orig` 만 원어다. `ko` / `en` 같은 트랙은 YouTube 가 기계 번역한 것일 수
    있고, 실제로 영어 영상에 `ko` 를 요청하면 한국어 번역 자막이 내려온다.
    번역된 자막을 원어 근거로 쓰면 멀쩡한 전사를 번역문으로 오판해 막는다.
    """
    return bool(language) and str(language).endswith("-orig")


def _metadata_script(bundle: Path) -> str | None:
    """yt-dlp 가 말하는 이 영상의 원어. 모르면 None.

    두 가지만 본다. 둘 다 **소리에 대한 정보**다.

    1. `language` — 업로더가 붙였거나 YouTube 가 판정한 원어
    2. `automatic_captions` 의 `*-orig` 트랙 — YouTube 자체 음성 인식이 어느
       언어로 알아들었는가

    제목과 설명글의 문자는 **일부러 쓰지 않는다.** 한국어 강의에 영어 제목을
    다는 일이 흔하고(그 반대도 있다), 글자와 말이 어긋나면 멀쩡한 전사를
    번역문으로 오판해 막게 된다. 없던 실패를 만드는 근거는 쓰지 않는다.
    """
    path = bundle / "raw" / METADATA_NAME
    if not path.exists():
        return None
    try:
        payload = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    declared = payload.get("language")
    if isinstance(declared, str) and declared.strip():
        found = _requested_script(declared)
        if found is not None:
            return found
    for code in payload.get("auto_caption_langs") or []:
        if not isinstance(code, str):
            continue
        found = _requested_script(code[: -len("-orig")] if code.endswith("-orig") else code)
        if found is not None:
            return found
    return None


def _expected_script(*, captions: str, langs: str,
                     captions_language: str | None,
                     metadata_script: str | None = None) -> str | None:
    """이 구간의 전사가 어느 문자 체계여야 하는가. 모르면 None.

    근거를 센 것부터 본다. 원어 자막 > 사람이 지정한 언어 > 영상 메타데이터.
    메타데이터를 맨 뒤에 두는 이유는 앞의 둘이 이 영상에 직접 딸린 자료이고,
    메타데이터는 YouTube 의 판정이라 틀릴 여지가 조금 더 크기 때문이다.
    """
    if (_captions_are_original(captions_language)
            and len(captions) >= _TRANSLATION_CAPTION_MIN_CHARS):
        found = _dominant_script(captions)
        if found is not None:
            return found
    return _requested_script(langs) or metadata_script


def _looks_translated(transcript: str, *, captions: str, langs: str,
                      captions_language: str | None = None,
                      metadata_script: str | None = None) -> bool:
    """받아적기가 아니라 번역문으로 보이는가.

    기대하는 문자 체계를 정한 뒤, 전사에 그 문자가 사실상 없으면 번역문이다.
    """
    if len(transcript) < _TRANSLATION_MIN_CHARS:
        return False
    expected = _expected_script(captions=captions, langs=langs,
                                captions_language=captions_language,
                                metadata_script=metadata_script)
    if expected is None:
        return False
    return _script_share(transcript, expected) < _TRANSLATION_TRACE_SHARE


def _captions_evidence(bundle: Path) -> tuple[str, str | None]:
    """자막 본문과 그 언어. 언어를 모르면 근거로 쓰지 않는다."""
    path = bundle / "raw" / "captions.json"
    if not path.exists():
        return "", None
    try:
        payload = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return "", None
    cues = payload.get("cues", []) or []
    language = payload.get("language")
    if language is None:
        # `language` 가 없던 시절 파일. 그때는 ko-orig 만 받았다.
        source = str(payload.get("source", ""))
        language = source[len("youtube-"):] if source.startswith("youtube-") else None
    if payload.get("original") is False:
        # 폴백으로 받은 트랙. 번역일 수 있어 원어 근거로 쓰지 않는다.
        language = None
    return cues, language


def _captions_in_range(cues: list[dict[str, Any]], start: float, end: float) -> str:
    """그 구간과 겹치는 자막만 잇는다.

    영상 전체 자막과 청크 하나를 대조하면, 한국어 강의 중간의 영어 발표
    구간이나 음악 구간이 통째로 번역문으로 몰린다. 판단은 같은 시간대끼리
    한다.
    """
    parts = []
    for cue in cues:
        try:
            cue_start = float(cue.get("start", 0.0))
            cue_end = float(cue.get("end", cue_start))
        except (TypeError, ValueError):
            continue
        if cue_end >= start and cue_start <= end:
            parts.append(str(cue.get("text", "")))
    return " ".join(parts)


def _reusable_raw(bundle: Path, job: dict[str, Any], chunk: dict[str, Any],
                  langs: str) -> Path | None:
    """호출 없이 다시 파싱할 수 있는 저장된 응답.

    언어 설정뿐 아니라 입력 오디오와 청크 구간까지 같아야 쓴다. `--force` 로
    다시 받아 오디오 형식이 바뀌거나 `--chunk-max-secs` 를 고치면 경계가
    옮겨가는데, 그때 옛 응답을 그대로 쓰면 타임스탬프가 조용히 어긋난다.
    """
    path = _raw_path(bundle, chunk)
    if not path.exists():
        return None
    try:
        stored = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    expected = _raw_meta(job, chunk, langs)
    if any(stored.get(key) != value for key, value in expected.items()):
        # 꼬리표가 없던 시절의 응답도 여기서 걸린다. 조용히 어긋난 결과를
        # 내느니 호출 한 번을 더 쓰는 편이 낫다. 원문은 그대로 남으므로
        # `transcribe.py --from-raw` 로 수동 복구할 수 있다.
        return None
    return path


def stage_transcribe(bundle: Path, job: dict[str, Any], *, ledger: Path, api_key: str,
                     daily_limit: int, rpm_limit: int | None,
                     request_interval: float, free_mode: bool = True,
                     transcriber=None, force: bool = False) -> dict[str, Any]:
    """청크별 Gemini 전사. 완료 청크도, 응답 원문이 남은 청크도 재호출하지 않는다."""
    pending = _pending_chunks(job, bundle)
    if not pending:
        if force:
            # 조용히 아무것도 안 하면 사용자는 다시 받아쓴 줄 안다. 완료된
            # 청크를 여기서 재호출하지는 않는다 — 쿼터를 쓰는 결정은 사람이 한다.
            _log("  경고: --force 로는 이미 완료된 청크를 다시 전사하지 않는다. "
                 "정말 다시 받아쓰려면 plan 을 함께 돌려라 "
                 "(--stages plan,transcribe --force). 청크마다 Gemini 를 부른다.")
        _log("  전사 완료된 청크만 있음, 호출 없음")
        job["status"] = "complete"
        _save_job(bundle, job)
        return job

    caption_cues, captions_language = _captions_evidence(bundle)
    codes = job["config"]["language_codes"]
    langs = ",".join(codes) if codes else "auto"
    metadata_script = _metadata_script(bundle)

    # 근거가 하나도 없으면 이 실행은 번역문을 잡지 못한다. 조용히 넘어가면
    # 사람은 검사를 통과한 줄 안다. 상태에도 남겨 나중에 확인할 수 있게 한다.
    all_captions = " ".join(str(cue.get("text", "")) for cue in caption_cues)
    guard_basis = _expected_script(captions=all_captions, langs=langs,
                                   captions_language=captions_language,
                                   metadata_script=metadata_script)
    job["translation_guard"] = "active" if guard_basis else "skipped"
    if guard_basis is None:
        _log("  경고: 원어 자막도, 지정 언어도, 영상 메타데이터도 없어 "
             "번역문 검사를 하지 않는다. --language 로 원어를 지정하면 검사한다")

    # 저장된 응답으로 되살릴 수 있는 청크는 쿼터 추정에서 뺀다.
    reusable = {} if force else {
        chunk["index"]: path
        for chunk in pending
        if (path := _reusable_raw(bundle, job, chunk, langs)) is not None
    }
    to_call = [chunk for chunk in pending if chunk["index"] not in reusable]
    if to_call:
        status_line = usage.preflight(ledger, api_key, len(to_call), daily_limit=daily_limit,
                                      rpm_limit=rpm_limit, free_mode=free_mode)
        _log(usage.format_status(status_line))

    job["status"] = "running"
    _save_job(bundle, job)
    calls_made = 0

    for chunk in pending:
        chunk_mp3 = bundle / chunk["path"]
        raw_path = _raw_path(bundle, chunk)
        stored = reusable.get(chunk["index"])

        if stored is not None:
            try:
                result = transcribe_mod.from_raw(stored)
            except Exception as error:
                chunk["status"] = "failed"
                chunk["error"] = str(error)[:500]
                job["status"] = "partial"
                _save_job(bundle, job)
                raise StageError(
                    "청크 %d: 저장된 응답으로 복구하지 못했습니다. 다시 호출하려면 "
                    "%s 를 삭제하고 재실행하세요. 원인: %s"
                    % (chunk["index"], raw_path, error)
                ) from error
            _log("  청크 %d: 저장된 응답 재사용 (Gemini 호출 없음)" % chunk["index"])
        else:
            if not chunk_mp3.exists():
                raise StageError("청크 오디오가 없습니다: %s" % chunk_mp3)
            if calls_made:
                # 고정 간격으로 무조건 쉬면 분당 한도가 비어 있어도 기다린다.
                # 원장이 최근 1분 시도를 들고 있으므로 창이 찼을 때만 쉰다.
                delay = (usage.seconds_until_slot(ledger, api_key, rpm_limit=rpm_limit)
                         if rpm_limit else request_interval)
                if delay > 0:
                    _log("  분당 한도가 차 %.0f초 기다린다" % delay)
                    time.sleep(delay)
            chunk["attempts"] += 1
            chunk["status"] = "running"
            _save_job(bundle, job)  # 요청 직전 checkpoint
            usage.record_attempt(ledger, api_key)
            calls_made += 1
            try:
                result = (transcriber(str(chunk_mp3), langs) if transcriber is not None
                          else transcribe_mod.transcribe(
                              str(chunk_mp3), langs, raw_path=raw_path,
                              meta=_raw_meta(job, chunk, langs)))
            except Exception as error:
                chunk["status"] = "failed"
                chunk["error"] = str(error)[:500]
                job["status"] = "partial"
                _save_job(bundle, job)
                hint = ""
                if raw_path.exists():
                    hint = (" 응답 원문은 %s 에 남아 있으므로 재실행 시 호출 없이 "
                            "다시 시도한다." % raw_path)
                raise StageError(
                    "청크 %d 전사 실패. 완료된 청크는 보존된다. 같은 명령을 다시 "
                    "실행하면 이어서 진행한다.%s 원인: %s"
                    % (chunk["index"], hint, error)
                ) from error

        text = " ".join(str(word.get("text", "")) for word in result["words"])
        in_range = _captions_in_range(caption_cues, float(chunk["start"]),
                                      float(chunk["end"]))
        if _looks_translated(text, captions=in_range, langs=langs,
                             captions_language=captions_language,
                             metadata_script=metadata_script):
            chunk["status"] = "failed"
            chunk["error"] = "번역문으로 보임"
            job["status"] = "partial"
            _save_job(bundle, job)
            raise StageError(
                "청크 %d 는 받아적기가 아니라 번역문으로 보인다 "
                "(전사 문자 체계 %s, 기대 %s). "
                "원문이 아니므로 근거로 쓸 수 없어 여기서 멈춘다 — 남은 청크의 "
                "호출을 아낀다. "
                "이어가려면 --language 로 원어를 지정하고 같은 명령을 다시 "
                "실행해라 (예: --language ko-KR). 언어를 바꾸면 config 가 달라져 "
                "이 영상의 청크를 전부 다시 부른다. 설정을 바꾸지 않고 다시 "
                "실행하면 저장된 응답을 그대로 다시 읽어 같은 판정이 나온다 "
                "(호출은 쓰지 않는다). 판정이 틀렸다면 그 언어를 --language 로 "
                "지정하거나 `python src/transcribe.py --from-raw %s <out.json>` "
                "로 호출 없이 수동 복구할 수 있다."
                % (chunk["index"], _dominant_script(text) or "불명",
                   _expected_script(captions=in_range, langs=langs,
                                    captions_language=captions_language,
                                    metadata_script=metadata_script) or "불명",
                   _raw_path(bundle, chunk))
            )

        offset = float(chunk["start"])
        for word in result["words"]:
            word["start"] = round(float(word["start"]) + offset, 3)
            word["end"] = round(float(word["end"]) + offset, 3)
        result.update({
            "video_id": job["video_id"],
            "chunk_index": chunk["index"],
            "chunk_start": chunk["start"],
            "chunk_end": chunk["end"],
        })
        _write_json(bundle / chunk["transcript_path"], result)
        chunk["status"] = "complete"
        chunk["error"] = None
        _save_job(bundle, job)
        repairs = len(result.get("timestamp_repairs", []))
        _log("  청크 %d: %d단어%s"
             % (chunk["index"], len(result["words"]),
                (", timestamp 보정 %d" % repairs) if repairs else ""))

    job["status"] = "complete"
    _save_job(bundle, job)
    return job


def stage_assemble(bundle: Path, job: dict[str, Any]) -> dict[str, Any]:
    """청크 transcript 를 화자 정합해 하나로 잇는다. Gemini 호출 없음."""
    chunks = []
    for chunk in job["chunks"]:
        path = bundle / chunk["transcript_path"]
        if not path.exists():
            raise StageError(
                "청크 transcript 가 없습니다: %s. transcribe 단계를 먼저 실행하세요." % path)
        chunks.append(_read_json(path))

    # reconcile_chunks 가 transcript.json 형태를 그대로 돌려준다.
    # video_id 만 job 기준으로 확정한다.
    payload = speakers.reconcile_chunks(chunks)
    payload["video_id"] = job["video_id"]
    _write_json(bundle / "derived" / "transcript.json", payload)

    unresolved = sum(1 for w in payload["words"]
                     if w.get("speaker_status") == "unresolved")
    removed = payload.get("speaker_mapping", {}).get("duplicates_removed", 0)
    _log("  transcript %d단어, 화자 미확정 %d단어, overlap 중복 제거 %d"
         % (len(payload["words"]), unresolved, removed))
    return payload


def stage_merge(bundle: Path) -> dict[str, Any]:
    """YouTube 자막으로 누락 영어 용어를 복원한다. Gemini 호출 없음."""
    transcript = bundle / "derived" / "transcript.json"
    captions = bundle / "raw" / "captions.json"
    output = bundle / "derived" / "merged.json"
    if not transcript.exists():
        raise StageError("derived/transcript.json 이 없습니다. assemble 단계를 먼저 실행하세요.")
    if not captions.exists():
        _write_json(captions, {"source": "youtube", "language": None,
                               "video_id": bundle.name, "cues": []})
    merged = merge_mod.merge_files(transcript, captions, output)
    inserted = sum(1 for w in merged["words"] if w.get("origin") == "youtube")
    _log("  merged %d단어, youtube 삽입 %d" % (len(merged["words"]), inserted))
    return merged


def stage_render(bundle: Path, *, width: int) -> tuple[Path, Path]:
    """자막과 평문을 만든다. 단어 소실 0. Gemini 호출 없음."""
    source = bundle / "derived" / "merged.json"
    if not source.exists():
        source = bundle / "derived" / "transcript.json"
    if not source.exists():
        raise StageError("derived 전사가 없습니다. merge 또는 assemble 을 먼저 실행하세요.")
    srt, txt = render_mod.render(source, bundle / "derived" / "output", width)

    payload = _read_json(source)
    rendered = sum(len(cue.words) for cue in render_mod.build_cues(payload["words"], width))
    if rendered != len(payload["words"]):
        raise StageError("렌더 단어 소실: %d/%d" % (rendered, len(payload["words"])))
    _log("  %s, %s — %d단어 100%% 보존" % (srt.name, txt.name, rendered))
    return srt, txt


def stage_chapters(bundle: Path, *, url: str | None = None) -> dict[str, Any]:
    result = chapters.build(bundle, url=url)
    needs_titles = sum(1 for item in result["chapters"] if item["needs_title"])
    _log("  chapters %d개, host 제목 후보 %d개"
         % (len(result["chapters"]), needs_titles))
    return result


def ensure_video(bundle: Path, *, url: str | None = None) -> Path | None:
    """프레임을 뽑기 직전에만 영상을 확보한다. Gemini 호출 없음.

    기본 분석은 영상을 받지 않으므로 프레임 요청 시점에 없는 것이 정상이다.
    URL 은 `job.json` 의 `input.source` 에 이미 있다 — 새 영속 설정을 만들지
    않는다. 이미 받아둔 영상은 그대로 쓰고 절대 지우지 않는다.
    """
    found = visual.source_video(bundle)
    if found is not None:
        return found
    if url is None:
        job_path = bundle / "job.json"
        if not job_path.exists():
            _log("  영상이 없고 job.json 도 없어 원본 URL 을 모른다. "
                 "fetch/plan 을 먼저 돌리거나 URL 을 직접 줘라")
            return None
        try:
            url = _read_json(job_path).get("input", {}).get("source")
        except (OSError, json.JSONDecodeError):
            url = None
        if not url:
            _log("  job.json 에 원본 URL 이 없다")
            return None
    _log("  영상이 없다. 프레임용으로 지금 받는다 (%s)" % url)
    raw = bundle / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    downloaded, error = _download(url, VIDEO_FORMAT, raw, "source_video",
                                  visual.VIDEO_NAMES)
    if downloaded is None:
        tail = error.strip().splitlines()
        _log("  경고: 영상 취득 실패 (%s)" % (tail[-1][:200] if tail else "원인 불명"))
        return None
    _log("  영상 %.1fMB (%s)" % (downloaded.stat().st_size / 1048576, downloaded.name))
    return visual.source_video(bundle)


def stage_visual(bundle: Path, *, at: list[float] | None = None,
                 max_frames: int = visual.DEFAULT_MAX_FRAMES,
                 url: str | None = None, acquire: bool = True,
                 keep_video: bool = False) -> dict[str, Any]:
    """화면 참조·복원 용어 시각의 프레임을 뽑는다 (CONTRACT 11절). Gemini 호출 없음.

    `acquire` 가 거짓이면 이미 받아둔 영상만 쓴다. `--skip-video` 로 껐는데
    여기서 곧바로 받아오면 그 옵션이 무의미해진다.

    프레임을 뽑고 나면 영상은 쓸 데가 없으므로 놓아준다 (`keep_video` 로 끈다).
    남는 것은 프레임 jpg 이고, 나중에 다른 시각이 필요하면 `ensure_video` 가
    `job.json` 의 원본 URL 로 다시 받는다. 실측 14.5~16MB 를 회수한다.
    """
    # 전사가 없으면 visual.build 가 어차피 실패한다. 쓰지도 못할 영상을 먼저
    # 받아 버리지 않는다.
    if not any((bundle / "derived" / name).exists()
               for name in ("merged.json", "transcript.json")):
        raise StageError(
            "derived 전사가 없습니다. assemble/merge 단계를 먼저 실행하세요.")
    video_path = ensure_video(bundle, url=url) if acquire else visual.source_video(bundle)
    result = visual.build(bundle, at=at, max_frames=max_frames)
    if video_path is None and not result["frames"]:
        # 영상을 못 구한 것과 후보가 없던 것은 사용자가 할 일이 다르다.
        result["note"] = ("영상을 확보하지 못해 프레임을 뽑지 못했다. "
                          "후보 시각 %d개는 계산했다." % result["candidates_considered"])
        _write_json(bundle / "derived" / "frames.json", result)
    frames = result["frames"]
    if frames:
        ocr = sum(1 for frame in frames if frame.get("ocr_text"))
        _log("  프레임 %d장 / 후보 %d, OCR %d장"
             % (len(frames), result["candidates_considered"], ocr))
    else:
        _log("  프레임 0장 / 후보 %d — %s"
             % (result["candidates_considered"], result.get("note") or ""))
    if frames and not keep_video:
        released = _release_video(bundle)
        if released:
            _log("  영상 %s 를 지웠다 (%.1fMB 회수). 필요하면 원본 URL 로 다시 받는다"
                 % released)
    return result


def _release_video(bundle: Path) -> tuple[str, float] | None:
    """프레임을 다 뽑은 영상을 지운다. 다시 받을 수 없으면 두고 본다."""
    found = visual.source_video(bundle)
    if found is None:
        return None
    job_path = bundle / "job.json"
    source_url = None
    if job_path.exists():
        try:
            source_url = (_read_json(job_path).get("input") or {}).get("source")
        except (OSError, json.JSONDecodeError):
            source_url = None
    if not source_url:
        # 다시 받을 주소를 모르면 지우지 않는다. 되돌릴 수 없는 삭제다.
        return None
    size_mb = found.stat().st_size / 1048576
    found.unlink()
    return found.name, size_mb


def stage_index(bundle: Path) -> Path:
    """SQLite 색인을 만든다. Gemini 호출 없음."""
    index = context.build_index(bundle)
    _log("  %s" % index.name)
    return index


# ----------------------------------------------------------------------------- run

def run(url: str, *, bundle_root: Path = Path("data"),
        stages: tuple[str, ...] | None = None,
        chunk_max_secs: float = audio.DEFAULT_CHUNK_MAX_SECS,
        overlap_secs: float = audio.DEFAULT_OVERLAP_SECS,
        language_codes: list[str] | None = None,
        width: int = DEFAULT_WIDTH, daily_limit: int = DEFAULT_DAILY_LIMIT,
        rpm_limit: int | None = DEFAULT_RPM_LIMIT,
        request_interval: float = DEFAULT_REQUEST_INTERVAL,
        ledger: Path | None = None, force: bool = False,
        video: bool = True, keep_video: bool = False,
        at: list[float] | None = None,
        max_frames: int = visual.DEFAULT_MAX_FRAMES,
        transcriber=None) -> dict[str, Any]:
    video_id = video_id_from_url(url)
    bundle = bundle_path(bundle_root, video_id)
    bundle.mkdir(parents=True, exist_ok=True)
    ledger = ledger or (Path(bundle_root) / "usage.json")
    summary: dict[str, Any] = {"video_id": video_id, "bundle": str(bundle), "stages": {}}
    job: dict[str, Any] | None = None

    for stage in resolve_stages(stages):
        _log("[%s]" % stage)
        if stage == "fetch":
            summary["stages"][stage] = stage_fetch(bundle, url, force=force, video=video)
        elif stage == "plan":
            job = stage_plan(bundle, url, chunk_max_secs=chunk_max_secs,
                             overlap_secs=overlap_secs, language_codes=language_codes,
                             force=force)
            summary["stages"][stage] = {"chunks": len(job["chunks"])}
        elif stage == "transcribe":
            job = job if job is not None else _load_job(bundle)
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise StageError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
            job = stage_transcribe(bundle, job, ledger=ledger, api_key=api_key,
                                   daily_limit=daily_limit, rpm_limit=rpm_limit,
                                   request_interval=request_interval,
                                   transcriber=transcriber, force=force)
            summary["stages"][stage] = {"status": job["status"]}
        elif stage == "assemble":
            job = job if job is not None else _load_job(bundle)
            payload = stage_assemble(bundle, job)
            summary["stages"][stage] = {"words": len(payload["words"])}
        elif stage == "merge":
            merged = stage_merge(bundle)
            summary["stages"][stage] = {
                "words": len(merged["words"]),
                "inserted": sum(1 for w in merged["words"] if w.get("origin") == "youtube"),
            }
        elif stage == "chapters":
            result = stage_chapters(bundle, url=url)
            summary["stages"][stage] = {
                "chapters": len(result["chapters"]),
                "needs_titles": sum(1 for item in result["chapters"] if item["needs_title"]),
            }
        elif stage == "render":
            srt, txt = stage_render(bundle, width=width)
            summary["stages"][stage] = {"srt": str(srt), "txt": str(txt)}
        elif stage == "visual":
            frames = stage_visual(bundle, at=at, max_frames=max_frames, url=url,
                                  acquire=video, keep_video=keep_video)
            summary["stages"][stage] = {
                "frames": len(frames["frames"]),
                "candidates": frames["candidates_considered"],
            }
        elif stage == "index":
            summary["stages"][stage] = {"index": str(stage_index(bundle))}
    return summary


def _guard_state(job: dict[str, Any], complete: int) -> str:
    """번역문 검사가 어느 상태인지. `null` 은 돌려주지 않는다.

    CONTRACT 4절은 판정하지 않았다는 사실을 남기라고 한다. 그런데 값을 쓰는
    곳이 transcribe 단계뿐이라, 그 단계를 아직 돌리지 않은 번들은 키가 없어서
    `null` 이 나갔다. 읽는 쪽에서 "검사하고 통과" 와 "검사 자체를 안 함" 이
    구분되지 않는다.

      active   검사했고 통과
      skipped  검사했으나 근거(원어 자막·요청 언어·메타데이터)가 없었다
      not-run  전사 단계를 아직 돌리지 않았다
      unknown  전사는 끝났는데 기록이 없다 (기록이 없던 시절의 번들)
    """
    recorded = job.get("translation_guard")
    if recorded in ("active", "skipped"):
        return recorded
    return "unknown" if complete else "not-run"


def status(bundle: Path, *, ledger: Path | None = None, api_key: str | None = None,
           daily_limit: int = DEFAULT_DAILY_LIMIT) -> dict[str, Any]:
    job_path = bundle / "job.json"
    info: dict[str, Any] = {"video_id": bundle.name, "bundle": str(bundle),
                            "exists": bundle.exists()}
    if job_path.exists():
        job = _read_json(job_path)
        info["status"] = job["status"]
        complete = sum(1 for c in job["chunks"] if c["status"] == "complete")
        # 장부와 별개로 전사 파일이 실제로 몇 개 있는지 센다. 존재 확인만 하므로
        # 응답 원문을 파싱하지 않는다 (원문은 청크당 700KB 급이다).
        on_disk = sum(1 for c in job["chunks"]
                      if c.get("transcript_path")
                      and (bundle / c["transcript_path"]).exists())
        info["chunks"] = {
            "total": len(job["chunks"]),
            "complete": complete,
            "transcripts_on_disk": on_disk,
            "failed": [c["index"] for c in job["chunks"] if c["status"] == "failed"],
        }
        # 둘이 어긋나면 장부만 초기화된 옛 번들이다. 그대로 0/N 을 보여 주면
        # 분석이 안 된 줄 알고 다시 돌린다. 무엇을 하면 되는지 함께 적는다.
        if on_disk > complete:
            info["chunks"]["note"] = (
                "전사 파일 %d개가 장부보다 앞서 있다. transcribe 를 다시 돌리면 "
                "저장된 응답으로 이어받는다(설정이 같으면 Gemini 호출 없음)." % on_disk
            )
        # 번역문 검사를 실제로 했는지. 로그는 흘러가므로 여기에 남는다.
        info["translation_guard"] = _guard_state(job, complete)
    info["artifacts"] = {
        name: (bundle / rel).exists() for name, rel in (
            ("captions", "raw/captions.json"),
            ("transcript", "derived/transcript.json"),
            ("merged", "derived/merged.json"),
            ("chapters", "derived/chapters.json"),
            ("srt", "derived/output.srt"),
            ("txt", "derived/output.txt"),
            ("frames", "derived/frames.json"),
            ("index", "index.sqlite3"),
        )
    }
    # 자막은 선택 자료지만, 0 cue 가 "원래 없는 영상" 인지 "취득이 고장난 것"
    # 인지는 구분돼야 한다. yt-dlp 나 YouTube 가 바뀌면 조용히 후자가 된다.
    captions_path = bundle / "raw" / "captions.json"
    info["captions"] = None
    if captions_path.exists():
        try:
            payload = _read_json(captions_path)
        except (OSError, json.JSONDecodeError) as error:
            info["captions"] = {"cues": 0, "language": None, "original": False,
                                "failed": True, "error": str(error)[:200]}
        else:
            info["captions"] = {
                "cues": len(payload.get("cues", []) or []),
                "language": payload.get("language"),
                "original": bool(payload.get("original")),
                "failed": bool(payload.get("error")) or payload.get("language") is None,
                "error": payload.get("error"),
            }

    # 자막이 없어도 메타데이터가 원어를 알려주면 번역문 검사가 산다.
    info["metadata_script"] = _metadata_script(bundle)

    # 기본에서 빠진 단계의 산출물은 없는 것이 정상이다. 읽는 쪽이 그것을
    # 실패로 오인하지 않도록 이름을 함께 준다.
    info["optional_artifacts"] = [name for stage in OPTIONAL_STAGES
                                  for name in STAGE_ARTIFACTS.get(stage, ())]
    if ledger is not None and api_key:
        current = usage.get_usage(ledger, api_key)
        info["usage"] = {**current, "daily_limit": daily_limit,
                         "remaining": max(0, daily_limit - current["attempts"]),
                         "accuracy": "local estimate"}
    return info


PURGE_SCOPES = ("chunks", "video", "derived", "raw", "all")


def purge(bundle: Path, *, scope: str = "derived") -> list[str]:
    """raw 자료 삭제와 derived 재생성을 위한 명시적 삭제 (CONTRACT 12절).

    `chunks` 는 전사용 청크 오디오만 지운다. 청크는 원본 오디오에서 언제든
    다시 뽑을 수 있고(`stage_plan` 이 없으면 자동으로 다시 뽑는다), 전사가
    끝난 뒤에는 재개에도 쓰이지 않는다. bundle 용량의 20~25% 를 차지한다.

    `video` 는 프레임용 영상만 지운다. `visual` 이 끝나면 자동으로 지우지만,
    옛 bundle 이나 `keep_video` 로 남긴 것을 정리할 때 쓴다. 필요해지면
    `job.json` 의 원본 URL 로 다시 받는다.
    """
    if scope not in PURGE_SCOPES:
        raise ValueError("scope 는 %s 여야 합니다." % " | ".join(PURGE_SCOPES))
    removed: list[str] = []
    targets: list[Path] = []
    if scope == "chunks":
        # 원본이 없으면 청크가 이 bundle 의 유일한 오디오다. 지우면 되돌릴 수
        # 없으므로 거부한다.
        if audio.source_audio(bundle) is None:
            raise ValueError(
                "raw 에 원본 오디오가 없어 청크를 다시 만들 수 없습니다. "
                "청크가 이 bundle 의 유일한 오디오이므로 지우지 않습니다.")
        targets.append(bundle / "raw" / "audio")
    if scope == "video":
        found = visual.source_video(bundle)
        if found is not None:
            targets.append(found)
    if scope in {"derived", "all"}:
        targets += [bundle / "derived", bundle / "index.sqlite3"]
    if scope in {"raw", "all"}:
        targets += [bundle / "raw"]
    if scope == "all":
        targets.append(bundle / "job.json")
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(str(target))
        elif target.exists():
            target.unlink()
            removed.append(str(target))
    return removed


# ----------------------------------------------------------------------------- cli

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="전체 또는 일부 단계 실행")
    run_cmd.add_argument("url")
    run_cmd.add_argument("--bundle-root", type=Path, default=Path("data"))
    run_cmd.add_argument("--stages", default=None,
                         help="쉼표 구분. 생략하면 기본(%s), all 이면 전체"
                              % ",".join(DEFAULT_STAGES))
    run_cmd.add_argument("--chunk-max-secs", type=float, default=audio.DEFAULT_CHUNK_MAX_SECS)
    run_cmd.add_argument("--overlap-secs", type=float, default=audio.DEFAULT_OVERLAP_SECS)
    run_cmd.add_argument("--language", default=None, help="쉼표 구분. 생략하면 자동 감지")
    run_cmd.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    run_cmd.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)
    run_cmd.add_argument("--rpm-limit", type=int, default=DEFAULT_RPM_LIMIT)
    run_cmd.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL)
    run_cmd.add_argument("--force", action="store_true", help="캐시를 무시하고 다시 만든다")
    run_cmd.add_argument("--skip-video", action="store_true",
                         help="영상을 받지 않는다. 프레임이 필요해지면 그때 받는다")
    run_cmd.add_argument("--keep-video", action="store_true",
                         help="프레임을 뽑은 뒤에도 영상을 지우지 않는다")
    run_cmd.add_argument("--at", default=None, help="프레임을 뽑을 시각. 쉼표 구분 초")
    run_cmd.add_argument("--max-frames", type=int, default=visual.DEFAULT_MAX_FRAMES)

    status_cmd = sub.add_parser("status", help="작업 상태와 로컬 사용량 추정")
    status_cmd.add_argument("video_id")
    status_cmd.add_argument("--bundle-root", type=Path, default=Path("data"))
    status_cmd.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)

    purge_cmd = sub.add_parser("purge", help="영상 자료 삭제")
    purge_cmd.add_argument("video_id")
    purge_cmd.add_argument("--bundle-root", type=Path, default=Path("data"))
    purge_cmd.add_argument("--scope", choices=list(PURGE_SCOPES), default="derived",
                           help="chunks 는 전사용 청크 오디오만 지운다 (원본 오디오에서 재생성 가능)")

    args = parser.parse_args()

    if args.command == "run":
        codes = None
        if args.language:
            codes = [s.strip() for s in args.language.split(",") if s.strip()]
        at = [float(s) for s in args.at.split(",") if s.strip()] if args.at else None
        summary = run(args.url, bundle_root=args.bundle_root,
                      stages=resolve_stages(args.stages),
                      chunk_max_secs=args.chunk_max_secs, overlap_secs=args.overlap_secs,
                      language_codes=codes, width=args.width, daily_limit=args.daily_limit,
                      rpm_limit=args.rpm_limit, request_interval=args.request_interval,
                      force=args.force, video=not args.skip_video,
                      keep_video=args.keep_video, at=at,
                      max_frames=args.max_frames)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    elif args.command == "status":
        bundle = bundle_path(args.bundle_root, args.video_id)
        ledger = Path(args.bundle_root) / "usage.json"
        key = os.environ.get("GEMINI_API_KEY")
        print(json.dumps(
            status(bundle, ledger=ledger if key else None, api_key=key,
                   daily_limit=args.daily_limit), ensure_ascii=False, indent=1))
    elif args.command == "purge":
        removed = purge(bundle_path(args.bundle_root, args.video_id), scope=args.scope)
        print(json.dumps({"removed": removed}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
