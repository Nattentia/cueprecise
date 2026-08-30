"""Concurrency-safe local estimate of Gemini request attempts."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class _USPacific(tzinfo):
    """tzdata 가 없는 환경(주로 Windows venv)을 위한 US/Pacific 폴백.

    2007년 이후 미국 규칙: 3월 둘째 일요일 02:00 에 DST 시작, 11월 첫째
    일요일 02:00 에 종료. 새 외부 의존성을 들이지 않으려고 직접 계산한다.
    """

    _STD = timedelta(hours=-8)
    _DST = timedelta(hours=-7)

    @staticmethod
    def _nth_sunday(year: int, month: int, nth: int) -> datetime:
        first = datetime(year, month, 1)
        first_sunday = 1 + (6 - first.weekday()) % 7  # weekday(): 월=0 … 일=6
        return datetime(year, month, first_sunday + 7 * (nth - 1), 2, 0)

    def _is_dst(self, dt: datetime) -> bool:
        naive = dt.replace(tzinfo=None)
        return (self._nth_sunday(naive.year, 3, 2)
                <= naive < self._nth_sunday(naive.year, 11, 1))

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self._DST if dt is not None and self._is_dst(dt) else self._STD

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=1) if dt is not None and self._is_dst(dt) else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "PDT" if dt is not None and self._is_dst(dt) else "PST"


def _pacific() -> tzinfo:
    try:
        return ZoneInfo("America/Los_Angeles")
    except (ZoneInfoNotFoundError, KeyError):
        return _USPacific()


PACIFIC = _pacific()


class UsageLimitExceeded(RuntimeError):
    pass


def key_hash(api_key: str) -> str:
    if not api_key:
        raise ValueError("API key가 비어 있습니다.")
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "keys": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"사용량 원장을 읽지 못했습니다: {path}") from error
    if payload.get("schema_version") != 1 or not isinstance(payload.get("keys"), dict):
        raise RuntimeError(f"지원하지 않는 사용량 원장 형식입니다: {path}")
    return payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _clock(now: datetime | None = None) -> tuple[datetime, str]:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now는 timezone-aware datetime이어야 합니다.")
    pacific = instant.astimezone(PACIFIC)
    return instant.astimezone(timezone.utc), pacific.date().isoformat()


def _entry(payload: dict[str, Any], digest: str, day: str) -> dict[str, Any]:
    key_data = payload["keys"].setdefault(digest, {"days": {}})
    return key_data["days"].setdefault(day, {"attempts": 0, "recent_utc": []})


def get_usage(path: Path, api_key: str, now: datetime | None = None) -> dict[str, Any]:
    utc_now, day = _clock(now)
    digest = key_hash(api_key)
    with _file_lock(path.with_suffix(path.suffix + ".lock")):
        payload = _read(path)
        entry = payload.get("keys", {}).get(digest, {}).get("days", {}).get(day, {})
    recent = [
        value for value in entry.get("recent_utc", [])
        if datetime.fromisoformat(value) > utc_now - timedelta(minutes=1)
    ]
    return {"day": day, "attempts": int(entry.get("attempts", 0)), "rpm_attempts": len(recent)}


def record_attempt(path: Path, api_key: str, now: datetime | None = None) -> dict[str, Any]:
    """Increment immediately before a real API request, including retries."""
    utc_now, day = _clock(now)
    digest = key_hash(api_key)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _file_lock(lock_path):
        payload = _read(path)
        entry = _entry(payload, digest, day)
        cutoff = utc_now - timedelta(minutes=1)
        recent = [
            value for value in entry.get("recent_utc", [])
            if datetime.fromisoformat(value) > cutoff
        ]
        recent.append(utc_now.isoformat())
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["recent_utc"] = recent
        _atomic_write(path, payload)
        return {"day": day, "attempts": entry["attempts"], "rpm_attempts": len(recent)}


def preflight(
    path: Path,
    api_key: str,
    expected_calls: int,
    *,
    daily_limit: int,
    rpm_limit: int | None = None,
    free_mode: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    if expected_calls < 0 or daily_limit <= 0 or (rpm_limit is not None and rpm_limit <= 0):
        raise ValueError("호출 예상/한도 설정이 올바르지 않습니다.")
    current = get_usage(path, api_key, now)
    remaining = max(0, daily_limit - current["attempts"])
    if free_mode and expected_calls > remaining:
        raise UsageLimitExceeded(
            f"예상 호출 {expected_calls}회가 로컬 추정 잔여 {remaining}회를 초과합니다."
        )
    if rpm_limit is not None and current["rpm_attempts"] >= rpm_limit:
        raise UsageLimitExceeded(
            f"최근 1분 로컬 시도 {current['rpm_attempts']}회가 설정 RPM {rpm_limit}회에 도달했습니다."
        )
    return {
        **current,
        "expected_calls": expected_calls,
        "after_expected": current["attempts"] + expected_calls,
        "daily_limit": daily_limit,
        "remaining": remaining,
        "accuracy": "local estimate",
    }


def format_status(status: dict[str, Any], now: datetime | None = None) -> str:
    _, day = _clock(now)
    pacific_now = (now or datetime.now(timezone.utc)).astimezone(PACIFIC)
    reset = datetime.combine(
        pacific_now.date() + timedelta(days=1), datetime.min.time(), PACIFIC
    )
    return "\n".join([
        f"오늘 로컬 기록: {status['attempts']}회",
        f"이번 작업 예상: {status['expected_calls']}회",
        f"작업 후 예상: {status['after_expected']}회",
        f"설정된 일일 한도: {status['daily_limit']}회",
        f"예상 잔여: {max(0, status['daily_limit'] - status['after_expected'])}회",
        f"초기화: {reset.isoformat()} (Pacific midnight, day={day})",
        "정확도: local estimate",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--expected-calls", type=int, required=True)
    parser.add_argument("--daily-limit", type=int, required=True)
    parser.add_argument("--rpm-limit", type=int)
    args = parser.parse_args()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
    status = preflight(
        args.ledger, api_key, args.expected_calls,
        daily_limit=args.daily_limit, rpm_limit=args.rpm_limit,
    )
    print(format_status(status))


if __name__ == "__main__":
    main()
