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
import credential_store


# 두 가지 Google API 키 모양을 받는다. `AIza` 로 시작하는 옛 키와, 2026 년부터
# 발급되는 `AQ.` 로 시작하는 새 Google Auth 키(예: `AQ.Ab8...`, 뒤에 긴 토큰이
# 붙는다). 어느 쪽도 아니면 붙여넣기 실수로 본다.
API_KEY_PATTERN = re.compile(
    r"^(?:AIza[0-9A-Za-z_-]{30,}|AQ\.[A-Za-z0-9_.-]{20,})$")

# 번들 MCP 서버 실행 파일(옛 setup.exe 설치본).
SERVER_EXECUTABLES = ("cueprecise-mcp.exe",)
# 임베더블 파이썬 zip 설치본의 진입점. `python313._pth` 가 `..\app` 를
# sys.path 에 이미 넣어 두므로 `-m mcp_server` 로 부를 수 있다(installer/mcpb/
# manifest.json 이 Claude Desktop 용으로 쓰는 것과 같은 꼴).
PYTHON_SUBDIR = "py"
PYTHON_EXECUTABLE = "python.exe"
PYTHON_SERVER_ARGS = ("-m", "mcp_server")
# initialize 응답의 serverInfo.name 허용값.
SERVER_NAMES = {"cueprecise"}


def _bundled_server(install_dir: Path) -> Path | None:
    """설치 폴더에서 부를 서버 실행 파일을 찾는다.

    옛 setup.exe 설치본은 `cueprecise-mcp.exe` 를 바로 두고, zip 설치본은
    `py\\python.exe` 를 둔다. 후자는 실행 파일 이름만으로는 구분되지 않으므로
    `_server_launch_args` 가 함께 인자를 결정한다.
    """
    for filename in SERVER_EXECUTABLES:
        candidate = (install_dir / filename).resolve()
        if candidate.is_file():
            return candidate
    candidate = (install_dir / PYTHON_SUBDIR / PYTHON_EXECUTABLE).resolve()
    if candidate.is_file():
        return candidate
    return None


def _server_launch_args(server: Path) -> list[str]:
    """`python.exe` 형태는 자신을 그냥 부르는 게 아니라 `-m mcp_server` 로 불러야 한다."""
    if server.name.lower() == PYTHON_EXECUTABLE:
        return list(PYTHON_SERVER_ARGS)
    return []


def _bundled_ffmpeg(server: Path) -> Path | None:
    """`py\\ffmpeg.exe` 가 있으면 그것을 돌려준다.

    zip 설치본은 FFmpeg 를 `python.exe` 옆에 두므로 `runtime.tool()` 이 PATH
    없이도 그 자리를 바로 찾는다(옛 setup.exe 설치본은 winget 이 깐 FFmpeg 를
    PATH 로 물려줘야 하므로 이 자리에 없다).
    """
    candidate = server.parent / "ffmpeg.exe"
    return candidate if candidate.is_file() else None


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
        return key, ("Google AI Studio에서 복사한 API 키인지 확인해 주세요"
                     "(AIza로 시작하거나 AQ.으로 시작하는 키).")
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
    detail = configuration.mask_secrets((result.stderr or result.stdout or "").strip())[-500:]
    return None, "FFmpeg 자동 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요." + (f"\n\n{detail}" if detail else "")


def probe_mcp(server: Path, bundle_root: Path, environment: dict[str, str]) -> tuple[bool, str | None]:
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
    command = [str(server), *_server_launch_args(server), "--bundle-root", str(bundle_root)]
    try:
        result = subprocess.run(
            command, input=request,
            capture_output=True, text=True, timeout=20,
            env={**os.environ, **environment},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"CuePrecise 연결 시험을 시작하지 못했습니다: {error}"
    try:
        response: dict[str, Any] = json.loads((result.stdout or "").splitlines()[0])
    except (IndexError, json.JSONDecodeError):
        # 서버는 키를 환경변수로 받았다. 우리가 만들지 않은 라이브러리가 그 값을
        # 예외에 실어 stderr 로 낼 수 있으므로 값 자체를 지운다.
        detail = configuration.mask_secrets(
            (result.stderr or result.stdout or "응답 없음").strip(),
            *configuration.secrets_of(environment))[-500:]
        return False, f"CuePrecise가 올바른 응답을 보내지 않았습니다.\n\n{detail}"
    name = response.get("result", {}).get("serverInfo", {}).get("name")
    if name not in SERVER_NAMES:
        return False, "CuePrecise 연결 시험의 응답을 확인하지 못했습니다."
    return True, None


def connect_clients(api_key: str, install_dir: Path, *,
                    targets: list[configuration.ClientTarget] | None = None,
                    config_path: Path | None = None,
                    bundle_root: Path | None = None,
                    credential_path: Path | None = None) -> dict[str, Any]:
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
            "설치된 CuePrecise 서버(cueprecise-mcp.exe 또는 py\\python.exe)를 찾지 못했습니다. "
            "CuePrecise를 다시 설치해 주세요.")
    chosen = configuration.detected_clients() if targets is None else list(targets)
    if not chosen:
        # 붙인 앱이 하나도 없는 것을 성공이라고 말하면 안 된다.
        raise RuntimeError(
            "연결할 AI 앱을 찾지 못했습니다. Claude Desktop, Codex, Claude Code, "
            "VS Code 중 하나를 설치한 뒤 다시 시도해 주세요.")
    bundled_ffmpeg = _bundled_ffmpeg(server)
    server_environment: dict[str, str] = {}
    if bundled_ffmpeg is not None:
        # zip 설치본은 `python.exe` 옆에 FFmpeg 를 두므로 `runtime.tool()` 이
        # PATH 없이도 그 자리를 찾는다. winget 설치·PATH 주입을 건너뛴다.
        ffmpeg_bin = bundled_ffmpeg.parent
    else:
        ffmpeg_bin, ffmpeg_error = ensure_ffmpeg()
        if ffmpeg_error or ffmpeg_bin is None:
            raise RuntimeError(ffmpeg_error)
        server_environment["PATH"] = str(ffmpeg_bin) + os.pathsep + os.environ.get("PATH", "")

    destination = bundle_root or configuration.default_bundle_root()
    destination.mkdir(parents=True, exist_ok=True)
    ok, probe_error = probe_mcp(server, destination, {**server_environment, "GEMINI_API_KEY": key})
    if not ok:
        raise RuntimeError(probe_error)

    config_key: str | None = key
    credential_state = None
    protected_path = None
    if credential_store.supported():
        protected_path = credential_path or credential_store.default_path()
        credential_state = credential_store.snapshot(protected_path)
        protected_path = credential_store.store(key, protected_path)
        server_environment[credential_store.CREDENTIAL_ENV] = str(protected_path)
        config_key = None

    server_args = _server_launch_args(server)
    connected: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for target in chosen:
        try:
            result = target.install(
                destination, api_key=config_key, server_command=str(server),
                server_args=server_args, extra_env=server_environment,
                config_path=config_path if len(chosen) == 1 else None)
        except Exception as error:  # 한 앱의 실패가 나머지를 막지 않는다.
            failed.append({"key": target.key, "label": target.label, "reason": str(error)})
        else:
            connected.append({"key": target.key, "label": target.label, **result})
    if not connected:
        if credential_state is not None:
            credential_store.restore(credential_state, protected_path)
        raise RuntimeError("\n".join(
            f"{item['label']}: {item['reason']}" for item in failed))
    for item in connected:
        item["api_key_configured"] = True
        item["credential_protected"] = protected_path is not None
    return {"connected": connected, "failed": failed,
            "ffmpeg_bin": str(ffmpeg_bin), "connection_tested": True,
            "bundle_root": str(destination.resolve()),
            "credential_protected": protected_path is not None}


def connect(api_key: str, install_dir: Path, *,
            config_path: Path | None = None,
            bundle_root: Path | None = None,
            credential_path: Path | None = None) -> dict[str, Any]:
    """Claude Desktop 하나에만 붙인다. `connect_clients` 의 얇은 이름이다."""
    result = connect_clients(api_key, install_dir, targets=[configuration.CLAUDE_DESKTOP],
                             config_path=config_path, bundle_root=bundle_root,
                             credential_path=credential_path)
    entry = result["connected"][0]
    return {**entry, "ffmpeg_bin": result["ffmpeg_bin"], "connection_tested": True}


def migrate(install_dir: Path, config_path: Path | None = None,
            credential_path: Path | None = None) -> dict[str, Any]:
    """이미 연결돼 있던 설정을 이번에 설치한 실행 파일로 다시 가리킨다.

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
    environment: dict[str, str] = {}
    credential_state = None
    protected_path = None
    saved_secrets = configuration.secrets_of(entry.get("env"))
    old_key = saved_secrets[0] if saved_secrets else None
    candidate = credential_path or credential_store.default_path()
    if credential_store.supported() and (old_key or candidate.exists()):
        protected_path = candidate
        credential_state = credential_store.snapshot(protected_path)
        if old_key:
            protected_path = credential_store.store(old_key, protected_path)
        environment[credential_store.CREDENTIAL_ENV] = str(protected_path.resolve())
    try:
        result = configuration.setup_claude(
            config, destination, api_key=None, server_command=str(server),
            server_args=_server_launch_args(server), extra_env=environment)
    except Exception:
        if credential_state is not None:
            credential_store.restore(credential_state, protected_path)
        raise
    return {**result, "changed": True, "previous_key": key,
            "api_key_configured": bool(old_key or protected_path),
            "credential_protected": protected_path is not None}


def migrate_clients(install_dir: Path, *,
                    targets: list[configuration.ClientTarget] | None = None,
                    credential_path: Path | None = None) -> dict[str, Any]:
    """연결된 모든 Windows 클라이언트의 평문 키를 DPAPI 저장소로 옮긴다.

    앱 설치 여부가 아니라 실제 설정 항목을 기준으로 찾는다. 업그레이드 사이에
    앱을 지웠더라도 남은 설정을 새 실행 파일로 고쳐야 하기 때문이다. 서로 다른
    키가 발견되면 임의로 하나를 고르지 않고 아무 설정도 바꾸지 않는다.
    """
    server = _bundled_server(install_dir)
    if server is None:
        return {"changed": False, "reason": "번들 서버 실행 파일을 찾지 못했다.",
                "migrated": [], "failed": []}

    found: list[tuple[configuration.ClientTarget, Path, dict[str, Any]]] = []
    failed: list[dict[str, str]] = []
    for target in (targets if targets is not None else list(configuration.CLIENTS)):
        path = target.locate_config()
        try:
            loader = target.load_config if isinstance(target, configuration.CliClientTarget) \
                else configuration.read_config
            value = loader(path)
            managed = configuration.find_managed_entry(value, target.servers_key)
        except Exception as error:
            failed.append({"key": target.key, "label": target.label,
                           "reason": configuration.mask_secrets(str(error))})
            continue
        if managed is not None:
            found.append((target, path, managed[1]))

    if not found:
        return {"changed": False, "reason": "연결된 항목이 없다.",
                "migrated": [], "failed": failed}

    plaintext_keys = {
        entry.get("env", {}).get("GEMINI_API_KEY")
        for _target, _path, entry in found
        if isinstance(entry.get("env"), dict)
        and isinstance(entry["env"].get("GEMINI_API_KEY"), str)
        and entry["env"]["GEMINI_API_KEY"]
    }
    if len(plaintext_keys) > 1:
        return {"changed": False,
                "reason": "클라이언트마다 서로 다른 Gemini API 키가 있어 자동 이관하지 않았다.",
                "migrated": [], "failed": failed}

    state = None
    protected_path: Path | None = None
    if credential_store.supported():
        try:
            candidate = credential_path or credential_store.default_path()
            if plaintext_keys or candidate.exists():
                state = credential_store.snapshot(candidate)
                protected_path = (credential_store.store(next(iter(plaintext_keys)), candidate)
                                  if plaintext_keys else candidate.resolve())
        except credential_store.CredentialError as error:
            return {"changed": False, "reason": str(error), "migrated": [],
                    "failed": failed}

    migrated: list[dict[str, Any]] = []
    for target, path, entry in found:
        destination = (configuration.bundle_root_of(entry)
                       or configuration.default_bundle_root())
        environment: dict[str, str] = {}
        if protected_path is not None:
            environment[credential_store.CREDENTIAL_ENV] = str(protected_path)
        existed = path.exists()
        try:
            original = path.read_bytes() if existed else b""
        except OSError as error:
            failed.append({"key": target.key, "label": target.label,
                           "reason": configuration.mask_secrets(
                               f"설정 복구용 사본을 메모리에 읽지 못했다: {error}",
                               *plaintext_keys)})
            continue
        try:
            result = target.install(
                destination, api_key=None, server_command=str(server),
                server_args=_server_launch_args(server),
                extra_env=environment, config_path=path)
        except Exception as error:
            # CLI 방식은 기존 항목을 지운 뒤 다시 추가한다. 추가가 실패하면
            # 디스크 백업에 평문 키를 남기지 않고 메모리에 잡아 둔 원본으로
            # 되돌린다.
            try:
                if existed:
                    path.write_bytes(original)
                else:
                    path.unlink(missing_ok=True)
            except OSError as restore_error:
                error = RuntimeError(f"{error}; 원래 설정 복구도 실패했다: {restore_error}")
            failed.append({"key": target.key, "label": target.label,
                           "reason": configuration.mask_secrets(
                               str(error), *plaintext_keys)})
        else:
            migrated.append({"key": target.key, "label": target.label, **result,
                             "api_key_configured": bool(plaintext_keys or protected_path),
                             "credential_protected": protected_path is not None})

    if not migrated and state is not None:
        credential_store.restore(state, protected_path)
    return {"changed": bool(migrated), "migrated": migrated, "failed": failed,
            "credential_protected": bool(migrated and protected_path)}


def disconnect(config_path: Path | None = None) -> dict[str, Any]:
    """Claude 설정에서 이 프로그램이 만든 항목만 제거한다.

    CuePrecise 항목을 지우되, 같은 이름을 쓰는
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
    credential_removed = False
    if not failed and credential_store.supported():
        try:
            credential_removed = credential_store.delete()
        except OSError as error:
            failed.append({"key": "credential", "label": "Windows credential",
                           "reason": str(error)})
    return {"removed": removed, "failed": failed, "changed": bool(removed),
            "credential_removed": credential_removed}
