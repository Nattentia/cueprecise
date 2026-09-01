"""여러 MCP 클라이언트를 상대하는 레지스트리 테스트.

이름 문자열을 맞추는 테스트가 아니다. 앱이 늘어도 **남의 설정을 깨뜨리지
않는지**, 그리고 Claude Desktop 의 기존 동작이 그대로인지를 본다.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_setup_refuses_to_overwrite_someone_elses_entry(self) -> None:
        """덮어쓰면 남의 서버 설정이 사라지고 그 환경변수가 우리 항목에 옮겨 붙는다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "claude.json"
            original = json.dumps({"mcpServers": {
                "cueprecise": {"command": "not-ours.exe", "env": {"THEIR_KEY": "secret"}}}})
            config.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "남의 항목"):
                configuration.setup_file_client(
                    config, root / "data", api_key=None,
                    server_command="cueprecise-mcp.exe", server_args=[])

            self.assertEqual(config.read_text(encoding="utf-8"), original)

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

def _toml_servers(servers: dict) -> str:
    """테스트가 Codex 설정을 흉내 낼 만큼만 TOML 을 쓴다."""
    lines = []
    for name, entry in servers.items():
        lines.append(f"[mcp_servers.{name}]")
        for field, value in entry.items():
            if field != "env":
                lines.append(f"{field} = {json.dumps(value)}")
        for key, value in (entry.get("env") or {}).items():
            if key == next(iter(entry["env"])):
                lines.append(f"[mcp_servers.{name}.env]")
            lines.append(f"{key} = {json.dumps(value)}")
        lines.append("")
    return "\n".join(lines)


class FakeCli:
    """`codex`/`claude` 를 대신한다. 부른 인자를 남기고 설정 파일을 바꾼다.

    진짜 CLI 를 부르면 이 PC 의 설정이 바뀐다. 테스트는 그러면 안 된다.
    """

    def __init__(self, path: Path, servers: dict, *, toml: bool, servers_key: str,
                 obey: bool = True) -> None:
        self.path, self.servers, self.toml = path, dict(servers), toml
        self.servers_key, self.obey = servers_key, obey
        self.calls: list[list[str]] = []
        self.environments: list[dict] = []
        self._flush()

    def _flush(self) -> None:
        if self.toml:
            self.path.write_text(_toml_servers(self.servers), encoding="utf-8")
        else:
            self.path.write_text(json.dumps({self.servers_key: self.servers}),
                                 encoding="utf-8")

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.environments.append(kwargs.get("env") or {})
        if self.obey and len(argv) >= 4 and argv[1] == "mcp":
            name = argv[3]
            if argv[2] == "remove":
                self.servers.pop(name, None)
            elif argv[2] == "add":
                # 앱마다 모양이 다르다. `codex`/`claude` 는 `-- <실행파일>` 을
                # 요구하고 Gemini CLI 는 구분자 없이 바로 받는다. 둘 다 읽는다.
                index, environment = 4, {}
                while index < len(argv):
                    token = argv[index]
                    if token == "--":
                        index += 1
                        break
                    if not token.startswith("-"):
                        break
                    value = argv[index + 1]
                    if "=" in value:
                        key, _, rest = value.partition("=")
                        environment[key] = rest
                    index += 2
                entry = {"command": argv[index], "args": argv[index + 1:]}
                if environment:
                    entry["env"] = environment
                self.servers[name] = entry
            self._flush()
        # 종료 코드는 언제나 0 이다. 진짜 `claude mcp add` 도 거절하면서 0 을 준다.
        return subprocess.CompletedProcess(argv, 0, "", "denied")

    def last_add(self) -> list[str]:
        return [call for call in self.calls if call[2] == "add"][-1]


class CliTargetTest(unittest.TestCase):
    def _target(self, tmp: Path, base: configuration.CliClientTarget,
                servers: dict, **fake):
        path = tmp / ("config.toml" if base.reads_toml else "config.json")
        cli = FakeCli(path, servers, toml=base.reads_toml,
                      servers_key=base.servers_key, **fake)
        return dataclasses.replace(base, locate_config=lambda: path), cli, path

    def test_codex_add_carries_env_bundle_root_and_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, path = self._target(root, configuration.CODEX, {})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.install(root / "data", api_key="secret",
                                        server_command="python.exe",
                                        server_args=["server.py"])

            argv = cli.last_add()
            self.assertEqual(argv[1:4], ["mcp", "add", "cueprecise"])
            self.assertIn("--env", argv)
            self.assertIn("GEMINI_API_KEY=secret", argv)
            tail = argv[argv.index("--") + 1:]
            self.assertEqual(tail[:2], ["python.exe", "server.py"])
            self.assertEqual(tail[2], "--bundle-root")
            # 읽는 파일과 쓰는 홈이 어긋나면 등록이 조용히 딴 데로 간다.
            self.assertEqual(cli.environments[-1]["CODEX_HOME"], str(path.parent))
            self.assertTrue(result["api_key_configured"])

    def test_claude_code_uses_user_scope_and_replaces_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = {"cueprecise": {"command": "cueprecise-mcp.exe",
                                       "args": ["--bundle-root", "old"],
                                       "env": {"GEMINI_API_KEY": "old-key"}}}
            target, cli, _ = self._target(root, configuration.CLAUDE_CODE, existing)
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.install(root / "data", api_key=None,
                                        server_command="cueprecise-mcp.exe",
                                        server_args=[])

            self.assertEqual([call[2] for call in cli.calls], ["remove", "add"])
            for call in cli.calls:
                self.assertIn("-s", call)
                self.assertIn("user", call)
            # 키를 다시 주지 않아도 저장돼 있던 것이 살아남아야 한다.
            self.assertIn("GEMINI_API_KEY=old-key", cli.last_add())
            self.assertTrue(result["api_key_configured"])

    def test_install_refuses_someone_elses_entry_without_running_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(
                root, configuration.CODEX, {"cueprecise": {"command": "not-ours.exe"}})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                with self.assertRaisesRegex(SystemExit, "남의 항목"):
                    target.install(root / "data", api_key=None,
                                   server_command="python.exe", server_args=[])
            self.assertEqual(cli.calls, [])

    def test_success_is_read_back_not_taken_from_the_exit_code(self) -> None:
        """`claude mcp add` 는 거절하면서도 종료 코드 0 을 준다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, configuration.CLAUDE_CODE, {}, obey=False)
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                with self.assertRaisesRegex(SystemExit, "등록하지 못했다"):
                    target.install(root / "data", api_key=None,
                                   server_command="cueprecise-mcp.exe", server_args=[])

    def test_a_stale_entry_is_not_mistaken_for_success(self) -> None:
        """지우기가 실패하고 넣기가 거절당하면 옛 항목이 그대로 남는다.

        항목이 있는지만 보면 그것을 성공으로 읽는다. `claude` 는 거절하면서도
        종료 코드 0 을 주므로 실제로 일어날 수 있다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = {"cueprecise": {"command": "cueprecise-mcp.exe",
                                    "args": ["--bundle-root", "C:/old/data"]}}
            target, cli, _ = self._target(root, configuration.CLAUDE_CODE, stale, obey=False)
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                with self.assertRaisesRegex(SystemExit, "등록하지 못했다"):
                    target.install(root / "data", api_key=None,
                                   server_command="cueprecise-mcp.exe", server_args=[])

    def test_an_explicit_config_path_is_used_not_the_default_one(self) -> None:
        """부르는 쪽이 경로를 주면 그곳을 봐야 한다. 기본 위치를 보면 안 된다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chosen = root / "chosen.toml"
            cli = FakeCli(chosen, {}, toml=True, servers_key="mcp_servers")
            missing = root / "never" / "config.toml"
            target = dataclasses.replace(configuration.CODEX, locate_config=lambda: missing)

            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.install(root / "data", api_key=None,
                                        server_command="python.exe",
                                        server_args=["-m", "mcp_server"],
                                        config_path=chosen)

            self.assertEqual(result["config"], str(chosen))
            self.assertIn("cueprecise", cli.servers)
            self.assertFalse(missing.exists())
            # 환경변수로 정해지는 홈도 넘겨받은 경로를 따라야 한다.
            self.assertEqual(cli.environments[-1]["CODEX_HOME"], str(chosen.parent))

            with mock.patch.object(configuration.subprocess, "run", cli.run):
                removed = target.remove(config_path=chosen)
            self.assertTrue(removed["changed"])
            self.assertEqual(removed["config"], str(chosen))

    def test_remove_skips_entries_that_are_not_ours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(
                root, configuration.CODEX, {"cueprecise": {"command": "not-ours.exe"}})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.remove()
            self.assertFalse(result["changed"])
            self.assertEqual(cli.calls, [])

    def test_remove_runs_and_reads_back(self) -> None:
        ours = {"cueprecise": {"command": "cueprecise-mcp.exe", "args": []}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, configuration.CODEX, dict(ours))
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.remove()
            self.assertTrue(result["changed"])
            self.assertEqual(result["removed"], ["cueprecise"])
            self.assertNotIn("cueprecise", cli.servers)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, configuration.CODEX, dict(ours), obey=False)
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                with self.assertRaisesRegex(SystemExit, "제거하지 못했다"):
                    target.remove()

    def test_missing_executable_is_reported_not_crashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = dataclasses.replace(
                configuration.CODEX, locate_config=lambda: root / "config.toml")
            with mock.patch.object(configuration.subprocess, "run",
                                   side_effect=OSError("없는 명령")):
                with self.assertRaisesRegex(SystemExit, "실행하지 못했다"):
                    target.install(root / "data", api_key=None,
                                   server_command="python.exe", server_args=[])

    def test_the_command_is_called_by_its_resolved_path(self) -> None:
        """이름만 주면 Windows 에서 `code.CMD` 가 실행되지 않는다.

        `subprocess.run(['code', ...])` 는 `FileNotFoundError [WinError 2]` 를
        낸다. 완전 경로를 주면 된다. 이름으로 부르면 VS Code 는 이 PC 에서
        한 번도 붙지 않는다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, configuration.CODEX, {})
            with mock.patch.object(configuration.shutil, "which",
                                   return_value="C:/found/codex.EXE"), \
                    mock.patch.object(configuration.subprocess, "run", cli.run):
                target.install(root / "data", api_key=None, server_command="python.exe",
                               server_args=["-m", "mcp_server"])
            self.assertEqual(cli.calls[-1][0], "C:/found/codex.EXE")

    def test_a_command_that_is_not_on_path_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, configuration.CODEX, {})
            with mock.patch.object(configuration.shutil, "which", return_value=None):
                with self.assertRaisesRegex(SystemExit, "찾지 못했다"):
                    target.install(root / "data", api_key=None,
                                   server_command="python.exe", server_args=[])
            self.assertEqual(cli.calls, [])

    def test_detection_uses_the_executable_on_path(self) -> None:
        with mock.patch.object(configuration.shutil, "which", return_value=None):
            self.assertFalse(configuration.CODEX.is_installed())
        with mock.patch.object(configuration.shutil, "which", return_value="codex"):
            self.assertTrue(configuration.CODEX.is_installed())

    def test_claude_code_config_follows_its_home_variable(self) -> None:
        """`CLAUDE_CONFIG_DIR` 은 `.claude.json` 을 옮긴다(실측).

        무시하면 `claude mcp add` 는 옮겨진 곳에 잘 써 넣는데 우리는 홈을 읽고
        실패라고 말한다. 등록은 됐는데 실패로 보고하게 된다.
        """
        with mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": "C:/elsewhere"}):
            self.assertEqual(configuration.default_claude_code_config(),
                             Path("C:/elsewhere") / ".claude.json")
        with mock.patch.dict("os.environ"):
            configuration.os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(configuration.default_claude_code_config(),
                             Path.home() / ".claude.json")

    def test_claude_code_passes_its_home_to_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, path = self._target(root, configuration.CLAUDE_CODE, {})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                target.install(root / "data", api_key=None,
                               server_command="cueprecise-mcp.exe", server_args=[])
            self.assertEqual(cli.environments[-1]["CLAUDE_CONFIG_DIR"], str(path.parent))

    def test_codex_config_follows_the_home_variable(self) -> None:
        with mock.patch.dict("os.environ", {"CODEX_HOME": "C:/elsewhere"}):
            self.assertEqual(configuration.default_codex_config(),
                             Path("C:/elsewhere") / "config.toml")
        with mock.patch.dict("os.environ"):
            configuration.os.environ.pop("CODEX_HOME", None)
            self.assertEqual(configuration.default_codex_config(),
                             Path.home() / ".codex" / "config.toml")


class FakeVsCode:
    """`code --add-mcp` 를 대신한다. 실측한 대로 동기적으로 파일을 쓴다."""

    def __init__(self, path: Path, servers: dict, *, obey: bool = True) -> None:
        self.path, self.servers, self.obey = path, dict(servers), obey
        self.calls: list[list[str]] = []
        self._flush()

    def _flush(self) -> None:
        self.path.write_text(json.dumps({"servers": self.servers, "inputs": []}),
                             encoding="utf-8")

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.obey and len(argv) == 3 and argv[1] == "--add-mcp":
            payload = json.loads(argv[2])
            name = payload.pop("name")
            self.servers[name] = payload
            self._flush()
        return subprocess.CompletedProcess(argv, 0, "", "denied")


class VsCodeTargetTest(unittest.TestCase):
    def _target(self, tmp: Path, servers: dict, **fake):
        path = tmp / "mcp.json"
        cli = FakeVsCode(path, servers, **fake)
        return dataclasses.replace(
            configuration.VS_CODE, locate_config=lambda: path), cli, path

    def test_payload_uses_slashes_and_carries_bundle_root(self) -> None:
        """`code.cmd` 를 지나며 역슬래시가 죽는다. 경로는 슬래시로 보낸다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, {})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                target.install(root / "data", api_key="secret",
                               server_command="C:\\app\\cueprecise-mcp.exe",
                               server_args=["--flag"])

            argv = cli.calls[-1]
            self.assertEqual(argv[1], "--add-mcp")
            self.assertNotIn("\\", argv[2])
            payload = json.loads(argv[2])
            self.assertEqual(payload["name"], "cueprecise")
            self.assertEqual(payload["command"], "C:/app/cueprecise-mcp.exe")
            self.assertEqual(payload["args"][:2], ["--flag", "--bundle-root"])
            self.assertEqual(payload["env"]["GEMINI_API_KEY"], "secret")

    def test_install_writes_under_servers_and_spares_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, path = self._target(root, {"other": {"command": "keep"}})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                target.install(root / "data", api_key=None,
                               server_command="cueprecise-mcp.exe", server_args=[])

            saved = _read(path)
            self.assertEqual(saved["servers"]["other"]["command"], "keep")
            self.assertIn("cueprecise", saved["servers"])
            self.assertEqual(saved["inputs"], [])
            self.assertNotIn("mcpServers", saved)

    def test_existing_key_is_inherited_across_a_reinstall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, {"cueprecise": {
                "command": "cueprecise-mcp.exe", "args": ["--bundle-root", "old"],
                "env": {"GEMINI_API_KEY": "old-key"}}})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.install(root / "data", api_key=None,
                                        server_command="cueprecise-mcp.exe",
                                        server_args=[])
            payload = json.loads(cli.calls[-1][2])
            self.assertEqual(payload["env"]["GEMINI_API_KEY"], "old-key")
            self.assertTrue(result["api_key_configured"])

    def test_install_refuses_someone_elses_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, {"cueprecise": {"command": "not-ours.exe"}})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                with self.assertRaisesRegex(SystemExit, "남의 항목"):
                    target.install(root / "data", api_key=None,
                                   server_command="cueprecise-mcp.exe", server_args=[])
            self.assertEqual(cli.calls, [])

    def test_success_is_read_back_not_taken_from_the_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, {}, obey=False)
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                with self.assertRaisesRegex(SystemExit, "등록하지 못했다"):
                    target.install(root / "data", api_key=None,
                                   server_command="cueprecise-mcp.exe", server_args=[])

    def test_removal_edits_the_file_because_there_is_no_command_for_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, path = self._target(root, {
                "cueprecise": {"command": "cueprecise-mcp.exe"},
                "other": {"command": "keep"}})
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.remove()

            saved = _read(path)
            self.assertTrue(result["changed"])
            self.assertNotIn("cueprecise", saved["servers"])
            self.assertEqual(saved["servers"]["other"]["command"], "keep")
            self.assertEqual(saved["inputs"], [])
            self.assertEqual(cli.calls, [])

    def test_an_explicit_config_path_is_used_not_the_default_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chosen = root / "chosen.json"
            cli = FakeVsCode(chosen, {})
            missing = root / "never" / "mcp.json"
            target = dataclasses.replace(
                configuration.VS_CODE, locate_config=lambda: missing)
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.install(root / "data", api_key=None,
                                        server_command="cueprecise-mcp.exe",
                                        server_args=[], config_path=chosen)
            self.assertEqual(result["config"], str(chosen))
            self.assertIn("cueprecise", _read(chosen)["servers"])
            self.assertFalse(missing.exists())

    def test_removal_spares_someone_elses_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, _, path = self._target(root, {"cueprecise": {"command": "not-ours.exe"}})
            original = path.read_text(encoding="utf-8")
            result = target.remove()
            self.assertFalse(result["changed"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)


class FileClientRegistryTest(unittest.TestCase):
    """Cursor·Windsurf·Gemini CLI 는 붙여 보지 못했다. 지킬 것만 지킨다."""

    UNVERIFIED = ("cursor", "windsurf", "gemini-cli")

    def test_every_registered_client_has_a_distinct_key_and_config(self) -> None:
        keys = [target.key for target in configuration.CLIENTS]
        self.assertEqual(len(keys), len(set(keys)))
        paths = [str(target.locate_config()) for target in configuration.CLIENTS]
        self.assertEqual(len(paths), len(set(paths)))

    def test_unverified_clients_are_detected_by_executable_only(self) -> None:
        """설정 폴더의 존재를 앱이 있다는 증거로 삼지 않는다.

        이 PC 의 `~/.cursor` 와 `~/.gemini/settings.json` 은 다른 도구가
        만들어 둔 것이고 앱은 없다. 폴더로 판정하면 안 쓰는 앱에 항목을 쓴다.
        """
        for key in self.UNVERIFIED:
            target = configuration.client_by_key(key)
            self.assertTrue(target.executable)
            with mock.patch.object(configuration.shutil, "which", return_value=None):
                self.assertFalse(target.is_installed(), key)
            with mock.patch.object(configuration.shutil, "which", return_value="found"):
                self.assertTrue(target.is_installed(), key)

    def test_unverified_clients_still_obey_the_shared_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for key in self.UNVERIFIED:
                config = root / f"{key}.json"
                config.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}}}),
                                  encoding="utf-8")
                target = dataclasses.replace(
                    configuration.client_by_key(key), locate_config=lambda p=config: p)

                target.install(root / "data", api_key="secret",
                               server_command="cueprecise-mcp.exe", server_args=[])
                saved = _read(config)
                self.assertEqual(saved["mcpServers"]["other"]["command"], "keep", key)
                self.assertEqual(
                    saved["mcpServers"]["cueprecise"]["env"]["GEMINI_API_KEY"], "secret", key)

                self.assertTrue(target.remove()["changed"], key)
                self.assertNotIn("cueprecise", _read(config)["mcpServers"], key)
                self.assertEqual(_read(config)["mcpServers"]["other"]["command"], "keep", key)

    def test_claude_desktop_still_falls_back_to_its_folder(self) -> None:
        """CLI 가 아예 없는 앱은 설정 폴더로 판정할 수밖에 없다."""
        self.assertEqual(configuration.CLAUDE_DESKTOP.executable, "")
        with tempfile.TemporaryDirectory() as tmp:
            present = Path(tmp) / "config.json"
            target = dataclasses.replace(
                configuration.CLAUDE_DESKTOP, locate_config=lambda: present)
            self.assertTrue(target.is_installed())
            absent = Path(tmp) / "nothing" / "config.json"
            target = dataclasses.replace(
                configuration.CLAUDE_DESKTOP, locate_config=lambda: absent)
            self.assertFalse(target.is_installed())


class GeminiCliTest(unittest.TestCase):
    """명령을 먼저 쓰고, 듣지 않으면 설정 파일에 쓴다.

    이 앱은 이 PC 에 없어 명령을 태워 보지 못했다. 인자 배치가 판마다 다를 수
    있으므로, 틀렸을 때 조용히 실패하지 않고 파일 쓰기로 돌아가는지를 본다.
    """

    def _target(self, tmp: Path, servers: dict, **fake):
        path = tmp / "settings.json"
        cli = FakeCli(path, servers, toml=False, servers_key="mcpServers", **fake)
        return dataclasses.replace(
            configuration.GEMINI_CLI, locate_config=lambda: path), cli, path

    def test_command_has_user_scope_and_no_separator(self) -> None:
        """`gemini mcp add <이름> -e K=V <실행파일> <인자>` — `--` 가 없다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, _ = self._target(root, {})
            with mock.patch.object(configuration.shutil, "which", return_value="gemini"), \
                    mock.patch.object(configuration.subprocess, "run", cli.run):
                target.install(root / "data", api_key="secret",
                               server_command="cueprecise-mcp.exe", server_args=[])
            argv = cli.last_add()
            self.assertEqual(argv[1:4], ["mcp", "add", "cueprecise"])
            self.assertNotIn("--", argv)
            self.assertIn("-s", argv)
            self.assertIn("user", argv)
            self.assertIn("-e", argv)
            self.assertIn("GEMINI_API_KEY=secret", argv)
            self.assertEqual(argv[-2:], ["--bundle-root", str((root / "data").resolve())])

    def test_falls_back_to_the_file_when_the_command_does_not_take(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, path = self._target(root, {"other": {"command": "keep"}}, obey=False)
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.install(root / "data", api_key="secret",
                                        server_command="cueprecise-mcp.exe", server_args=[])
            saved = _read(path)
            self.assertEqual(saved["mcpServers"]["other"]["command"], "keep")
            self.assertEqual(saved["mcpServers"]["cueprecise"]["env"]["GEMINI_API_KEY"],
                             "secret")
            self.assertTrue(result["api_key_configured"])

    def test_falls_back_when_the_command_is_missing_entirely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, _cli, path = self._target(root, {})
            with mock.patch.object(configuration.shutil, "which", return_value=None):
                target.install(root / "data", api_key=None,
                               server_command="cueprecise-mcp.exe", server_args=[])
            self.assertIn("cueprecise", _read(path)["mcpServers"])

    def test_a_foreign_entry_stops_it_instead_of_falling_back(self) -> None:
        """남의 항목은 다른 방법으로 붙여서도 안 되는 실패다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, path = self._target(
                root, {"cueprecise": {"command": "not-ours.exe"}}, obey=False)
            original = path.read_text(encoding="utf-8")
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                with self.assertRaises(configuration.ForeignEntryError):
                    target.install(root / "data", api_key=None,
                                   server_command="cueprecise-mcp.exe", server_args=[])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_removal_falls_back_to_the_file_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, cli, path = self._target(root, {
                "cueprecise": {"command": "cueprecise-mcp.exe"},
                "other": {"command": "keep"}}, obey=False)
            with mock.patch.object(configuration.subprocess, "run", cli.run):
                result = target.remove()
            saved = _read(path)
            self.assertTrue(result["changed"])
            self.assertNotIn("cueprecise", saved["mcpServers"])
            self.assertEqual(saved["mcpServers"]["other"]["command"], "keep")


class FailureIsCatchableTest(unittest.TestCase):
    """설치 화면은 `except Exception` 으로 실패를 잡는다.

    순수한 `SystemExit` 은 `BaseException` 이라 그 그물을 빠져나간다. 그러면
    진행 막대가 도는 채로 아무 말도 없이 멈추고, 조용히 도는 이관은
    "실패해도 설치를 막지 않는다"는 약속을 깬다.
    """

    def _failures(self, tmp: Path):
        broken = tmp / "broken.json"
        broken.write_text("not json", encoding="utf-8")
        stranger = tmp / "stranger.json"
        stranger.write_text(
            json.dumps({"mcpServers": {"cueprecise": {"command": "not-ours.exe"}}}),
            encoding="utf-8")
        return [
            lambda: configuration.read_config(broken),
            lambda: configuration.setup_file_client(
                stranger, tmp / "data", api_key=None,
                server_command="cueprecise-mcp.exe", server_args=[]),
            lambda: configuration.client_by_key("nope"),
        ]

    def test_every_failure_is_catchable_as_a_plain_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for index, failing in enumerate(self._failures(Path(tmp))):
                with self.subTest(index):
                    with self.assertRaises(Exception):
                        failing()

    def test_the_cli_still_sees_them_as_system_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for index, failing in enumerate(self._failures(Path(tmp))):
                with self.subTest(index):
                    with self.assertRaises(SystemExit):
                        failing()

    def test_a_failing_command_is_catchable_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = dataclasses.replace(
                configuration.CODEX, locate_config=lambda: root / "config.toml")
            with mock.patch.object(configuration.subprocess, "run",
                                   side_effect=OSError("없는 명령")):
                with self.assertRaises(Exception):
                    target.install(root / "data", api_key=None,
                                   server_command="python.exe", server_args=[])


class RealLaunchTest(unittest.TestCase):
    """이 PC 에 있는 명령을 **모킹 없이** 실제로 띄워 본다.

    나머지 테스트는 `subprocess.run` 을 가짜로 바꾼다. 그래서 "이 명령이 정말
    실행되는가"는 아무도 보지 않았고, `code` 가 Windows 에서 `code.CMD` 라
    이름만으로는 실행되지 않는다는 것을 놓쳤다. 설정 파일은 건드리지 않고
    실행 가능한지만 본다. 없는 앱은 건너뛴다.
    """

    def test_every_present_command_can_actually_be_launched(self) -> None:
        targets = [target for target in configuration.CLIENTS
                   if isinstance(target, configuration.CliClientTarget)
                   and target.is_installed()]
        if not targets:
            self.skipTest("이 PC 에 명령으로 붙이는 앱이 없다")
        for target in targets:
            # `skipTest` 를 여기서 부르면 나머지 앱까지 함께 건너뛴다.
            # 없는 앱은 위에서 이미 걸렀다.
            with self.subTest(target.key):
                executable = target._resolve_executable()
                self.assertTrue(Path(executable).is_file(), executable)
                result = subprocess.run([executable, "--version"], capture_output=True,
                                        text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr[:200])


class ManagedJudgementTest(unittest.TestCase):
    """우리 항목은 0.1.0 부터 예외 없이 `--bundle-root` 를 달고 있다."""

    def test_bundled_executable_is_ours(self) -> None:
        self.assertTrue(configuration.is_managed_server({"command": "cueprecise-mcp.exe"}))
        self.assertTrue(configuration.is_managed_server({"command": "ytx-mcp.exe"}))

    def test_our_source_entry_is_ours(self) -> None:
        self.assertTrue(configuration.is_managed_server(
            {"command": "python", "args": ["src/mcp_server.py", "--bundle-root", "d"]}))

    def test_someone_elses_mcp_server_is_not_ours(self) -> None:
        self.assertFalse(configuration.is_managed_server(
            {"command": "python", "args": ["their/mcp_server.py"]}))


if __name__ == "__main__":
    unittest.main()
