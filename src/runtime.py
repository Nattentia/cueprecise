"""소스 실행과 Windows 번들 실행이 같은 외부 도구를 찾게 한다."""
from __future__ import annotations

import sys
from pathlib import Path


def tool(name: str) -> str:
    """번들 옆 실행 파일이 있으면 우선하고, 없으면 PATH 이름을 돌려준다."""
    if getattr(sys, "frozen", False):
        directory = Path(sys.executable).resolve().parent
        for filename in (f"{name}.exe", name):
            candidate = directory / filename
            if candidate.is_file():
                return str(candidate)
    return name
