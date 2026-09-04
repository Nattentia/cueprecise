"""여러 앱을 한 번에 붙이는 입구 테스트.

레지스트리가 아니라 **사용자가 실제로 지나는 길**을 본다. 앱을 찾고, 고르고,
하나가 실패해도 나머지를 붙이고, 무엇이 되고 무엇이 안 됐는지 말하는가.
"""
from __future__ import annotations

import dataclasses
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import configuration
import credential_store
import cueprecise_cli
import installer_support


VALID_KEY = "AIza" + "A" * 35


def _file_target(key: str, path: Path) -> configuration.ClientTarget:
    return configuration.ClientTarget(
        key=key, label=key.upper(), locate_config=lambda: path, detector=lambda: True)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ConnectClientsTest(unittest.TestCase):
    def _install(self, root: Path) -> Path:
        install = root / "app"
        install.mkdir()
        (install / "cueprecise-mcp.exe").write_bytes(b"test")
        return install

    def _ready(self, root: Path):
        ffmpeg = root / "ffmpeg"
        ffmpeg.mkdir(exist_ok=True)
        return (mock.patch("installer_support.ensure_ffmpeg", return_value=(ffmpeg, None)),
                mock.patch("installer_support.probe_mcp", return_value=(True, None)))

    def test_connects_every_chosen_app_in_one_go(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = [_file_target(name, root / f"{name}.json") for name in ("one", "two")]
            ffmpeg, probe = self._ready(root)
            with ffmpeg, probe:
                result = installer_support.connect_clients(
                    VALID_KEY, self._install(root), targets=targets,
                    bundle_root=root / "data", credential_path=root / "key.dpapi")

            self.assertEqual([item["key"] for item in result["connected"]], ["one", "two"])
            self.assertEqual(result["failed"], [])
            for target in targets:
                entry = _read(target.locate_config())["mcpServers"]["cueprecise"]
                if sys.platform == "win32":
                    self.assertNotIn("GEMINI_API_KEY", entry["env"])
                    self.assertEqual(entry["env"][credential_store.CREDENTIAL_ENV],
                                     str((root / "key.dpapi").resolve()))
                else:
                    self.assertEqual(entry["env"]["GEMINI_API_KEY"], VALID_KEY)
            if sys.platform == "win32":
                self.assertEqual(credential_store.load(root / "key.dpapi"), VALID_KEY)

    def test_one_broken_app_does_not_stop_the_others(self) -> None:
        """앱마다 사정이 다르다. 하나가 막혔다고 멀쩡한 앱까지 포기하지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = _file_target("good", root / "good.json")
            stranger = root / "taken.json"
            stranger.write_text(
                json.dumps({"mcpServers": {"cueprecise": {"command": "not-ours.exe"}}}),
                encoding="utf-8")
            blocked = _file_target("blocked", stranger)

            ffmpeg, probe = self._ready(root)
            with ffmpeg, probe:
                result = installer_support.connect_clients(
                    VALID_KEY, self._install(root), targets=[blocked, good],
                    bundle_root=root / "data", credential_path=root / "key.dpapi")

            self.assertEqual([item["key"] for item in result["connected"]], ["good"])
            self.assertEqual([item["key"] for item in result["failed"]], ["blocked"])
            self.assertIn("남의 항목", result["failed"][0]["reason"])
            # 막힌 앱의 설정은 손대지 않았다.
            self.assertEqual(_read(stranger)["mcpServers"]["cueprecise"]["command"],
                             "not-ours.exe")

    def test_no_app_found_is_a_failure_not_a_quiet_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "찾지 못했습니다"):
                installer_support.connect_clients(
                    VALID_KEY, self._install(root), targets=[], bundle_root=root / "data")

    def test_every_app_failing_is_reported_as_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            taken = root / "taken.json"
            taken.write_text(
                json.dumps({"mcpServers": {"cueprecise": {"command": "not-ours.exe"}}}),
                encoding="utf-8")
            ffmpeg, probe = self._ready(root)
            with ffmpeg, probe:
                with self.assertRaisesRegex(RuntimeError, "남의 항목"):
                    installer_support.connect_clients(
                        VALID_KEY, self._install(root),
                        targets=[_file_target("blocked", taken)], bundle_root=root / "data",
                        credential_path=root / "key.dpapi")
            self.assertFalse((root / "key.dpapi").exists())

    def test_tools_are_checked_once_not_once_per_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = [_file_target(name, root / f"{name}.json")
                       for name in ("one", "two", "three")]
            ffmpeg = root / "ffmpeg"
            ffmpeg.mkdir()
            with mock.patch("installer_support.ensure_ffmpeg",
                            return_value=(ffmpeg, None)) as ensure, \
                    mock.patch("installer_support.probe_mcp",
                               return_value=(True, None)) as probe:
                installer_support.connect_clients(
                    VALID_KEY, self._install(root), targets=targets,
                    bundle_root=root / "data", credential_path=root / "key.dpapi")
            self.assertEqual(ensure.call_count, 1)
            self.assertEqual(probe.call_count, 1)

    def test_the_old_single_app_entry_point_still_answers_the_same(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            config.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}}}),
                              encoding="utf-8")
            ffmpeg, probe = self._ready(root)
            with ffmpeg, probe:
                result = installer_support.connect(
                    VALID_KEY, self._install(root), config_path=config,
                    bundle_root=root / "data", credential_path=root / "key.dpapi")
            self.assertTrue(result["connection_tested"])
            self.assertEqual(result["server_key"], "cueprecise")
            self.assertEqual(_read(config)["mcpServers"]["other"]["command"], "keep")


@unittest.skipUnless(sys.platform == "win32", "Windows DPAPI 이관 전용")
class MigrateClientsTest(unittest.TestCase):
    def _install(self, root: Path) -> Path:
        install = root / "app"
        install.mkdir()
        (install / "cueprecise-mcp.exe").write_bytes(b"test")
        return install

    def test_moves_one_plaintext_key_out_of_every_connected_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = [_file_target(name, root / f"{name}.json")
                       for name in ("one", "two")]
            for target in targets:
                target.locate_config().write_text(json.dumps({"mcpServers": {
                    "cueprecise": {"command": "cueprecise-mcp.exe",
                                   "args": ["--bundle-root", str(root / "data")],
                                   "env": {"GEMINI_API_KEY": VALID_KEY,
                                           "KEEP": "yes"}}}}), encoding="utf-8")

            result = installer_support.migrate_clients(
                self._install(root), targets=targets,
                credential_path=root / "key.dpapi")

            self.assertTrue(result["changed"])
            self.assertEqual(len(result["migrated"]), 2)
            self.assertEqual(credential_store.load(root / "key.dpapi"), VALID_KEY)
            for target in targets:
                entry = _read(target.locate_config())["mcpServers"]["cueprecise"]
                self.assertNotIn("GEMINI_API_KEY", entry["env"])
                self.assertEqual(entry["env"]["KEEP"], "yes")
                self.assertEqual(entry["env"][credential_store.CREDENTIAL_ENV],
                                 str((root / "key.dpapi").resolve()))
                for backup in root.glob(f"{target.locate_config().name}.*.bak"):
                    self.assertNotIn(VALID_KEY, backup.read_text(encoding="utf-8"))

    def test_different_keys_are_not_silently_combined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = [_file_target(name, root / f"{name}.json")
                       for name in ("one", "two")]
            for index, target in enumerate(targets):
                target.locate_config().write_text(json.dumps({"mcpServers": {
                    "cueprecise": {"command": "cueprecise-mcp.exe",
                                   "args": ["--bundle-root", str(root / "data")],
                                   "env": {"GEMINI_API_KEY": VALID_KEY + str(index)}}}}),
                    encoding="utf-8")

            before = [target.locate_config().read_text(encoding="utf-8") for target in targets]
            result = installer_support.migrate_clients(
                self._install(root), targets=targets,
                credential_path=root / "key.dpapi")

            self.assertFalse(result["changed"])
            self.assertIn("서로 다른", result["reason"])
            self.assertFalse((root / "key.dpapi").exists())
            self.assertEqual(before, [target.locate_config().read_text(encoding="utf-8")
                                      for target in targets])

    def test_failed_cli_style_migration_restores_the_original_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.json"
            original = json.dumps({"mcpServers": {"cueprecise": {
                "command": "cueprecise-mcp.exe",
                "args": ["--bundle-root", str(root / "data")],
                "env": {"GEMINI_API_KEY": VALID_KEY}}}})
            path.write_text(original, encoding="utf-8")

            class DestructiveFailure:
                key = "broken-cli"
                label = "Broken CLI"
                servers_key = "mcpServers"

                @staticmethod
                def locate_config():
                    return path

                @staticmethod
                def install(*_args, **_kwargs):
                    path.write_text("partially changed", encoding="utf-8")
                    raise RuntimeError("add failed")

            result = installer_support.migrate_clients(
                self._install(root), targets=[DestructiveFailure()],
                credential_path=root / "key.dpapi")

            self.assertFalse(result["changed"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertFalse((root / "key.dpapi").exists())


class DisconnectClientsTest(unittest.TestCase):
    def test_removes_from_every_app_and_survives_a_broken_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ours = json.dumps({"mcpServers": {
                "cueprecise": {"command": "cueprecise-mcp.exe"},
                "other": {"command": "keep"}}})
            first, second = root / "a.json", root / "b.json"
            first.write_text(ours, encoding="utf-8")
            second.write_text(ours, encoding="utf-8")
            broken = root / "broken.json"
            broken.write_text("not json", encoding="utf-8")

            result = installer_support.disconnect_clients(targets=[
                _file_target("a", first), _file_target("broken", broken),
                _file_target("b", second)])

            self.assertEqual([item["key"] for item in result["removed"]], ["a", "b"])
            self.assertEqual([item["key"] for item in result["failed"]], ["broken"])
            self.assertTrue(result["changed"])
            for path in (first, second):
                self.assertNotIn("cueprecise", _read(path)["mcpServers"])
                self.assertEqual(_read(path)["mcpServers"]["other"]["command"], "keep")
            self.assertEqual(broken.read_text(encoding="utf-8"), "not json")


class ClientReportTest(unittest.TestCase):
    def test_reports_one_row_per_registered_app(self) -> None:
        rows = cueprecise_cli.client_report()
        self.assertEqual([row["key"] for row in rows],
                         [target.key for target in configuration.CLIENTS])
        for row in rows:
            self.assertIn("installed", row)
            self.assertIn("connected", row)

    def test_a_broken_config_does_not_hide_the_other_apps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken = root / "broken.json"
            broken.write_text("not json", encoding="utf-8")
            connected = root / "good.json"
            connected.write_text(json.dumps({"mcpServers": {"cueprecise": {
                "command": "cueprecise-mcp.exe"}}}), encoding="utf-8")
            with mock.patch.object(configuration, "CLIENTS",
                                   (_file_target("broken", broken),
                                    _file_target("good", connected))):
                rows = cueprecise_cli.client_report()
            self.assertIn("error", rows[0])
            self.assertFalse(rows[0]["connected"])
            self.assertTrue(rows[1]["connected"])

    def test_doctor_passes_when_any_app_is_connected(self) -> None:
        """Claude Desktop 이 아니어도 어딘가 붙어 있으면 쓸 수 있다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elsewhere = root / "codexish.json"
            elsewhere.write_text(json.dumps({"mcpServers": {"cueprecise": {
                "command": "cueprecise-mcp.exe"}}}), encoding="utf-8")
            absent = root / "no-claude.json"
            with mock.patch("cueprecise_cli.shutil.which", return_value="tool"), \
                    mock.patch.object(configuration, "CLIENTS",
                                      (_file_target("elsewhere", elsewhere),)):
                checks, ok = cueprecise_cli.doctor(absent)
            self.assertTrue(ok)
            self.assertFalse(checks["claude_config"]["ok"])
            self.assertTrue(checks["clients"][0]["connected"])

    def test_doctor_fails_when_nothing_is_connected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("cueprecise_cli.shutil.which", return_value="tool"), \
                    mock.patch.object(configuration, "CLIENTS",
                                      (_file_target("nowhere", root / "none.json"),)):
                _checks, ok = cueprecise_cli.doctor(root / "no-claude.json")
            self.assertFalse(ok)


class SetupCommandTest(unittest.TestCase):
    def _run(self, argv: list[str], clients: tuple) -> int:
        with mock.patch.object(configuration, "CLIENTS", clients):
            return cueprecise_cli._setup_main(argv)

    def test_setup_without_a_client_uses_every_detected_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = tuple(_file_target(name, root / f"{name}.json")
                            for name in ("one", "two"))
            with mock.patch.object(cueprecise_cli.sys, "stdin", io.StringIO(VALID_KEY + "\n")), \
                    mock.patch("credential_store.default_path", return_value=root / "key.dpapi"):
                code = self._run(["--bundle-root", str(root / "data"),
                                  "--api-key", "-"], targets)
            self.assertEqual(code, 0)
            for target in targets:
                self.assertIn("cueprecise",
                              _read(target.locate_config())["mcpServers"])

    def test_setup_can_name_one_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = tuple(_file_target(name, root / f"{name}.json")
                            for name in ("one", "two"))
            with mock.patch.object(cueprecise_cli.sys, "stdin", io.StringIO(VALID_KEY + "\n")), \
                    mock.patch("credential_store.default_path", return_value=root / "key.dpapi"):
                code = self._run(["--client", "two", "--bundle-root", str(root / "data"),
                                  "--api-key", "-"], targets)
            self.assertEqual(code, 0)
            self.assertFalse((root / "one.json").exists())
            self.assertIn("cueprecise", _read(root / "two.json")["mcpServers"])

    def test_setup_reports_failure_when_no_app_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            absent = dataclasses.replace(
                _file_target("gone", root / "gone.json"), detector=lambda: False)
            code = self._run(["--bundle-root", str(root / "data")], (absent,))
            self.assertEqual(code, 1)
            self.assertFalse((root / "gone.json").exists())

    def test_setup_reports_failure_when_every_app_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            taken = root / "taken.json"
            taken.write_text(
                json.dumps({"mcpServers": {"cueprecise": {"command": "not-ours.exe"}}}),
                encoding="utf-8")
            code = self._run(["--bundle-root", str(root / "data")],
                             (_file_target("blocked", taken),))
            self.assertEqual(code, 1)

    def test_config_is_refused_when_more_than_one_app_is_chosen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = tuple(_file_target(name, root / f"{name}.json")
                            for name in ("one", "two"))
            code = self._run(["--config", str(root / "x.json"),
                              "--bundle-root", str(root / "data")], targets)
            self.assertEqual(code, 2)
            self.assertFalse((root / "one.json").exists())


if __name__ == "__main__":
    unittest.main()
