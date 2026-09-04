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


def default_claude_config() -> Path:
    return configuration.default_claude_config()


def default_bundle_root() -> Path:
    return configuration.default_bundle_root()


def program_name(argv0: str | None = None) -> str:
    return Path(argv0 or sys.argv[0] or "cueprecise").stem


def _read_config(path: Path) -> dict[str, Any]:
    return configuration.read_config(path)


def _write_config(path: Path, value: dict[str, Any]) -> Path | None:
    return configuration.write_config(path, value)


def setup_claude(config_path: Path, bundle_root: Path, *, api_key: str | None,
                 server_command: str | None = None,
                 server_args: list[str] | None = None,
                 extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    """Claude Desktop 설정에 CuePrecise를 idempotent하게 등록한다.

    다른 MCP 서버 설정은 그대로 둔다.
    """
    if server_command is None:
        server_command = sys.executable
        server_args = ["-m", "mcp_server"]
    return configuration.setup_claude(
        config_path, bundle_root, api_key=api_key, server_command=server_command,
        server_args=server_args or [], extra_env=extra_env)


def client_report() -> list[dict[str, Any]]:
    """등록된 앱마다 설치 여부와 연결 여부를 살핀다.

    설정을 읽다 실패해도 그 앱만 이유를 달고 넘어간다. 한 앱의 깨진 설정이
    나머지 진단까지 가리면 안 된다.
    """
    rows: list[dict[str, Any]] = []
    for target in configuration.CLIENTS:
        row: dict[str, Any] = {"key": target.key, "label": target.label,
                               "installed": False, "connected": False}
        try:
            row["installed"] = target.is_installed()
            path = target.locate_config()
            row["config"] = str(path)
            # Codex 는 TOML 이다. 타깃이 자기 포맷으로 읽게 둔다.
            loader = getattr(target, "load_config", None)
            config = loader(path) if loader is not None else _read_config(path)
            found = configuration.find_managed_entry(config, target.servers_key)
        except Exception as error:
            # 설정 위치조차 정할 수 없는 환경(HOME 이 없는 등)이나 깨진 설정
            # 파일이 나머지 앱의 진단까지 가리면 안 된다.
            row["error"] = str(error)
        else:
            row["connected"] = found is not None
            if found is not None and found[0] != configuration.SERVER_KEY:
                row["legacy_entry"] = found[0]
        rows.append(row)
    return rows


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
    except SystemExit as error:
        checks["claude_config"]["error"] = str(error)
    checks["clients"] = client_report()
    # 어느 앱에든 붙어 있으면 쓸 수 있다. `claude_config` 하나로 판정하면
    # Codex 나 VS Code 에만 붙인 사용자가 실패로 나온다.
    connected = any(row["connected"] for row in checks["clients"])
    required_ok = (all(checks[name]["ok"] for name in ("python", "ffmpeg", "ffprobe"))
                   and (connected or checks["claude_config"]["ok"]))
    return checks, required_ok


def _resolve_setup_key(api_key: str | None, api_key_file: Path | None) -> str | None:
    """등록에 쓸 키를 정한다. 명령줄에 값을 적는 길은 남기되 값을 치른다.

    `--api-key <값>` 은 그 값을 셸 기록 파일에 영구히 남긴다. 명령줄 노출은 그
    프로세스가 사는 동안뿐이지만 기록 파일은 지울 때까지 남는다. 자동화가 이미
    쓰고 있을 수 있으므로 없애지 않고, 대신 소리를 내고 대안을 알린다.
    """
    if api_key_file is not None:
        # 빈 파일은 첫 줄이 없다. 키가 없는 것으로 보고 넘어간다 — 키 없이도
        # 조회 도구는 등록되고 동작한다.
        lines = api_key_file.read_text(encoding="utf-8").splitlines()
        return (lines[0].strip() or None) if lines else None
    if api_key == "-":
        return sys.stdin.readline().strip() or None
    if api_key:
        print("주의: `--api-key` 에 적은 키는 셸 기록에 남는다. "
              "`--api-key-file <파일>` 이나 `--api-key -` (표준 입력)를 권한다.",
              file=sys.stderr)
        return api_key
    return os.environ.get("GEMINI_API_KEY")


def _setup_main(argv: list[str]) -> int:
    known = ", ".join(target.key for target in configuration.CLIENTS)
    parser = argparse.ArgumentParser(
        prog="cueprecise setup",
        description="이 PC에 있는 AI 앱에 CuePrecise MCP를 등록한다.")
    parser.add_argument("--client", default="all",
                        help=f"쉼표로 구분한다. 생략하면 감지된 앱 전부다. 가능한 값: all, {known}")
    parser.add_argument("--config", type=Path, default=None,
                        help="앱 하나만 지정했을 때 그 앱의 설정 파일 경로를 직접 준다.")
    parser.add_argument("--bundle-root", type=Path, default=default_bundle_root())
    parser.add_argument("--api-key", default=None,
                        help="키를 직접 적으면 셸 기록에 남는다. `-` 를 주면 표준 입력에서 "
                             "읽고, `--api-key-file` 은 파일에서 읽는다. "
                             "생략하면 현재 GEMINI_API_KEY를 사용한다. "
                             "키 없이도 조회 도구는 동작한다.")
    parser.add_argument("--api-key-file", type=Path, default=None,
                        help="키가 들어 있는 파일. 첫 줄만 읽는다.")
    args = parser.parse_args(argv)
    if args.api_key is not None and args.api_key_file is not None:
        print("`--api-key` 와 `--api-key-file` 은 함께 쓸 수 없다.", file=sys.stderr)
        return 2
    try:
        key = _resolve_setup_key(args.api_key, args.api_key_file)
    except OSError as error:
        print(f"키 파일을 읽지 못했다: {error}", file=sys.stderr)
        return 2

    targets = configuration.resolve_clients(args.client.split(","))
    if not targets:
        print("연결할 AI 앱을 찾지 못했다. `--client` 로 이름을 직접 줄 수 있다: "
              f"{known}", file=sys.stderr)
        return 1
    if args.config is not None and len(targets) != 1:
        print("`--config` 는 앱을 하나만 지정했을 때만 쓸 수 있다.", file=sys.stderr)
        return 2

    command, arguments = sys.executable, ["-m", "mcp_server"]
    connected: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for target in targets:
        try:
            result = target.install(args.bundle_root, api_key=key, server_command=command,
                                    server_args=arguments, config_path=args.config)
        except Exception as error:  # 한 앱의 실패가 나머지를 막지 않는다.
            failed.append({"key": target.key, "label": target.label, "reason": str(error)})
        else:
            connected.append({"key": target.key, "label": target.label, **result})

    print(json.dumps({"connected": connected, "failed": failed}, ensure_ascii=False, indent=2))
    if not key:
        print("주의: GEMINI_API_KEY가 없어 전사 기능은 키를 설정할 때까지 동작하지 않는다.", file=sys.stderr)
    for item in failed:
        print(f"{item['label']}: {item['reason']}", file=sys.stderr)
    if not connected:
        return 1
    names = ", ".join(item["label"] for item in connected)
    print(f"{names}을(를) 다시 시작한 뒤 CuePrecise 도구를 사용할 수 있다.", file=sys.stderr)
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

각 명령의 도움말: cueprecise <command> --help""")
        return 0
    if argv[0] not in {"run", "status", "purge"}:
        print(f"알 수 없는 명령: {argv[0]}", file=sys.stderr)
        return 2
    import pipeline
    return pipeline.main()


if __name__ == "__main__":
    raise SystemExit(main())
