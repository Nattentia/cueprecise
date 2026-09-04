"""Windows 보호 자격 증명 저장소의 경계 테스트."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import credential_store


class CredentialStoreTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI 전용")
    def test_dpapi_round_trip_does_not_store_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.dpapi"
            secret = "AIza" + "z" * 36
            credential_store.store(secret, path)
            self.assertNotIn(secret.encode(), path.read_bytes())
            self.assertEqual(credential_store.load(path), secret)

    def test_inline_key_wins_for_claude_mcpb_compatibility(self) -> None:
        self.assertEqual(
            credential_store.resolve({"GEMINI_API_KEY": "inline",
                                      credential_store.CREDENTIAL_ENV: "missing"}),
            "inline")

    @unittest.skipUnless(sys.platform == "win32", "Windows 경로 전용")
    def test_missing_profile_is_a_controlled_error(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(credential_store.CredentialError):
                credential_store.default_path()


if __name__ == "__main__":
    unittest.main()
