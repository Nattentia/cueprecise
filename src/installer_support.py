"""Windows 초보자 설치 화면의 검증·연결 로직."""
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
        return False, f"ytx 연결 시험을 시작하지 못했습니다: {error}"
    try:
        response: dict[str, Any] = json.loads((result.stdout or "").splitlines()[0])
    except (IndexError, json.JSONDecodeError):
        detail = (result.stderr or result.stdout or "응답 없음").strip()[-500:]
        return False, f"ytx가 올바른 응답을 보내지 않았습니다.\n\n{detail}"
    if response.get("result", {}).get("serverInfo", {}).get("name") != "ytx":
        return False, "ytx 연결 시험의 응답을 확인하지 못했습니다."
    return True, None


def connect(api_key: str, install_dir: Path, *,
            config_path: Path | None = None,
            bundle_root: Path | None = None) -> dict[str, Any]:
    """필수 도구를 확인하고 Claude 설정을 안전하게 저장한 뒤 MCP를 시험한다."""
    key, error = validate_api_key(api_key)
    if error:
        raise ValueError(error)
    server = (install_dir / "ytx-mcp.exe").resolve()
    if not server.is_file():
        raise FileNotFoundError("설치된 ytx-mcp.exe를 찾지 못했습니다. ytx를 다시 설치해 주세요.")
    ffmpeg_bin, ffmpeg_error = ensure_ffmpeg()
    if ffmpeg_error or ffmpeg_bin is None:
        raise RuntimeError(ffmpeg_error)

    destination = bundle_root or (Path.home() / ".ytx" / "data")
    config = config_path or configuration.default_claude_config()
    server_environment = {
        "PATH": str(ffmpeg_bin) + os.pathsep + os.environ.get("PATH", ""),
    }
    destination.mkdir(parents=True, exist_ok=True)
    ok, probe_error = probe_mcp(server, destination, {**server_environment, "GEMINI_API_KEY": key})
    if not ok:
        raise RuntimeError(probe_error)
    result = configuration.setup_claude(
        config, destination, api_key=key, server_command=str(server), server_args=[],
        extra_env=server_environment)
    return {**result, "ffmpeg_bin": str(ffmpeg_bin), "connection_tested": True}


def disconnect(config_path: Path | None = None) -> dict[str, Any]:
    """Claude 설정에서 ytx 항목만 제거하며 영상 데이터와 FFmpeg는 보존한다."""
    config = config_path or configuration.default_claude_config()
    value = configuration.read_config(config)
    servers = value.get("mcpServers")
    if not isinstance(servers, dict) or "ytx" not in servers:
        return {"changed": False, "config": str(config), "backup": None}
    del servers["ytx"]
    backup = configuration.write_config(config, value)
    return {"changed": True, "config": str(config),
            "backup": str(backup) if backup else None}
