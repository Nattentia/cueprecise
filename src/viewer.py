"""CuePrecise 뷰어 — MCP 프로세스 안 HTTP 서버 (CONTRACT.md 16절).

owner: claude

번역 자막을 몰입해서 보는 뷰어다. `cueprecise_subtitle` 이 처음 불릴 때만
`ensure_server` 로 지연 시작한다 — MCP 프로세스가 뜨는 순간에는 포트를 열지
않는다. `127.0.0.1` 에만 붙고, 표준 라이브러리(`http.server`)만 쓴다.

라우트는 GET 셋뿐이다: `/`(내장 HTML), `/api/subtitle`(JSON), 헬스 체크.
DNS 리바인딩을 막기 위해 `Host` 헤더를 검사하고, 요청에서 파일 경로를
만들지 않는다(정적 파일 서빙이 아예 없다 — HTML은 파이썬 문자열 상수다).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pipeline
import subtitle

HOST = "127.0.0.1"
PORT_RANGE: tuple[int, ...] = tuple(range(8787, 8798))  # 8787..8797
APP_NAME = "cueprecise-viewer"

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LANG_RE = re.compile(r"^[a-z]{2,8}$")

# 프로세스 전역 단일 서버. `ensure_server` 는 idempotent 하다 — 이미 같은
# bundle_root 로 떠 있으면 그 주소를 그대로 돌려준다.
_lock = threading.Lock()
_server: "ThreadingHTTPServer | None" = None
_server_thread: threading.Thread | None = None
_server_bundle_root: Path | None = None
_server_base_url: str | None = None


def _bundle_root_hash(bundle_root: Path) -> str:
    return hashlib.sha256(str(Path(bundle_root).resolve()).encode("utf-8")).hexdigest()[:16]


def _probe_health(host: str, port: int, timeout: float = 0.25) -> dict[str, Any] | None:
    """그 포트에 이미 뭔가 떠 있으면 헬스 응답을 읽어 본다. 없으면 None."""
    try:
        with urllib.request.urlopen(
                f"http://{host}:{port}/__cueprecise/health", timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (URLError, OSError, ValueError, TimeoutError):
        return None


class _ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler_cls: type[BaseHTTPRequestHandler],
                bundle_root: Path) -> None:
        super().__init__(address, handler_cls)
        self.bundle_root = Path(bundle_root)
        self.bundle_root_hash_value = _bundle_root_hash(bundle_root)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # stdio MCP 서버와 같은 프로세스다. stderr 로 무엇이든 내보내면 그
        # 프로토콜 파이프를 오염시킬 수 있다 — 조용히 무시한다.
        pass

    # ------------------------------------------------------------ helpers

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        port = self.server.server_port
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _write(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._write(status, body, "application/json; charset=utf-8")

    def _send_html(self, status: int, html: str) -> None:
        self._write(status, html.encode("utf-8"), "text/html; charset=utf-8")

    # --------------------------------------------------------------- GET

    def do_GET(self) -> None:  # noqa: N802 - http.server 관례
        if not self._host_ok():
            self._send_json(403, {"error": "invalid host"})
            return
        parsed = urlsplit(self.path)
        path = parsed.path

        if path == "/__cueprecise/health":
            self._send_json(200, {"app": APP_NAME,
                                  "bundle_root_hash": self.server.bundle_root_hash_value})
            return

        if path == "/":
            self._send_html(200, PAGE_HTML)
            return

        if path == "/api/subtitle":
            query = parse_qs(parsed.query)
            video_id = (query.get("v") or [""])[0]
            lang = (query.get("lang") or ["ko"])[0]
            if not _VIDEO_ID_RE.match(video_id) or not _LANG_RE.match(lang):
                self._send_json(400, {"error": "invalid video_id or lang"})
                return
            bundle = pipeline.bundle_path(self.server.bundle_root, video_id)
            try:
                payload = subtitle.viewer_payload(bundle, lang)
            except subtitle.SubtitleError as error:
                self._send_json(404, {"error": str(error)})
                return
            self._send_json(200, payload)
            return

        self._send_json(404, {"error": "not found"})

    # 그 외 메서드는 전부 405. GET/HEAD 만 받는다.
    def _method_not_allowed(self) -> None:
        self._send_json(405, {"error": "method not allowed"})

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed

    def do_HEAD(self) -> None:  # noqa: N802
        if not self._host_ok():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200 if urlsplit(self.path).path == "/" else 404)
        self.end_headers()


def _try_bind(port: int, bundle_root: Path) -> _ViewerServer | None:
    try:
        return _ViewerServer((HOST, port), _Handler, bundle_root)
    except OSError:
        return None


def ensure_server(bundle_root: Path) -> tuple[str | None, str | None]:
    """처음 부를 때만 서버를 띄운다. `(base_url, 실패 이유)` 를 돌려준다.

    이미 같은 `bundle_root` 로 서버가 떠 있으면(이 프로세스가 띄웠든, 포트를
    먼저 잡고 있던 다른 CuePrecise 든) 새로 열지 않고 그 주소를 재사용한다.
    8787~8797 이 전부 막혀 있으면 임의 포트(0)로 마지막 시도를 한다.
    """
    global _server, _server_thread, _server_bundle_root, _server_base_url
    bundle_root = Path(bundle_root)
    resolved = bundle_root.resolve()
    target_hash = _bundle_root_hash(bundle_root)

    with _lock:
        if _server is not None and _server_bundle_root == resolved:
            return _server_base_url, None

        for port in PORT_RANGE:
            existing = _probe_health(HOST, port)
            if existing is not None:
                if (existing.get("app") == APP_NAME
                        and existing.get("bundle_root_hash") == target_hash):
                    url = f"http://{HOST}:{port}"
                    _server_bundle_root, _server_base_url = resolved, url
                    return url, None
                continue  # 다른 앱이거나 다른 bundle_root — 다음 포트.
            server = _try_bind(port, bundle_root)
            if server is not None:
                return _start_locked(server, resolved)

        server = _try_bind(0, bundle_root)  # 전부 막혔으면 임의 포트.
        if server is not None:
            return _start_locked(server, resolved)
        return None, "127.0.0.1 에 뷰어 서버를 열 포트를 찾지 못했다."


def _start_locked(server: _ViewerServer, resolved_bundle_root: Path) -> tuple[str, None]:
    """`_lock` 을 쥔 채로만 부른다."""
    global _server, _server_thread, _server_bundle_root, _server_base_url
    thread = threading.Thread(target=server.serve_forever, name="cueprecise-viewer", daemon=True)
    thread.start()
    _server, _server_thread = server, thread
    _server_bundle_root = resolved_bundle_root
    _server_base_url = f"http://{HOST}:{server.server_port}"
    return _server_base_url, None


def viewer_url_for(base_url: str, video_id: str) -> str:
    return f"{base_url}/?v={video_id}"


def stop_server() -> None:
    """테스트가 끝난 뒤(또는 프로세스 종료 시) 서버를 내린다."""
    global _server, _server_thread, _server_bundle_root, _server_base_url
    with _lock:
        server = _server
        _server = None
        _server_thread = None
        _server_bundle_root = None
        _server_base_url = None
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass


# --------------------------------------------------------------------- HTML

PAGE_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CuePrecise 뷰어</title>
<style>
  :root {
    --bg: #0b0c0f; --surface: #14161b; --surface-2: #1b1e25; --line: #252932;
    --text: #eceef2; --dim: #9098a5; --faint: #5d6470;
    --accent: #7cb4ff; --accent-soft: rgba(124,180,255,.13); --gloss: #ffd57a;
    --radius: 14px; --shadow: 0 10px 40px rgba(0,0,0,.45);
  }
  * { box-sizing: border-box; }
  html { color-scheme: dark; }
  html, body { margin: 0; height: 100%; }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-thumb { background: #2c313b; border-radius: 10px; border: 3px solid transparent; background-clip: content-box; }
  ::-webkit-scrollbar-thumb:hover { background-color: #3a4150; }
  ::-webkit-scrollbar-track { background: transparent; }
  body { background: var(--bg); color: var(--text); -webkit-font-smoothing: antialiased;
         font: 14px/1.55 "Pretendard", "Apple SD Gothic Neo", -apple-system, "Segoe UI",
               "Malgun Gothic", system-ui, sans-serif; }
  button { font: inherit; color: inherit; }
  svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2;
        stroke-linecap: round; stroke-linejoin: round; flex: none; }

  /* ---------------------------------------------------------- 머리말 */
  header { display: flex; align-items: center; gap: 14px; padding: 12px 22px; }
  .brand { display: flex; align-items: center; gap: 8px; font-weight: 700; letter-spacing: -.01em;
           color: var(--text); white-space: nowrap; }
  .brand i { width: 22px; height: 22px; border-radius: 7px; display: grid; place-items: center;
             background: linear-gradient(135deg, #7cb4ff, #9f8cff); color: #0b0c0f;
             font-style: normal; font-size: 12px; font-weight: 800; }
  .titleBox { min-width: 0; flex: 1; border-left: 1px solid var(--line); padding-left: 14px; }
  #title { font-size: 15px; margin: 0; font-weight: 650; white-space: nowrap; overflow: hidden;
           text-overflow: ellipsis; }
  #sub { color: var(--dim); font-size: 12px; }
  .status { font-size: 12px; padding: 4px 10px; border-radius: 999px; white-space: nowrap;
            background: var(--surface-2); color: var(--dim); border: 1px solid var(--line); }
  .status.done { color: #8fe3b0; border-color: rgba(143,227,176,.3); background: rgba(143,227,176,.08); }
  .status.live { color: var(--gloss); border-color: rgba(255,213,122,.3); background: rgba(255,213,122,.08); }

  .wrap { display: grid; grid-template-columns: minmax(0,1fr) 400px; gap: 20px;
          padding: 4px 22px 22px; align-items: start; }
  @media (max-width: 980px) { .wrap { grid-template-columns: 1fr; } }

  /* ---------------------------------------------------------- 극장 */
  #theater { position: relative; background: var(--surface); border-radius: var(--radius);
             box-shadow: var(--shadow); overflow: visible; border: 1px solid var(--line); }
  #stage { position: relative; background: #000; aspect-ratio: 16/9; overflow: hidden;
           border-radius: var(--radius) var(--radius) 0 0; }
  #theater:fullscreen { display: flex; flex-direction: column; border-radius: 0; border: 0; background: #000; }
  #theater:fullscreen #stage { flex: 1; aspect-ratio: auto; border-radius: 0; }
  #theater:fullscreen .bar { background: #000; border-top-color: #111; transition: opacity .3s; }
  #theater:fullscreen.idle .bar { opacity: .15; }
  /* 진짜 Fullscreen API 가 없거나(인앱 브라우저 패널) 거부될 때 쓰는 CSS 대체 전체화면.
     :fullscreen 과 같은 모양을 position:fixed 로 흉내 낸다 — 16:9 는 유튜브 iframe 이
     자체적으로 레터박스 처리한다(#stage 배경이 검정이라 나머지가 비어 보이지 않는다). */
  #theater.pseudo-fs { position: fixed; inset: 0; z-index: 2147483647; display: flex;
                        flex-direction: column; border-radius: 0; border: 0; background: #000;
                        width: 100vw; height: 100vh; }
  #theater.pseudo-fs #stage { flex: 1; aspect-ratio: auto; border-radius: 0; }
  #theater.pseudo-fs .bar { background: #000; border-top-color: #111; transition: opacity .3s; }
  #theater.pseudo-fs.idle .bar { opacity: .15; }
  body.pseudo-fs-active { overflow: hidden; }
  #player { position: absolute; inset: 0; width: 100%; height: 100%; }
  #player iframe { width: 100%; height: 100%; border: 0; }
  #blocked { position: absolute; inset: 0; display: none; align-items: center; justify-content: center;
             flex-direction: column; gap: 12px; color: var(--dim); background: #000; text-align: center; padding: 20px; }
  #blocked a { color: var(--accent); }
  #blocked .blockedNote { max-width: 480px; font-size: 12.5px; color: var(--faint); line-height: 1.5; }

  /* 자막: 기본은 상자 없이 사방 그림자(가독성이 가장 높은 방식), 설정에서 상자로 바꿀 수 있다. */
  #cueLayer { position: absolute; left: 5%; right: 5%; pointer-events: none;
              display: flex; flex-direction: column; align-items: center; }
  .cueBox { color: #fff; padding: 2px 10px; text-align: center; line-height: 1.38; font-weight: 600;
            letter-spacing: -.005em; pointer-events: none;
            text-shadow: 0 0 2px #000, 0 0 4px #000, 0 1px 3px #000, 0 0 8px rgba(0,0,0,.6); }
  .styleBox .cueBox { background: rgba(8,8,10,.74); border-radius: 6px; padding: 4px 14px;
                      text-shadow: none; font-weight: 500; }
  .cueBox.pending { color: #ffe3a3; }
  .cueLine { display: block; white-space: nowrap; }
  .cueEn { display: block; opacity: .8; font-size: .68em; font-weight: 500; margin-top: 3px; }
  .cueBox u.term { text-decoration: none; box-shadow: inset 0 -.14em 0 rgba(255,213,122,.75); }

  #termLayer { position: absolute; left: 6%; right: 6%; display: flex; flex-direction: column;
               align-items: center; pointer-events: none; gap: 3px; }
  .termLine { color: #fff; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
              text-shadow: 0 0 2px #000, 0 0 4px #000, 0 1px 3px #000; transition: opacity .4s ease, transform .4s ease; }
  .termLine b { color: var(--gloss); font-weight: 700; margin-right: .4em; }
  .termLine.fade { opacity: 0; transform: translateY(-4px); }

  /* ---------------------------------------------------------- 조작 막대 */
  .bar { position: relative; display: flex; align-items: center; gap: 6px; padding: 10px 12px;
         border-top: 1px solid var(--line); flex-wrap: wrap; }
  .grp { display: flex; align-items: center; gap: 4px; }
  .sep { width: 1px; height: 22px; background: var(--line); margin: 0 6px; }
  .iconBtn { display: inline-grid; place-items: center; width: 36px; height: 36px; border-radius: 10px;
             background: transparent; border: 0; color: var(--dim); cursor: pointer; }
  .iconBtn:hover { background: var(--surface-2); color: var(--text); }
  .iconBtn.on { color: var(--accent); background: var(--accent-soft); }
  .iconBtn small { font-size: 10px; font-weight: 700; margin-top: -2px; }
  /* 전체화면 API 를 못 쓸 때(인앱 브라우저 패널 등)만 나타나는 작은 텍스트 버튼. */
  .textBtn { display: none; align-items: center; height: 36px; padding: 0 10px; border-radius: 10px;
             background: transparent; border: 0; color: var(--dim); cursor: pointer; font-size: 12.5px;
             white-space: nowrap; }
  .textBtn.show { display: inline-flex; }
  .textBtn:hover { background: var(--surface-2); color: var(--text); }
  .seg { display: inline-flex; background: var(--bg); border: 1px solid var(--line); border-radius: 10px; padding: 3px; }
  .seg button { border: 0; background: transparent; color: var(--dim); padding: 5px 12px; border-radius: 7px;
                cursor: pointer; font-size: 13px; }
  .seg button.on { background: var(--surface-2); color: var(--text); box-shadow: 0 1px 2px rgba(0,0,0,.4); }
  .switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; color: var(--dim);
            font-size: 13px; padding: 6px 10px; border-radius: 10px; user-select: none; }
  .switch:hover { background: var(--surface-2); color: var(--text); }
  .switch input { display: none; }
  .switch .track { width: 30px; height: 18px; border-radius: 9px; background: #3a3f4a; position: relative; transition: background .2s; }
  .switch .track::after { content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px;
                          border-radius: 50%; background: #fff; transition: transform .2s; }
  .switch input:checked + .track { background: var(--gloss); }
  .switch input:checked + .track::after { transform: translateX(12px); }
  .switch input:checked ~ span { color: var(--text); }
  .clock { margin-left: auto; color: var(--dim); font-variant-numeric: tabular-nums; font-size: 12.5px; padding: 0 8px; }
  .clock b { color: var(--text); font-weight: 600; }

  #settings { position: absolute; bottom: calc(100% + 8px); right: 10px; width: 300px; z-index: 10;
              background: var(--surface-2); border: 1px solid var(--line); border-radius: 12px;
              box-shadow: var(--shadow); padding: 6px 14px 12px; display: none; }
  #settings.open { display: block; }
  #settings h5 { margin: 12px 0 6px; font-size: 11px; font-weight: 700; color: var(--faint);
                 letter-spacing: .06em; }
  .field { display: flex; align-items: center; justify-content: space-between; gap: 10px;
           padding: 5px 0; font-size: 13px; }
  .field output { color: var(--dim); font-variant-numeric: tabular-nums; min-width: 44px; text-align: right; }
  .field input[type=range] { flex: 1; accent-color: var(--accent); }
  .stepper { display: inline-flex; align-items: center; gap: 4px; }
  .stepper button { width: 28px; height: 28px; border-radius: 8px; border: 1px solid var(--line);
                    background: var(--bg); cursor: pointer; }
  .keys { color: var(--faint); font-size: 11.5px; margin-top: 10px; line-height: 1.8; }
  kbd { font: 11px ui-monospace, monospace; padding: 1px 5px; border-radius: 4px; border: 1px solid var(--line);
        background: var(--bg); color: var(--dim); }

  /* ---------------------------------------------------------- 옆 패널 */
  .panel { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
           display: flex; flex-direction: column; height: calc(100vh - 92px); min-height: 360px;
           position: sticky; top: 12px; overflow: hidden; }
  @media (max-width: 980px) { .panel { position: static; height: 60vh; } }
  .tabs { display: flex; gap: 4px; padding: 8px; border-bottom: 1px solid var(--line); }
  .tabs button { flex: 1; border: 0; background: transparent; color: var(--dim); padding: 7px 0;
                 border-radius: 8px; cursor: pointer; font-size: 13px; }
  .tabs button:hover { color: var(--text); }
  .tabs button.active { background: var(--surface-2); color: var(--text); }
  .tabs button em { font-style: normal; color: var(--faint); font-size: 11px; margin-left: 4px; }
  .tabBody { flex: 1; overflow-y: auto; display: none; position: relative; }
  .tabBody.active { display: flex; flex-direction: column; }
  #tabSent { overflow: hidden; }
  .head { padding: 10px 12px; position: sticky; top: 0; background: var(--surface); z-index: 2; }
  .search { display: flex; align-items: center; gap: 8px; background: var(--bg); border: 1px solid var(--line);
            border-radius: 10px; padding: 0 10px; color: var(--faint); }
  .search:focus-within { border-color: var(--accent); }
  .search input { flex: 1; background: transparent; border: 0; outline: 0; color: var(--text);
                  padding: 8px 0; font-size: 13px; }
  #list { padding: 0 8px 60px; overflow-y: auto; flex: 1; }
  .cue { display: grid; grid-template-columns: 50px 1fr; gap: 8px; padding: 8px 10px; border-radius: 10px;
         cursor: pointer; position: relative; color: #c3c8d1; transition: background .15s; }
  .cue:hover { background: rgba(255,255,255,.04); }
  .cue .t { color: var(--faint); font-variant-numeric: tabular-nums; font-size: 12px; padding-top: 1px; }
  .cue:hover .t { color: var(--accent); }
  .cue.active { background: var(--accent-soft); color: var(--text); }
  .cue.active::before { content: ""; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px;
                        border-radius: 2px; background: var(--accent); }
  .cue.active .t { color: var(--accent); }
  .cue.english { color: var(--faint); }
  .cue .tag { color: #ffb86b; font-size: 10.5px; margin-left: 6px; border: 1px solid rgba(255,184,107,.35);
              padding: 0 5px; border-radius: 4px; }
  mark { background: rgba(255,213,122,.25); color: inherit; border-radius: 3px; }
  #resume { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); display: none;
            align-items: center; gap: 6px; border: 0; border-radius: 999px; padding: 8px 14px;
            background: var(--accent); color: #0b0c0f; font-weight: 600; font-size: 12.5px; cursor: pointer;
            box-shadow: var(--shadow); z-index: 3; }
  #resume.show { display: inline-flex; }
  .empty { color: var(--faint); padding: 28px 16px; text-align: center; font-size: 13px; }

  #termList, #qualList { padding: 8px 10px 20px; }
  .termRow { padding: 12px; border-radius: 10px; cursor: pointer; border: 1px solid transparent; }
  .termRow:hover { background: rgba(255,255,255,.03); border-color: var(--line); }
  .termRow .top { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .termRow b { color: var(--gloss); font-weight: 650; }
  .termRow .tgt { color: var(--text); }
  .termRow .when { margin-left: auto; color: var(--faint); font-size: 11.5px; font-variant-numeric: tabular-nums; }
  .termRow .meaning { color: var(--dim); font-size: 12.5px; margin-top: 4px; }
  .pill { font-size: 10.5px; color: #ffb86b; border: 1px solid rgba(255,184,107,.35); border-radius: 4px; padding: 0 5px; }

  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 8px; }
  .stat { background: var(--surface-2); border-radius: 10px; padding: 10px; }
  .stat b { display: block; font-size: 18px; font-weight: 700; }
  .stat span { color: var(--faint); font-size: 11.5px; }
  .qSection { padding: 12px 4px; border-top: 1px solid var(--line); }
  .qSection h4 { margin: 0 0 8px; font-size: 11px; color: var(--faint); letter-spacing: .06em; }
  .qRow { display: flex; gap: 6px; align-items: baseline; padding: 4px 0; font-size: 13px; }
  .qRow s { color: var(--faint); text-decoration: line-through; }
  .qRow .ev { color: var(--faint); font-size: 11px; margin-left: auto; }
  .chip { display: inline-block; margin: 2px; padding: 2px 8px; border-radius: 6px; background: var(--surface-2);
          cursor: pointer; font-size: 12px; }
  .chip:hover { color: var(--accent); }
</style>
</head>
<body>
<header>
  <div class="brand"><i>C</i>CuePrecise</div>
  <div class="titleBox">
    <h1 id="title">불러오는 중…</h1>
    <div id="sub"></div>
  </div>
  <span class="status" id="status">준비 중</span>
</header>

<div class="wrap">
  <div id="theater">
    <div id="stage">
      <div id="player"></div>
      <div id="blocked"><div>업로더가 외부 사이트 재생을 허용하지 않은 영상입니다.</div>
        <div class="blockedNote">YouTube 페이지 위에 자막을 얹는 브라우저 확장 프로그램을 준비하고 있습니다.
          그동안은 CuePrecise MCP 도구로 영상 내용을 질문하거나 요약·타임스탬프 목차·스크립트
          같은 정리 파일을 만들 수 있습니다.</div>
        <a id="ytLink" href="#" target="_blank" rel="noopener">YouTube에서 보기</a></div>
      <div id="cueLayer"></div>
      <div id="termLayer"></div>
    </div>
    <div class="bar">
      <div class="grp">
        <button class="iconBtn" id="btnBack" title="5초 뒤로 (←)"><svg viewBox="0 0 24 24"><path d="M11 17l-5-5 5-5"/><path d="M18 17l-5-5 5-5"/></svg></button>
        <button class="iconBtn" id="btnFwd" title="5초 앞으로 (→)"><svg viewBox="0 0 24 24"><path d="M13 17l5-5-5-5"/><path d="M6 17l5-5-5-5"/></svg></button>
      </div>
      <span class="sep"></span>
      <div class="seg" role="group" aria-label="자막 언어">
        <button class="mode" data-mode="ko" title="한국어 (C)">한국어</button>
        <button class="mode" data-mode="both" title="한국어+영어 (C)">병기</button>
        <button class="mode" data-mode="en" title="영어 (C)">영어</button>
      </div>
      <label class="switch" title="용어 해설 (T)">
        <input id="cTerms" type="checkbox"><span class="track"></span><span>용어 해설</span>
      </label>
      <span class="clock" id="clock"><b>0:00</b> / 0:00</span>
      <button class="iconBtn" id="gearBtn" title="자막 설정"><svg viewBox="0 0 24 24"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/></svg></button>
      <button class="iconBtn" id="fsBtn" title="전체화면 (F)"><svg viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/></svg></button>
      <button class="textBtn" id="openBrowserBtn" title="기본 웹 브라우저에서 열기">브라우저에서 열기</button>
      <div id="settings">
        <h5>자막</h5>
        <div class="field">크기 <input id="rFont" type="range" min="0.6" max="2.0" step="0.05"><output id="oFont"></output></div>
        <div class="field">높이 <input id="rPos" type="range" min="4" max="40" step="1"><output id="oPos"></output></div>
        <div class="field">스타일
          <div class="seg"><button class="look" data-look="shadow">그림자</button><button class="look" data-look="box">상자</button></div>
        </div>
        <h5>싱크</h5>
        <div class="field">자막 시간 조정
          <span class="stepper"><button id="syncMinus">−</button><output id="oSync"></output><button id="syncPlus">+</button></span>
        </div>
        <input id="rSync" type="range" min="-5" max="5" step="0.1" hidden>
        <h5>문장 목록</h5>
        <div class="field">재생 위치 따라가기 <input id="cFollow" type="checkbox"></div>
        <div class="keys"><kbd>Space</kbd> 재생·정지 · <kbd>←</kbd><kbd>→</kbd> 5초 · <kbd>C</kbd> 자막 언어 · <kbd>T</kbd> 용어 해설 · <kbd>F</kbd> 전체화면</div>
      </div>
    </div>
  </div>

  <aside class="panel">
    <div class="tabs">
      <button data-tab="sent" class="active">스크립트</button>
      <button data-tab="term">용어<em id="termCount"></em></button>
      <button data-tab="qual">품질</button>
    </div>
    <div class="tabBody active" id="tabSent">
      <div class="head"><label class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg><input id="q" placeholder="스크립트 검색"></label></div>
      <div id="list"></div>
      <button id="resume"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12l7 7 7-7"/></svg>현재 위치로</button>
    </div>
    <div class="tabBody" id="tabTerm"><div id="termList"></div></div>
    <div class="tabBody" id="tabQual"><div id="qualList"></div></div>
  </aside>
</div>

<script src="https://www.youtube.com/iframe_api"></script>
<script>
"use strict";
const VIDEO_ID = (new URLSearchParams(location.search).get("v") || "").trim();
const READ_CPS = 13, MAX_LINES = 2, MIN_CUE = 1.5, KO_WIDTH = 1.0, LATIN_WIDTH = 0.55;
const $ = id => document.getElementById(id);

// ------------------------------------------------------------- 저장 설정
// 용어 해설 on/off 는 저장하지 않는다 — 페이지를 열 때마다 항상 꺼진 채로
// 시작한다(사용자 확정 사항). 그래서 `PERSISTED_KEYS` 목록에 "terms" 를
// 넣지 않고, 저장할 때도 그 키만 골라서 쓴다.
const PERSISTED_KEYS = ["fontScale", "posPct", "mode", "follow", "syncByVideo", "look"];
function loadPrefs() {
  const def = { fontScale: 1.0, posPct: 11, mode: "ko", follow: true, syncByVideo: {}, look: "shadow" };
  try {
    const raw = localStorage.getItem("cueprecise:prefs");
    if (raw) Object.assign(def, JSON.parse(raw));
  } catch (e) { /* 접근 불가면 기본값으로 진행 */ }
  def.terms = false; // 항상 꺼진 채로 시작. 저장된 값이 있어도 무시한다.
  return def;
}
function savePrefs() {
  const toSave = {};
  for (const key of PERSISTED_KEYS) toSave[key] = prefs[key];
  try { localStorage.setItem("cueprecise:prefs", JSON.stringify(toSave)); } catch (e) {}
}
const prefs = loadPrefs();
function syncOffset() { return prefs.syncByVideo[VIDEO_ID] || 0; }
function setSyncOffset(v) { prefs.syncByVideo[VIDEO_ID] = v; savePrefs(); }

// --------------------------------------------------------------- 유틸
function fmt(s) {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  const pad = n => String(n).padStart(2, "0");
  return h ? h + ":" + pad(m) + ":" + pad(x) : m + ":" + pad(x);
}
function esc(t) {
  return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function charWeight(ch) {
  const code = ch.codePointAt(0);
  const hangul = (code >= 0xAC00 && code <= 0xD7A3) || (code >= 0x3130 && code <= 0x318F);
  return hangul ? KO_WIDTH : LATIN_WIDTH;
}
function textWeight(t) {
  let w = 0;
  for (const ch of t) w += charWeight(ch);
  return w;
}

// ------------------------------------------------------ 자막 줄바꿈/배치
// 한 줄 폭은 글자 수로 어림하지 않고 실제 글꼴로 잰다. 어림이 넘치면 브라우저가
// 줄을 다시 접어 네 줄이 되고 어절 중간("있/어서")에서 끊긴다.
let measureCtx = null;
function cueFontPx() {
  return Math.max(12, $("cueLayer").getBoundingClientRect().width * 0.032 * prefs.fontScale);
}
function lineCapacity() {
  const box = $("cueLayer").getBoundingClientRect();
  const fontPx = cueFontPx();
  if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d");
  const family = getComputedStyle(document.body).fontFamily;
  return { maxPx: Math.max(80, box.width - 24 - 8), font: fontPx + "px " + family };
}
function textPx(text, capacity) {
  measureCtx.font = capacity.font;
  return measureCtx.measureText(text).width;
}

// 두 줄 이하로 나눈다. 못 담으면 null. 공백에서만 자르고, 두 줄 중 긴 쪽이 가장
// 짧아지는 자리를 고르되 쉼표·마침표 뒤를 조금 우대한다.
function wrapToLines(text, capacity) {
  text = text.trim();
  if (!text) return [""];
  if (textPx(text, capacity) <= capacity.maxPx) return [text];
  const words = text.split(/\s+/);
  if (words.length < 2) return null;
  let best = null, bestScore = Infinity;
  for (let i = 1; i < words.length; i++) {
    const first = words.slice(0, i).join(" ");
    const second = words.slice(i).join(" ");
    const w1 = textPx(first, capacity), w2 = textPx(second, capacity);
    if (w1 > capacity.maxPx || w2 > capacity.maxPx) continue;
    let score = Math.max(w1, w2);
    if (/[,.?!]$/.test(first)) score *= 0.9;
    if (i === words.length - 1 || i === 1) score *= 1.15; // 한 어절만 떨어뜨리는 나눔은 피한다.
    if (score < bestScore) { bestScore = score; best = [first, second]; }
  }
  return best;
}

// 두 줄에 못 담으면 공백에서 둘로 쪼개 시간도 글자 폭 비율로 나눈다.
function splitOverflow(text, start, end, capacity) {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length < 2 || wrapToLines(text, capacity)) {
    return [{ text, start, end }];
  }
  const total = textPx(text, capacity) || 1;
  let mid = 1, bestDiff = Infinity;
  for (let i = 1; i < words.length; i++) {
    const diff = Math.abs(textPx(words.slice(0, i).join(" "), capacity) - total / 2);
    const bonus = /[,.?!]$/.test(words[i - 1]) ? 0.85 : 1;
    if (diff * bonus < bestDiff) { bestDiff = diff * bonus; mid = i; }
  }
  const left = words.slice(0, mid).join(" ");
  const right = words.slice(mid).join(" ");
  const cut = start + (end - start) * (textPx(left, capacity) / total);
  return [...splitOverflow(left, start, cut, capacity),
          ...splitOverflow(right, cut, end, capacity)];
}

// 문장 하나(ko 세그먼트 또는 pending 영어 단어열)에서 화면 큐 목록을 만든다.
function layoutPieces(pieces, capacity) {
  const cues = [];
  for (const piece of pieces) {
    for (const part of splitOverflow(piece.text, piece.start, piece.end, capacity)) {
      cues.push({ start: part.start, end: part.end,
                  lines: wrapToLines(part.text, capacity) || [part.text] });
    }
  }
  // 1.5초 미만 큐 처리: 같은 문장 안에서 합치거나(2줄 초과하면 못 합치고)
  // 끝 시각만 다음 큐 시작 직전까지 늘린다.
  for (let i = 0; i < cues.length; i++) {
    const cue = cues[i];
    if (cue.end - cue.start >= MIN_CUE) continue;
    const next = cues[i + 1];
    if (next) {
      const mergedText = cue.lines.join(" ") + " " + next.lines.join(" ");
      const mergedLines = wrapToLines(mergedText.trim(), capacity);
      if (mergedLines && mergedLines.length <= MAX_LINES) {
        next.start = cue.start;
        next.lines = mergedLines;
        cues.splice(i, 1); i--; continue;
      }
      cue.end = Math.max(cue.end, next.start - 0.01);
    }
  }
  return cues;
}

function wordsInRange(words, a, b) {
  return words.filter(w => w[0] < b - 1e-6 && w[1] > a + 1e-6).map(w => w[2]);
}

// 문장 하나 -> 화면 큐 목록(ko 우선, 없으면 영어를 단어 시각으로).
function mergedFollowers(sentence) {
  // 이 문장 번역에 합쳐진 뒤 문장들(번역 단계에서 "<번호>|=" 로 병합).
  const result = [];
  if (!payload) return result;
  for (let i = sentence.no; i < payload.sentences.length; i++) {
    const next = payload.sentences[i];
    if (!next || next.merged_into !== sentence.key) break;
    result.push(next);
  }
  return result;
}

function sentenceCues(sentence, mode, capacity) {
  // 앞 문장 번역에 합쳐진 문장은 한국어 큐가 따로 없다. 앞 문장의 조각이 이 시간까지 덮는다.
  if (sentence.merged_into && mode !== "en") return [];
  const hasKo = !!sentence.ko && sentence.state !== "untranslated";
  if (mode === "en" || !hasKo) {
    const pieces = [{ text: sentence.en, start: sentence.start, end: sentence.end }];
    return layoutPieces(pieces, capacity).map(c => ({ ...c, pending: !hasKo }));
  }
  const segs = (sentence.segments && sentence.segments.length)
    ? sentence.segments
    : [{ text: sentence.ko, start: sentence.start, end: sentence.end }];
  const pieces = segs.map(s => ({ text: s.text, start: s.start, end: s.end }));
  const cues = layoutPieces(pieces, capacity);
  if (mode === "both") {
    const words = [sentence, ...mergedFollowers(sentence)].flatMap(s => s.words);
    for (const cue of cues) {
      const en = wordsInRange(words, cue.start, cue.end).join(" ");
      cue.en = en;
    }
  }
  return cues;
}

// ------------------------------------------------------------- 상태
let payload = null, player = null, blocked = false;
let displayCues = [], activeCueIndex = -1;
let activeSentenceNo = -1;
let pollTimer = null;
let termQueue = []; // 화면에 떠 있는 해설 줄
let firedTerms = new Set();

function rebuildDisplay() {
  if (!payload) return;
  const capacity = lineCapacity();
  displayCues = [];
  for (const sentence of payload.sentences) {
    for (const cue of sentenceCues(sentence, prefs.mode, capacity)) {
      displayCues.push({ ...cue, no: sentence.no });
    }
  }
  activeCueIndex = -1;
}

function findIndex(list, t, key) {
  let lo = 0, hi = list.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid][key !== undefined ? key : "start"] <= t) { ans = mid; lo = mid + 1; }
    else { hi = mid - 1; }
  }
  return ans;
}

function renderCue(cue) {
  const layer = $("cueLayer");
  if (!cue) { layer.innerHTML = ""; return; }
  const box = document.createElement("div");
  box.className = "cueBox" + (cue.pending ? " pending" : "");
  box.style.fontSize = cueFontPx() + "px";
  let html = cue.lines.map(line => {
    const safe = esc(line);
    return '<span class="cueLine">' + (prefs.terms ? underlineTerms(safe) : safe) + '</span>';
  }).join("");
  if (cue.en) html += '<span class="cueEn">' + esc(cue.en) + '</span>';
  box.innerHTML = html;
  placeCueLayer();
  layer.innerHTML = "";
  layer.appendChild(box);
  renderTermQueue();
}

// 유튜브 조작 막대(재생바) 높이만큼은 늘 비워 둔다. 자막은 클릭을 통과시킨다.
const YT_BAR_PX = 56;
function placeCueLayer() {
  const stage = $("stage").getBoundingClientRect();
  const px = Math.max(YT_BAR_PX, stage.height * prefs.posPct / 100);
  $("cueLayer").style.bottom = px + "px";
}

function underlineTerms(html) {
  if (!payload || !payload.terms) return html;
  let out = html;
  for (const term of payload.terms) {
    if (term.status !== "confirmed" || !term.tgt) continue;
    const safe = esc(term.tgt);
    if (!safe || out.indexOf(safe) < 0) continue;
    out = out.split(safe).join('<u class="term">' + safe + '</u>');
  }
  return out;
}

// ------------------------------------------------------------- 용어 해설
function termDuration(text) {
  return Math.max(5, text.length / 8 + 2);
}

function tryFireTerm(t) {
  if (!prefs.terms || !payload) return;
  for (const term of payload.terms) {
    if (term.status !== "confirmed" || !term.short) continue;
    if (term.first_t == null) continue;
    const key = term.src + "@" + term.first_t;
    const already = firedTerms.has(key);
    const passed = t >= term.first_t && t < term.first_t + 8;
    if (passed && !already) {
      firedTerms.add(key);
      showTermLine(term.src + ": " + term.short);
    } else if (!passed && t < term.first_t) {
      firedTerms.delete(key); // 되감아 다시 지나가면 다시 뜨게.
    }
  }
}

function showTermLine(text) {
  const cueTwoLines = displayCues[activeCueIndex] && displayCues[activeCueIndex].lines.length >= 2;
  const doShow = () => {
    if (termQueue.length >= 3) {
      const oldEnough = termQueue.filter(x => (Date.now() - x.born) >= 3000);
      if (!oldEnough.length) return; // 전부 3초 미만이면 버린다.
      const victim = oldEnough[0];
      removeTermLine(victim);
    }
    const entry = { text, born: Date.now(), el: null };
    entry.timer = setTimeout(() => fadeTermLine(entry), termDuration(text) * 1000);
    termQueue.push(entry);
    renderTermQueue();
  };
  if (cueTwoLines && termQueue.length >= 2) {
    setTimeout(doShow, 2000);
  } else {
    doShow();
  }
}
function fadeTermLine(entry) {
  entry.fading = true;
  renderTermQueue();
  setTimeout(() => removeTermLine(entry), 420);
}
function removeTermLine(entry) {
  clearTimeout(entry.timer);
  termQueue = termQueue.filter(x => x !== entry);
  renderTermQueue();
}
function renderTermQueue() {
  const layer = $("termLayer");
  const cueBox = $("cueLayer").getBoundingClientRect();
  const stage = $("stage").getBoundingClientRect();
  // 해설 줄은 자막 상자 바로 위에 붙인다(자막이 없으면 자막 자리 위).
  const cueTop = cueBox.height ? cueBox.top : stage.bottom - Math.max(YT_BAR_PX, stage.height * prefs.posPct / 100);
  layer.style.bottom = (stage.bottom - cueTop + 4) + "px";
  const fontPx = Math.max(10, cueBox.width * 0.032 * prefs.fontScale * 0.55);
  layer.innerHTML = "";
  // 레이어는 아래(자막 쪽)부터 채워진다. 가장 최근 줄이 마지막 자식 = 자막 바로 위.
  for (let i = 0; i < termQueue.length; i++) {
    const entry = termQueue[i];
    const div = document.createElement("div");
    div.className = "termLine" + (entry.fading ? " fade" : "");
    div.style.fontSize = fontPx + "px";
    div.style.maxWidth = (cueBox.width * 0.86) + "px";
    const cut = entry.text.indexOf(": ");
    if (cut > 0) {
      const b = document.createElement("b"); b.textContent = entry.text.slice(0, cut);
      div.appendChild(b); div.appendChild(document.createTextNode(entry.text.slice(cut + 2)));
    } else {
      div.textContent = entry.text;
    }
    layer.appendChild(div);
  }
}

// -------------------------------------------------------------- 재생 루프
function tick() {
  if (player && player.getCurrentTime && !blocked) {
    const t = player.getCurrentTime() + syncOffset();
    const dur = player.getDuration ? player.getDuration() : 0;
    $("clock").innerHTML = "<b>" + fmt(t) + "</b> / " + fmt(dur || 0);

    const idx = findIndex(displayCues, t);
    const cue = (idx >= 0 && t <= displayCues[idx].end + 0.15) ? displayCues[idx] : null;
    if (idx !== activeCueIndex || !cue) {
      activeCueIndex = cue ? idx : -1;
      renderCue(cue);
    }
    tryFireTerm(t);
    highlightSentenceAt(t);
  }
  requestAnimationFrame(tick);
}

function highlightSentenceAt(t) {
  if (!payload) return;
  const sentences = payload.sentences;
  let lo = 0, hi = sentences.length - 1, no = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (sentences[mid].start <= t) { no = sentences[mid].no; lo = mid + 1; } else { hi = mid - 1; }
  }
  if (no === activeSentenceNo) return;
  activeSentenceNo = no;
  const prev = $("list").querySelector(".cue.active");
  if (prev) prev.classList.remove("active");
  const row = $("list").querySelector('.cue[data-no="' + no + '"]');
  if (row) {
    row.classList.add("active");
    if (prefs.follow && !followPaused) scrollListTo(row);
  }
}

// 사용자가 목록을 직접 굴리면 따라가기를 잠시 멈추고 "현재 위치로" 버튼을 띄운다.
let followPaused = false, programmaticScroll = false;
function scrollListTo(row) {
  programmaticScroll = true;
  row.scrollIntoView({ block: "center", behavior: "smooth" });
  setTimeout(() => { programmaticScroll = false; }, 700);
}
["wheel", "touchmove", "keydown"].forEach(ev => $("list").addEventListener(ev, () => {
  if (!prefs.follow || programmaticScroll) return;
  followPaused = true; $("resume").classList.add("show");
}, { passive: true }));
$("resume").onclick = () => {
  followPaused = false; $("resume").classList.remove("show");
  const row = $("list").querySelector(".cue.active");
  if (row) scrollListTo(row);
};

// --------------------------------------------------------------- 패널
function renderSentenceList(filterText) {
  const list = $("list");
  const q = (filterText || "").trim().toLowerCase();
  const rx = q ? new RegExp("(" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig") : null;
  const mark = s => rx ? esc(s).replace(rx, "<mark>$1</mark>") : esc(s);
  // 병합된 문장은 앞 문장 줄에 묶어 보인다(영어 원문도 이어 붙여 검색된다).
  const items = payload.sentences.filter(s => !s.merged_into).map(s => {
    const followers = mergedFollowers(s);
    return followers.length ? { ...s, en: [s.en, ...followers.map(f => f.en)].join(" ") } : s;
  }).filter(s =>
    !q || s.en.toLowerCase().includes(q) || (s.ko || "").toLowerCase().includes(q));
  if (!items.length) { list.innerHTML = '<div class="empty">찾은 문장이 없습니다.</div>'; return; }
  list.innerHTML = items.map(s => {
    const english = !s.ko;
    const body = s.ko ? mark(s.ko) : mark(s.en);
    const tag = english ? '<span class="tag">영어</span>' : "";
    return '<div class="cue' + (english ? " english" : "") + '" data-no="' + s.no +
           '" data-start="' + s.start + '"><span class="t">' + fmt(s.start) + '</span>' +
           '<span>' + body + tag + '</span></div>';
  }).join("");
}

function explainableTermCount() {
  return (payload && payload.terms ? payload.terms : [])
    .filter(t => t.status === "confirmed" && t.short).length;
}

function renderTermTab() {
  const el = $("termList");
  if (!payload.terms.length) { el.innerHTML = '<div class="empty">용어가 없습니다.</div>'; return; }
  $("termCount").textContent = payload.terms.length;
  // 용어 해설(cTerms)을 켰는데 영상에 뜰 짧은해설(short)이 하나도 없으면,
  // 영상 위에는 아무것도 안 뜨는 게 정상이더라도 여기 탭에서는 알려준다.
  const notice = (prefs.terms && !explainableTermCount())
    ? '<div class="empty">이 영상에는 표시할 용어 해설이 없습니다.</div>' : "";
  const terms = payload.terms.slice().sort((a, b) => (a.first_t ?? 1e9) - (b.first_t ?? 1e9));
  el.innerHTML = notice + terms.map(t => {
    const status = t.status === "uncertain" ? ' <span class="pill">불확실</span>' : "";
    const same = t.tgt === t.src;
    return '<div class="termRow" data-first-t="' + (t.first_t == null ? "" : t.first_t) + '">' +
           '<div class="top"><b>' + esc(t.src) + '</b>' +
           (same ? "" : '<span class="tgt">' + esc(t.tgt) + '</span>') + status +
           (t.first_t != null ? '<span class="when">' + fmt(t.first_t) + '</span>' : "") + '</div>' +
           (t.note ? '<div class="meaning">' + esc(t.note) + '</div>' : "") +
           "</div>";
  }).join("");
}

function renderQualityTab() {
  const el = $("qualList");
  const r = payload.report, p = payload.progress;
  const fixes = r.corrections;  // 표기만 다른 항목은 서버가 이미 걸렀다.
  const corrections = fixes.length
    ? fixes.map(c => '<div class="qRow"><s>' + esc(c.from) + "</s> → <b>" + esc(c.to) +
        '</b><span class="ev">' + esc(c.evidence) + "</span></div>").join("")
    : '<div class="empty">없음</div>';
  const uncertain = r.uncertain.length ? esc(r.uncertain.join(", ")) : '<span class="ev">없음</span>';
  const untranslated = r.untranslated.length
    ? r.untranslated.map(no => '<span class="chip qRow" data-no="' + no + '">#' + no + "</span>").join("")
    : '<span class="ev">없음</span>';
  const warnings = (r.warnings || []).length
    ? '<div class="qSection" style="color:var(--gloss)"><h4>경고</h4>' +
      r.warnings.map(w => '<div class="qRow">' + esc(w) + "</div>").join("") + "</div>"
    : "";
  const pct = p.sentences ? Math.round(p.translated / p.sentences * 100) : 0;
  el.innerHTML =
    warnings +
    '<div class="stats">' +
      '<div class="stat"><b>' + pct + '%</b><span>번역</span></div>' +
      '<div class="stat"><b>' + (r.terms_count || 0) + '</b><span>용어 수</span></div>' +
      '<div class="stat"><b>' + fixes.length + '</b><span>용어 교정</span></div>' +
      '<div class="stat"><b>' + (r.speed_over_ratio * 100).toFixed(1) + '%</b><span>읽기 속도 초과</span></div>' +
    '</div>' +
    '<div class="qSection"><h4>교정한 표기</h4>' + corrections + "</div>" +
    '<div class="qSection"><h4>확인하지 못한 용어</h4>' + uncertain + "</div>" +
    '<div class="qSection"><h4>영어로 남은 문장</h4>' + untranslated + "</div>";
}

function seekTo(t) {
  if (player) { player.seekTo(Math.max(0, t), true); player.playVideo(); }
}

$("list").addEventListener("click", e => {
  const row = e.target.closest(".cue");
  if (row) seekTo(parseFloat(row.dataset.start));
});
$("q").addEventListener("input", e => renderSentenceList(e.target.value));
$("termList").addEventListener("click", e => {
  const row = e.target.closest(".termRow");
  const t = row && row.dataset.firstT;
  if (t) seekTo(parseFloat(t));
});
$("qualList").addEventListener("click", e => {
  const row = e.target.closest(".qRow[data-no]");
  if (!row) return;
  const s = payload.sentences.find(x => x.no === parseInt(row.dataset.no, 10));
  if (s) seekTo(s.start);
});
document.querySelectorAll(".tabs button").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".tabs button").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tabBody").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    $("tab" + btn.dataset.tab.charAt(0).toUpperCase() + btn.dataset.tab.slice(1)).classList.add("active");
  };
});

$("btnBack").onclick = () => { if (player) player.seekTo(Math.max(0, player.getCurrentTime() - 5), true); };
$("btnFwd").onclick = () => { if (player) player.seekTo(player.getCurrentTime() + 5, true); };

// --------------------------------------------------------------- 설정 UI
function applyPrefsToUI() {
  $("rFont").value = prefs.fontScale;
  $("oFont").textContent = Math.round(prefs.fontScale * 100) + "%";
  $("rPos").value = prefs.posPct;
  $("oPos").textContent = prefs.posPct + "%";
  $("rSync").value = syncOffset();
  $("oSync").textContent = (syncOffset() >= 0 ? "+" : "") + syncOffset().toFixed(1) + "초";
  $("cFollow").checked = !!prefs.follow;
  $("cTerms").checked = !!prefs.terms;
  document.querySelectorAll(".mode").forEach(b =>
    b.classList.toggle("on", b.dataset.mode === prefs.mode));
  document.querySelectorAll(".look").forEach(b =>
    b.classList.toggle("on", b.dataset.look === prefs.look));
  $("stage").classList.toggle("styleBox", prefs.look === "box");
}
$("gearBtn").onclick = e => {
  e.stopPropagation();
  const open = $("settings").classList.toggle("open");
  $("gearBtn").classList.toggle("on", open);
};
$("settings").addEventListener("click", e => e.stopPropagation());
document.addEventListener("click", () => {
  $("settings").classList.remove("open"); $("gearBtn").classList.remove("on");
});
document.querySelectorAll(".look").forEach(btn => {
  btn.onclick = () => { prefs.look = btn.dataset.look; savePrefs(); applyPrefsToUI(); };
});
function nudgeSync(delta) {
  const v = Math.max(-5, Math.min(5, Math.round((syncOffset() + delta) * 10) / 10));
  setSyncOffset(v); applyPrefsToUI();
}
$("syncMinus").onclick = () => nudgeSync(-0.1);
$("syncPlus").onclick = () => nudgeSync(0.1);
document.querySelectorAll(".mode").forEach(btn => {
  btn.onclick = () => {
    prefs.mode = btn.dataset.mode; savePrefs(); applyPrefsToUI(); rebuildDisplay();
  };
});
$("rFont").oninput = e => { prefs.fontScale = parseFloat(e.target.value); savePrefs(); applyPrefsToUI(); rebuildDisplay(); };
$("rPos").oninput = e => { prefs.posPct = parseFloat(e.target.value); savePrefs(); applyPrefsToUI(); placeCueLayer(); renderTermQueue(); };
$("rSync").oninput = e => { setSyncOffset(parseFloat(e.target.value)); };
$("cFollow").onchange = e => { prefs.follow = e.target.checked; savePrefs(); };
$("cTerms").onchange = e => {
  prefs.terms = e.target.checked; savePrefs();
  if (!prefs.terms) { termQueue.forEach(removeTermLine); firedTerms.clear(); }
  if (payload) renderTermTab();
};

// 자막은 클릭을 막지 않는다(영상 일시정지·재생바). 위치는 설정의 슬라이더로 조절한다.

// 창 크기·전체화면이 바뀌면 즉시 재배치.
window.addEventListener("resize", () => { rebuildDisplay(); placeCueLayer(); });
document.addEventListener("fullscreenchange", () => { rebuildDisplay(); placeCueLayer(); updateFsBtn(); });

// --------------------------------------------------------------- 전체화면
// Claude Desktop 인앱 브라우저 패널 같은 곳은 Fullscreen API 가 아예 없거나
// (document.fullscreenEnabled === false) requestFullscreen 이 조용히 거부된다.
// 그럴 땐 CSS 로 흉내낸 "의사 전체화면"(.pseudo-fs)으로 대체한다 — 다만 그
// 패널 자체를 벗어날 수는 없으므로, 진짜 전체화면을 원하면 기본 브라우저로
// 열도록 안내하는 버튼도 같이 보여준다.
function isFullscreenish() {
  return !!(document.fullscreenElement || $("theater").classList.contains("pseudo-fs"));
}

function updateFsBtn() {
  const on = isFullscreenish();
  $("fsBtn").classList.toggle("on", on);
  $("fsBtn").title = on ? "전체화면 종료 (F)" : "전체화면 (F)";
}

function showOpenBrowserBtn() { $("openBrowserBtn").classList.add("show"); }

function enterPseudoFullscreen() {
  $("theater").classList.add("pseudo-fs");
  document.body.classList.add("pseudo-fs-active");
  rebuildDisplay(); placeCueLayer(); updateFsBtn();
  showOpenBrowserBtn(); // 진짜 전체화면이 아니므로 빠져나갈 길을 늘 보여준다.
}

function exitPseudoFullscreen() {
  $("theater").classList.remove("pseudo-fs");
  document.body.classList.remove("pseudo-fs-active");
  rebuildDisplay(); placeCueLayer(); updateFsBtn();
}

function fullscreenApiUsable() {
  const el = $("theater");
  if (document.fullscreenEnabled === false) return false;
  return !!(el.requestFullscreen || el.webkitRequestFullscreen);
}

if (!fullscreenApiUsable()) showOpenBrowserBtn();

$("openBrowserBtn").onclick = () => { window.open(location.href, "_blank", "noopener"); };

$("fsBtn").onclick = () => {
  const theater = $("theater");
  if (document.fullscreenElement || theater.classList.contains("pseudo-fs")) {
    if (document.fullscreenElement && document.exitFullscreen) {
      Promise.resolve(document.exitFullscreen()).catch(() => {});
    }
    if (theater.classList.contains("pseudo-fs")) exitPseudoFullscreen();
    return;
  }
  const request = theater.requestFullscreen
    ? theater.requestFullscreen.bind(theater)
    : (theater.webkitRequestFullscreen ? theater.webkitRequestFullscreen.bind(theater) : null);
  if (document.fullscreenEnabled === false || !request) {
    enterPseudoFullscreen();
    return;
  }
  try {
    const result = request();
    if (result && typeof result.then === "function") {
      result.catch(() => { enterPseudoFullscreen(); });
    }
  } catch (e) {
    enterPseudoFullscreen();
  }
};

// 전체화면(진짜 또는 의사)에서 마우스가 멈추면 조작 막대를 흐리게 한다
// (자리는 그대로라 자막이 흔들리지 않는다).
let idleTimer = null;
document.addEventListener("mousemove", () => {
  $("theater").classList.remove("idle");
  clearTimeout(idleTimer);
  if (isFullscreenish()) idleTimer = setTimeout(() => $("theater").classList.add("idle"), 2500);
});

// 단축키. 검색창에 입력 중이면 가로채지 않는다.
document.addEventListener("keydown", e => {
  if (e.target.closest("input, textarea") || e.ctrlKey || e.metaKey || e.altKey) return;
  const key = e.key.toLowerCase();
  if (key === " " || key === "k") {
    if (!player || !player.getPlayerState) return;
    e.preventDefault();
    if (player.getPlayerState() === 1) player.pauseVideo(); else player.playVideo();
  } else if (key === "arrowleft") { e.preventDefault(); $("btnBack").click(); }
  else if (key === "arrowright") { e.preventDefault(); $("btnFwd").click(); }
  else if (key === "f") { $("fsBtn").click(); }
  else if (key === "t") { $("cTerms").click(); }
  else if (key === "c") {
    const order = ["ko", "both", "en"];
    prefs.mode = order[(order.indexOf(prefs.mode) + 1) % order.length];
    savePrefs(); applyPrefsToUI(); rebuildDisplay();
  } else if (key === "escape") {
    $("settings").classList.remove("open");
    if ($("theater").classList.contains("pseudo-fs")) exitPseudoFullscreen();
  }
});

// ----------------------------------------------------------------- 로드
async function fetchPayload() {
  const res = await fetch("/api/subtitle?v=" + encodeURIComponent(VIDEO_ID) + "&lang=ko");
  if (!res.ok) throw new Error("subtitle fetch failed: " + res.status);
  return res.json();
}

function renderStatus() {
  const p = payload.progress;
  $("sub").textContent = (payload.channel ? payload.channel + " · " : "") + "문장 " + p.sentences + "개";
  const el = $("status");
  el.className = "status " + (payload.complete ? "done" : "live");
  el.textContent = payload.complete ? "한국어 자막 완성"
    : "번역 중 " + (p.sentences ? Math.round(p.translated / p.sentences * 100) : 0) + "%";
}

async function boot() {
  applyPrefsToUI();
  updateFsBtn();
  try {
    payload = await fetchPayload();
  } catch (e) {
    $("title").textContent = "자막을 불러오지 못했습니다";
    $("status").textContent = "오류";
    return;
  }
  $("title").textContent = payload.title || VIDEO_ID;
  document.title = (payload.title || VIDEO_ID) + " · CuePrecise";
  renderStatus();
  $("ytLink").href = "https://www.youtube.com/watch?v=" + VIDEO_ID;
  rebuildDisplay();
  renderSentenceList("");
  renderTermTab();
  renderQualityTab();

  if (!payload.complete) {
    pollTimer = setInterval(async () => {
      const keepTime = player && player.getCurrentTime ? player.getCurrentTime() : null;
      try { payload = await fetchPayload(); } catch (e) { return; }
      rebuildDisplay();
      renderSentenceList($("q").value);
      renderTermTab();
      renderQualityTab();
      renderStatus();
      if (payload.complete) { clearInterval(pollTimer); pollTimer = null; }
      if (keepTime != null) { /* 재생 위치는 그대로 둔다 - seek 하지 않는다 */ }
    }, 10000);
  }
}

window.onYouTubeIframeAPIReady = function () {
  player = new YT.Player("player", {
    videoId: VIDEO_ID,
    playerVars: { cc_load_policy: 0, modestbranding: 1, rel: 0, fs: 0 },
    events: {
      onReady: function () { tick(); },
      onError: function (e) {
        const fatal = [101, 150, 153, 100].indexOf(e.data) >= 0;
        if (fatal) { blocked = true; $("blocked").style.display = "flex"; }
      },
    },
  });
};

boot();
</script>
</body>
</html>
"""
