"""여러 MCP 클라이언트를 상대하는 레지스트리 테스트.

이름 문자열을 맞추는 테스트가 아니다. 앱이 늘어도 **남의 설정을 깨뜨리지
않는지**, 그리고 Claude Desktop 의 기존 동작이 그대로인지를 본다.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import configuration


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RegistryTest(unittest.TestCase):
    def test_claude_desktop_is_registered_and_lookup_works(self) -> None:
        target = configuration.client_by_key("claude-desktop")
        self.assertIs(target, configuration.CLAUDE_DESKTOP)
        self.assertEqual(target.servers_key, "mcpServers")
        self.assertEqual(target.locate_config(), configuration.default_claude_config())

    def test_unknown_client_names_the_known_ones(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            configuration.client_by_key("nope")
        self.assertIn("claude-desktop", str(caught.exception))

    def test_resolve_clients_defaults_to_detected_and_deduplicates(self) -> None:
        detected = configuration.detected_clients()
        self.assertEqual(configuration.resolve_clients(None), detected)
        self.assertEqual(configuration.resolve_clients([]), detected)
        self.assertEqual(configuration.resolve_clients(["all"]), detected)
        self.assertEqual(
            configuration.resolve_clients(["claude-desktop", " claude-desktop "]),
            [configuration.CLAUDE_DESKTOP])

    def test_detection_does_not_invent_config_files(self) -> None:
        """감지는 읽기만 한다. 설치되지 않은 앱에 파일을 만들지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nothing" / "config.json"
            target = configuration.ClientTarget(
                key="ghost", label="Ghost", locate_config=lambda: missing)
            self.assertFalse(target.is_installed())
            self.assertFalse(missing.exists())
            self.assertFalse(missing.parent.exists())

    def test_detector_overrides_config_presence(self) -> None:
        """CLI 로 붙이는 앱은 설정 파일이 없어도 설치돼 있을 수 있다."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nothing" / "config.json"
            target = configuration.ClientTarget(
                key="cli-app", label="CLI App", locate_config=lambda: missing,
                detector=lambda: True)
            self.assertTrue(target.is_installed())


class ServersKeyTest(unittest.TestCase):
    """VS Code 는 `servers`, 나머지는 `mcpServers` 를 쓴다."""

    def test_setup_writes_under_the_targets_key_and_keeps_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "mcp.json"
            config.write_text(json.dumps({
                "servers": {"other": {"command": "keep"}},
                "inputs": [],
            }), encoding="utf-8")

            result = configuration.setup_file_client(
                config, root / "data", api_key="secret",
                server_command="C:/Python/python.exe", server_args=["-m", "mcp_server"],
                servers_key="servers")

            saved = _read(config)
            self.assertEqual(saved["servers"]["other"]["command"], "keep")
            self.assertEqual(saved["inputs"], [])
            self.assertNotIn("mcpServers", saved)
            entry = saved["servers"]["cueprecise"]
            self.assertEqual(entry["command"], "C:/Python/python.exe")
            self.assertEqual(entry["env"]["GEMINI_API_KEY"], "secret")
            self.assertIsNotNone(result["backup"])

    def test_remove_honours_the_targets_key_and_spares_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            config.write_text(json.dumps({"servers": {
                "cueprecise": {"command": "cueprecise-mcp.exe"},
                "other": {"command": "keep"},
            }}), encoding="utf-8")

            result = configuration.remove_file_client(config, "servers")

            saved = _read(config)
            self.assertNotIn("cueprecise", saved["servers"])
            self.assertEqual(saved["servers"]["other"]["command"], "keep")
            self.assertEqual(result["removed"], ["cueprecise"])

    def test_remove_spares_someone_elses_entry_of_the_same_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "mcp.json"
            original = json.dumps({"servers": {"cueprecise": {"command": "not-ours.exe"}}})
            config.write_text(original, encoding="utf-8")

            result = configuration.remove_file_client(config, "servers")

            self.assertFalse(result["changed"])
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_non_object_servers_value_is_reported_with_its_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "mcp.json"
            config.write_text(json.dumps({"servers": ["wrong"]}), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "servers"):
                configuration.setup_file_client(
                    config, root / "data", api_key=None,
                    server_command="server.exe", server_args=[], servers_key="servers")

    def test_find_managed_entry_reads_the_given_key(self) -> None:
        config = {"servers": {"cueprecise": {"command": "cueprecise-mcp.exe"}}}
        self.assertIsNone(configuration.find_managed_entry(config))
        found = configuration.find_managed_entry(config, "servers")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "cueprecise")


class ClaudeDesktopParityTest(unittest.TestCase):
    """이름만 옮겼다. Claude Desktop 의 동작은 한 글자도 달라지지 않아야 한다."""

    def _write_via(self, config: Path, bundle_root: Path, use_wrapper: bool) -> None:
        arguments = dict(api_key="secret", server_command="C:/Python/python.exe",
                         server_args=["-m", "mcp_server"])
        if use_wrapper:
            configuration.setup_claude(config, bundle_root, **arguments)
        else:
            configuration.setup_file_client(config, bundle_root, **arguments)

    def test_wrapper_and_generic_produce_identical_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}})
            results = []
            for index, use_wrapper in enumerate((True, False)):
                config = root / f"claude-{index}.json"
                config.write_text(seed, encoding="utf-8")
                self._write_via(config, root / "data", use_wrapper)
                results.append(_read(config))
            self.assertEqual(results[0], results[1])

    def test_target_install_and_remove_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            config.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}}}),
                              encoding="utf-8")
            target = configuration.CLAUDE_DESKTOP

            target.install(root / "data", api_key="secret",
                           server_command="cueprecise-mcp.exe", server_args=[],
                           config_path=config)
            self.assertIn("cueprecise", _read(config)["mcpServers"])

            result = target.remove(config_path=config)

            saved = _read(config)
            self.assertTrue(result["changed"])
            self.assertNotIn("cueprecise", saved["mcpServers"])
            self.assertEqual(saved["mcpServers"]["other"]["command"], "keep")

if __name__ == "__main__":
    unittest.main()
