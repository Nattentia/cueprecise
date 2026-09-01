"""릴리스 버전 검사 테스트.

문자열을 맞추는 테스트가 아니다. **버전이 박힌 네 곳이 지금 서로 맞는지**를
평소 CI 에서 미리 걸러내고, 어긋났을 때 릴리스가 실제로 멈추는지를 본다.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "installer"))
import release_version

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake_repo(root: Path, pyproject: str, iss: str, readme: str, readme_ko: str) -> None:
    (root / "installer").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "cueprecise-mcp"\nversion = "{pyproject}"\n', encoding="utf-8"
    )
    (root / "installer" / "cueprecise.iss").write_text(
        f'#define MyAppName "CuePrecise"\n#define MyAppVersion "{iss}"\n', encoding="utf-8"
    )
    (root / "README.md").write_text(f"> `v{readme}` is a prerelease.\n", encoding="utf-8")
    (root / "README.ko.md").write_text(f"> `v{readme_ko}`은 시험판이다.\n", encoding="utf-8")


class RepositoryConsistencyTest(unittest.TestCase):
    """실제 저장소를 검사한다. 이 테스트가 깨지면 릴리스도 멈춘다."""

    def test_all_declared_versions_agree(self) -> None:
        found = release_version.declared_versions(REPO_ROOT)
        self.assertEqual(len(found), 4)
        self.assertEqual(
            len(set(found.values())),
            1,
            f"버전이 박힌 곳이 서로 다르다: {found}",
        )

    def test_repository_version_passes_its_own_check(self) -> None:
        current = release_version.declared_versions(REPO_ROOT)["pyproject.toml"]
        self.assertEqual(release_version.check(current, REPO_ROOT), f"v{current}")


class NormalizeTest(unittest.TestCase):
    def test_accepts_bare_and_prefixed_forms(self) -> None:
        for given in ("0.3.0", "v0.3.0", "V0.3.0", "  v0.3.0  "):
            with self.subTest(given=given):
                self.assertEqual(release_version.normalize(given), "0.3.0")

    def test_rejects_malformed_versions(self) -> None:
        for given in ("0.3", "0.3.0-rc1", "release", "", "0.3.0.1"):
            with self.subTest(given=given):
                with self.assertRaises(release_version.VersionMismatch):
                    release_version.normalize(given)


class MismatchTest(unittest.TestCase):
    def test_check_passes_when_every_file_agrees(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _fake_repo(root, "0.3.0", "0.3.0", "0.3.0", "0.3.0")
            self.assertEqual(release_version.check("0.3.0", root), "v0.3.0")

    def test_check_names_every_file_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            # 0.2.0 을 낼 때 실제로 났던 사고: 일부만 올리고 나머지를 잊는다.
            _fake_repo(root, "0.3.0", "0.2.0", "0.3.0", "0.2.0")
            with self.assertRaises(release_version.VersionMismatch) as caught:
                release_version.check("0.3.0", root)
            message = str(caught.exception)
            self.assertIn("installer/cueprecise.iss", message)
            self.assertIn("README.ko.md", message)
            self.assertNotIn("pyproject.toml", message)

    def test_check_rejects_version_nobody_declared(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _fake_repo(root, "0.3.0", "0.3.0", "0.3.0", "0.3.0")
            with self.assertRaises(release_version.VersionMismatch):
                release_version.check("0.4.0", root)

    def test_check_reports_disagreement_inside_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _fake_repo(root, "0.3.0", "0.3.0", "0.3.0", "0.3.0")
            (root / "README.md").write_text(
                "> `v0.3.0` is a prerelease.\n\n낡은 문장에 `v0.2.0` 이 남았다.\n",
                encoding="utf-8",
            )
            with self.assertRaises(release_version.VersionMismatch) as caught:
                release_version.check("0.3.0", root)
            self.assertIn("README.md", str(caught.exception))

    def test_check_reports_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _fake_repo(root, "0.3.0", "0.3.0", "0.3.0", "0.3.0")
            (root / "README.ko.md").unlink()
            with self.assertRaises(release_version.VersionMismatch) as caught:
                release_version.check("0.3.0", root)
            self.assertIn("README.ko.md", str(caught.exception))


class CommandLineTest(unittest.TestCase):
    def test_main_prints_tag_and_succeeds(self) -> None:
        current = release_version.declared_versions(REPO_ROOT)["pyproject.toml"]
        self.assertEqual(release_version.main([current, "--repo-root", str(REPO_ROOT)]), 0)

    def test_main_fails_on_mismatch(self) -> None:
        self.assertEqual(release_version.main(["9.9.9", "--repo-root", str(REPO_ROOT)]), 1)


if __name__ == "__main__":
    unittest.main()
