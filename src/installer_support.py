"""CuePrecise Windows 설치 화면의 검증·연결 로직."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import configuration


API_KEY_PATTERN = re.compile(r"^AIza[0-9A-Za-z_-]{30,}$")

# 번들 MCP 서버 실행 파일. 앞의 것을 먼저 찾고, 없으면 이전 이름으로 설치된
# 0.1.0 위에 덮어 설치된 경우를 위해 뒤의 것을 본다.
SERVER_EXECUTABLES = ("cueprecise-mcp.exe", "ytx-mcp.exe")
# initialize 응답의 serverInfo.name 허용값. 이전 이름도 받아 준다.
SERVER_NAMES = {"cueprecise", "ytx"}


def _bundled_server(install_dir: Path) -> Path | None:
    for filename in SERVER_EXECUTABLES:
        candidate = (install_dir / filename).resolve()
        if candidate.is_file():
            return candidate
    return None


def normalize_api_key(value: str) -> str:
    """붙여넣기 중 들어온 따옴표와 앞뒤 공백만 안전하게 제거한다."""
    key = value.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {'"', "'"}:
        key = key[1:-1].strip()
    return key


def validate_api_key(value: str) -> tuple[str, str | None]:
    key = normalize_api_key(value)
    if not key:
        return "", "Gemini API 키를 붙여넣어 주세요."
    if any(character.isspace() for character in key):
        return key, "API 키 중간에 공백이 있습니다. 전체 키를 다시 복사해 주세요."
    if not API_KEY_PATTERN.fullmatch(key):
        return key, "Google AI Studio에서 복사한 AIza로 시작하는 API 키인지 확인해 주세요."
    return key, None


def _winget_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Microsoft" / "WinGet" / "Packages"


def find_ffmpeg_bin() -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return Path(ffmpeg).resolve().parent
    root = _winget_root()
    if not root.exists():
        return None
    for candidate in root.glob("Gyan.FFmpeg_*/*/bin/ffmpeg.exe"):
        if (candidate.parent / "ffprobe.exe").is_file():
            return candidate.parent.resolve()
    return None


def ensure_ffmpeg() -> tuple[Path | None, str | None]:
    existing = find_ffmpeg_bin()
    if existing:
        return existing, None
    winget = shutil.which("winget")
    if not winget:
        return None, "Windows 앱 설치 도구(winget)를 찾지 못했습니다. Windows를 업데이트한 뒤 다시 시도해 주세요."
    result = subprocess.run(
        [winget, "install", "--id", "Gyan.FFmpeg", "-e", "--silent",
         "--accept-package-agreements", "--accept-source-agreements"],
        capture_output=True, text=True, timeout=600,
    )
    found = find_ffmpeg_bin()
    if found:
        return found, None
    detail = (result.stderr or result.stdout or "").strip()[-500:]
    return None, "FFmpeg 자동 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요." + (f"\n\n{detail}" if detail else "")


def probe_mcp(server: Path, bundle_root: Path, environment: dict[str, str]) -> tuple[bool, str | None]:
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
    try:
        result = subprocess.run(
            [str(server), "--bundle-root", str(bundle_root)], input=request,
            capture_output=True, text=True, timeout=20,
            env={**os.environ, **environment},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"CuePrecise 연결 시험을 시작하지 못했습니다: {error}"
    try:
        response: dict[str, Any] = json.loads((result.stdout or "").splitlines()[0])
    except (IndexError, json.JSONDecodeError):
        detail = (result.stderr or result.stdout or "응답 없음").strip()[-500:]
        return False, f"CuePrecise가 올바른 응답을 보내지 않았습니다.\n\n{detail}"
    name = response.get("result", {}).get("serverInfo", {}).get("name")
    if name not in SERVER_NAMES:
        return False, "CuePrecise 연결 시험의 응답을 확인하지 못했습니다."
    return True, None


def connect_clients(api_key: str, install_dir: Path, *,
                    targets: list[configuration.ClientTarget] | None = None,
                    config_path: Path | None = None,
                    bundle_root: Path | None = None) -> dict[str, Any]:
    """이 PC에 있는 AI 앱을 찾아 하나씩 붙인다.

    한 앱이 실패해도 나머지를 계속 붙인다. 앱마다 사정이 다른데 하나가
    막혔다고 전부 포기하면, 멀쩡한 앱까지 못 쓰게 된다. 대신 무엇이 되고
    무엇이 안 됐는지를 그대로 돌려준다.

    필수 도구 확인과 연결 시험은 앱 수와 무관하게 한 번만 한다.
    """
    key, error = validate_api_key(api_key)
    if error:
        raise ValueError(error)
    server = _bundled_server(install_dir)
    if server is None:
        raise FileNotFoundError(
            "설치된 cueprecise-mcp.exe를 찾지 못했습니다. CuePrecise를 다시 설치해 주세요.")
    chosen = configuration.detected_clients() if targets is None else list(targets)
    if not chosen:
        # 붙인 앱이 하나도 없는 것을 성공이라고 말하면 안 된다.
        raise RuntimeError(
            "연결할 AI 앱을 찾지 못했습니다. Claude Desktop, Codex, Claude Code, "
            "VS Code 중 하나를 설치한 뒤 다시 시도해 주세요.")
    ffmpeg_bin, ffmpeg_error = ensure_ffmpeg()
    if ffmpeg_error or ffmpeg_bin is None:
        raise RuntimeError(ffmpeg_error)

    destination = bundle_root or configuration.default_bundle_root()
    server_environment = {
        "PATH": str(ffmpeg_bin) + os.pathsep + os.environ.get("PATH", ""),
    }
    destination.mkdir(parents=True, exist_ok=True)
    ok, probe_error = probe_mcp(server, destination, {**server_environment, "GEMINI_API_KEY": key})
    if not ok:
        raise RuntimeError(probe_error)

    connected: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for target in chosen:
        try:
            result = target.install(
                destination, api_key=key, server_command=str(server), server_args=[],
                extra_env=server_environment,
                config_path=config_path if len(chosen) == 1 else None)
        except Exception as error:  # 한 앱의 실패가 나머지를 막지 않는다.
            failed.append({"key": target.key, "label": target.label, "reason": str(error)})
        else:
            connected.append({"key": target.key, "label": target.label, **result})
    if not connected:
        raise RuntimeError("\n".join(
            f"{item['label']}: {item['reason']}" for item in failed))
    return {"connected": connected, "failed": failed,
            "ffmpeg_bin": str(ffmpeg_bin), "connection_tested": True,
            "bundle_root": str(destination.resolve())}


def connect(api_key: str, install_dir: Path, *,
            config_path: Path | None = None,
            bundle_root: Path | None = None) -> dict[str, Any]:
    """Claude Desktop 하나에만 붙인다. `connect_clients` 의 얇은 이름이다."""
    result = connect_clients(api_key, install_dir, targets=[configuration.CLAUDE_DESKTOP],
                             config_path=config_path, bundle_root=bundle_root)
    entry = result["connected"][0]
    return {**entry, "ffmpeg_bin": result["ffmpeg_bin"], "connection_tested": True}


def migrate(install_dir: Path, config_path: Path | None = None) -> dict[str, Any]:
    """이미 연결돼 있던 설정만 새 이름으로 옮긴다.

    설치 프로그램이 조용히 부른다. 네트워크를 쓰지 않고 사용자에게 아무것도
    묻지 않으며, 연결된 적이 없으면 아무 일도 하지 않는다. API 키를 포함한
    기존 환경변수는 `configuration.setup_claude` 가 물려받는다.
    """
    config = config_path or configuration.default_claude_config()
    try:
        value = configuration.read_config(config)
    except SystemExit as error:
        return {"changed": False, "reason": str(error)}
    found = configuration.find_managed_entry(value)
    if found is None:
        return {"changed": False, "reason": "연결된 항목이 없다."}
    key, entry = found
    server = _bundled_server(install_dir)
    if server is None:
        return {"changed": False, "reason": "번들 서버 실행 파일을 찾지 못했다."}
    destination = configuration.bundle_root_of(entry) or configuration.default_bundle_root()
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 등록된 경로가 지금은 없는 드라이브일 수 있다. 그때는 기본 위치로 돌아간다.
        destination = configuration.default_bundle_root()
    result = configuration.setup_claude(
        config, destination, api_key=None, server_command=str(server), server_args=[])
    return {**result, "changed": True, "previous_key": key}


def disconnect(config_path: Path | None = None) -> dict[str, Any]:
    """Claude 설정에서 이 프로그램이 만든 항목만 제거한다.

    CuePrecise 항목과 이전 이름(ytx) 항목을 모두 지우되, 같은 이름을 쓰는
    남의 항목과 다른 MCP 서버는 건드리지 않는다. 영상 데이터와 FFmpeg도
    그대로 둔다.
    """
    config = config_path or configuration.default_claude_config()
    return configuration.remove_claude(config)


def disconnect_clients(targets: list[configuration.ClientTarget] | None = None) -> dict[str, Any]:
    """붙였던 앱 전부에서 우리 항목만 뗀다.

    감지되지 않은 앱도 살펴본다. 붙인 뒤에 앱을 지운 사용자의 설정에 우리
    항목이 남아 있을 수 있고, 남겨 두면 그 앱을 다시 깔았을 때 없는 서버를
    가리킨다. 한 앱에서 실패해도 나머지는 계속 뗀다.
    """
    removed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for target in (targets if targets is not None else list(configuration.CLIENTS)):
        try:
            result = target.remove()
        except Exception as error:
            failed.append({"key": target.key, "label": target.label, "reason": str(error)})
        else:
            if result.get("changed"):
                removed.append({"key": target.key, "label": target.label, **result})
    return {"removed": removed, "failed": failed, "changed": bool(removed)}
