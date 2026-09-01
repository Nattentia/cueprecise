"""MCP 클라이언트 설정을 보존하며 CuePrecise 항목만 관리한다.

0.1.1 까지는 Claude Desktop 하나만 상대했다. 붙일 앱이 늘어나도 "남의 설정을
건드리지 않는다"는 규칙은 같으므로, 그 규칙을 담은 함수는 하나로 두고 앱마다
다른 것(설정 파일 위치, 서버 목록을 담는 키 이름)만 `ClientTarget` 으로 뽑았다.
`setup_claude` 와 `remove_claude` 는 Claude Desktop 을 가리키는 얇은 이름으로
남는다. 부르는 쪽을 한꺼번에 고치면 무엇이 깨졌는지 알 수 없기 때문이다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable


class ConfigurationError(SystemExit, Exception):
    """설정을 고치지 못했다.

    `SystemExit` 를 이어받는다. CLI 는 전처럼 메시지를 내고 끝난다.
    `Exception` 도 함께 이어받는다. 설치 화면은 `except Exception` 으로 실패를
    잡아 사용자에게 알리는데, 순수한 `SystemExit` 은 `BaseException` 이라
    그 그물을 빠져나간다. 그러면 진행 막대가 도는 채로 아무 말도 없이 멈추고,
    조용히 도는 이관(`--migrate`)은 "실패해도 설치를 막지 않는다"는 약속을
    깬다. 둘 다 이어받으면 부르는 쪽을 고치지 않고 양쪽을 만족시킨다.
    """


SERVER_KEY = "cueprecise"
# 서버 목록을 담는 최상위 키. 대부분의 앱이 이 이름을 쓰지만 VS Code 는
# `servers` 를 쓴다. 그래서 상수로 두고 타깃마다 덮어쓸 수 있게 한다.
DEFAULT_SERVERS_KEY = "mcpServers"
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
        raise ConfigurationError(f"설정 파일을 읽을 수 없다: {path}\n{error}") from error
    if not isinstance(value, dict):
        raise ConfigurationError(f"설정 파일 최상위 값은 JSON 객체여야 한다: {path}")
    return value


def backup_file(path: Path) -> Path | None:
    """고치기 전 사본을 남긴다. 없는 파일은 남길 것도 없다."""
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def read_toml_config(path: Path) -> dict[str, Any]:
    """Codex 의 `config.toml` 을 읽기만 한다.

    쓰기는 `codex mcp add` 가 한다. 우리가 TOML 을 쓰면 사용자의 주석과
    서식이 날아가고, 외부 패키지도 들여야 한다.
    """
    if not path.exists():
        return {}
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"설정 파일을 읽을 수 없다: {path}\n{error}") from error


def write_config(path: Path, value: dict[str, Any]) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_file(path)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return backup


def is_managed_server(entry: Any) -> bool:
    """이 프로그램이 등록한 MCP 항목인지 판정한다.

    같은 이름을 쓰는 남의 항목을 지우거나 덮어쓰지 않기 위한 안전장치다.
    번들 실행 파일 이름이거나, 이 저장소의 `mcp_server` 를 **우리 번들을
    가리키며** 부르는 항목만 우리 것으로 본다.

    `mcp_server` 라는 글자만 보면 남이 자기 `mcp_server.py` 를 등록해 두고
    이름까지 `cueprecise` 로 지었을 때 오판한다. 우리 항목은 0.1.0 부터
    예외 없이 `--bundle-root` 를 달고 있으므로 그것을 함께 요구한다.
    상대하는 설정 파일이 하나에서 여럿으로 늘어 오판이 닿는 범위도
    넓어졌다.
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
    return "mcp_server" in joined and "--bundle-root" in joined


def find_managed_entry(config: dict[str, Any],
                       servers_key: str = DEFAULT_SERVERS_KEY) -> tuple[str, dict[str, Any]] | None:
    """설정에서 이 프로그램이 만든 MCP 항목을 찾는다. 새 이름을 먼저 본다."""
    servers = config.get(servers_key)
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


def setup_file_client(config_path: Path, bundle_root: Path, *, api_key: str | None,
                      server_command: str, server_args: list[str],
                      extra_env: dict[str, str] | None = None,
                      servers_key: str = DEFAULT_SERVERS_KEY) -> dict[str, Any]:
    """JSON 설정 파일에 CuePrecise 항목만 idempotent 하게 쓴다.

    앱이 달라도 지켜야 하는 것은 같다. 남의 항목과 다른 설정을 보존하고,
    쓰기 전에 백업하고, 이미 저장된 환경변수를 물려받는다.
    """
    config = read_config(config_path)
    servers = config.setdefault(servers_key, {})
    if not isinstance(servers, dict):
        raise ConfigurationError(f"{servers_key}는 JSON 객체여야 한다: {config_path}")

    existing = servers.get(SERVER_KEY)
    # 이름은 같은데 우리 것이 아니면 멈춘다. 덮어쓰면 남의 서버 설정이 사라지고,
    # 그 항목의 환경변수까지 우리 항목으로 옮겨 붙는다. 붙지 않는 편이 낫다.
    if existing is not None and not is_managed_server(existing):
        raise ConfigurationError(
            f"이미 `{SERVER_KEY}` 라는 남의 항목이 있다. 건드리지 않는다: {config_path}")
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


def remove_file_client(config_path: Path,
                       servers_key: str = DEFAULT_SERVERS_KEY) -> dict[str, Any]:
    """이 프로그램이 만든 항목만 제거한다. 다른 MCP 설정은 건드리지 않는다."""
    value = read_config(config_path)
    servers = value.get(servers_key)
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


def setup_claude(config_path: Path, bundle_root: Path, *, api_key: str | None,
                 server_command: str, server_args: list[str],
                 extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    """Claude Desktop 설정에 등록한다. `setup_file_client` 의 얇은 이름이다."""
    return setup_file_client(
        config_path, bundle_root, api_key=api_key, server_command=server_command,
        server_args=server_args, extra_env=extra_env)


def remove_claude(config_path: Path) -> dict[str, Any]:
    """Claude Desktop 설정에서 제거한다. `remove_file_client` 의 얇은 이름이다."""
    return remove_file_client(config_path)


@dataclass(frozen=True)
class ClientTarget:
    """CuePrecise 를 붙일 수 있는 MCP 클라이언트 하나.

    앱마다 다른 것만 담는다. "우리 항목만 건드린다"는 규칙은 담지 않는다.
    그 규칙은 `setup_file_client` / `remove_file_client` 안에 하나로 있고,
    타깃은 그 함수에 넘길 값만 안다.
    """

    key: str
    label: str
    locate_config: Callable[[], Path]
    servers_key: str = DEFAULT_SERVERS_KEY
    # 설치 판정에 쓸 실행 파일. 설정 폴더의 존재보다 이것이 낫다. 남의 도구가
    # 만들어 둔 설정 폴더를 앱이 있다는 증거로 삼으면 안 쓰는 앱에 항목을
    # 써 넣게 된다(이 PC 의 `~/.cursor` 와 `~/.gemini` 가 그런 경우다).
    executable: str = ""
    detector: Callable[[], bool] | None = None

    def is_installed(self) -> bool:
        if self.detector is not None:
            return self.detector()
        if self.executable:
            return shutil.which(self.executable) is not None
        # CLI 가 아예 없는 앱(Claude Desktop)만 여기로 온다. 그 설정 폴더는
        # 그 앱만 만든다.
        path = self.locate_config()
        return path.exists() or path.parent.is_dir()

    def install(self, bundle_root: Path, *, api_key: str | None,
                server_command: str, server_args: list[str],
                extra_env: dict[str, str] | None = None,
                config_path: Path | None = None) -> dict[str, Any]:
        return setup_file_client(
            config_path or self.locate_config(), bundle_root, api_key=api_key,
            server_command=server_command, server_args=server_args,
            extra_env=extra_env, servers_key=self.servers_key)

    def remove(self, config_path: Path | None = None) -> dict[str, Any]:
        return remove_file_client(config_path or self.locate_config(), self.servers_key)


@dataclass(frozen=True)
class CliClientTarget(ClientTarget):
    """설정 파일을 우리가 쓰지 않고 앱이 주는 명령으로 붙이는 클라이언트.

    Codex 는 `config.toml` 을, Claude Code 는 `~/.claude.json` 을 쓴다. 우리가
    직접 쓰면 앱마다 다른 서식과 주석을 지키느라 코드가 늘고, Codex 는 TOML
    쓰기 패키지까지 들여야 한다. 앱이 자기 파일을 쓰게 두고, 우리는 **읽어서
    우리 항목인지 판정**하는 일만 맡는다.
    """

    # `claude` 는 기본이 프로젝트 스코프다. 전역으로 붙이려면 `-s user` 가 필요하다.
    scope_args: tuple[str, ...] = ()
    env_flag: str = "--env"
    reads_toml: bool = False
    # 설정 위치를 환경변수로 정하는 앱이 있다. 그 값을 비워 두면 이 프로세스가
    # 물려받은 값에 따라 엉뚱한 홈에 쓰게 된다. 읽는 파일과 쓰는 곳을 맞춘다.
    home_var: str = ""

    def load_config(self, path: Path) -> dict[str, Any]:
        return read_toml_config(path) if self.reads_toml else read_config(path)

    def _current_entry(self, path: Path) -> Any:
        servers = self.load_config(path).get(self.servers_key)
        return servers.get(SERVER_KEY) if isinstance(servers, dict) else None

    def _run(self, arguments: list[str], path: Path) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        if self.home_var:
            environment[self.home_var] = str(path.parent)
        try:
            # 인자는 반드시 리스트로 넘긴다. 쉘을 거치면 `/c` 같은 인자가
            # 경로로 오인돼 조용히 바뀐다.
            return subprocess.run([self.executable, *arguments], capture_output=True,
                                  text=True, timeout=60, env=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ConfigurationError(f"{self.label} 명령을 실행하지 못했다: {error}") from error

    def install(self, bundle_root: Path, *, api_key: str | None,
                server_command: str, server_args: list[str],
                extra_env: dict[str, str] | None = None,
                config_path: Path | None = None) -> dict[str, Any]:
        path = config_path or self.locate_config()
        existing = self._guard(path)
        environment = self._environment(existing, extra_env, api_key)

        backup = backup_file(path)
        bundle_root.mkdir(parents=True, exist_ok=True)
        # `codex mcp add` 는 같은 이름을 덮어쓰지만 `claude mcp add` 는 거절한다.
        # 게다가 거절하면서도 종료 코드는 0 이다. 먼저 지우고 다시 넣는다.
        if existing is not None:
            self._run(["mcp", "remove", SERVER_KEY, *self.scope_args], path)

        arguments = ["mcp", "add", SERVER_KEY, *self.scope_args]
        for name, value in environment.items():
            arguments += [self.env_flag, f"{name}={value}"]
        arguments += ["--", server_command, *server_args,
                      "--bundle-root", str(bundle_root.resolve())]
        result = self._run(arguments, path)

        self._confirm(path, server_command, bundle_root, result)
        return {"config": str(path), "bundle_root": str(bundle_root.resolve()),
                "backup": str(backup) if backup else None,
                "api_key_configured": bool(environment.get("GEMINI_API_KEY")),
                "server_key": SERVER_KEY,
                # 0.1.0 은 Claude Desktop 에만 썼다. 여기에 옛 항목은 있을 수 없다.
                "migrated_from": None}

    def remove(self, config_path: Path | None = None) -> dict[str, Any]:
        path = config_path or self.locate_config()
        if not is_managed_server(self._current_entry(path)):
            return {"changed": False, "config": str(path), "backup": None, "removed": []}
        backup = backup_file(path)
        result = self._run(["mcp", "remove", SERVER_KEY, *self.scope_args], path)
        if self._current_entry(path) is not None:
            detail = (result.stderr or result.stdout or "").strip()[-300:]
            raise ConfigurationError(f"{self.label} 에서 제거하지 못했다.\n{detail}")
        return {"changed": True, "config": str(path),
                "backup": str(backup) if backup else None, "removed": [SERVER_KEY]}

    def _guard(self, path: Path) -> Any:
        """이미 있는 항목이 우리 것인지 본다. 남의 것이면 아무것도 하지 않는다."""
        existing = self._current_entry(path)
        if existing is not None and not is_managed_server(existing):
            raise ConfigurationError(
                f"{self.label} 에 이미 `{SERVER_KEY}` 라는 남의 항목이 있다. 건드리지 않는다.")
        return existing

    @staticmethod
    def _environment(existing: Any, extra_env: dict[str, str] | None,
                     api_key: str | None) -> dict[str, str]:
        environment = _inherited_environment(existing)
        environment.update(extra_env or {})
        if api_key:
            environment["GEMINI_API_KEY"] = api_key
        return environment

    def _confirm(self, path: Path, server_command: str, bundle_root: Path,
                 result: subprocess.CompletedProcess[str]) -> None:
        """정말 우리가 넣으려던 항목이 들어갔는지 설정을 다시 읽어 본다.

        항목이 있는지만 보면 모자란다. 지우기가 실패하고 넣기가 거절당하면
        **옛 항목이 그대로 남아 있는데** 있다는 이유로 성공이라고 보고하게
        된다. `claude` 는 거절하면서도 종료 코드 0 을 주므로 실제로 일어난다.
        그래서 실행 파일과 번들 경로가 우리가 넘긴 것과 같은지까지 본다.
        """
        entry = self._current_entry(path)
        entry = entry if isinstance(entry, dict) else {}
        expected = _forward_slashes(server_command).lower()
        actual = _forward_slashes(str(entry.get("command", ""))).lower()
        if actual == expected and bundle_root_of(entry) == bundle_root.resolve():
            return
        detail = (result.stderr or result.stdout or "").strip()[-300:]
        raise ConfigurationError(f"{self.label} 에 등록하지 못했다.\n{detail}")


def _forward_slashes(value: str) -> str:
    """경로의 역슬래시를 슬래시로 바꾼다.

    VS Code 는 JSON 한 덩어리를 인자로 받는데, `code.cmd` 래퍼를 지나며
    역슬래시가 죽어 `Bad escaped character in JSON` 이 난다. Windows 는
    슬래시 경로를 그대로 받아 준다.
    """
    return value.replace("\\", "/")


@dataclass(frozen=True)
class VsCodeTarget(CliClientTarget):
    """붙이는 것은 VS Code 명령이, 떼는 것은 우리가 한다.

    VS Code 는 `--add-mcp` 만 주고 떼는 명령을 주지 않는다. 그래서 제거는
    설정 파일에서 직접 하되, 파일 방식과 같은 규칙(우리 항목인지 판정하고,
    고치기 전에 백업하고, 남의 것은 건드리지 않는다)을 그대로 쓴다.
    """

    def install(self, bundle_root: Path, *, api_key: str | None,
                server_command: str, server_args: list[str],
                extra_env: dict[str, str] | None = None,
                config_path: Path | None = None) -> dict[str, Any]:
        path = config_path or self.locate_config()
        existing = self._guard(path)
        environment = self._environment(existing, extra_env, api_key)

        backup = backup_file(path)
        bundle_root.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "name": SERVER_KEY,
            "command": _forward_slashes(server_command),
            "args": [_forward_slashes(str(item)) for item in
                     [*server_args, "--bundle-root", str(bundle_root.resolve())]],
        }
        if environment:
            payload["env"] = environment
        # 같은 이름이면 덮어쓰고 다른 항목은 그대로 둔다(실측 확인).
        result = self._run(["--add-mcp", json.dumps(payload)], path)

        self._confirm(path, server_command, bundle_root, result)
        return {"config": str(path), "bundle_root": str(bundle_root.resolve()),
                "backup": str(backup) if backup else None,
                "api_key_configured": bool(environment.get("GEMINI_API_KEY")),
                "server_key": SERVER_KEY, "migrated_from": None}

    def remove(self, config_path: Path | None = None) -> dict[str, Any]:
        return remove_file_client(config_path or self.locate_config(), self.servers_key)


def default_vscode_config() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Code" / "User" / "mcp.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "mcp.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Code" / "User" / "mcp.json"


def default_codex_config() -> Path:
    """Codex 가 실제로 읽는 설정 파일.

    `CODEX_HOME` 이 있으면 그것을 따른다. 이 변수를 무시하고 `~/.codex` 로
    단정하면, 그 변수를 바꿔 쓰는 환경에서 등록이 엉뚱한 홈으로 들어가
    사용자 눈에는 아무 일도 일어나지 않은 것처럼 보인다.
    """
    home = os.environ.get("CODEX_HOME")
    return (Path(home) if home else Path.home() / ".codex") / "config.toml"


def default_claude_code_config() -> Path:
    return Path.home() / ".claude.json"


CLAUDE_DESKTOP = ClientTarget(
    key="claude-desktop", label="Claude Desktop", locate_config=default_claude_config)

CODEX = CliClientTarget(
    key="codex", label="Codex", locate_config=default_codex_config,
    servers_key="mcp_servers", executable="codex", env_flag="--env",
    reads_toml=True, home_var="CODEX_HOME")

CLAUDE_CODE = CliClientTarget(
    key="claude-code", label="Claude Code", locate_config=default_claude_code_config,
    executable="claude", scope_args=("-s", "user"), env_flag="-e")

VS_CODE = VsCodeTarget(
    key="vscode", label="VS Code", locate_config=default_vscode_config,
    # VS Code 만 최상위 키가 `servers` 다. `mcpServers` 로 쓰면 조용히 무시된다.
    servers_key="servers", executable="code")

# 아래 셋은 이 PC 에 없어 실제로 붙여 보지 못했다. 경로와 키 이름은 각 앱의
# 문서를 따랐다. 붙이는 방식은 Claude Desktop 과 같은 파일 쓰기이므로 규칙은
# 이미 검증돼 있고, 확인되지 않은 것은 "그 앱이 이 파일을 읽는가" 하나다.
# 그래서 감지는 실행 파일로만 한다. 설정 폴더가 있다고 앱이 있다고 보면
# 남의 도구가 만들어 둔 폴더에 항목을 써 넣게 된다.
CURSOR = ClientTarget(
    key="cursor", label="Cursor", executable="cursor",
    locate_config=lambda: Path.home() / ".cursor" / "mcp.json")

WINDSURF = ClientTarget(
    key="windsurf", label="Windsurf", executable="windsurf",
    locate_config=lambda: Path.home() / ".codeium" / "windsurf" / "mcp_config.json")

GEMINI_CLI = ClientTarget(
    key="gemini-cli", label="Gemini CLI", executable="gemini",
    locate_config=lambda: Path.home() / ".gemini" / "settings.json")

CLIENTS: tuple[ClientTarget, ...] = (
    CLAUDE_DESKTOP, CODEX, CLAUDE_CODE, VS_CODE, CURSOR, WINDSURF, GEMINI_CLI)


def client_by_key(key: str) -> ClientTarget:
    for target in CLIENTS:
        if target.key == key:
            return target
    known = ", ".join(target.key for target in CLIENTS)
    raise ConfigurationError(f"알 수 없는 클라이언트: {key} (가능한 값: {known})")


def detected_clients() -> list[ClientTarget]:
    """이 PC 에 설치돼 있다고 판정된 앱만 돌려준다.

    설치되지 않은 앱에 설정 파일을 새로 만들지 않기 위한 것이다.
    """
    return [target for target in CLIENTS if target.is_installed()]


def resolve_clients(names: Iterable[str] | None) -> list[ClientTarget]:
    """`--client` 값을 타깃 목록으로 바꾼다. 생략과 `all` 은 감지된 앱 전부다."""
    requested = [name.strip() for name in (names or []) if name.strip()]
    if not requested or "all" in requested:
        return detected_clients()
    seen: dict[str, ClientTarget] = {}
    for name in requested:
        target = client_by_key(name)
        seen.setdefault(target.key, target)
    return list(seen.values())
