"""이름과 설치 표면이 CuePrecise 하나로 고정돼 있는지 검사한다 (CONTRACT 15절)."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import configuration
import credential_store
import installer_support
import mcp_server


VALID_KEY = "AIza" + "A" * 35
EXISTING_ENTRY = {
    "command": "C:/Users/x/AppData/Local/Programs/CuePrecise/cueprecise-mcp.exe",
    "args": ["--bundle-root", "C:/Users/x/.cueprecise/data"],
    "env": {"GEMINI_API_KEY": VALID_KEY, "PATH": "C:/ffmpeg/bin"},
}


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ReinstallTest(unittest.TestCase):
    """재설치는 이미 연결된 설정을 보존한 채 실행 파일만 다시 가리킨다."""

    def _migrated(self, tmp: Path, extra: dict | None = None) -> dict:
        config = tmp / "claude.json"
        entry = dict(EXISTING_ENTRY)
        # 실제로 존재하는 경로를 등록해 둔 상태를 흉내낸다.
        self.data = tmp / ".cueprecise" / "data"
        self.data.mkdir(parents=True)
        entry["args"] = ["--bundle-root", str(self.data)]
        servers = {"cueprecise": entry}
        servers.update(extra or {})
        _write(config, {"theme": "dark", "mcpServers": servers})
        install = tmp / "app"
        install.mkdir()
        (install / "cueprecise-mcp.exe").write_bytes(b"")
        self.credential = tmp / "key.dpapi"
        installer_support.migrate(install, config_path=config,
                                  credential_path=self.credential)
        return _read(config)

    def test_entry_points_at_the_newly_installed_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = self._migrated(root)
            self.assertEqual(list(saved["mcpServers"]), ["cueprecise"])
            self.assertEqual(Path(saved["mcpServers"]["cueprecise"]["command"]),
                             (root / "app" / "cueprecise-mcp.exe").resolve())

    def test_api_key_is_preserved_without_being_supplied_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = self._migrated(Path(tmp))
            environment = saved["mcpServers"]["cueprecise"]["env"]
            self.assertNotIn("GEMINI_API_KEY", environment)
            self.assertEqual(environment[credential_store.CREDENTIAL_ENV],
                             str(self.credential.resolve()))
            self.assertEqual(credential_store.load(self.credential), VALID_KEY)

    def test_other_servers_and_settings_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = self._migrated(Path(tmp), extra={"other": {"command": "keep", "args": ["x"]}})
            self.assertEqual(saved["mcpServers"]["other"], {"command": "keep", "args": ["x"]})
            self.assertEqual(saved["theme"], "dark")

    def test_bundle_root_of_existing_entry_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = self._migrated(Path(tmp))
            arguments = saved["mcpServers"]["cueprecise"]["args"]
            self.assertIn("--bundle-root", arguments)
            kept = Path(arguments[arguments.index("--bundle-root") + 1])
            self.assertEqual(kept, self.data.resolve())

    def test_repeated_setup_leaves_exactly_one_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            _write(config, {"mcpServers": {"cueprecise": dict(EXISTING_ENTRY)}})
            for _ in range(3):
                configuration.setup_claude(config, root / "data", api_key=None,
                                           server_command="python", server_args=["-m", "mcp_server"])
            servers = _read(config)["mcpServers"]
            self.assertEqual(list(servers), ["cueprecise"])

    def test_config_is_backed_up_before_being_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            _write(config, {"mcpServers": {"cueprecise": dict(EXISTING_ENTRY)}})
            result = configuration.setup_claude(config, root / "data", api_key=None,
                                                server_command="python", server_args=[])
            backup = Path(result["backup"])
            self.assertTrue(backup.is_file())
            text = backup.read_text(encoding="utf-8")
            saved = json.loads(text)["mcpServers"]["cueprecise"]
            # 되돌리는 데 필요한 것은 전부 남아 있어야 한다. 비밀값만 빠진다 —
            # 백업은 복구 수단이지 폐기한 키의 보관소가 아니다.
            expected = {name: (value if name != "env"
                               else {k: v for k, v in value.items()
                                     if k not in configuration.SECRET_ENV_NAMES})
                        for name, value in EXISTING_ENTRY.items()}
            self.assertEqual(saved, expected)
            self.assertNotIn(EXISTING_ENTRY["env"]["GEMINI_API_KEY"], text)

    def test_foreign_entry_sharing_the_name_is_not_taken_over(self) -> None:
        foreign = {"command": "some-other-tool", "args": ["--serve"]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            _write(config, {"mcpServers": {"cueprecise": foreign}})
            with self.assertRaises(configuration.ForeignEntryError):
                configuration.setup_claude(config, root / "data", api_key=None,
                                           server_command="python", server_args=[])
            self.assertEqual(_read(config)["mcpServers"]["cueprecise"], foreign)

    def test_migrate_does_nothing_when_never_connected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            _write(config, {"mcpServers": {"other": {"command": "keep"}}})
            result = installer_support.migrate(root, config_path=config)
            self.assertFalse(result["changed"])
            self.assertEqual(_read(config)["mcpServers"], {"other": {"command": "keep"}})


class UninstallTest(unittest.TestCase):
    def test_removes_only_our_entries(self) -> None:
        foreign = {"command": "some-other-tool"}
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "claude.json"
            _write(config, {"mcpServers": {
                "cueprecise": {"command": "cueprecise-mcp.exe"},
                "other": foreign,
            }})
            result = installer_support.disconnect(config)
            servers = _read(config)["mcpServers"]
            self.assertEqual(list(servers), ["other"])
            self.assertEqual(servers["other"], foreign)
            self.assertEqual(result["removed"], ["cueprecise"])

    def test_keeps_a_foreign_entry_that_merely_shares_the_name(self) -> None:
        foreign = {"command": "some-other-tool", "args": ["--serve"]}
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "claude.json"
            _write(config, {"mcpServers": {"cueprecise": foreign}})
            result = installer_support.disconnect(config)
            self.assertFalse(result["changed"])
            self.assertEqual(_read(config)["mcpServers"]["cueprecise"], foreign)

    def test_uninstall_on_a_config_we_never_touched_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "claude.json"
            original = '{"mcpServers": {"other": {"command": "keep"}}}'
            config.write_text(original, encoding="utf-8")
            self.assertFalse(installer_support.disconnect(config)["changed"])
            self.assertEqual(config.read_text(encoding="utf-8"), original)


class BundleRootTest(unittest.TestCase):
    def test_default_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(configuration.default_bundle_root(home),
                             home / ".cueprecise" / "data")


class EntryPointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_only_the_current_commands_are_registered(self) -> None:
        self.assertEqual(self.pyproject["project"]["scripts"],
                         {"cueprecise": "cueprecise_cli:main",
                          "cueprecise-mcp": "mcp_server:main"})

    def test_distribution_name_is_the_new_one(self) -> None:
        self.assertEqual(self.pyproject["project"]["name"], "cueprecise-mcp")


class McpToolNameTest(unittest.TestCase):
    def test_listed_tools_use_the_new_prefix_only(self) -> None:
        names = [tool["name"] for tool in mcp_server.TOOLS]
        self.assertTrue(all(name.startswith("cueprecise_") for name in names), names)
        self.assertEqual(len(names), len(set(names)))

    def test_server_identifies_itself_with_the_new_name(self) -> None:
        self.assertEqual(mcp_server.SERVER_INFO["name"], "cueprecise")


class InstallerMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (ROOT / "installer" / "cueprecise.iss").read_text(encoding="utf-8")

    def _define(self, name: str) -> str:
        match = re.search(rf'#define {name} "([^"]+)"', self.script)
        self.assertIsNotNone(match, f"{name} 정의를 찾지 못했다")
        return match.group(1)

    def _setting(self, name: str) -> str:
        match = re.search(rf"^{name}=(.+)$", self.script, re.MULTILINE)
        self.assertIsNotNone(match, f"{name} 설정을 찾지 못했다")
        return match.group(1).strip()

    def test_installer_version_matches_the_package_version(self) -> None:
        package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(self._define("MyAppVersion"), package["project"]["version"])

    def test_product_publisher_and_output_file_name(self) -> None:
        self.assertEqual(self._define("MyAppName"), "CuePrecise")
        self.assertEqual(self._define("MyAppPublisher"), "Nattentia")
        self.assertEqual(self._setting("OutputBaseFilename"), "cueprecise-setup")
        self.assertEqual(self._setting("UninstallDisplayName"), "CuePrecise")

    def test_install_directory_uses_the_product_name(self) -> None:
        self.assertEqual(self._setting("DefaultDirName"), r"{localappdata}\Programs\CuePrecise")

    def test_appid_is_unchanged_so_the_old_install_upgrades(self) -> None:
        self.assertEqual(self._setting("AppId"), "{{E5118050-5C8A-47D9-8A61-A4A94C6298ED}")

    def test_bundled_files_use_the_new_executable_names(self) -> None:
        for filename in ("cueprecise-mcp.exe", "cueprecise-onboarding.exe", "yt-dlp.exe"):
            self.assertIn(filename, self.script)

    def test_upgrade_migrates_the_configuration_before_deleting_old_files(self) -> None:
        self.assertIn('Parameters: "--migrate"', self.script)
        self.assertLess(self.script.index("[InstallDelete]"), self.script.index("[Files]"))


class PolicyDocumentTest(unittest.TestCase):
    def test_unsigned_release_status_is_clear(self) -> None:
        for name in ("CODE_SIGNING_POLICY.md", "README.md", "README.ko.md"):
            with self.subTest(document=name):
                text = (ROOT / name).read_text(encoding="utf-8").lower()
                self.assertTrue("unsigned" in text or "서명되지" in text)

    def test_repository_links_point_at_the_new_name(self) -> None:
        for name in ("README.md", "SECURITY.md", "pyproject.toml"):
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(document=name):
                self.assertNotIn("github.com/Nattentia/ytx", text)


if __name__ == "__main__":
    unittest.main()
