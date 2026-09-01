"""Claude Desktop 설정을 보존하며 CuePrecise 항목만 관리한다."""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SERVER_KEY = "cueprecise"
# 0.1.0 까지 쓰던 항목 이름. 새 설치는 만들지 않고, 발견하면 SERVER_KEY 로 옮긴다.
LEGACY_SERVER_KEYS = ("ytx",)
# 0.1.0 까지 쓰던 기본 데이터 폴더. 발견하면 옮기지 않고 그대로 계속 쓴다.
LEGACY_BUNDLE_DIRS = (".ytx",)

# 이 프로그램이 만든 MCP 항목인지 판정할 때 쓰는 실행 파일 이름.
MANAGED_COMMANDS = {
    "cueprecise-mcp", "cueprecise-mcp.exe",
    "ytx-mcp", "ytx-mcp.exe",
}


def default_claude_config() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


def default_bundle_root(home: Path | None = None) -> Path:
    """번들을 쌓을 기본 디렉터리.

    이름이 바뀌었다고 사용자의 분석 자료를 옮기지 않는다. 옮기는 순간
    중간에 실패하면 되돌릴 수 없고, 얻는 것은 폴더 이름뿐이다. 그래서
    `~/.ytx/data` 가 이미 있으면 그것을 계속 쓰고, 없을 때만 새 이름으로
    만든다. 사용자는 `--bundle-root` 로 언제든 직접 지정할 수 있다.
    """
    base = home or Path.home()
    current = base / ".cueprecise" / "data"
    if current.is_dir():
        return current
    for legacy in LEGACY_BUNDLE_DIRS:
        candidate = base / legacy / "data"
        if candidate.is_dir():
            return candidate
    return current


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"설정 파일을 읽을 수 없다: {path}\n{error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"설정 파일 최상위 값은 JSON 객체여야 한다: {path}")
    return value


def write_config(path: Path, value: dict[str, Any]) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        shutil.copy2(path, backup)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return backup


def is_managed_server(entry: Any) -> bool:
    """이 프로그램이 등록한 MCP 항목인지 판정한다.

    같은 이름을 쓰는 남의 항목을 지우거나 덮어쓰지 않기 위한 안전장치다.
    번들 실행 파일 이름이거나, 이 저장소의 `mcp_server` 모듈을 부르는
    항목만 우리 것으로 본다.
    """
    if not isinstance(entry, dict):
        return False
    command = str(entry.get("command", ""))
    if Path(command).name.lower() in MANAGED_COMMANDS:
        return True
    arguments = entry.get("args")
    if not isinstance(arguments, list):
        return False
    joined = " ".join(str(item) for item in arguments).replace("\\", "/").lower()
    return "mcp_server" in joined


def find_managed_entry(config: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """설정에서 이 프로그램이 만든 MCP 항목을 찾는다. 새 이름을 먼저 본다."""
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    for key in (SERVER_KEY, *LEGACY_SERVER_KEYS):
        entry = servers.get(key)
        if is_managed_server(entry):
            return key, entry
    return None


def bundle_root_of(entry: dict[str, Any]) -> Path | None:
    """등록된 항목의 `--bundle-root` 값을 읽는다. 데이터 위치를 유지하기 위해서다."""
    arguments = entry.get("args")
    if not isinstance(arguments, list):
        return None
    for index, item in enumerate(arguments):
        if item == "--bundle-root" and index + 1 < len(arguments):
            return Path(str(arguments[index + 1]))
    return None


def _take_legacy_entry(servers: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """레거시 항목이 우리 것이면 떼어내고 그 설정을 돌려준다."""
    for key in LEGACY_SERVER_KEYS:
        entry = servers.get(key)
        if is_managed_server(entry):
            del servers[key]
            return entry, key
    return None, None


def _inherited_environment(*sources: Any) -> dict[str, str]:
    environment: dict[str, str] = {}
    for source in sources:
        if isinstance(source, dict) and isinstance(source.get("env"), dict):
            for name, value in source["env"].items():
                if isinstance(value, str):
                    environment[name] = value
    return environment


def setup_claude(config_path: Path, bundle_root: Path, *, api_key: str | None,
                 server_command: str, server_args: list[str],
                 extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    config = read_config(config_path)
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SystemExit(f"mcpServers는 JSON 객체여야 한다: {config_path}")

    existing = servers.get(SERVER_KEY)
    legacy, legacy_key = _take_legacy_entry(servers)

    server: dict[str, Any] = {
        "command": server_command,
        "args": [*server_args, "--bundle-root", str(bundle_root.resolve())],
    }
    # 이전 항목의 환경변수를 물려받는다. 새 키를 주지 않아도 이미 저장된
    # GEMINI_API_KEY 가 사라지면 안 된다.
    environment = _inherited_environment(legacy, existing)
    environment.update(extra_env or {})
    if api_key:
        environment["GEMINI_API_KEY"] = api_key
    if environment:
        server["env"] = environment

    servers[SERVER_KEY] = server
    bundle_root.mkdir(parents=True, exist_ok=True)
    backup = write_config(config_path, config)
    return {"config": str(config_path), "bundle_root": str(bundle_root.resolve()),
            "backup": str(backup) if backup else None,
            "api_key_configured": bool(environment.get("GEMINI_API_KEY")),
            "server_key": SERVER_KEY,
            "migrated_from": legacy_key}


def remove_claude(config_path: Path) -> dict[str, Any]:
    """이 프로그램이 만든 항목만 제거한다. 다른 MCP 설정은 건드리지 않는다."""
    value = read_config(config_path)
    servers = value.get("mcpServers")
    if not isinstance(servers, dict):
        return {"changed": False, "config": str(config_path), "backup": None, "removed": []}
    removed = [key for key in (SERVER_KEY, *LEGACY_SERVER_KEYS)
               if is_managed_server(servers.get(key))]
    if not removed:
        return {"changed": False, "config": str(config_path), "backup": None, "removed": []}
    for key in removed:
        del servers[key]
    backup = write_config(config_path, value)
    return {"changed": True, "config": str(config_path),
            "backup": str(backup) if backup else None, "removed": removed}
