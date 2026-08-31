from __future__ import annotations

import json
import multiprocessing
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import usage


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _record_worker(path: str, api_key: str, instant: str) -> None:
    usage.record_attempt(Path(path), api_key, datetime.fromisoformat(instant))


class UsageLedgerTests(unittest.TestCase):
    def test_key_is_hashed_and_attempts_increment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            first = usage.record_attempt(path, "secret-key", NOW)
            second = usage.record_attempt(path, "secret-key", NOW)
            self.assertEqual((first["attempts"], second["attempts"]), (1, 2))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-key", raw)
            self.assertIn(usage.key_hash("secret-key"), raw)

    def test_keys_and_pacific_days_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            usage.record_attempt(path, "a", NOW)
            self.assertEqual(usage.get_usage(path, "b", NOW)["attempts"], 0)
            tomorrow = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
            self.assertEqual(usage.get_usage(path, "a", tomorrow)["attempts"], 0)

    def test_pacific_midnight_changes_ledger_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            before = datetime(2026, 8, 30, 6, 59, tzinfo=timezone.utc)
            after = datetime(2026, 8, 30, 7, 1, tzinfo=timezone.utc)
            self.assertEqual(usage.record_attempt(path, "key", before)["day"], "2026-08-29")
            self.assertEqual(usage.get_usage(path, "key", after)["attempts"], 0)
            self.assertEqual(usage.record_attempt(path, "key", after)["day"], "2026-08-30")

    def test_concurrent_process_increments_are_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            context = multiprocessing.get_context("spawn")
            processes = [
                context.Process(
                    target=_record_worker,
                    args=(str(path), "key", NOW.isoformat()),
                )
                for _ in range(8)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(usage.get_usage(path, "key", NOW)["attempts"], 8)

    def test_preflight_stops_before_estimated_daily_overage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            for _ in range(3):
                usage.record_attempt(path, "key", NOW)
            with self.assertRaises(usage.UsageLimitExceeded):
                usage.preflight(path, "key", 3, daily_limit=5, now=NOW)
            status = usage.preflight(path, "key", 2, daily_limit=5, now=NOW)
            self.assertIn("정확도: local estimate", usage.format_status(status, NOW))

    def test_rpm_attempts_include_failed_request_slots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            usage.record_attempt(path, "key", NOW)
            usage.record_attempt(path, "key", NOW)
            with self.assertRaises(usage.UsageLimitExceeded):
                usage.preflight(path, "key", 1, daily_limit=25, rpm_limit=2, now=NOW)

    def test_ledger_is_valid_json_after_each_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            for index in range(5):
                usage.record_attempt(path, "key", NOW)
                self.assertEqual(json.loads(path.read_text())["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()


class SlotWaitTests(unittest.TestCase):
    """RPM 한도가 여유로우면 기다리지 않는다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "usage.json"
        self.key = "k"
        self.now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_wait_when_budget_is_free(self) -> None:
        self.assertEqual(
            usage.seconds_until_slot(self.path, self.key, rpm_limit=2, now=self.now), 0.0)

    def test_no_wait_below_limit(self) -> None:
        usage.record_attempt(self.path, self.key, now=self.now)
        self.assertEqual(
            usage.seconds_until_slot(self.path, self.key, rpm_limit=2,
                                     now=self.now + timedelta(seconds=1)), 0.0)

    def test_waits_until_oldest_call_leaves_the_window(self) -> None:
        usage.record_attempt(self.path, self.key, now=self.now)
        usage.record_attempt(self.path, self.key, now=self.now + timedelta(seconds=5))
        delay = usage.seconds_until_slot(self.path, self.key, rpm_limit=2,
                                         now=self.now + timedelta(seconds=10))
        self.assertAlmostEqual(delay, 50.0, delta=1.0)

    def test_no_wait_after_window_passes(self) -> None:
        usage.record_attempt(self.path, self.key, now=self.now)
        usage.record_attempt(self.path, self.key, now=self.now + timedelta(seconds=5))
        self.assertEqual(
            usage.seconds_until_slot(self.path, self.key, rpm_limit=2,
                                     now=self.now + timedelta(seconds=61)), 0.0)

    def test_no_limit_means_no_wait(self) -> None:
        usage.record_attempt(self.path, self.key, now=self.now)
        self.assertEqual(
            usage.seconds_until_slot(self.path, self.key, rpm_limit=None,
                                     now=self.now), 0.0)
