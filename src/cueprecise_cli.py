"""CuePrecise 설치·진단 명령과 기존 파이프라인 CLI를 한 진입점으로 묶는다."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import configuration
import runtime


def _force_utf8(*streams: Any) -> None:
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


DEPRECATED_PROGRAMS = {"ytx", "ytx-mcp"}


def default_claude_config() -> Path:
    return configuration.default_claude_config()


def default_bundle_root() -> Path:
    return configuration.default_bundle_root()


def program_name(argv0: str | None = None) -> str:
    return Path(argv0 or sys.argv[0] or "cueprecise").stem


def warn_if_deprecated_alias(argv0: str | None = None) -> str | None:
    """옛 이름으로 실행하면 계속 동작하되 새 이름을 알린다."""
    name = program_name(argv0)
    if name not in DEPRECATED_PROGRAMS:
        return None
    replacement = "cueprecise-mcp" if name == "ytx-mcp" else "cueprecise"
    print(f"주의: `{name}`는 이전 이름이며 계속 동작하지만, 앞으로는 "
          f"`{replacement}`를 사용하기 바란다.", file=sys.stderr)
    return name


def _read_config(path: Path) -> dict[str, Any]:
    return configuration.read_config(path)


def _write_config(path: Path, value: dict[str, Any]) -> Path | None:
    return configuration.write_config(path, value)


def setup_claude(config_path: Path, bundle_root: Path, *, api_key: str | None,
                 server_command: str | None = None,
                 server_args: list[str] | None = None,
                 extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    """Claude Desktop 설정에 CuePrecise를 idempotent하게 등록한다.

    이전 이름(`ytx`) 항목이 있으면 API 키를 포함한 설정을 물려받아 옮기고,
    다른 MCP 서버 설정은 그대로 둔다.
    """
    if server_command is None:
        server_command = sys.executable
        server_args = ["-m", "mcp_server"]
    return configuration.setup_claude(
        config_path, bundle_root, api_key=api_key, server_command=server_command,
        server_args=server_args or [], extra_env=extra_env)


def doctor(config_path: Path) -> tuple[dict[str, Any], bool]:
    checks: dict[str, Any] = {
        "python": {"ok": sys.version_info >= (3, 11), "value": sys.version.split()[0]},
        "ffmpeg": {"ok": shutil.which(runtime.tool("ffmpeg")) is not None},
        "ffprobe": {"ok": shutil.which(runtime.tool("ffprobe")) is not None},
        "gemini_api_key": {"ok": bool(os.environ.get("GEMINI_API_KEY")), "required_for": "transcribe"},
        "claude_config": {"ok": False, "path": str(config_path)},
    }
    try:
        config = _read_config(config_path)
        servers = config.get("mcpServers", {})
        servers = servers if isinstance(servers, dict) else {}
        checks["claude_config"]["ok"] = configuration.SERVER_KEY in servers
        legacy = [key for key in configuration.LEGACY_SERVER_KEYS if key in servers]
        if legacy:
            checks["claude_config"]["legacy_entries"] = legacy
    except SystemExit as error:
        checks["claude_config"]["error"] = str(error)
    required_ok = all(checks[name]["ok"] for name in ("python", "ffmpeg", "ffprobe", "claude_config"))
    return checks, required_ok


def _setup_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cueprecise setup",
        description="Claude Desktop에 CuePrecise MCP를 등록한다.")
    parser.add_argument("--config", type=Path, default=default_claude_config())
    parser.add_argument("--bundle-root", type=Path, default=default_bundle_root())
    parser.add_argument("--api-key", default=None,
                        help="생략하면 현재 GEMINI_API_KEY를 사용한다. 키 없이도 조회 도구는 동작한다.")
    args = parser.parse_args(argv)
    key = args.api_key or os.environ.get("GEMINI_API_KEY")
    result = setup_claude(args.config, args.bundle_root, api_key=key)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not key:
        print("주의: GEMINI_API_KEY가 없어 전사 기능은 키를 설정할 때까지 동작하지 않는다.", file=sys.stderr)
    if result.get("migrated_from"):
        print(f"이전 `{result['migrated_from']}` 항목을 `{result['server_key']}`로 옮겼다. "
              "저장돼 있던 설정은 그대로 유지된다.", file=sys.stderr)
    print("Claude Desktop을 다시 시작한 뒤 CuePrecise 도구를 사용할 수 있다.", file=sys.stderr)
    return 0


def _doctor_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cueprecise doctor", description="CuePrecise 실행 환경을 진단한다.")
    parser.add_argument("--config", type=Path, default=default_claude_config())
    args = parser.parse_args(argv)
    checks, ok = doctor(args.config)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main() -> int:
    _force_utf8(sys.stdin, sys.stdout, sys.stderr)
    warn_if_deprecated_alias()
    argv = sys.argv[1:]
    if argv and argv[0] == "setup":
        return _setup_main(argv[1:])
    if argv and argv[0] == "doctor":
        return _doctor_main(argv[1:])
    if not argv or argv[0] in {"-h", "--help"}:
        print("""usage: cueprecise {setup,doctor,run,status,purge} ...

CuePrecise — Find the exact moment in any YouTube video.

설치와 MCP 등록:
  setup             Claude Desktop에 CuePrecise MCP 등록
  doctor            Python, ffmpeg, API 키, MCP 설정 진단

영상 파이프라인:
  run               영상 분석
  status            작업 상태와 사용량 조회
  purge             파생 자료 또는 원본 삭제

각 명령의 도움말: cueprecise <command> --help

`ytx`, `ytx-mcp`는 이전 이름이며 호환을 위해 계속 동작한다.""")
        return 0
    if argv[0] not in {"run", "status", "purge"}:
        print(f"알 수 없는 명령: {argv[0]}", file=sys.stderr)
        return 2
    import pipeline
    return pipeline.main()


if __name__ == "__main__":
    raise SystemExit(main())
