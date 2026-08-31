"""Claude Desktop 설정을 보존하며 ytx 항목만 관리한다."""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def default_claude_config() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"


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


def setup_claude(config_path: Path, bundle_root: Path, *, api_key: str | None,
                 server_command: str, server_args: list[str],
                 extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    config = read_config(config_path)
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SystemExit(f"mcpServers는 JSON 객체여야 한다: {config_path}")
    server: dict[str, Any] = {
        "command": server_command,
        "args": [*server_args, "--bundle-root", str(bundle_root.resolve())],
    }
    environment = dict(extra_env or {})
    if api_key:
        environment["GEMINI_API_KEY"] = api_key
    if environment:
        server["env"] = environment
    servers["ytx"] = server
    bundle_root.mkdir(parents=True, exist_ok=True)
    backup = write_config(config_path, config)
    return {"config": str(config_path), "bundle_root": str(bundle_root.resolve()),
            "backup": str(backup) if backup else None, "api_key_configured": bool(api_key)}

