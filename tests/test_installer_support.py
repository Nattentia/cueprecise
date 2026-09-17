"""Windows 설치 화면의 키 처리와 안전한 Claude 연결 테스트."""
from __future__ import annotations

import json
import subprocess
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

    def test_non_frozen_runtime_also_prefers_sibling_executable(self) -> None:
        """MCPB의 임베더블 파이썬은 얼려지지 않았지만 python.exe 옆에 ffmpeg.exe 를 둔다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "python.exe"
            executable.write_bytes(b"")
            tool = root / "ffmpeg.exe"
            tool.write_bytes(b"")
            with mock.patch.object(sys, "frozen", False, create=True), \
                    mock.patch.object(sys, "executable", str(executable)):
                self.assertEqual(runtime.tool("ffmpeg"), str(tool.resolve()))

    def test_falls_back_to_path_name_when_no_sibling_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "python.exe"
            executable.write_bytes(b"")
            with mock.patch.object(sys, "frozen", False, create=True), \
                    mock.patch.object(sys, "executable", str(executable)):
                self.assertEqual(runtime.tool("ffmpeg"), "ffmpeg")


class RuntimeCommandTest(unittest.TestCase):
    def test_sibling_executable_wins(self) -> None:
        """setup.exe 설치본은 yt-dlp.exe 를 서버 옆에 따로 둔다."""
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "cueprecise-mcp.exe"
            server.write_bytes(b"")
            (Path(tmp) / "yt-dlp.exe").write_bytes(b"")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(server)):
                self.assertEqual(runtime.command("yt-dlp"),
                                 [str(Path(tmp).resolve() / "yt-dlp.exe")])

    def test_frozen_build_with_module_recalls_itself_with_a_flag(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "C:/dist/cueprecise-mcp.exe"), \
                mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertEqual(
                runtime.command("yt-dlp"),
                ["C:/dist/cueprecise-mcp.exe", "--yt-dlp"],
            )

    def test_frozen_build_without_module_uses_path_name(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "C:/dist/cueprecise-mcp.exe"), \
                mock.patch("importlib.util.find_spec", return_value=None):
            self.assertEqual(runtime.command("yt-dlp"), ["yt-dlp"])

    def test_non_frozen_with_importable_module_uses_module_flag(self) -> None:
        """MCPB의 임베더블 파이썬은 site-packages 의 yt_dlp 를 -m 으로 부른다."""
        with mock.patch.object(sys, "frozen", False, create=True), \
                mock.patch.object(sys, "executable", "C:/mcpb/py/python.exe"), \
                mock.patch("importlib.util.find_spec", return_value=object()):
            self.assertEqual(
                runtime.command("yt-dlp"),
                ["C:/mcpb/py/python.exe", "-m", "yt_dlp"],
            )

    def test_non_frozen_without_module_falls_back_to_path_name(self) -> None:
        with mock.patch.object(sys, "frozen", False, create=True), \
                mock.patch.object(sys, "executable", "C:/py/python.exe"), \
                mock.patch("importlib.util.find_spec", return_value=None):
            self.assertEqual(runtime.command("yt-dlp"), ["yt-dlp"])


class PythonZipServerDetectionTest(unittest.TestCase):
    """zip 설치본(`py\\python.exe`)도 옛 `cueprecise-mcp.exe` 설치본처럼 찾아야 한다."""

    def test_bundled_server_finds_the_embeddable_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            (install / "py").mkdir()
            python_exe = install / "py" / "python.exe"
            python_exe.write_bytes(b"")
            self.assertEqual(installer_support._bundled_server(install), python_exe.resolve())

    def test_bundled_server_prefers_the_old_exe_when_both_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp)
            exe = install / "cueprecise-mcp.exe"
            exe.write_bytes(b"")
            (install / "py").mkdir()
            (install / "py" / "python.exe").write_bytes(b"")
            self.assertEqual(installer_support._bundled_server(install), exe.resolve())

    def test_bundled_server_is_none_when_neither_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(installer_support._bundled_server(Path(tmp)))

    def test_server_launch_args_uses_module_flag_only_for_python_exe(self) -> None:
        self.assertEqual(installer_support._server_launch_args(Path("C:/x/py/python.exe")),
                         ["-m", "mcp_server"])
        # 대소문자를 가리지 않는다(Windows 파일 시스템 자체가 그렇다).
        self.assertEqual(installer_support._server_launch_args(Path("C:/x/py/Python.EXE")),
                         ["-m", "mcp_server"])
        self.assertEqual(
            installer_support._server_launch_args(Path("C:/x/cueprecise-mcp.exe")), [])

    def test_bundled_ffmpeg_is_found_next_to_python_exe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            server = root / "python.exe"
            server.write_bytes(b"")
            (root / "ffmpeg.exe").write_bytes(b"")
            self.assertEqual(installer_support._bundled_ffmpeg(server), root / "ffmpeg.exe")

    def test_bundled_ffmpeg_is_none_for_the_old_exe_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp).resolve() / "cueprecise-mcp.exe"
            server.write_bytes(b"")
            self.assertIsNone(installer_support._bundled_ffmpeg(server))


class ProbeMcpCommandTest(unittest.TestCase):
    def test_probe_adds_the_module_flag_for_python_exe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "python.exe"
            server.write_bytes(b"")
            response = json.dumps({"result": {"serverInfo": {"name": "cueprecise"}}}) + "\n"
            with mock.patch("installer_support.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, response, "")
                ok, error = installer_support.probe_mcp(server, Path(tmp) / "data", {})
            self.assertTrue(ok)
            self.assertIsNone(error)
            command = run.call_args[0][0]
            self.assertEqual(command[0], str(server))
            self.assertEqual(command[1:3], ["-m", "mcp_server"])
            self.assertEqual(command[3], "--bundle-root")

    def test_probe_calls_the_old_exe_directly_without_a_module_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "cueprecise-mcp.exe"
            server.write_bytes(b"")
            response = json.dumps({"result": {"serverInfo": {"name": "cueprecise"}}}) + "\n"
            with mock.patch("installer_support.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, response, "")
                installer_support.probe_mcp(server, Path(tmp) / "data", {})
            command = run.call_args[0][0]
            self.assertEqual(command, [str(server), "--bundle-root", str(Path(tmp) / "data")])


class ZipInstallConnectTest(unittest.TestCase):
    """zip 설치본(`py\\python.exe` + 옆의 `ffmpeg.exe`)으로 붙이는 경로."""

    def _install(self, root: Path, *, with_ffmpeg: bool = True) -> tuple[Path, Path]:
        install = root / "app"
        (install / "py").mkdir(parents=True)
        python_exe = install / "py" / "python.exe"
        python_exe.write_bytes(b"")
        if with_ffmpeg:
            (install / "py" / "ffmpeg.exe").write_bytes(b"")
        return install, python_exe

    def test_connect_uses_the_python_module_command_and_skips_ffmpeg_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install, python_exe = self._install(root)
            config = root / "claude.json"
            config.write_text("{}", encoding="utf-8")

            with mock.patch("installer_support.ensure_ffmpeg") as ensure, \
                    mock.patch("installer_support.probe_mcp", return_value=(True, None)) as probe:
                installer_support.connect(
                    VALID_KEY, install, config_path=config, bundle_root=root / "data",
                    credential_path=root / "key.dpapi")

            ensure.assert_not_called()
            self.assertEqual(probe.call_args[0][0], python_exe.resolve())

            saved = json.loads(config.read_text(encoding="utf-8"))
            entry = saved["mcpServers"]["cueprecise"]
            self.assertEqual(Path(entry["command"]), python_exe.resolve())
            self.assertEqual(entry["args"][:2], ["-m", "mcp_server"])
            self.assertEqual(entry["args"][2], "--bundle-root")
            # zip 설치본은 python.exe 옆의 ffmpeg.exe 를 runtime.tool() 이 바로 찾으므로
            # PATH 를 끼워 넣지 않는다.
            self.assertNotIn("PATH", entry.get("env", {}))

    def test_connect_falls_back_to_ensure_ffmpeg_without_the_bundled_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install, _python_exe = self._install(root, with_ffmpeg=False)
            config = root / "claude.json"
            config.write_text("{}", encoding="utf-8")
            ffmpeg_bin = root / "ffmpeg"
            ffmpeg_bin.mkdir()

            with mock.patch("installer_support.ensure_ffmpeg",
                            return_value=(ffmpeg_bin, None)) as ensure, \
                    mock.patch("installer_support.probe_mcp", return_value=(True, None)):
                installer_support.connect(
                    VALID_KEY, install, config_path=config, bundle_root=root / "data",
                    credential_path=root / "key.dpapi")

            ensure.assert_called_once()
            saved = json.loads(config.read_text(encoding="utf-8"))
            entry = saved["mcpServers"]["cueprecise"]
            self.assertTrue(entry["env"]["PATH"].startswith(str(ffmpeg_bin)))


if __name__ == "__main__":
    unittest.main()
