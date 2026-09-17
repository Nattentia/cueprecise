"""소스 실행과 Windows 번들 실행이 같은 외부 도구를 찾게 한다."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def tool(name: str) -> str:
    """번들 옆 실행 파일이 있으면 우선하고, 없으면 PATH 이름을 돌려준다.

    예전에는 PyInstaller 번들(`sys.frozen`)일 때만 옆자리를 봤다. 이제는
    MCPB 의 임베더블 파이썬도 `python.exe` 옆에 `ffmpeg.exe`/`ffprobe.exe`
    를 두므로, 얼려졌는지와 무관하게 항상 `sys.executable` 옆을 먼저 본다.
    """
    directory = Path(sys.executable).resolve().parent
    for filename in (f"{name}.exe", name):
        candidate = directory / filename
        if candidate.is_file():
            return str(candidate)
    return name


def command(name: str) -> list[str]:
    """외부 파이썬 도구(yt-dlp)를 부르는 명령을 돌려준다.

    실행 파일 옆에 `<name>.exe` 가 있으면 그것을 쓴다(setup.exe 설치본은
    yt-dlp.exe 를 따로 만들어 옆에 둔다). PyInstaller 번들이 모듈을 품고
    있으면 자신을 `--<name>` 플래그로 다시 부른다. 얼려지지 않았고 모듈을
    import 할 수 있으면(MCPB 의 임베더블 파이썬) 같은 인터프리터를 `-m` 으로
    부른다. 모두 아니면 PATH 에서 이름 그대로 찾는다.
    """
    module = name.replace("-", "_")
    sibling = tool(name)
    if sibling != name:
        return [sibling]
    available = importlib.util.find_spec(module) is not None
    if getattr(sys, "frozen", False):
        return [sys.executable, f"--{name}"] if available else [name]
    if available:
        return [sys.executable, "-m", module]
    return [name]
