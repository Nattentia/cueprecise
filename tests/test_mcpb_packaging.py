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


if __name__ == "__main__":
    unittest.main()
