import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "installer" / "mcpb" / "manifest.json"
BUILD_SCRIPT = ROOT / "installer" / "mcpb" / "build_mcpb.ps1"
NOTICES = ROOT / "installer" / "mcpb" / "THIRD_PARTY_NOTICES.md"
POC_SCRIPT = ROOT / "installer" / "mcpb" / "build_mcpb_poc.ps1"


class McpbPackagingTest(unittest.TestCase):
    def test_binary_manifest_has_safe_key_and_local_data_configuration(self):
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(payload["manifest_version"], "0.3")
        project_version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml")
                                    .read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual(payload["version"], project_version)
        self.assertEqual(payload["server"]["type"], "binary")
        self.assertEqual(
            payload["server"]["entry_point"], "py/python.exe"
        )
        self.assertEqual(
            payload["server"]["mcp_config"]["command"],
            "${__dirname}/py/python.exe",
        )
        self.assertEqual(
            payload["server"]["mcp_config"]["args"],
            ["-m", "mcp_server", "--bundle-root", "${user_config.data_directory}"],
        )
        self.assertEqual(
            payload["server"]["mcp_config"]["env"]["GEMINI_API_KEY"],
            "${user_config.gemini_api_key}",
        )
        self.assertTrue(payload["user_config"]["gemini_api_key"]["sensitive"])
        key_description = payload["user_config"]["gemini_api_key"]["description"]
        self.assertIn("https://aistudio.google.com/api-keys", key_description)
        self.assertIn("Keep this key private", key_description)
        self.assertIn("delete it anytime", key_description)
        self.assertEqual(
            payload["user_config"]["data_directory"]["default"],
            "${HOME}/.cueprecise/data",
        )
        self.assertEqual(payload["compatibility"]["platforms"], ["win32"])

    def test_poc_does_not_claim_to_bundle_external_executables(self):
        script = POC_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn('Copy-Item -LiteralPath $ffmpeg', script)
        self.assertNotIn('Copy-Item -LiteralPath $ytDlp', script)

    def test_release_script_no_longer_builds_a_pyinstaller_binary(self):
        """The MCPB used to ship a PyInstaller onefile exe and a C# shim; SAC
        blocked both on a real machine, so the build must not produce them."""
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("run --with pyinstaller", script)
        self.assertNotIn("--onefile", script)
        self.assertNotIn("csc.exe", script)
        self.assertNotIn("yt_dlp_shim.cs", script)
        self.assertFalse((ROOT / "installer" / "mcpb" / "yt_dlp_shim.cs").exists())
        self.assertFalse((ROOT / "installer" / "mcpb" / "mcpb_entrypoint.py").exists())

    def test_release_script_pins_and_verifies_the_embeddable_python(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("python.org/ftp/python", script)
        self.assertRegex(script, r'\$pythonVersion = "3\.13\.\d+"')
        self.assertRegex(script, r'\$pythonSha256 = "[0-9A-F]{64}"')
        self.assertIn("Get-FileHash", script)
        # Every build re-verifies the cached download, it does not trust it blindly.
        self.assertIn("failed SHA-256 verification", script)
        self.assertIn("python313._pth", script)
        self.assertIn('Join-Path $pythonDir "lib"', script)
        self.assertIn("import site", script)

    def test_release_script_installs_locked_dependencies_with_uv_target(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("uv export", script)
        self.assertIn("--no-emit-project", script)
        self.assertIn("uv pip install", script)
        self.assertIn("--target", script)
        self.assertIn("--python-platform x86_64-pc-windows-msvc", script)
        self.assertIn("--only-binary :all:", script)

    def test_release_script_copies_only_app_source_not_tests(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('Filter "*.py"', script)
        self.assertIn('Join-Path $stage "app"', script)
        # Only the "src" tree is copied in; the tests/ directory is never named
        # as a copy source (a leftover reference to "tests" would ship test code).
        copy_sources = re.findall(r'Join-Path \$repo "([^"]+)"', script)
        self.assertNotIn("tests", copy_sources)
        self.assertIn("src", copy_sources)

    def test_release_script_pins_ffmpeg_and_takes_only_the_two_programs(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("GyanD/codexffmpeg", script)
        self.assertRegex(script, r'\$ffmpegVersion = "9\.0\.1"')
        self.assertRegex(script, r'\$ffmpegSha256 = "[0-9A-F]{64}"')
        for filename in ("ffmpeg.exe", "ffprobe.exe"):
            self.assertIn(filename, script)
        # ffplay.exe is present in the pinned archive but must never be copied out.
        copied = re.findall(r'Copy-Item -LiteralPath \$source -Destination \$pythonDir', script)
        self.assertTrue(copied)
        self.assertNotIn('"ffplay.exe"', script)
        self.assertIn("FFmpeg-LICENSE.txt", script)

    def test_notices_cover_python_ffmpeg_and_yt_dlp(self):
        notices = NOTICES.read_text(encoding="utf-8")

        self.assertIn("python.org", notices)
        self.assertIn("3.13.15", notices)
        self.assertIn("Python Software Foundation", notices)
        self.assertIn("github.com/yt-dlp/yt-dlp", notices)
        self.assertIn("-m yt_dlp", notices)
        self.assertIn("GyanD/codexffmpeg/releases/tag/9.0.1", notices)
        self.assertIn("ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz", notices)
        self.assertIn("GNU General Public License version 3", notices)
        self.assertIn("separate child processes", notices)

    def test_notices_ffmpeg_pin_matches_the_build_script(self):
        """The notice must name the build that is actually bundled."""
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        notices = NOTICES.read_text(encoding="utf-8")
        version = re.search(r'\$ffmpegVersion = "([^"]+)"', script).group(1)

        self.assertIn("codexffmpeg/releases/tag/%s" % version, notices)

    def test_notices_python_pin_matches_the_build_script(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        notices = NOTICES.read_text(encoding="utf-8")
        version = re.search(r'\$pythonVersion = "([^"]+)"', script).group(1)

        self.assertIn(version, notices)

    def test_legacy_frozen_build_can_still_run_yt_dlp_via_itself(self):
        """The old PyInstaller setup.exe (installer/build_windows.ps1) is out of
        scope here, but src/mcp_server.py must keep answering `--yt-dlp` so that
        src/runtime.py's frozen-mode command() has something to call into."""
        mcp_server = (ROOT / "src" / "mcp_server.py").read_text(encoding="utf-8")

        self.assertIn('sys.argv[1] == "--yt-dlp"', mcp_server)


if __name__ == "__main__":
    unittest.main()
