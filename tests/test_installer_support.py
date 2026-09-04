"""Windows 설치 화면의 키 처리와 안전한 Claude 연결 테스트."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import installer_support
import credential_store
import runtime


VALID_KEY = "AIza" + "A" * 35


class ApiKeyTest(unittest.TestCase):
    def test_normalizes_pasted_quotes_and_spaces(self) -> None:
        key, error = installer_support.validate_api_key(f'  "{VALID_KEY}"\n')
        self.assertEqual(key, VALID_KEY)
        self.assertIsNone(error)

    def test_rejects_missing_or_malformed_key_with_plain_message(self) -> None:
        self.assertIn("붙여넣어", installer_support.validate_api_key(" ")[1])
        self.assertIn("AIza", installer_support.validate_api_key("wrong-key")[1])
        self.assertIn("공백", installer_support.validate_api_key("AIza aaa")[1])


class ConnectTest(unittest.TestCase):
    def test_connect_preserves_other_servers_and_uses_bundled_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "app"
            install.mkdir()
            server = install / "cueprecise-mcp.exe"
            server.write_bytes(b"test")
            config = root / "claude.json"
            config.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}}}), encoding="utf-8")
            ffmpeg_bin = root / "ffmpeg"
            ffmpeg_bin.mkdir()

            with mock.patch("installer_support.ensure_ffmpeg", return_value=(ffmpeg_bin, None)), \
                    mock.patch("installer_support.probe_mcp", return_value=(True, None)):
                result = installer_support.connect(
                    VALID_KEY, install, config_path=config, bundle_root=root / "data",
                    credential_path=root / "key.dpapi")

            saved = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(saved["mcpServers"]["other"]["command"], "keep")
            entry = saved["mcpServers"]["cueprecise"]
            self.assertEqual(Path(entry["command"]), server.resolve())
            if sys.platform == "win32":
                self.assertNotIn("GEMINI_API_KEY", entry["env"])
                self.assertEqual(entry["env"][credential_store.CREDENTIAL_ENV],
                                 str((root / "key.dpapi").resolve()))
                self.assertEqual(credential_store.load(root / "key.dpapi"), VALID_KEY)
            else:
                self.assertEqual(entry["env"]["GEMINI_API_KEY"], VALID_KEY)
            self.assertTrue(entry["env"]["PATH"].startswith(str(ffmpeg_bin)))
            self.assertTrue(result["connection_tested"])
            self.assertTrue(Path(result["backup"]).is_file())

    def test_invalid_key_does_not_touch_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            config.write_text('{"safe": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                installer_support.connect("bad", root, config_path=config)
            self.assertEqual(config.read_text(encoding="utf-8"), '{"safe": true}')

    def test_failed_connection_test_does_not_touch_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "app"
            install.mkdir()
            (install / "cueprecise-mcp.exe").write_bytes(b"test")
            config = root / "claude.json"
            original = '{"mcpServers": {"other": {"command": "keep"}}}'
            config.write_text(original, encoding="utf-8")
            ffmpeg_bin = root / "ffmpeg"
            ffmpeg_bin.mkdir()

            with mock.patch("installer_support.ensure_ffmpeg", return_value=(ffmpeg_bin, None)), \
                    mock.patch("installer_support.probe_mcp", return_value=(False, "연결 실패")):
                with self.assertRaisesRegex(RuntimeError, "연결 실패"):
                    installer_support.connect(
                        VALID_KEY, install, config_path=config, bundle_root=root / "data")

            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_disconnect_removes_only_our_entry_and_preserves_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            config.write_text(json.dumps({
                "theme": "dark",
                "mcpServers": {
                    "cueprecise": {"command": "cueprecise-mcp.exe"},
                    "other": {"command": "keep"},
                },
            }), encoding="utf-8")
            result = installer_support.disconnect(config)
            saved = json.loads(config.read_text(encoding="utf-8"))
            self.assertNotIn("cueprecise", saved["mcpServers"])
            self.assertEqual(saved["mcpServers"]["other"]["command"], "keep")
            self.assertEqual(saved["theme"], "dark")
            self.assertTrue(result["changed"])
            self.assertTrue(Path(result["backup"]).is_file())


class RuntimeToolTest(unittest.TestCase):
    def test_frozen_runtime_prefers_sibling_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "cueprecise-mcp.exe"
            executable.write_bytes(b"")
            tool = root / "yt-dlp.exe"
            tool.write_bytes(b"")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(executable)):
                self.assertEqual(runtime.tool("yt-dlp"), str(tool.resolve()))


if __name__ == "__main__":
    unittest.main()
