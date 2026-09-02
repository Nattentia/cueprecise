"""CuePrecise MCP 서버 — stdio JSON-RPC (CONTRACT.md 12절).

owner: claude

계약이 요구하는 도구 표면을 제공한다.

  cueprecise_register     영상 등록/분석 시작 (단계 선택 가능)
  cueprecise_status       작업 상태와 로컬 Gemini 사용량 추정 조회
  cueprecise_outline      영상 개요와 timestamp 목차 조회
  cueprecise_query        내용 질의. 근거 span/frame 과 timestamp 반환
  cueprecise_excerpt      특정 시각 구간의 자막과 프레임 조회
  cueprecise_purge        derived 재생성 및 명시적 영상 자료 삭제

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
import chapters
import pipeline
import summary as summary_mod
import visual

MAX_EXCERPT_CHARS = 12000
SERVER_INFO = {"name": "cueprecise", "version": "1.0.0"}

# 스펙 2026-07-28 은 initialize 악수를 없애고 요청마다 판을 실어 보낸다. 그
# 이전 판들은 악수로 세션을 연다. 두 쪽을 다 받는다(dual-era).
#
# 이 서버는 원래부터 요청 사이에 아무 상태도 두지 않는다. `handle()` 은
# (요청, bundle_root) 만 보는 순수 함수라 신식 요구를 이미 만족한다. 남은
# 것은 자기가 신식도 한다고 밝히는 일뿐이라, 아래 상수와 `server/discover`
# 응답이 전부다. 요청 처리 경로에는 판 검사 한 줄만 늘어난다.
MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = (MODERN_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION)
# 신식 요청이 판을 싣고 오는 자리. 스펙이 정한 이름이라 바꾸면 안 된다.
PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
# UnsupportedProtocolVersionError. 스펙이 정한 코드다.
UNSUPPORTED_VERSION_CODE = -32022

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
        "%s 에 전사가 없다. 먼저 cueprecise_register 로 분석하라." % bundle.name)


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
    selected = pipeline.resolve_stages(stages)
    return pipeline.run(url, bundle_root=bundle_root, stages=selected, language_codes=codes)


def tool_status(bundle_root: Path, *, video_id: str, api_key: str | None = None,
                daily_limit: int = pipeline.DEFAULT_DAILY_LIMIT) -> dict[str, Any]:
    bundle = pipeline.bundle_path(bundle_root, video_id)
    ledger = Path(bundle_root) / "usage.json"
    return pipeline.status(bundle, ledger=ledger if api_key else None,
                           api_key=api_key, daily_limit=daily_limit)


def tool_outline(bundle_root: Path, *, video_id: str,
                 max_entries: int = 100) -> dict[str, Any]:
    """영속 chapter를 만들거나 읽고, host가 지을 제목 후보를 반환한다."""
    bundle = pipeline.bundle_path(bundle_root, video_id)
    payload = _transcript(bundle)
    words = payload["words"]
    if not words:
        raise ToolError("전사에 단어가 없다.")
    chapter_path = bundle / "derived" / "chapters.json"
    chapter_payload = _read_json(chapter_path) if chapter_path.exists() else None
    if (chapter_payload is None
            or chapter_payload.get("transcript_fingerprint")
            != chapters.transcript_fingerprint(bundle)):
        job_path = bundle / "job.json"
        source_url = None
        if job_path.exists():
            source_url = (_read_json(job_path).get("input") or {}).get("source")
        chapter_payload = chapters.build(bundle, url=source_url)
    selected_chapters = chapter_payload["chapters"][:max_entries]
    entries = []
    for item in selected_chapters:
        entries.append({**item, "timecode": _fmt(float(item["start"]))})

    restored = [w for w in words if w.get("origin") == "youtube"]
    return {
        "video_id": payload.get("video_id") or video_id,
        "duration": float(words[-1]["end"]),
        "word_count": len(words),
        "restored_terms": sorted({str(w["text"]) for w in restored}),
        # unresolved 라벨은 화자 목록에서 확정 정체성처럼 노출하지 않는다.
        "speakers": sorted({str(w.get("speaker_global") or w.get("speaker"))
                            for w in words
                            if (w.get("speaker") or w.get("speaker_global"))
                            and w.get("speaker_status") in {"confirmed", "inferred"}}),
        "unresolved_speaker_candidates": sorted({
            str(w.get("speaker_global") or w.get("speaker"))
            for w in words
            if (w.get("speaker") or w.get("speaker_global"))
            and w.get("speaker_status") == "unresolved"
        }),
        "unresolved_speaker_words": sum(
            1 for w in words if w.get("speaker_status") == "unresolved"),
        "transcript_fingerprint": chapter_payload["transcript_fingerprint"],
        "needs_titles": [
            {"id": item["id"], "start": item["start"], "end": item["end"],
             "keywords": item["keywords"], "excerpts": item["excerpts"]}
            for item in selected_chapters if item["needs_title"]
        ],
        "title_action": ("needs_titles 각각에 짧은 title을 직접 지은 뒤 "
                         "cueprecise_set_chapter_titles를 한 번 호출하라. 설명은 만들지 마라."
                         if any(item["needs_title"] for item in selected_chapters) else None),
        "outline": entries,
    }


def tool_set_chapter_titles(bundle_root: Path, *, video_id: str, fingerprint: str,
                            titles: list[dict[str, Any]]) -> dict[str, Any]:
    """호스트가 처음부터 지은 짧은 title만 검증해 일괄 저장한다."""
    bundle = pipeline.bundle_path(bundle_root, video_id)
    result = chapters.set_titles(bundle, fingerprint=fingerprint, titles=titles)
    return {"video_id": video_id, "updated": len(titles),
            "quality": result["generation"]["quality"],
            "outline": [{"id": item["id"], "start": item["start"],
                         "end": item["end"], "title": item["title"],
                         "title_source": item["title_source"]}
                        for item in result["chapters"]]}


def tool_summary(bundle_root: Path, *, video_id: str) -> dict[str, Any]:
    """요청 시에만 영속 요약을 만들고 선택적 host 개선 패킷을 반환한다."""
    bundle = pipeline.bundle_path(bundle_root, video_id)
    result = summary_mod.build(bundle)
    if result["needs_host_summary"]:
        result["summary_action"] = (
            "현재 summary는 로컬 추출본이므로 그대로 답해도 된다. 문장 품질을 개선할 수 "
            "있으면 packet의 근거만 사용해 overview, key_points, chapter_summaries, terms를 "
            "작성하고 cueprecise_set_summary를 한 번 호출하라. timestamp는 작성하지 마라."
        )
    else:
        result["summary_action"] = None
    return result


def tool_set_summary(bundle_root: Path, *, video_id: str, fingerprint: str,
                     content: dict[str, Any]) -> dict[str, Any]:
    """호스트의 구조화된 요약을 검증하고 timestamp를 서버에서 붙여 저장한다."""
    bundle = pipeline.bundle_path(bundle_root, video_id)
    return summary_mod.set_host_summary(
        bundle, fingerprint=fingerprint, content=content)


def tool_query(bundle_root: Path, *, video_id: str, query: str,
               limit: int = 8) -> dict[str, Any]:
    """SQLite 색인에서 찾고 근거 span 과 frame 을 함께 돌려준다."""
    bundle = pipeline.bundle_path(bundle_root, video_id)
    index = bundle / "index.sqlite3"
    if not index.exists():
        raise ToolError("index.sqlite3 가 없다. cueprecise_register 의 index 단계를 실행하라.")

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
    text = " ".join(str(w["text"]) for w in words)
    result = {
        "video_id": video_id,
        "start": start,
        "end": end,
        "timecode": "%s - %s" % (_fmt(start), _fmt(end)),
        "text": text,
        "word_count": len(words),
        "frames": [f for f in _frames(bundle)
                   if start <= float(f["timestamp"]) <= end],
    }
    if len(text) > MAX_EXCERPT_CHARS:
        cut = text.rfind(" ", 0, MAX_EXCERPT_CHARS)
        if cut <= 0:
            cut = MAX_EXCERPT_CHARS
        result["text"] = text[:cut]
        result["truncated"] = True
        result["note"] = ("구간이 길어 앞부분만 돌려준다. 나머지는 start 를 "
                          "옮겨 다시 부른다. 자른 지점의 원문 길이는 %d 자다."
                          % len(text))
    return result


def tool_purge(bundle_root: Path, *, video_id: str,
               scope: str = "derived") -> dict[str, Any]:
    bundle = pipeline.bundle_path(bundle_root, video_id)
    removed = pipeline.purge(bundle, scope=scope)
    return {
        "video_id": video_id,
        "scope": scope,
        "removed": removed,
        "note": "derived 는 raw 가 남아 있으면 cueprecise_register 로 재생성할 수 있다. "
                "chunks 는 원본 오디오가 남아 있으면 plan 단계가 다시 뽑는다.",
    }


def tool_frames(bundle_root: Path, *, video_id: str,
                at: list[float] | None = None,
                max_frames: int = visual.DEFAULT_MAX_FRAMES) -> dict[str, Any]:
    # visual.build 를 직접 부르지 않는다. 기본 분석이 영상을 받지 않으므로
    # 프레임 요청 시점에 영상을 확보하는 일까지 pipeline 이 맡는다.
    bundle = pipeline.bundle_path(bundle_root, video_id)
    return pipeline.stage_visual(bundle, at=at, max_frames=max_frames)


TOOLS: list[dict[str, Any]] = [
    {
        "name": "cueprecise_register",
        "description": "YouTube 영상을 등록하고 분석 파이프라인을 실행한다. "
                       "stages 를 주면 일부 단계만 재실행한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "YouTube URL 또는 video_id"},
                "stages": {"type": "array", "items": {"type": "string"},
                           "description": "생략하면 기본(%s). 전체는 [\"all\"]. "
                                          "가능: %s"
                                          % (", ".join(pipeline.DEFAULT_STAGES),
                                             ", ".join(pipeline.STAGES))},
                "language": {"type": "string",
                             "description": "쉼표 구분 BCP-47. 생략하면 자동 감지"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "cueprecise_status",
        "description": "작업 상태, 청크 진행도, 산출물 존재 여부, "
                       "로컬 Gemini 사용량 추정을 조회한다.",
        "inputSchema": {
            "type": "object",
            "properties": {"video_id": {"type": "string"}},
            "required": ["video_id"],
        },
    },
    {
        "name": "cueprecise_outline",
        "description": "영상 개요와 timestamp 목차를 조회한다. needs_titles가 있으면 "
                       "근거를 보고 제목을 직접 지은 뒤 cueprecise_set_chapter_titles를 호출한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "max_entries": {"type": "integer", "description": "기본 100"},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "cueprecise_query",
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
        "name": "cueprecise_summary",
        "description": "사용자가 전체 영상 요약을 요청할 때만 요약을 만들거나 "
                       "현재 요약을 재사용한다. 로컬 요약은 즉시 사용 가능하며, packet이 "
                       "있으면 호스트가 cueprecise_set_summary로 한 번 개선할 수 있다.",
        "inputSchema": {
            "type": "object",
            "properties": {"video_id": {"type": "string"}},
            "required": ["video_id"],
        },
    },
    {
        "name": "cueprecise_set_summary",
        "description": "cueprecise_summary packet만 근거로 작성한 구조화 요약을 검증·저장한다. "
                       "chapter 경계와 timestamp는 서버가 결정한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "fingerprint": {"type": "string"},
                "content": {
                    "type": "object",
                    "properties": {
                        "overview": {"type": "string"},
                        "key_points": {"type": "array", "items": {"type": "object"}},
                        "chapter_summaries": {"type": "array", "items": {"type": "object"}},
                        "terms": {"type": "array", "items": {"type": "object"},
                                  "description": "선택. 없으면 빈 배열"},
                    },
                    "required": ["overview", "key_points", "chapter_summaries"],
                },
            },
            "required": ["video_id", "fingerprint", "content"],
        },
    },
    {
        "name": "cueprecise_set_chapter_titles",
        "description": "cueprecise_outline의 needs_titles에 대해 호스트가 직접 지은 제목을 "
                       "검증 후 저장한다. 경계와 원문은 바꿀 수 없다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "fingerprint": {"type": "string"},
                "titles": {"type": "array", "items": {"type": "object",
                           "properties": {"id": {"type": "string"},
                                          "title": {"type": "string"}},
                           "required": ["id", "title"]}},
            },
            "required": ["video_id", "fingerprint", "titles"],
        },
    },
    {
        "name": "cueprecise_excerpt",
        "description": "지정한 시각 구간의 자막 원문과 그 구간의 프레임을 조회한다. 긴 구간은 앞부분만 돌아오고 truncated 가 참이 된다 — 그때는 start 를 옮겨 이어 부른다.",
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
        "name": "cueprecise_frames",
        "description": "화면 참조 시각의 프레임을 추출하고 frames.json 을 갱신한다. "
                       "기본 분석은 영상을 받지 않으므로 이 도구가 필요할 때 "
                       "원본 URL 로 360p 영상을 받아온다. Gemini 호출 없음.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string"},
                "at": {"type": "array", "items": {"type": "number"},
                       "description": "추가로 뽑을 초 단위 시각"},
                "max_frames": {"type": "integer",
                               "description": "최대 프레임 수. 기본 %d"
                                              % visual.DEFAULT_MAX_FRAMES},
            },
            "required": ["video_id"],
        },
    },
    {
        "name": "cueprecise_purge",
        "description": "영상 자료를 명시적으로 삭제한다. "
                       "scope: derived(기본) | chunks | raw | all. "
                       "chunks 는 전사용 청크 오디오만 지우며 원본 오디오에서 다시 만들 수 있다.",
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


TOOL_PREFIX = "cueprecise_"


def dispatch(name: str, arguments: dict[str, Any], *, bundle_root: Path,
             api_key: str | None = None) -> dict[str, Any]:
    if name == "cueprecise_register":
        return tool_register(bundle_root, url=arguments["url"],
                             stages=arguments.get("stages"),
                             language=arguments.get("language"))
    if name == "cueprecise_status":
        return tool_status(bundle_root, video_id=arguments["video_id"], api_key=api_key)
    if name == "cueprecise_outline":
        return tool_outline(bundle_root, video_id=arguments["video_id"],
                            max_entries=int(arguments.get("max_entries", 100)))
    if name == "cueprecise_query":
        return tool_query(bundle_root, video_id=arguments["video_id"],
                          query=arguments["query"], limit=int(arguments.get("limit", 8)))
    if name == "cueprecise_summary":
        return tool_summary(bundle_root, video_id=arguments["video_id"])
    if name == "cueprecise_set_summary":
        return tool_set_summary(bundle_root, video_id=arguments["video_id"],
                                fingerprint=arguments["fingerprint"],
                                content=arguments["content"])
    if name == "cueprecise_set_chapter_titles":
        return tool_set_chapter_titles(
            bundle_root, video_id=arguments["video_id"],
            fingerprint=arguments["fingerprint"], titles=arguments["titles"])
    if name == "cueprecise_excerpt":
        return tool_excerpt(bundle_root, video_id=arguments["video_id"],
                            start=float(arguments["start"]), end=float(arguments["end"]))
    if name == "cueprecise_frames":
        return tool_frames(bundle_root, video_id=arguments["video_id"],
                           at=arguments.get("at"),
                           max_frames=int(arguments.get("max_frames",
                                                        visual.DEFAULT_MAX_FRAMES)))
    if name == "cueprecise_purge":
        return tool_purge(bundle_root, video_id=arguments["video_id"],
                          scope=arguments.get("scope", "derived"))
    raise ToolError("알 수 없는 도구: %s" % name)


# --------------------------------------------------------------------- protocol

def requested_protocol_version(message: dict[str, Any]) -> str | None:
    """신식 요청이 `_meta` 에 실어 보낸 판을 읽는다. 없으면 None.

    구식 요청에는 이 자리가 없다. 없다고 거절하면 지금 쓰는 클라이언트가
    전부 끊기므로, 없으면 판을 따지지 않는다.
    """
    meta = (message.get("params") or {}).get("_meta")
    if not isinstance(meta, dict):
        return None
    version = meta.get(PROTOCOL_VERSION_KEY)
    return version if isinstance(version, str) else None


def handle(message: dict[str, Any], *, bundle_root: Path,
           api_key: str | None = None) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")

    version = requested_protocol_version(message)
    if version is not None and version not in SUPPORTED_PROTOCOL_VERSIONS:
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": UNSUPPORTED_VERSION_CODE,
                          "message": "Unsupported protocol version",
                          "data": {"supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                                   "requested": version}}}

    if method == "server/discover":
        # 신식 클라이언트가 말을 걸기 전에 딱 한 번 묻는 것. 미리 적어 둔
        # 것을 그대로 돌려준다. 계산도 파일 접근도 없다.
        result: dict[str, Any] = {
            "resultType": "complete",
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": {"tools": {}},
            "_meta": {SERVER_INFO_KEY: SERVER_INFO},
        }
    elif method == "initialize":
        # 구식 악수. 클라이언트가 우리가 아는 판을 부르면 그 판으로 답하고,
        # 모르는 판이면 우리 판을 알린다(구 스펙의 협상 규칙).
        asked = (message.get("params") or {}).get("protocolVersion")
        result = {
            "protocolVersion": (asked if asked in SUPPORTED_PROTOCOL_VERSIONS
                                else LEGACY_PROTOCOL_VERSION),
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
            # 프로토콜 프레임만 ASCII 로 내보낸다. 비ASCII 는 \uXXXX 로 이스케이프
            # 되며 JSON 규격이고 클라이언트가 원문 그대로 복원한다. UTF-8 고정
            # (_force_utf8) 이 어떤 이유로 실패해도 이 줄에서는 안 죽는다.
            stream_out.write(json.dumps(response, ensure_ascii=True) + "\n")
            stream_out.flush()


def _force_utf8(*streams) -> None:
    """stdio 통로를 UTF-8 로 고정한다.

    MCP 는 stdout/stdin 이 항상 파이프다. 파이프면 Python 이 인코딩을
    로케일에서 가져오는데, 한국어 Windows 는 cp949 다. cp949 는 한글은 되지만
    `—` `’` 같은 문자를 못 쓴다. 전사·OCR 결과에 흔히 섞이는 문자들이라
    응답 한 건에 UnicodeEncodeError 가 나고 서버가 죽는다.

    테스트는 `serve()` 에 StringIO 를 직접 넘기므로 여기는 실제 실행 경로
    에서만 부른다. reconfigure 가 없는 스트림은 건드리지 않는다.
    """
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 고정에 실패해도 죽지는 않는다. 아래 ensure_ascii 안전망이 받는다.
            pass


def main() -> int:
    import os

    # argparse의 --help도 비ASCII 문서를 출력하므로 파싱 전에 UTF-8로 고정한다.
    _force_utf8(sys.stdin, sys.stdout, sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    serve(sys.stdin, sys.stdout, bundle_root=args.bundle_root,
          api_key=os.environ.get("GEMINI_API_KEY"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
