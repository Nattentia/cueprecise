"""소스 실행과 Windows 번들 실행이 같은 외부 도구를 찾게 한다."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def tool(name: str) -> str:
    """번들 옆 실행 파일이 있으면 우선하고, 없으면 PATH 이름을 돌려준다."""
    suffix = ".exe" if os.name == "nt" else ""
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / f"{name}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return name

