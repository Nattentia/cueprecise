"""ytx 설치·진단 명령과 기존 파이프라인 CLI를 한 진입점으로 묶는다."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pipeline


def _force_utf8(*streams: Any) -> None:
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def default_claude_config() -> Path:
    """현재 운영체제의 Claude Desktop MCP 설정 경로를 반환한다."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"설정 파일을 읽을 수 없다: {path}\n{error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"설정 파일 최상위 값은 JSON 객체여야 한다: {path}")
    return value


def _write_config(path: Path, value: dict[str, Any]) -> Path | None:
    """기존 설정을 백업하고 같은 디렉터리에서 원자적으로 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        shutil.copy2(path, backup)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return backup


def setup_claude(config_path: Path, bundle_root: Path, *, api_key: str | None) -> dict[str, Any]:
    """Claude Desktop 설정에 ytx를 idempotent하게 등록한다."""
    config = _read_config(config_path)
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SystemExit(f"mcpServers는 JSON 객체여야 한다: {config_path}")

    server: dict[str, Any] = {
        "command": sys.executable,
        "args": ["-m", "mcp_server", "--bundle-root", str(bundle_root.resolve())],
    }
    if api_key:
        server["env"] = {"GEMINI_API_KEY": api_key}
    servers["ytx"] = server
    bundle_root.mkdir(parents=True, exist_ok=True)
    backup = _write_config(config_path, config)
    return {"config": str(config_path), "bundle_root": str(bundle_root.resolve()),
            "backup": str(backup) if backup else None, "api_key_configured": bool(api_key)}


def doctor(config_path: Path) -> tuple[dict[str, Any], bool]:
    checks: dict[str, Any] = {
        "python": {"ok": sys.version_info >= (3, 11), "value": sys.version.split()[0]},
        "ffmpeg": {"ok": shutil.which("ffmpeg") is not None},
        "ffprobe": {"ok": shutil.which("ffprobe") is not None},
        "gemini_api_key": {"ok": bool(os.environ.get("GEMINI_API_KEY")), "required_for": "transcribe"},
        "claude_config": {"ok": False, "path": str(config_path)},
    }
    try:
        config = _read_config(config_path)
        checks["claude_config"]["ok"] = "ytx" in config.get("mcpServers", {})
    except SystemExit as error:
        checks["claude_config"]["error"] = str(error)
    required_ok = all(checks[name]["ok"] for name in ("python", "ffmpeg", "ffprobe", "claude_config"))
    return checks, required_ok


def _setup_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ytx setup", description="Claude Desktop에 ytx MCP를 등록한다.")
    parser.add_argument("--config", type=Path, default=default_claude_config())
    parser.add_argument("--bundle-root", type=Path, default=Path.home() / ".ytx" / "data")
    parser.add_argument("--api-key", default=None,
                        help="생략하면 현재 GEMINI_API_KEY를 사용한다. 키 없이도 조회 도구는 동작한다.")
    args = parser.parse_args(argv)
    key = args.api_key or os.environ.get("GEMINI_API_KEY")
    result = setup_claude(args.config, args.bundle_root, api_key=key)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not key:
        print("주의: GEMINI_API_KEY가 없어 전사 기능은 키를 설정할 때까지 동작하지 않는다.", file=sys.stderr)
    print("Claude Desktop을 다시 시작한 뒤 ytx 도구를 사용할 수 있다.", file=sys.stderr)
    return 0


def _doctor_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ytx doctor", description="ytx 실행 환경을 진단한다.")
    parser.add_argument("--config", type=Path, default=default_claude_config())
    args = parser.parse_args(argv)
    checks, ok = doctor(args.config)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main() -> int:
    _force_utf8(sys.stdin, sys.stdout, sys.stderr)
    argv = sys.argv[1:]
    if argv and argv[0] == "setup":
        return _setup_main(argv[1:])
    if argv and argv[0] == "doctor":
        return _doctor_main(argv[1:])
    if not argv or argv[0] in {"-h", "--help"}:
        print("""usage: ytx {setup,doctor,run,status,purge} ...

설치와 MCP 등록:
  setup             Claude Desktop에 ytx MCP 등록
  doctor            Python, ffmpeg, API 키, MCP 설정 진단

영상 파이프라인:
  run               영상 분석
  status            작업 상태와 사용량 조회
  purge             파생 자료 또는 원본 삭제

각 명령의 도움말: ytx <command> --help""")
        return 0
    if argv[0] not in {"run", "status", "purge"}:
        print(f"알 수 없는 명령: {argv[0]}", file=sys.stderr)
        return 2
    return pipeline.main()


if __name__ == "__main__":
    raise SystemExit(main())
