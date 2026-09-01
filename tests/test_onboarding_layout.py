"""설치 화면이 내용에 맞는 크기를 갖는지 본다.

잘림은 시험이 눈으로 보지 못한다. 대신 **필요한 높이와 실제 창 높이**는 물어볼
수 있다. 앱 일곱을 체크박스로 늘어놓았는데 창이 `620x500` 으로 못 박혀 있어
148px 이 잘렸고, 잘린 자리가 연결 버튼이었다. 시험 429개가 모두 통과하는
상태에서 설치 프로그램은 못 쓰는 물건이었다.

화면이 없는 환경(CI)에서는 건너뛴다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "installer"))


def _tk_or_skip(case: unittest.TestCase):
    try:
        import tkinter as tk
    except ImportError as error:  # pragma: no cover - 빌드에 따라 없을 수 있다
        case.skipTest(f"tkinter 없음: {error}")
    try:
        root = tk.Tk()
    except Exception as error:  # 화면이 없는 환경
        case.skipTest(f"화면 없음: {error}")
    root.withdraw()

    def close() -> None:
        # 시험이 먼저 닫았을 수 있다. 두 번 닫는 것은 잘못이 아니다.
        try:
            root.destroy()
        except tk.TclError:
            pass

    case.addCleanup(close)
    return root


class WindowFitsItsContentsTest(unittest.TestCase):
    def _app(self, root, clients):
        import configuration
        import cueprecise_onboarding as onboarding

        with mock.patch.object(configuration, "CLIENTS", clients):
            return onboarding.OnboardingApp(root)

    def _target(self, key: str, installed: bool):
        import configuration

        return configuration.ClientTarget(
            key=key, label=key, detector=lambda: installed,
            locate_config=lambda: Path("/nowhere") / f"{key}.json")

    def _geometry_height(self, root) -> int:
        root.update_idletasks()
        size, _, _ = root.geometry().partition("+")
        return int(size.split("x")[1])

    def test_nothing_is_cut_off_when_every_app_is_present(self) -> None:
        """가장 긴 경우다. 여기서 안 잘리면 나머지도 안 잘린다."""
        root = _tk_or_skip(self)
        clients = tuple(self._target(f"app{index}", True) for index in range(7))
        self._app(root, clients)
        root.update_idletasks()
        self.assertGreaterEqual(self._geometry_height(root), root.winfo_reqheight())

    def _height_for(self, clients) -> int:
        root = _tk_or_skip(self)
        self._app(root, clients)
        root.update_idletasks()
        height = root.winfo_reqheight()
        root.destroy()
        return height

    def test_apps_that_are_missing_cost_far_less_than_present_ones(self) -> None:
        """없는 앱까지 줄로 늘어놓으면 창이 앱 수만큼 길어진다.

        임의의 픽셀 수를 못 박지 않는다. **있는 앱 여섯을 더할 때와 없는 앱
        여섯을 더할 때**를 견준다. 글꼴이나 화면 배율이 달라져도 뜻이 유지된다.
        """
        one = (self._target("here", True),)
        with_missing = one + tuple(self._target(f"gone{i}", False) for i in range(6))
        with_present = one + tuple(self._target(f"more{i}", True) for i in range(6))

        base = self._height_for(one)
        missing_cost = self._height_for(with_missing) - base
        present_cost = self._height_for(with_present) - base

        self.assertGreater(present_cost, 0)
        # 없는 앱 여섯은 안내 한 줄이다. 있는 앱 여섯의 절반도 되지 않아야 한다.
        self.assertLess(missing_cost, present_cost / 2)

    def test_only_installed_apps_are_offered(self) -> None:
        root = _tk_or_skip(self)
        clients = (self._target("yes", True), self._target("no", False))
        app = self._app(root, clients)
        self.assertEqual(list(app.client_vars), ["yes"])
        self.assertEqual(app._selected_targets()[0].key, "yes")


if __name__ == "__main__":
    unittest.main()
