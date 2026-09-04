"""설치형 CLI와 설정 마이그레이션 테스트."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import cueprecise_cli


class SetupTest(unittest.TestCase):
    def test_setup_preserves_existing_config_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "claude.json"
            config_path.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))

            with mock.patch.object(sys, "executable", "C:/Python/python.exe"):
                first = cueprecise_cli.setup_claude(config_path, root / "data", api_key="secret")
                second = cueprecise_cli.setup_claude(config_path, root / "data", api_key="secret")

            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["theme"], "dark")
            self.assertEqual(config["mcpServers"]["other"]["command"], "x")
            self.assertEqual(config["mcpServers"]["cueprecise"]["command"], "C:/Python/python.exe")
            self.assertIn("-m", config["mcpServers"]["cueprecise"]["args"])
            self.assertEqual(config["mcpServers"]["cueprecise"]["env"]["GEMINI_API_KEY"], "secret")
            self.assertIsNotNone(first["backup"])
            self.assertIsNotNone(second["backup"])

    def test_invalid_config_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "claude.json"
            config_path.write_text("not json", encoding="utf-8")
            with self.assertRaises(SystemExit):
                cueprecise_cli.setup_claude(config_path, Path(tmp) / "data", api_key=None)
            self.assertEqual(config_path.read_text(encoding="utf-8"), "not json")

    def test_doctor_treats_api_key_as_optional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "claude.json"
            config_path.write_text(json.dumps({"mcpServers": {"cueprecise": {}}}), encoding="utf-8")
            with mock.patch("cueprecise_cli.shutil.which", return_value="tool"), mock.patch.dict("os.environ", {}, clear=True):
                checks, ok = cueprecise_cli.doctor(config_path)
            self.assertTrue(ok)
            self.assertFalse(checks["gemini_api_key"]["ok"])


class SetupKeySourceTest(unittest.TestCase):
    """키를 명령줄에 적지 않고도 등록할 수 있어야 한다. 명령줄은 셸 기록에 남는다."""

    KEY = "AIza" + "c" * 36

    def test_key_file_is_read_without_touching_the_command_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.txt"
            path.write_text("\n".join([self.KEY, "두 번째 줄은 무시한다", ""]),
                            encoding="utf-8")
            self.assertEqual(cueprecise_cli._resolve_setup_key(None, path), self.KEY)

    def test_dash_reads_the_key_from_standard_input(self) -> None:
        with mock.patch.object(cueprecise_cli.sys, "stdin", io.StringIO(self.KEY + "\n")):
            self.assertEqual(cueprecise_cli._resolve_setup_key("-", None), self.KEY)

    def test_literal_key_still_works_but_says_why_not_to(self) -> None:
        # 자동화가 이미 쓰고 있다. 없애면 남의 파이프라인이 깨진다.
        stderr = io.StringIO()
        with mock.patch.object(cueprecise_cli.sys, "stderr", stderr):
            self.assertEqual(cueprecise_cli._resolve_setup_key(self.KEY, None), self.KEY)
        self.assertIn("셸 기록", stderr.getvalue())
        # 경고문이 키를 다시 흘리면 안 된다.
        self.assertNotIn(self.KEY, stderr.getvalue())

    def test_environment_is_used_when_nothing_is_given(self) -> None:
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": self.KEY}):
            self.assertEqual(cueprecise_cli._resolve_setup_key(None, None), self.KEY)

    def test_missing_key_file_is_reported_not_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with mock.patch.object(cueprecise_cli.sys, "stderr", stderr):
                code = cueprecise_cli._setup_main(
                    ["--api-key-file", str(Path(tmp) / "nope.txt")])
            self.assertEqual(code, 2)
            self.assertIn("키 파일을 읽지 못했다", stderr.getvalue())

    def test_two_key_sources_at_once_is_refused(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(cueprecise_cli.sys, "stderr", stderr):
            code = cueprecise_cli._setup_main(
                ["--api-key", self.KEY, "--api-key-file", "k.txt"])
        self.assertEqual(code, 2)
        self.assertNotIn(self.KEY, stderr.getvalue())

    def test_empty_key_file_is_treated_as_no_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.txt"
            path.write_text("", encoding="utf-8")
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertIsNone(cueprecise_cli._resolve_setup_key(None, path))


if __name__ == "__main__":
    unittest.main()
