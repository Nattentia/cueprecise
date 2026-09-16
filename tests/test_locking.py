"""번들 잠금 테스트. 두 앱이 같은 영상을 동시에 다루면 Gemini 를 두 번 쓴다."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import locking


class BundleLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _owner(self, **overrides) -> None:
        record = {"pid": os.getppid(), "host": socket.gethostname(),
                  "activity": "register", "started_at": "2026-01-01T00:00:00+00:00",
                  "started_epoch": time.time()}
        record.update(overrides)
        self.bundle.mkdir(parents=True, exist_ok=True)
        (self.bundle / locking.OWNER_NAME).write_text(json.dumps(record), encoding="utf-8")

    def _acquires(self) -> bool:
        try:
            with locking.bundle_lock(self.bundle):
                return True
        except locking.BundleBusy:
            return False

    def test_record_is_written_and_removed(self) -> None:
        with locking.bundle_lock(self.bundle, activity="register"):
            owner = locking.owner_of(self.bundle)
            self.assertEqual(owner["pid"], os.getpid())
            self.assertEqual(owner["activity"], "register")
        self.assertIsNone(locking.owner_of(self.bundle))

    def test_same_process_may_nest(self) -> None:
        """run 이 쥔 채로 stage_visual 이 다시 잡는다."""
        with locking.bundle_lock(self.bundle):
            with locking.bundle_lock(self.bundle, activity="visual"):
                pass
            self.assertIsNotNone(locking.owner_of(self.bundle), "안쪽에서 놓아 버렸다")
        self.assertIsNone(locking.owner_of(self.bundle))

    def test_released_after_exception(self) -> None:
        with self.assertRaises(ValueError):
            with locking.bundle_lock(self.bundle):
                raise ValueError("boom")
        self.assertIsNone(locking.owner_of(self.bundle))

    def test_live_process_on_this_machine_blocks(self) -> None:
        self._owner()
        with self.assertRaises(locking.BundleBusy) as caught:
            with locking.bundle_lock(self.bundle):
                pass
        self.assertIn("register", str(caught.exception))

    def test_dead_process_record_is_reclaimed(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self._owner(pid=child.pid)
        self.assertTrue(self._acquires(), "죽은 프로세스의 기록에 막혔다")

    def test_own_pid_record_not_held_is_reclaimed(self) -> None:
        """예전 프로세스의 번호를 물려받았다. 살아 있다고 보면 영영 못 연다."""
        self._owner(pid=os.getpid())
        self.assertTrue(self._acquires())

    def test_old_record_is_reclaimed_even_if_pid_looks_alive(self) -> None:
        """번호가 다른 프로그램에 재사용된 경우를 시간 상한이 푼다."""
        self._owner(started_epoch=time.time() - locking.DEFAULT_STALE_AFTER - 60)
        self.assertTrue(self._acquires())

    def test_other_machine_recent_record_blocks(self) -> None:
        self._owner(host="another-machine", pid=999999)
        self.assertFalse(self._acquires(), "다른 기기의 진행 중 기록을 무시했다")

    def test_other_machine_old_record_is_reclaimed(self) -> None:
        self._owner(host="another-machine", pid=999999,
                    started_epoch=time.time() - locking.DEFAULT_STALE_AFTER - 60)
        self.assertTrue(self._acquires())

    def test_record_without_timestamp_is_reclaimed(self) -> None:
        self._owner(started_epoch=None)
        self.assertTrue(self._acquires())

    def test_corrupt_record_does_not_block_forever(self) -> None:
        self.bundle.mkdir(parents=True)
        (self.bundle / locking.OWNER_NAME).write_text("{", encoding="utf-8")
        self.assertTrue(self._acquires())

    def test_another_process_cannot_take_a_held_bundle(self) -> None:
        with locking.bundle_lock(self.bundle):
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import sys, pathlib; sys.path.insert(0, %r); import locking\n"
                 "try:\n"
                 "    ctx = locking.bundle_lock(pathlib.Path(%r)); ctx.__enter__(); print('ACQUIRED')\n"
                 "except locking.BundleBusy:\n"
                 "    print('BUSY')" % (str(Path(locking.__file__).parent), str(self.bundle))],
                capture_output=True, text=True, timeout=60)
        self.assertIn("BUSY", probe.stdout)


class ProcessAliveTests(unittest.TestCase):
    def test_own_process_is_alive(self) -> None:
        self.assertTrue(locking.process_alive(os.getpid()))

    def test_invalid_pid_is_not_alive(self) -> None:
        self.assertFalse(locking.process_alive(0))

    def test_exited_process_is_not_alive_even_with_open_handle(self) -> None:
        """Windows 에서는 부모가 핸들을 쥐고 있으면 끝난 프로세스도 열린다."""
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()  # Popen 이 핸들을 계속 쥐고 있다
        self.assertFalse(locking.process_alive(child.pid))


if __name__ == "__main__":
    unittest.main()
