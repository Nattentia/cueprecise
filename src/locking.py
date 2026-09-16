"""번들 단위 단일 작업자 잠금.

owner: claude

두 겹으로 나눈다.

  `file_lock`   OS 파일 잠금. 짧은 임계 구역에만 쓴다. `usage.py` 가 할당량
                원장을 갱신할 때 쓰던 것을 그대로 옮겼다.
  `bundle_lock` 번들 하나를 한 프로세스만 다루게 한다. OS 잠금을 오래 쥐지
                않는다 — 소유자 기록 파일을 짧게 읽고 쓴 뒤 바로 놓는다.

오래 붙잡지 않는 이유: 전사는 몇 분씩 걸린다. 그동안 OS 잠금을 쥐고 있으면
다른 클라이언트는 이유도 모른 채 멈춰 선다. 소유자를 파일에 적어 두면 두 번째
프로세스가 "누가 언제부터 무엇을 하는 중"인지 즉시 알고 끝낼 수 있다.

비정상 종료로 남은 기록은 프로세스 생존 확인으로 회수한다. 프로세스가 이미
없으면 기록을 무시하고 가져온다. 살아 있으면 기다리지 않고 `BundleBusy` 를
올린다 — 사람이 판단할 일이다.

같은 프로세스 안에서는 다시 잡아도 된다. `run` 이 잡은 채로 `stage_visual` 이
또 잡는 경로가 있어서, 깊이를 세어 가장 바깥에서만 놓는다.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# 소유자 기록. 번들 루트에 둔다 (purge 대상 경로가 아니다).
OWNER_NAME = "lock.json"
# OS 잠금용 더미 파일. 내용은 쓰지 않는다.
GUARD_NAME = ".lock"

# 시간 상한. 이보다 오래된 기록은 프로세스가 살아 있어 보여도 버린다.
# 등록 한 번은 길어야 수십 분이다. 프로세스 번호 재사용과 다른 기기의 기록을
# 이것으로 회수한다.
DEFAULT_STALE_AFTER = 24 * 3600.0

# 같은 프로세스 안에서의 중첩 획득 깊이. 경로 문자열 -> 깊이.
_held: dict[str, int] = {}


class BundleBusy(RuntimeError):
    """다른 프로세스가 같은 번들을 다루는 중이다."""

    def __init__(self, bundle: Path, owner: dict[str, Any]) -> None:
        self.bundle = bundle
        self.owner = owner
        started = owner.get("started_at", "?")
        activity = owner.get("activity", "?")
        pid = owner.get("pid", "?")
        host = owner.get("host", "?")
        super().__init__(
            "이 번들은 다른 작업이 사용 중입니다: %s\n"
            "  작업: %s / 프로세스: %s@%s / 시작: %s\n"
            "그 작업이 끝난 뒤 다시 실행하세요. 비정상 종료로 남은 기록이라면 "
            "%s 를 지우면 됩니다."
            % (bundle, activity, pid, host, started, bundle / OWNER_NAME)
        )


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """OS 파일 잠금. 짧은 임계 구역에만 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415 - 플랫폼별
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 - 플랫폼별
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def process_alive(pid: int) -> bool:
    """해당 프로세스가 살아 있는지 확인한다. 알 수 없으면 살아 있다고 본다.

    확신이 없을 때 살아 있다고 보는 쪽이 안전하다. 틀려서 기다리게 하는 손해는
    작고, 틀려서 남의 작업을 덮어쓰는 손해는 크다.
    """
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes  # noqa: PLC0415 - 플랫폼별
        from ctypes import wintypes  # noqa: PLC0415
        # use_last_error: ctypes.windll 의 GetLastError 는 그사이 다른 API 호출이
        # 값을 덮을 수 있다. 접근 거부(살아 있음)를 "없음"으로 잘못 읽으면 남의
        # 잠금을 빼앗는다.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # 접근이 거부됐다면 프로세스는 존재한다 (다른 권한으로 도는 중).
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        # 핸들이 열린다고 살아 있는 것은 아니다. 부모가 핸들을 쥐고 있으면
        # 이미 끝난 프로세스도 열린다 — 종료 코드까지 봐야 한다.
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True  # 확인 불가. 살아 있다고 본다.
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_owner(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 깨진 기록은 없는 것으로 본다. 이걸로 작업을 막으면 되살릴 방법이 없다.
        return None
    return payload if isinstance(payload, dict) else None


def _write_owner(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def _is_stale(owner: dict[str, Any], stale_after: float) -> bool:
    """버려진 기록인지 판단한다.

    순서가 중요하다.
      1. 시간 상한. 등록 한 번이 하루를 넘기는 일은 없다. 프로세스 번호가 다른
         프로그램에 재사용돼 "살아 있음"으로 보이는 기록도 여기서 풀린다.
      2. 번호가 이 프로세스와 같다. `bundle_lock` 은 이 프로세스가 쥔 번들을
         먼저 걸러내므로, 여기까지 왔다면 예전 프로세스의 번호를 물려받은 것이다.
      3. 같은 기기에서는 생존 확인으로 판단한다.
      4. 다른 기기(공유 폴더)는 생존을 확인할 수 없어 시간 상한에만 맡긴다.
    """
    started = owner.get("started_epoch")
    if not isinstance(started, (int, float)):
        return True
    if (time.time() - started) > stale_after:
        return True
    pid = owner.get("pid")
    if not isinstance(pid, int):
        return True
    if owner.get("host") != socket.gethostname():
        return False
    if pid == os.getpid():
        return True
    return not process_alive(pid)


def owner_of(bundle: Path) -> dict[str, Any] | None:
    """현재 소유자 기록을 돌려준다. 없으면 None. 읽기 전용이다."""
    return _read_owner(Path(bundle) / OWNER_NAME)


@contextlib.contextmanager
def bundle_lock(bundle: Path, *, activity: str = "run",
                stale_after: float = DEFAULT_STALE_AFTER) -> Iterator[None]:
    """번들 하나를 이 프로세스만 다루게 한다.

    다른 프로세스가 쥐고 있으면 기다리지 않고 `BundleBusy` 를 올린다.
    """
    bundle = Path(bundle)
    key = str(bundle.resolve())
    if _held.get(key):
        # 같은 프로세스가 이미 쥐고 있다. 깊이만 올린다.
        _held[key] += 1
        try:
            yield
        finally:
            _held[key] -= 1
        return

    bundle.mkdir(parents=True, exist_ok=True)
    owner_path = bundle / OWNER_NAME
    guard_path = bundle / GUARD_NAME

    with file_lock(guard_path):
        existing = _read_owner(owner_path)
        if existing is not None and not _is_stale(existing, stale_after):
            raise BundleBusy(bundle, existing)
        _write_owner(owner_path, {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "activity": activity,
            "started_at": _now(),
            "started_epoch": time.time(),
        })

    _held[key] = 1
    try:
        yield
    finally:
        _held.pop(key, None)
        try:
            with file_lock(guard_path):
                current = _read_owner(owner_path)
                if current is not None and current.get("pid") == os.getpid():
                    owner_path.unlink(missing_ok=True)
        except OSError:
            # 놓지 못해도 작업 결과를 덮지 않는다. 남은 기록은 생존 확인으로
            # 다음 실행이 회수한다.
            pass
