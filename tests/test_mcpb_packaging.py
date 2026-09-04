import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "installer" / "mcpb" / "manifest.json"


class McpbPackagingTest(unittest.TestCase):
    def test_binary_manifest_has_safe_key_and_local_data_configuration(self):
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(payload["manifest_version"], "0.3")
        self.assertEqual(payload["server"]["type"], "binary")
        self.assertEqual(
            payload["server"]["entry_point"], "server/cueprecise-mcp.exe"
        )
        self.assertEqual(
            payload["server"]["mcp_config"]["env"]["GEMINI_API_KEY"],
            "${user_config.gemini_api_key}",
        )
        self.assertTrue(payload["user_config"]["gemini_api_key"]["sensitive"])
        self.assertEqual(
            payload["user_config"]["data_directory"]["default"],
            "${HOME}/.cueprecise/data",
        )
        self.assertEqual(payload["compatibility"]["platforms"], ["win32"])

    def test_poc_does_not_claim_to_bundle_external_executables(self):
        script = (ROOT / "installer" / "mcpb" / "build_mcpb_poc.ps1").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('Copy-Item -LiteralPath $ffmpeg', script)
        self.assertNotIn('Copy-Item -LiteralPath $ytDlp', script)

    def test_claude_release_bundle_contains_runtime_tools_and_licenses(self):
        script = (ROOT / "installer" / "mcpb" / "build_mcpb.ps1").read_text(
            encoding="utf-8"
        )
        notices = (ROOT / "installer" / "mcpb" / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )

        for filename in ("cueprecise-mcp.exe", "yt-dlp.exe", "ffmpeg.exe", "ffprobe.exe"):
            self.assertIn(filename, script)
        self.assertIn("FFmpeg-LICENSE.txt", script)
        self.assertIn("3C3DD10B1F4E3663F38A1FB574D7734F7606DBB758EAEC2E4F7D398B9ACDF78A", script)
        self.assertIn('$_.Name -ne "ffplay.exe"', script)
        self.assertIn("github.com/yt-dlp/yt-dlp", notices)
        self.assertIn("github.com/BtbN/FFmpeg-Builds", notices)

    def test_release_bundle_integrates_yt_dlp_without_a_second_pyinstaller_app(self):
        script = (ROOT / "installer" / "mcpb" / "build_mcpb.ps1").read_text(
            encoding="utf-8"
        )
        entrypoint = (ROOT / "installer" / "mcpb" / "mcpb_entrypoint.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("--collect-all yt_dlp", script)
        self.assertIn("yt_dlp_shim.cs", script)
        self.assertNotIn('PyInstaller failed: yt-dlp', script)
        self.assertIn('sys.argv[1] == "--yt-dlp"', entrypoint)


if __name__ == "__main__":
    unittest.main()
