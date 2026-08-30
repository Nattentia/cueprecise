"""ytx MCP 서버 — stdio JSON-RPC (CONTRACT.md 12절).

owner: claude

계약이 요구하는 도구 표면을 제공한다.

  ytx_register     영상 등록/분석 시작 (단계 선택 가능)
  ytx_status       작업 상태와 로컬 Gemini 사용량 추정 조회
  ytx_outline      영상 개요와 timestamp 목차 조회
  ytx_query        내용 질의. 근거 span/frame 과 timestamp 반환
  ytx_excerpt      특정 시각 구간의 자막과 프레임 조회
  ytx_purge        derived 재생성 및 명시적 영상 자료 삭제

의존성 없이 stdlib 만으로 MCP stdio 프로토콜을 구현한다. 외부 패키지를
새로 들이지 않는다는 합의서 7절 제약을 지키기 위해서다.

실행:
    python src/mcp_server.py [--bundle-root data]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import context
import pipeline
import visual

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ytx", "version": "1.0.0"}

OUTLINE_GAP_SECS = 12.0
"""이보다 긴 무음을 장 경계 후보로 본다."""


class ToolError(RuntimeError):
    """도구 실행 실패. 호출자에게 그대로 전달한다."""


# ---------------------------------------------------------------------- helpers

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _transcript(bundle: Path) -> dict[str, Any]:
    for name in ("merged.json", "transcript.json"):
        path = bundle / "derived" / name
        if path.exists():
            return _read_json(path)
    raise ToolError(
        "%s 에 전사가 없다. 먼저 ytx_register 로 분석하라." % bundle.name)


def _frames(bundle: Path) -> list[dict[str, Any]]:
    path = bundle / "derived" / "frames.json"
    if not path.exists():
        return []
    return _read_json(path).get("frames", [])


def _fmt(seconds: float) -> str:
    total = int(round(seconds))
    return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)


# ------------------------------------------------------------------------ tools

def tool_register(bundle_root: Path, *, url: str, stages: list[str] | None = None,
                  language: str | None = None) -> dict[str, Any]:
    codes = [s.strip() for s in language.split(",") if s.strip()] if language else None
    selected = tuple(stages) if stages else pipeline.STAGES
    return pipeline.run(url, bundle_root=bundle_root, stages=selected, language_codes=codes)


def tool_status(bundle_root: Path, *, video_id: str, api_key: str | None = None,
                daily_limit: int = pipeline.DEFAULT_DAILY_LIMIT) -> dict[str, Any]:
    bundle = pipeline.bundle_path(bundle_root, video_id)
    ledger = Path(bundle_root) / "usage.json"
    return pipeline.status(bundle, ledger=ledger if api_key else None,
                           api_key=api_key, daily_limit=daily_limit)


def tool_outline(bundle_root: Path, *, video_id: str,
                 max_entries: int = 30) -> dict[str, Any]:
    """무음 간격으로 장 경계를 잡아 timestamp 목차를 만든다."""
    bundle = pipeline.bundle_path(bundle_root, video_id)
    payload = _transcript(bundle)
    words = payload["words"]
    if not words:
        raise ToolError("전사에 단어가 없다.")

    boundaries = [0]
    for index in range(1, len(words)):
        if float(words[index]["start"]) - float(words[index - 1]["end"]) > OUTLINE_GAP_SECS:
            boundaries.append(index)

    # 목차가 너무 길면 간격이 큰 경계부터 남긴다.
    if len(boundaries) > max_entries:
        scored = sorted(
            boundaries[1:],
            key=lambda i: float(words[i]["start"]) - float(words[i - 1]["end"]),
            reverse=True,
        )[: max_entries - 1]
        boundaries = [0] + sorted(scored)

    entries = []
    for position, start_index in enumerate(boundaries):
        end_index = boundaries[position + 1] if position + 1 < len(boundaries) else len(words)
        segment = words[start_index:end_index]
        title = " ".join(str(w["text"]) for w in segment[:12])
        entries.append({
            "start": float(segment[0]["start"]),
            "end": float(segment[-1]["end"]),
            "timecode": _fmt(float(segment[0]["start"])),
            "title": title[:120],
            "words": len(segment),
        })

    restored = [w for w in words if w.get("origin") == "youtube"]
    return {
        "video_id": payload.get("video_id") or video_id,
        "duration": float(words[-1]["end"]),
        "word_count": len(words),
        "restored_terms": sorted({str(w["text"]) for w in restored}),
        "speakers": sorted({str(w.get("speaker_global") or w.get("speaker"))
                            for w in words if w.get("speaker") or w.get("speaker_global")}),
        "unresolved_speaker_words": sum(
            1 for w in words if w.get("speaker_status") == "unresolved"),
        "outline": entries,
    }


def tool_query(bundle_root: Path, *, video_id: str, query: str,
               limit: int = 8) -> dict[str, Any]:
    """SQLite 색인에서 찾고 근거 span 과 frame 을 함께 돌려준다."""
    bundle = pipeline.bundle_path(bundle_root, video_id)
    index = bundle / "index.sqlite3"
    if not index.exists():
        raise ToolError("index.sqlite3 가 없다. ytx_register 의 index 단계를 실행하라.")

    hits = context.search(index, query, limit=limit)
    if not hits:
        return {"video_id": video_id, "query": query, "evidence": [],
                "answer": "근거를 찾지 못했다. 이 영상 자료로는 답할 수 없다."}

    frames = _frames(bundle)
    for hit in hits:
        hit["timecode"] = _fmt(float(hit["start"]))
        hit["frames"] = [
            f for f in frames
            if float(hit["start"]) - 2.0 <= float(f["timestamp"]) <= float(hit["end"]) + 2.0
        ]
    return {
        "video_id": video_id,
        "query": query,
        "evidence": hits,
        "answer": "근거 %d건을 찾았다. 각 항목의 start/end 와 source_path 를 인용하라." % len(hits),
    }


def tool_excerpt(bundle_root: Path, *, video_id: str, start: float,
                 end: float) -> dict[str, Any]:
    """시각 구간의 자막 원문과 그 구간의 프레임을 돌려준다."""
    if end <= start:
        raise ToolError("end 는 start 보다 커야 한다.")
    bundle = pipeline.bundle_path(bundle_root, video_id)
    payload = _transcript(bundle)
    words = [w for w in payload["words"]
             if float(w["end"]) >= start and float(w["start"]) <= end]
    return {
        "video_id": video_id,
        "start": start,
        "end": end,
        "timecode": "%s - %s" % (_fmt(start), _fmt(end)),
        "text": " ".join(str(w["text"]) for w in words),
        "words": words,
        "frames": [f for f in _frames(bundle)
                   if start <= float(f["timestamp"]) <= end],
    }


def tool_purge(bundle_root: Path, *, video_id: str,
               scope: str = "derived") -> dict[str, Any]:
    bundle = pipeline.bundle_path(bundle_root, video_id)
    removed = pipeline.purge(bundle, scope=scope)
    return {
        "video_id": video_id,
        "scope": scope,
        "removed": removed,
        "note": "derived 는 raw 가 남아 있으면 ytx_register 로 재생성할 수 있다. "
                "chunks 는 source.mp3 가 남아 있으면 plan 단계가 다시 뽑는다.",
    }


def tool_frames(bundle_root: Path, *, video_id: str,
                at: list[float] | None = None) -> dict[str, Any]:
    bundle = pipeline.bundle_path(bundle_root, video_id)
    return visual.build(bundle, at=at)


TOOLS: list[dict[str, Any]] = [
    {
        "name": "ytx_register",
        "description": "YouTube 영상을 등록하고 분석 파이프라인을 실행한다. "
                       "stages 를 주면 일부 단계만 재실행한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "YouTube URL 또는 video_id"},
                "stages": {"type": "array", "items": {"type": "string"},
                           "description": "생략하면 전체. %s" % ", ".join(pipeline.STAGES)},
                "language": {"type": "string",
                             "description": "쉼표 구분 BCP-47. 생략하면 자동 감지"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "ytx_status",
        "description": "작업 상태, 청크 진행도, 산출물 존재 여부, "
                       "로컬 Gemini 사용량 추정을 조회한다.",
        "inputSchema": {
            "type": "object",
            "properties": {"video_id": {"type": "string"}},
            "required": ["video_id"],
        },
    },
    {
        "name": "ytx_outline",
        "description": "영상 개요와 timestamp 목차, 복원된 영어 용어, 화자 상태를 조회한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "max_entries": {"type": "integer", "description": "기본 30"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "ytx_query",
        "description": "영상 내용을 질의한다. 근거 span 과 frame 을 timestamp 와 함께 반환한다. "
                       "근거가 없으면 없다고 답한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "기본 8"},
            },
            "required": ["video_id", "query"],
        },
    },
    {
        "name": "ytx_excerpt",
        "description": "지정한 시각 구간의 자막 원문과 그 구간의 프레임을 조회한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "start": {"type": "number"},
                "end": {"type": "number"},
            },
            "required": ["video_id", "start", "end"],
        },
    },
    {
        "name": "ytx_frames",
        "description": "화면 참조 시각의 프레임을 추출하고 frames.json 을 갱신한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "at": {"type": "array", "items": {"type": "number"},
                       "description": "추가로 뽑을 초 단위 시각"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "ytx_purge",
        "description": "영상 자료를 명시적으로 삭제한다. "
                       "scope: derived(기본) | chunks | raw | all. "
                       "chunks 는 전사용 청크 오디오만 지우며 source.mp3 에서 다시 만들 수 있다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "scope": {"type": "string",
                          "enum": ["derived", "chunks", "raw", "all"]},
            },
            "required": ["video_id"],
        },
    },
]


def dispatch(name: str, arguments: dict[str, Any], *, bundle_root: Path,
             api_key: str | None = None) -> dict[str, Any]:
    if name == "ytx_register":
        return tool_register(bundle_root, url=arguments["url"],
                             stages=arguments.get("stages"),
                             language=arguments.get("language"))
    if name == "ytx_status":
        return tool_status(bundle_root, video_id=arguments["video_id"], api_key=api_key)
    if name == "ytx_outline":
        return tool_outline(bundle_root, video_id=arguments["video_id"],
                            max_entries=int(arguments.get("max_entries", 30)))
    if name == "ytx_query":
        return tool_query(bundle_root, video_id=arguments["video_id"],
                          query=arguments["query"], limit=int(arguments.get("limit", 8)))
    if name == "ytx_excerpt":
        return tool_excerpt(bundle_root, video_id=arguments["video_id"],
                            start=float(arguments["start"]), end=float(arguments["end"]))
    if name == "ytx_frames":
        return tool_frames(bundle_root, video_id=arguments["video_id"],
                           at=arguments.get("at"))
    if name == "ytx_purge":
        return tool_purge(bundle_root, video_id=arguments["video_id"],
                          scope=arguments.get("scope", "derived"))
    raise ToolError("알 수 없는 도구: %s" % name)


# --------------------------------------------------------------------- protocol

def handle(message: dict[str, Any], *, bundle_root: Path,
           api_key: str | None = None) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        try:
            payload = dispatch(params.get("name", ""), params.get("arguments") or {},
                               bundle_root=bundle_root, api_key=api_key)
            result = {"content": [{"type": "text",
                                   "text": json.dumps(payload, ensure_ascii=False, indent=1)}]}
        except Exception as error:
            result = {"content": [{"type": "text", "text": "실패: %s" % error}],
                      "isError": True}
    elif method in {"notifications/initialized", "initialized"}:
        return None  # 알림에는 응답하지 않는다
    elif method == "ping":
        result = {}
    else:
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": "지원하지 않는 method: %s" % method}}

    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stream_in, stream_out, *, bundle_root: Path, api_key: str | None = None) -> None:
    for line in stream_in:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message, bundle_root=bundle_root, api_key=api_key)
        if response is not None:
            stream_out.write(json.dumps(response, ensure_ascii=False) + "\n")
            stream_out.flush()


def main() -> int:
    import os

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    serve(sys.stdin, sys.stdout, bundle_root=args.bundle_root,
          api_key=os.environ.get("GEMINI_API_KEY"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
