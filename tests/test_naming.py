"""이름을 CuePrecise 로 옮길 때 기존 사용자를 깨뜨리지 않는지 검사한다 (CONTRACT 15절)."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import configuration
import cueprecise_cli
import installer_support
import mcp_server


VALID_KEY = "AIza" + "A" * 35
LEGACY_ENTRY = {
    "command": "C:/Users/x/AppData/Local/Programs/ytx/ytx-mcp.exe",
    "args": ["--bundle-root", "C:/Users/x/.ytx/data"],
    "env": {"GEMINI_API_KEY": VALID_KEY, "PATH": "C:/ffmpeg/bin"},
}


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class LegacyConfigMigrationTest(unittest.TestCase):
    """기존 ytx 항목을 CuePrecise 로 옮긴다."""

    def _migrated(self, tmp: Path, extra: dict | None = None) -> dict:
        config = tmp / "claude.json"
        legacy = dict(LEGACY_ENTRY)
        # 실제로 존재하는 경로를 등록해 둔 상태를 흉내낸다.
        self.legacy_data = tmp / ".ytx" / "data"
        self.legacy_data.mkdir(parents=True)
        legacy["args"] = ["--bundle-root", str(self.legacy_data)]
        servers = {"ytx": legacy}
        servers.update(extra or {})
        _write(config, {"theme": "dark", "mcpServers": servers})
        install = tmp / "app"
        install.mkdir()
        (install / "cueprecise-mcp.exe").write_bytes(b"")
        installer_support.migrate(install, config_path=config)
        return _read(config)

    def test_legacy_entry_moves_to_new_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = self._migrated(Path(tmp))
            self.assertIn("cueprecise", saved["mcpServers"])
            self.assertNotIn("ytx", saved["mcpServers"])

    def test_api_key_is_preserved_without_being_supplied_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saved = self._migrated(Path(tmp))
            self.assertEqual(saved["mcpServers"]["cueprecise"]["env"]["GEMINI_API_KEY"], VALID_KEY)

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
            self.assertEqual(kept, self.legacy_data.resolve())

    def test_repeated_setup_leaves_exactly_one_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            _write(config, {"mcpServers": {"ytx": dict(LEGACY_ENTRY)}})
            for _ in range(3):
                configuration.setup_claude(config, root / "data", api_key=None,
                                           server_command="python", server_args=["-m", "mcp_server"])
            servers = _read(config)["mcpServers"]
            self.assertEqual(list(servers), ["cueprecise"])

    def test_config_is_backed_up_before_being_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            _write(config, {"mcpServers": {"ytx": dict(LEGACY_ENTRY)}})
            result = configuration.setup_claude(config, root / "data", api_key=None,
                                                server_command="python", server_args=[])
            backup = Path(result["backup"])
            self.assertTrue(backup.is_file())
            self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["mcpServers"]["ytx"],
                             LEGACY_ENTRY)

    def test_foreign_entry_named_ytx_is_not_taken_over(self) -> None:
        foreign = {"command": "some-other-tool", "args": ["--serve"]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            _write(config, {"mcpServers": {"ytx": foreign}})
            configuration.setup_claude(config, root / "data", api_key=None,
                                       server_command="python", server_args=[])
            servers = _read(config)["mcpServers"]
            self.assertEqual(servers["ytx"], foreign)
            self.assertNotIn("GEMINI_API_KEY", servers["cueprecise"].get("env", {}))

    def test_migrate_does_nothing_when_never_connected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            _write(config, {"mcpServers": {"other": {"command": "keep"}}})
            result = installer_support.migrate(root, config_path=config)
            self.assertFalse(result["changed"])
            self.assertEqual(_read(config)["mcpServers"], {"other": {"command": "keep"}})


class UninstallTest(unittest.TestCase):
    def test_removes_both_names_but_only_our_entries(self) -> None:
        foreign = {"command": "some-other-tool"}
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "claude.json"
            _write(config, {"mcpServers": {
                "cueprecise": {"command": "cueprecise-mcp.exe"},
                "ytx": dict(LEGACY_ENTRY),
                "other": foreign,
            }})
            result = installer_support.disconnect(config)
            servers = _read(config)["mcpServers"]
            self.assertEqual(list(servers), ["other"])
            self.assertEqual(servers["other"], foreign)
            self.assertEqual(sorted(result["removed"]), ["cueprecise", "ytx"])

    def test_keeps_a_foreign_entry_that_merely_shares_the_name(self) -> None:
        foreign = {"command": "some-other-tool", "args": ["--serve"]}
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "claude.json"
            _write(config, {"mcpServers": {"ytx": foreign}})
            result = installer_support.disconnect(config)
            self.assertFalse(result["changed"])
            self.assertEqual(_read(config)["mcpServers"]["ytx"], foreign)

    def test_uninstall_on_a_config_we_never_touched_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "claude.json"
            original = '{"mcpServers": {"other": {"command": "keep"}}}'
            config.write_text(original, encoding="utf-8")
            self.assertFalse(installer_support.disconnect(config)["changed"])
            self.assertEqual(config.read_text(encoding="utf-8"), original)


class BundleRootTest(unittest.TestCase):
    """이름이 바뀌었다고 사용자 자료를 옮기지 않는다."""

    def test_existing_legacy_data_directory_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".ytx" / "data").mkdir(parents=True)
            self.assertEqual(configuration.default_bundle_root(home), home / ".ytx" / "data")

    def test_new_installs_use_the_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(configuration.default_bundle_root(home), home / ".cueprecise" / "data")

    def test_new_directory_wins_when_both_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".ytx" / "data").mkdir(parents=True)
            (home / ".cueprecise" / "data").mkdir(parents=True)
            self.assertEqual(configuration.default_bundle_root(home), home / ".cueprecise" / "data")


class EntryPointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_new_and_legacy_commands_are_both_registered(self) -> None:
        scripts = self.pyproject["project"]["scripts"]
        self.assertEqual(scripts["cueprecise"], "cueprecise_cli:main")
        self.assertEqual(scripts["cueprecise-mcp"], "mcp_server:main")
        self.assertEqual(scripts["ytx"], scripts["cueprecise"])
        self.assertEqual(scripts["ytx-mcp"], scripts["cueprecise-mcp"])

    def test_distribution_name_is_the_new_one(self) -> None:
        self.assertEqual(self.pyproject["project"]["name"], "cueprecise-mcp")

    def test_legacy_command_still_runs_and_announces_the_new_name(self) -> None:
        for legacy, replacement in (("ytx", "cueprecise"), ("ytx-mcp", "cueprecise-mcp")):
            with self.subTest(legacy=legacy):
                with mock.patch("sys.stderr") as stderr:
                    self.assertEqual(cueprecise_cli.warn_if_deprecated_alias(f"/usr/bin/{legacy}"),
                                     legacy)
                written = "".join(call.args[0] for call in stderr.write.call_args_list)
                self.assertIn(replacement, written)

    def test_new_command_says_nothing(self) -> None:
        self.assertIsNone(cueprecise_cli.warn_if_deprecated_alias("/usr/bin/cueprecise"))


class McpToolNameTest(unittest.TestCase):
    def test_listed_tools_use_the_new_prefix_only(self) -> None:
        names = [tool["name"] for tool in mcp_server.TOOLS]
        self.assertTrue(all(name.startswith("cueprecise_") for name in names), names)
        self.assertEqual(len(names), len(set(names)))

    def test_legacy_tool_names_still_reach_the_same_tool(self) -> None:
        for tool in mcp_server.TOOLS:
            legacy = "ytx_" + tool["name"][len("cueprecise_"):]
            with self.subTest(tool=tool["name"]):
                self.assertEqual(mcp_server.canonical_tool_name(legacy), tool["name"])

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
        self.assertNotIn("dist\\windows\\ytx-mcp.exe", self.script)

    def test_upgrade_migrates_the_configuration_before_deleting_old_files(self) -> None:
        self.assertIn('Parameters: "--migrate"', self.script)
        self.assertLess(self.script.index("[InstallDelete]"), self.script.index("[Files]"))


class PolicyDocumentTest(unittest.TestCase):
    def test_signpath_attribution_is_present_verbatim(self) -> None:
        required = ("Free code signing provided by SignPath.io, "
                    "certificate by SignPath Foundation")
        for name in ("CODE_SIGNING_POLICY.md", "README.md"):
            with self.subTest(document=name):
                self.assertIn(required, (ROOT / name).read_text(encoding="utf-8"))

    def test_repository_links_point_at_the_new_name(self) -> None:
        for name in ("README.md", "SECURITY.md", "pyproject.toml"):
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(document=name):
                self.assertNotIn("github.com/Nattentia/ytx", text)


if __name__ == "__main__":
    unittest.main()
