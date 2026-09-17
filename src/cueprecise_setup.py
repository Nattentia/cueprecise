"""CuePrecise 콘솔 설치 — tkinter가 없는 임베더블 파이썬에서도 돈다.

`py\\python.exe -m cueprecise_setup` 로 부른다. GUI 온보딩
(installer/cueprecise_onboarding.py)과 하는 일은 같지만 화면 없이 표준
입출력만 쓴다. 실제 연결·해제·이관 로직은 전부 installer_support 에 있고,
이 파일은 그것을 콘솔에서 부르는 앞단일 뿐이다.

  cueprecise_setup            대화형: 키를 묻고, 감지된 앱 중 고르고, 연결한다
  cueprecise_setup --uninstall   붙였던 앱 전부에서 뗀다
  cueprecise_setup --migrate     조용한 재설치용. 실패해도 설치를 막지 않는다
  cueprecise_setup --api-key-stdin --targets a,b   비대화형(에이전트·시험용)
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Any

import configuration
import installer_support


API_KEY_URL = "https://aistudio.google.com/api-keys"


def _old_setup_exe_install_dir() -> Path | None:
    """v0.2.5/v0.2.6 setup.exe 가 설치했던 옛 폴더가 아직 있으면 그 경로를 돌려준다.

    이 zip 설치본은 그 폴더에 손대지 않는다. 사용자가 원치 않는데 프로그램을
    지우면 안 되기 때문이다. 대신 남아 있다는 사실만 알려 사람이 직접
    Windows 설정에서 지우게 한다.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    candidate = Path(base) / "Programs" / "CuePrecise"
    return candidate if candidate.is_dir() else None


def install_directory() -> Path:
    """이 파일이 들어 있는 `py\\python.exe` 옆이 아니라, `py`와 `app`을 함께
    담은 설치 폴더를 돌려준다.

    zip 설치본은 언제나 `<설치 폴더>\\py\\python.exe -m cueprecise_setup` 으로
    실행되므로 `sys.executable` 의 조부모가 설치 폴더다. 소스에서 바로 실행할
    때(개발·테스트)는 그 가정이 성립하지 않으니 이 저장소 루트로 대신 돌아간다.
    """
    executable = Path(sys.executable).resolve()
    if executable.parent.name.lower() == installer_support.PYTHON_SUBDIR:
        return executable.parent.parent
    return Path(__file__).resolve().parents[1]


def _print_intro() -> None:
    print("CuePrecise를 이 PC의 AI 앱에 연결합니다.")
    print("유튜브 영상에서 찾던 그 대목을 정확히 짚어 줍니다. Gemini API 키만 있으면 됩니다.")
    print()
    print(f"1. 무료 Gemini API 키를 아직 만들지 않았다면: {API_KEY_URL}")
    print("2. 아래에 그 키를 붙여넣으세요(입력한 글자는 화면에 보이지 않습니다).")
    print()


def _prompt_api_key() -> str:
    while True:
        try:
            raw = getpass.getpass("Gemini API 키: ")
        except (EOFError, KeyboardInterrupt):
            print("\n취소했습니다.", file=sys.stderr)
            raise SystemExit(1)
        key, error = installer_support.validate_api_key(raw)
        if error:
            print(error)
            continue
        return key


def _read_api_key_stdin() -> str:
    """표준 입력 첫 줄을 키로 읽는다. 사람이 아니라 다른 프로그램이 부를 때 쓴다."""
    raw = sys.stdin.readline()
    key, error = installer_support.validate_api_key(raw)
    if error:
        print(error, file=sys.stderr)
        raise SystemExit(2)
    return key


def _prompt_targets(detected: list[configuration.ClientTarget]) -> list[configuration.ClientTarget]:
    if not detected:
        print("연결할 수 있는 AI 앱을 찾지 못했습니다. Claude Desktop, Codex, Claude Code, "
              "VS Code 중 하나를 설치한 뒤 다시 실행해 주세요.")
        raise SystemExit(1)
    print("3. 연결할 앱을 고르세요(이 PC에서 찾은 앱만 보여줍니다):")
    for index, target in enumerate(detected, start=1):
        print(f"  {index}. {target.label}")
    print("번호를 쉼표로 구분해 입력하세요. 그냥 Enter를 누르면 전부 연결합니다.")
    try:
        raw = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n취소했습니다.", file=sys.stderr)
        raise SystemExit(1)
    if not raw:
        return detected
    chosen: list[configuration.ClientTarget] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            position = int(token)
        except ValueError:
            print(f"무시한 입력: {token}")
            continue
        if 1 <= position <= len(detected):
            chosen.append(detected[position - 1])
        else:
            print(f"범위를 벗어난 번호라 무시합니다: {token}")
    if not chosen:
        print("고른 앱이 없어 전부 연결합니다.")
        return detected
    return chosen


def _resolve_targets(names: str | None) -> list[configuration.ClientTarget]:
    """`--targets a,b` 는 감지 여부와 무관하게 이름으로 고른다(에이전트·시험용).

    대화형에서는 이 PC에 설치돼 있다고 판정된 앱만 번호로 고르게 한다.
    """
    if names is not None:
        return configuration.resolve_clients(names.split(","))
    return _prompt_targets(configuration.detected_clients())


def _print_result(result: dict[str, Any]) -> None:
    print()
    for item in result["connected"]:
        print(f"연결됨: {item['label']}")
    for item in result["failed"]:
        print(f"연결 안 됨: {item['label']} - {item['reason']}")
    if result["connected"]:
        print()
        print("연결한 앱을 완전히 종료한 뒤 다시 여세요.")
        print("새 대화에서 '이 유튜브 영상을 분석해줘'라고 요청하면 됩니다.")
    old_install = _old_setup_exe_install_dir()
    if old_install is not None:
        print()
        print(f"이전 버전의 CuePrecise 설치 프로그램이 남아 있습니다: {old_install}")
        print("Windows 설정 > 앱에서 'CuePrecise'를 제거할 수 있습니다(지금 자동으로 "
              "지우지 않습니다).")


def _run_uninstall() -> int:
    result = installer_support.disconnect_clients()
    if result["removed"]:
        names = ", ".join(item["label"] for item in result["removed"])
        print(f"{names}에서 CuePrecise를 뗐습니다.")
    else:
        print("연결된 항목이 없었습니다.")
    for item in result["failed"]:
        print(f"연결 해제 실패: {item['label']} - {item['reason']}", file=sys.stderr)
    return 1 if result["failed"] else 0


def _run_migrate() -> int:
    """설치 프로그램이 조용히 부른다. 실패해도 설치를 막지 않는다."""
    try:
        installer_support.migrate_clients(install_directory())
    except Exception:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cueprecise_setup", description="CuePrecise를 이 PC의 AI 앱에 연결한다.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--uninstall", action="store_true",
                      help="붙였던 앱 전부에서 CuePrecise를 뗀다.")
    mode.add_argument("--migrate", action="store_true",
                      help="조용한 재설치용. 이미 연결된 항목만 새 실행 파일로 다시 가리킨다.")
    parser.add_argument("--api-key-stdin", action="store_true",
                        help="표준 입력 첫 줄을 Gemini API 키로 읽는다(화면 입력 없이 자동화할 때).")
    parser.add_argument("--targets", default=None,
                        help="쉼표로 구분한 클라이언트 키(예: claude-desktop,codex). "
                             "생략하면 이 PC에서 감지된 앱 중 번호로 고르라고 묻는다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.uninstall:
        return _run_uninstall()
    if args.migrate:
        return _run_migrate()

    install_dir = install_directory()
    if not args.api_key_stdin:
        _print_intro()
    key = _read_api_key_stdin() if args.api_key_stdin else _prompt_api_key()
    targets = _resolve_targets(args.targets)

    try:
        result = installer_support.connect_clients(key, install_dir, targets=targets)
    except Exception as error:
        print(f"연결하지 못했습니다: {error}", file=sys.stderr)
        return 1
    _print_result(result)
    return 0 if result["connected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
