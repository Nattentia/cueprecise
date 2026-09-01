"""릴리스 후보의 버전이 저장소 네 곳에서 같은지 확인한다.

0.2.0 을 낼 때 버전이 박힌 곳을 손으로 고치다 어긋난 적이 있다. 릴리스
워크플로가 빌드를 시작하기 전에 이 검사를 먼저 통과해야 한다.

성공하면 태그 이름(`v0.3.0`)만 표준 출력으로 내보낸다. 워크플로가 그 값을
그대로 태그로 쓴다. 사람이 읽을 설명은 모두 표준 오류로 간다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

# 파일마다 "이 자리에 적힌 버전" 을 뽑는 정규식. 한 파일에 여러 번 나오면
# 전부 같아야 한다.
DECLARATIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pyproject.toml", re.compile(r'^version = "(\d+\.\d+\.\d+)"', re.MULTILINE)),
    (
        "installer/cueprecise.iss",
        re.compile(r'^#define MyAppVersion "(\d+\.\d+\.\d+)"', re.MULTILINE),
    ),
    ("README.md", re.compile(r"`v(\d+\.\d+\.\d+)`")),
    ("README.ko.md", re.compile(r"`v(\d+\.\d+\.\d+)`")),
)


class VersionMismatch(Exception):
    """선언된 버전이 서로 다르거나 요청한 버전과 다르다."""


def normalize(version: str) -> str:
    """`v0.3.0`, ` 0.3.0 ` 같은 입력을 `0.3.0` 으로 맞춘다."""
    cleaned = version.strip()
    if cleaned.startswith(("v", "V")):
        cleaned = cleaned[1:]
    if not VERSION_PATTERN.match(cleaned):
        raise VersionMismatch(f"버전 형식이 아니다: {version!r} (예: 0.3.0)")
    return cleaned


def declared_versions(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """파일별로 선언된 버전을 읽는다. 한 파일 안에서 어긋나도 오류다."""
    found: dict[str, str] = {}
    for relative, pattern in DECLARATIONS:
        path = repo_root / relative
        if not path.exists():
            raise VersionMismatch(f"버전이 적힌 파일이 없다: {relative}")
        matches = pattern.findall(path.read_text(encoding="utf-8"))
        if not matches:
            raise VersionMismatch(f"{relative} 에서 버전을 찾지 못했다")
        if len(set(matches)) > 1:
            joined = ", ".join(sorted(set(matches)))
            raise VersionMismatch(f"{relative} 안에서 버전이 어긋난다: {joined}")
        found[relative] = matches[0]
    return found


def check(version: str, repo_root: Path = REPO_ROOT) -> str:
    """요청한 버전이 네 곳과 모두 같으면 태그 이름을 돌려준다."""
    wanted = normalize(version)
    found = declared_versions(repo_root)
    wrong = {name: value for name, value in found.items() if value != wanted}
    if wrong:
        lines = [f"요청한 버전은 {wanted} 인데 다음이 다르다:"]
        lines += [f"  {name}: {value}" for name, value in sorted(wrong.items())]
        lines.append("네 곳을 모두 같은 값으로 고친 뒤 다시 실행한다.")
        raise VersionMismatch("\n".join(lines))
    return f"v{wanted}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="배포할 버전 (예: 0.3.0)")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="검사할 저장소 경로 (기본값: 이 파일이 든 저장소)",
    )
    args = parser.parse_args(argv)
    try:
        tag = check(args.version, args.repo_root)
    except VersionMismatch as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"버전이 네 곳에서 모두 {tag} 로 일치한다.", file=sys.stderr)
    print(tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
