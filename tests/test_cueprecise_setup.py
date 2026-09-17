"""콘솔 설치 앞단(`cueprecise_setup`, tkinter 없는 zip 설치본용) 테스트.

실제 연결·해제·이관은 installer_support 의 몫이라 그 테스트에서 이미 본다.
여기서는 이 파일이 그것을 올바른 인자로 부르는지만 본다.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import configuration
import cueprecise_setup


VALID_KEY = "AIza" + "A" * 35


class InstallDirectoryTest(unittest.TestCase):
    def test_zip_layout_returns_the_folder_that_holds_py_and_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            python_exe = root / "py" / "python.exe"
            python_exe.parent.mkdir()
            python_exe.write_bytes(b"")
            with mock.patch.object(sys, "executable", str(python_exe)):
                self.assertEqual(cueprecise_setup.install_directory(), root)

    def test_zip_layout_is_recognized_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            python_exe = root / "PY" / "python.exe"
            python_exe.parent.mkdir()
            python_exe.write_bytes(b"")
            with mock.patch.object(sys, "executable", str(python_exe)):
                self.assertEqual(cueprecise_setup.install_directory(), root)

    def test_running_from_source_falls_back_to_the_repo_root(self) -> None:
        with mock.patch.object(sys, "executable", "C:/Python312/python.exe"):
            self.assertEqual(
                cueprecise_setup.install_directory(),
                Path(cueprecise_setup.__file__).resolve().parents[1])


class ApiKeyStdinTest(unittest.TestCase):
    def test_valid_key_is_read_from_the_first_line(self) -> None:
        with mock.patch.object(cueprecise_setup.sys, "stdin", io.StringIO(VALID_KEY + "\n")):
            self.assertEqual(cueprecise_setup._read_api_key_stdin(), VALID_KEY)

    def test_invalid_key_exits_with_code_2_and_a_message(self) -> None:
        with mock.patch.object(cueprecise_setup.sys, "stdin", io.StringIO("bad-key\n")):
            with self.assertRaises(SystemExit) as caught:
                cueprecise_setup._read_api_key_stdin()
            self.assertEqual(caught.exception.code, 2)


class TargetResolutionTest(unittest.TestCase):
    def test_targets_flag_resolves_by_key_regardless_of_detection(self) -> None:
        targets = cueprecise_setup._resolve_targets("claude-desktop,codex")
        self.assertEqual([target.key for target in targets], ["claude-desktop", "codex"])

    def test_unknown_target_name_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            cueprecise_setup._resolve_targets("not-a-real-client")

    def test_no_targets_flag_prompts_from_detected_clients_only(self) -> None:
        found = configuration.ClientTarget(
            key="found", label="Found", locate_config=lambda: Path("nowhere"),
            detector=lambda: True)
        gone = configuration.ClientTarget(
            key="gone", label="Gone", locate_config=lambda: Path("nowhere-else"),
            detector=lambda: False)
        with mock.patch.object(configuration, "CLIENTS", (found, gone)), \
                mock.patch("builtins.input", return_value=""):
            targets = cueprecise_setup._resolve_targets(None)
        self.assertEqual(targets, [found])

    def test_prompt_lets_the_user_pick_a_subset_by_number(self) -> None:
        one = configuration.ClientTarget(
            key="one", label="One", locate_config=lambda: Path("a"), detector=lambda: True)
        two = configuration.ClientTarget(
            key="two", label="Two", locate_config=lambda: Path("b"), detector=lambda: True)
        with mock.patch.object(configuration, "CLIENTS", (one, two)), \
                mock.patch("builtins.input", return_value="2"):
            targets = cueprecise_setup._resolve_targets(None)
        self.assertEqual(targets, [two])


class MainFlowTest(unittest.TestCase):
    def test_uninstall_calls_disconnect_clients_and_reports_success(self) -> None:
        with mock.patch("installer_support.disconnect_clients",
                        return_value={"removed": [{"key": "codex", "label": "Codex"}],
                                       "failed": []}) as disconnect:
            code = cueprecise_setup.main(["--uninstall"])
        disconnect.assert_called_once_with()
        self.assertEqual(code, 0)

    def test_uninstall_reports_failure_when_something_could_not_be_removed(self) -> None:
        with mock.patch("installer_support.disconnect_clients",
                        return_value={"removed": [],
                                       "failed": [{"key": "codex", "label": "Codex",
                                                    "reason": "거부됨"}]}):
            code = cueprecise_setup.main(["--uninstall"])
        self.assertEqual(code, 1)

    def test_migrate_calls_migrate_clients_and_never_raises(self) -> None:
        with mock.patch("installer_support.migrate_clients",
                        side_effect=RuntimeError("boom")) as migrate:
            code = cueprecise_setup.main(["--migrate"])
        migrate.assert_called_once()
        self.assertEqual(code, 0)

    def test_uninstall_and_migrate_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            cueprecise_setup.main(["--uninstall", "--migrate"])

    def test_happy_path_connects_with_a_stdin_key_and_named_targets(self) -> None:
        connected = {"connected": [{"key": "claude-desktop", "label": "Claude Desktop"}],
                    "failed": []}
        with mock.patch.object(cueprecise_setup.sys, "stdin", io.StringIO(VALID_KEY + "\n")), \
                mock.patch("installer_support.connect_clients",
                          return_value=connected) as connect:
            code = cueprecise_setup.main(["--api-key-stdin", "--targets", "claude-desktop"])
        self.assertEqual(code, 0)
        connect.assert_called_once()
        _args, kwargs = connect.call_args
        self.assertEqual([target.key for target in kwargs["targets"]], ["claude-desktop"])

    def test_failure_to_connect_any_app_is_reported_as_a_failing_exit_code(self) -> None:
        with mock.patch.object(cueprecise_setup.sys, "stdin", io.StringIO(VALID_KEY + "\n")), \
                mock.patch("installer_support.connect_clients",
                          return_value={"connected": [],
                                        "failed": [{"key": "codex", "label": "Codex",
                                                     "reason": "실패"}]}):
            code = cueprecise_setup.main(["--api-key-stdin", "--targets", "codex"])
        self.assertEqual(code, 1)

    def test_an_exception_while_connecting_is_reported_not_raised(self) -> None:
        with mock.patch.object(cueprecise_setup.sys, "stdin", io.StringIO(VALID_KEY + "\n")), \
                mock.patch("installer_support.connect_clients",
                          side_effect=RuntimeError("연결 실패")):
            code = cueprecise_setup.main(["--api-key-stdin", "--targets", "codex"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
