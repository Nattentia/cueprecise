"""설치형 CLI와 설정 마이그레이션 테스트."""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
