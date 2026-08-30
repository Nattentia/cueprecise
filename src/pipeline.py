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
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audio
import context
import fetch_youtube
import merge as merge_mod
import render as render_mod
import speakers
import transcribe as transcribe_mod
import usage
import visual

# transcribe 임포트는 SDK 를 요구하지 않는다. google-genai 는 실제 호출 경로
# (transcribe.request_raw) 안에서만 불러오므로 fetch/merge/render/index/status
# 와 저장된 응답 재파싱은 SDK 없이 동작한다.

STAGES = ("fetch", "plan", "transcribe", "assemble", "merge", "render", "visual", "index")

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

def _remove_stale_sources(bundle: Path, names: tuple[str, ...]) -> list[str]:
    """--force 로 다시 받기 전에 이전 원본을 지운다.

    형식이 바뀌면(mp3 -> webm, source.mp4 -> source_video.mp4) 옛 파일이 그대로
    남아 용량만 두 배가 된다. 다시 받는 자리이므로 지워도 잃는 것이 없다.
    """
    removed: list[str] = []
    for name in names:
        path = bundle / "raw" / name
        if path.exists():
            path.unlink()
            removed.append(name)
    return removed


def stage_fetch(bundle: Path, url: str, *, force: bool = False,
                video: bool = True) -> dict[str, Any]:
    """오디오·영상·자막을 받는다. Gemini 호출 없음."""
    raw = bundle / "raw"
    captions = raw / "captions.json"
    raw.mkdir(parents=True, exist_ok=True)

    source = audio.source_audio(bundle)
    if force or source is None:
        if force:
            _remove_stale_sources(bundle, audio.AUDIO_NAMES)
        command = [
            "yt-dlp", "--no-playlist", "-f", AUDIO_FORMAT,
            "-o", str(raw / "source") + ".%(ext)s", url,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        source = audio.source_audio(bundle)
        if result.returncode != 0 or source is None:
            raise StageError("오디오 다운로드 실패: " + (result.stderr or "")[-500:])
        _log("  오디오 %.1fMB (%s)" % (source.stat().st_size / 1048576, source.name))
    else:
        _log("  오디오 재사용 (%s)" % source.name)

    # 영상은 프레임 추출에만 쓴다. 자막과 마찬가지로 실패해도 치명이 아니다.
    if video:
        existing = visual.source_video(bundle)
        if existing is not None and not force:
            _log("  영상 재사용 (%s)" % existing.name)
        else:
            if force:
                _remove_stale_sources(bundle, visual.VIDEO_NAMES)
            command = [
                "yt-dlp", "--no-playlist", "-f", VIDEO_FORMAT,
                "-o", str(raw / "source_video") + ".%(ext)s", url,
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            downloaded = visual.source_video(bundle)
            if result.returncode != 0 or downloaded is None:
                tail = (result.stderr or "").strip().splitlines()
                _log("  경고: 영상 취득 실패, 프레임 추출을 건너뛴다 (%s)"
                     % (tail[-1][:200] if tail else "원인 불명"))
            else:
                _log("  영상 %.1fMB (%s)"
                     % (downloaded.stat().st_size / 1048576, downloaded.name))

    if force or not captions.exists():
        try:
            fetch_youtube.fetch(url, captions)
        except Exception as error:  # 자막은 선택 자료다. 없어도 파이프라인은 진행한다.
            _log("  경고: 자막 취득 실패, 영어 용어 복원을 건너뛴다 (%s)" % error)
            _write_json(captions, {"source": "youtube-ko-orig",
                                   "video_id": bundle.name, "cues": []})
    cue_count = len(_read_json(captions).get("cues", []))
    _log("  자막 %d cues" % cue_count)
    found = visual.source_video(bundle)
    return {"source_audio": str(source), "captions": str(captions), "cues": cue_count,
            "video": str(found) if found else None}


def stage_plan(bundle: Path, url: str, *, chunk_max_secs: float, overlap_secs: float,
               language_codes: list[str] | None, diarization: bool,
               force: bool = False) -> dict[str, Any]:
    """청크 계획과 분할. Gemini 호출 없음."""
    job_path = bundle / "job.json"
    source = audio.source_audio(bundle)
    if source is None:
        raise StageError("raw 에 원본 오디오가 없습니다. fetch 단계를 먼저 실행하세요.")

    config = {"chunk_max_secs": chunk_max_secs, "overlap_secs": overlap_secs,
              "language_codes": language_codes, "diarization": diarization}

    if job_path.exists() and not force:
        job = _read_json(job_path)
        if (job.get("input", {}).get("fingerprint") == audio.file_fingerprint(source)
                and job.get("config") == config):
            missing = [c for c in job["chunks"] if not (bundle / c["path"]).exists()]
            if missing:
                audio.extract_chunks(source, bundle, missing)
            _log("  기존 계획 재사용: 청크 %d개" % len(job["chunks"]))
            return job
        _log("  fingerprint/config 변경 감지, 계획을 다시 세운다")

    job = audio.create_job(
        source, bundle, bundle.name, url,
        chunk_max_secs=chunk_max_secs, overlap_secs=overlap_secs,
        language_codes=language_codes, diarization=diarization,
    )
    _log("  청크 %d개" % len(job["chunks"]))
    return job


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
        _log("  전사 완료된 청크만 있음, 호출 없음")
        job["status"] = "complete"
        _save_job(bundle, job)
        return job

    codes = job["config"]["language_codes"]
    langs = ",".join(codes) if codes else "auto"

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
                time.sleep(request_interval)
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
        _write_json(captions, {"source": "youtube-ko-orig",
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


def stage_visual(bundle: Path, *, at: list[float] | None = None,
                 max_frames: int = visual.DEFAULT_MAX_FRAMES) -> dict[str, Any]:
    """화면 참조·복원 용어 시각의 프레임을 뽑는다 (CONTRACT 11절). Gemini 호출 없음."""
    result = visual.build(bundle, at=at, max_frames=max_frames)
    frames = result["frames"]
    if frames:
        ocr = sum(1 for frame in frames if frame.get("ocr_text"))
        _log("  프레임 %d장 / 후보 %d, OCR %d장"
             % (len(frames), result["candidates_considered"], ocr))
    else:
        _log("  프레임 0장 / 후보 %d — %s"
             % (result["candidates_considered"], result.get("note") or ""))
    return result


def stage_index(bundle: Path) -> Path:
    """SQLite 색인을 만든다. Gemini 호출 없음."""
    index = context.build_index(bundle)
    _log("  %s" % index.name)
    return index


# ----------------------------------------------------------------------------- run

def run(url: str, *, bundle_root: Path = Path("data"), stages: tuple[str, ...] = STAGES,
        chunk_max_secs: float = audio.DEFAULT_CHUNK_MAX_SECS,
        overlap_secs: float = audio.DEFAULT_OVERLAP_SECS,
        language_codes: list[str] | None = None, diarization: bool = True,
        width: int = DEFAULT_WIDTH, daily_limit: int = DEFAULT_DAILY_LIMIT,
        rpm_limit: int | None = DEFAULT_RPM_LIMIT,
        request_interval: float = DEFAULT_REQUEST_INTERVAL,
        ledger: Path | None = None, force: bool = False,
        video: bool = True, at: list[float] | None = None,
        max_frames: int = visual.DEFAULT_MAX_FRAMES,
        transcriber=None) -> dict[str, Any]:
    video_id = video_id_from_url(url)
    bundle = bundle_path(bundle_root, video_id)
    bundle.mkdir(parents=True, exist_ok=True)
    ledger = ledger or (Path(bundle_root) / "usage.json")
    summary: dict[str, Any] = {"video_id": video_id, "bundle": str(bundle), "stages": {}}
    job: dict[str, Any] | None = None

    for stage in stages:
        if stage not in STAGES:
            raise ValueError("알 수 없는 단계: %s" % stage)
        _log("[%s]" % stage)
        if stage == "fetch":
            summary["stages"][stage] = stage_fetch(bundle, url, force=force, video=video)
        elif stage == "plan":
            job = stage_plan(bundle, url, chunk_max_secs=chunk_max_secs,
                             overlap_secs=overlap_secs, language_codes=language_codes,
                             diarization=diarization, force=force)
            summary["stages"][stage] = {"chunks": len(job["chunks"])}
        elif stage == "transcribe":
            job = job if job is not None else _read_json(bundle / "job.json")
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise StageError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
            job = stage_transcribe(bundle, job, ledger=ledger, api_key=api_key,
                                   daily_limit=daily_limit, rpm_limit=rpm_limit,
                                   request_interval=request_interval,
                                   transcriber=transcriber, force=force)
            summary["stages"][stage] = {"status": job["status"]}
        elif stage == "assemble":
            job = job if job is not None else _read_json(bundle / "job.json")
            payload = stage_assemble(bundle, job)
            summary["stages"][stage] = {"words": len(payload["words"])}
        elif stage == "merge":
            merged = stage_merge(bundle)
            summary["stages"][stage] = {
                "words": len(merged["words"]),
                "inserted": sum(1 for w in merged["words"] if w.get("origin") == "youtube"),
            }
        elif stage == "render":
            srt, txt = stage_render(bundle, width=width)
            summary["stages"][stage] = {"srt": str(srt), "txt": str(txt)}
        elif stage == "visual":
            frames = stage_visual(bundle, at=at, max_frames=max_frames)
            summary["stages"][stage] = {
                "frames": len(frames["frames"]),
                "candidates": frames["candidates_considered"],
            }
        elif stage == "index":
            summary["stages"][stage] = {"index": str(stage_index(bundle))}
    return summary


def status(bundle: Path, *, ledger: Path | None = None, api_key: str | None = None,
           daily_limit: int = DEFAULT_DAILY_LIMIT) -> dict[str, Any]:
    job_path = bundle / "job.json"
    info: dict[str, Any] = {"video_id": bundle.name, "bundle": str(bundle),
                            "exists": bundle.exists()}
    if job_path.exists():
        job = _read_json(job_path)
        info["status"] = job["status"]
        info["chunks"] = {
            "total": len(job["chunks"]),
            "complete": sum(1 for c in job["chunks"] if c["status"] == "complete"),
            "failed": [c["index"] for c in job["chunks"] if c["status"] == "failed"],
        }
    info["artifacts"] = {
        name: (bundle / rel).exists() for name, rel in (
            ("captions", "raw/captions.json"),
            ("transcript", "derived/transcript.json"),
            ("merged", "derived/merged.json"),
            ("srt", "derived/output.srt"),
            ("txt", "derived/output.txt"),
            ("frames", "derived/frames.json"),
            ("index", "index.sqlite3"),
        )
    }
    if ledger is not None and api_key:
        current = usage.get_usage(ledger, api_key)
        info["usage"] = {**current, "daily_limit": daily_limit,
                         "remaining": max(0, daily_limit - current["attempts"]),
                         "accuracy": "local estimate"}
    return info


PURGE_SCOPES = ("chunks", "derived", "raw", "all")


def purge(bundle: Path, *, scope: str = "derived") -> list[str]:
    """raw 자료 삭제와 derived 재생성을 위한 명시적 삭제 (CONTRACT 12절).

    `chunks` 는 전사용 청크 오디오만 지운다. 청크는 원본 오디오에서 언제든
    다시 뽑을 수 있고(`stage_plan` 이 없으면 자동으로 다시 뽑는다), 전사가
    끝난 뒤에는 재개에도 쓰이지 않는다. bundle 용량의 20~25% 를 차지한다.
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
    run_cmd.add_argument("--stages", default=",".join(STAGES), help="쉼표 구분")
    run_cmd.add_argument("--chunk-max-secs", type=float, default=audio.DEFAULT_CHUNK_MAX_SECS)
    run_cmd.add_argument("--overlap-secs", type=float, default=audio.DEFAULT_OVERLAP_SECS)
    run_cmd.add_argument("--language", default=None, help="쉼표 구분. 생략하면 자동 감지")
    run_cmd.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    run_cmd.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)
    run_cmd.add_argument("--rpm-limit", type=int, default=DEFAULT_RPM_LIMIT)
    run_cmd.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL)
    run_cmd.add_argument("--force", action="store_true", help="캐시를 무시하고 다시 만든다")
    run_cmd.add_argument("--skip-video", action="store_true",
                         help="영상을 받지 않는다. 프레임 추출도 건너뛴다")
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
                      stages=tuple(s.strip() for s in args.stages.split(",") if s.strip()),
                      chunk_max_secs=args.chunk_max_secs, overlap_secs=args.overlap_secs,
                      language_codes=codes, width=args.width, daily_limit=args.daily_limit,
                      rpm_limit=args.rpm_limit, request_interval=args.request_interval,
                      force=args.force, video=not args.skip_video, at=at,
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
