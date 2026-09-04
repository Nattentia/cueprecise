"""Store a Gemini API key with Windows DPAPI instead of MCP config plaintext."""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


CREDENTIAL_ENV = "CUEPRECISE_GEMINI_CREDENTIAL"
INLINE_KEY_ENV = "GEMINI_API_KEY"
_DESCRIPTION = "CuePrecise Gemini API key"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class CredentialError(RuntimeError):
    """The protected credential could not be stored or read."""


@dataclass(frozen=True)
class CredentialSnapshot:
    existed: bool
    payload: bytes = b""


def supported() -> bool:
    return sys.platform == "win32"


def default_path() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
        base = Path(configured)
    elif profile := os.environ.get("USERPROFILE"):
        base = Path(profile) / "AppData" / "Local"
    else:
        # 서비스 계정이나 격리된 진단 프로세스에는 HOME 계열 변수가 아예 없을
        # 수 있다. 그때 Path.home()이 던지는 RuntimeError를 그대로 새게 하지
        # 않고, 호출자가 사용자에게 설명할 수 있는 도메인 오류로 바꾼다.
        raise CredentialError("Windows 사용자 프로필 경로를 찾지 못했습니다")
    return base / "CuePrecise" / "credentials" / "gemini-api-key.dpapi"


if sys.platform == "win32":
    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _windows_api():
    if not supported():
        raise CredentialError("Windows DPAPI is not available on this platform")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _input_blob(payload: bytes):
    buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    return _DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def protect(plaintext: bytes) -> bytes:
    crypt32, kernel32 = _windows_api()
    source, keepalive = _input_blob(plaintext)
    destination = _DataBlob()
    if not crypt32.CryptProtectData(
            ctypes.byref(source), _DESCRIPTION, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(destination)):
        raise CredentialError(f"DPAPI encryption failed (Windows error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)
        del keepalive


def unprotect(ciphertext: bytes) -> bytes:
    crypt32, kernel32 = _windows_api()
    source, keepalive = _input_blob(ciphertext)
    destination = _DataBlob()
    description = wintypes.LPWSTR()
    if not crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(destination)):
        raise CredentialError(f"DPAPI decryption failed (Windows error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        if description:
            kernel32.LocalFree(description)
        kernel32.LocalFree(destination.pbData)
        del keepalive


def snapshot(path: Path | None = None) -> CredentialSnapshot:
    destination = path or default_path()
    try:
        if not destination.exists():
            return CredentialSnapshot(False)
        return CredentialSnapshot(True, destination.read_bytes())
    except OSError as error:
        raise CredentialError(f"Cannot read the existing protected credential: {destination}") from error


def restore(state: CredentialSnapshot, path: Path | None = None) -> None:
    destination = path or default_path()
    if not state.existed:
        destination.unlink(missing_ok=True)
        try:
            destination.parent.rmdir()
            destination.parent.parent.rmdir()
        except OSError:
            pass
        return
    _atomic_write(destination, state.payload)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def store(api_key: str, path: Path | None = None) -> Path:
    if not api_key:
        raise CredentialError("The API key is empty")
    destination = path or default_path()
    _atomic_write(destination, protect(api_key.encode("utf-8")))
    return destination.resolve()


def load(path: Path | None = None) -> str | None:
    source = path or default_path()
    if not source.exists():
        return None
    try:
        value = unprotect(source.read_bytes()).decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise CredentialError(f"Cannot read the protected credential: {source}") from error
    return value or None


def resolve(environment: Mapping[str, str] | None = None) -> str | None:
    values = os.environ if environment is None else environment
    if inline := values.get(INLINE_KEY_ENV):
        return inline
    named = values.get(CREDENTIAL_ENV)
    if named:
        return load(Path(named))
    if supported():
        return load()
    return None


def delete(path: Path | None = None) -> bool:
    destination = path or default_path()
    if not destination.exists():
        return False
    destination.unlink()
    return True
