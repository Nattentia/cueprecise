"""YouTube 롤링 자막 중복 제거 테스트. 네트워크를 쓰지 않는다."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import fetch_youtube

FIXTURE = Path(__file__).parent / "fixtures" / "jcBDSLSeud4.youtube.ko-orig.srt"


def block(start: float, end: float, *lines: str) -> fetch_youtube.Block:
    return fetch_youtube.Block(start, end, tuple(lines))


class TimestampTests(unittest.TestCase):
    def test_srt_timestamp_parsing(self) -> None:
        self.assertAlmostEqual(fetch_youtube._seconds("00:03:27,680"), 207.68)
        self.assertAlmostEqual(fetch_youtube._seconds("01:00:00,000"), 3600.0)
        self.assertAlmostEqual(fetch_youtube._seconds("00:00:00,001"), 0.001)


class RollingCollapseTests(unittest.TestCase):
    """유튜브 자동자막은 2줄 창이 밀려 올라가며 각 줄이 3블록에 걸쳐 나온다."""

    def test_line_keeps_first_appearance_start(self) -> None:
        blocks = [
            block(207.68, 209.42, "자, 그럼 어떻게이 능력을 학습을", "했을까요? self"),
            block(209.42, 209.43, "했을까요? self"),
            block(209.43, 210.22, "했을까요? self", "supervised"),
            block(210.22, 210.23, "supervised"),
            block(210.23, 211.86, "supervised", "learning이라는 방식으로 학습을"),
        ]
        cues = fetch_youtube.collapse_rolling_lines(blocks)
        by_text = {c["text"]: c for c in cues}
        self.assertAlmostEqual(by_text["했을까요? self"]["start"], 207.68)
        self.assertAlmostEqual(by_text["supervised"]["start"], 209.43)

    def test_end_extends_while_line_stays_on_screen(self) -> None:
        blocks = [
            block(10.0, 12.0, "가"),
            block(12.0, 14.0, "가", "나"),
            block(14.0, 16.0, "나"),
        ]
        cues = fetch_youtube.collapse_rolling_lines(blocks)
        by_text = {c["text"]: c for c in cues}
        self.assertAlmostEqual(by_text["가"]["end"], 14.0)
        self.assertAlmostEqual(by_text["나"]["end"], 16.0)

    def test_cues_are_sorted_by_start(self) -> None:
        cues = fetch_youtube.collapse_rolling_lines([
            block(30.0, 31.0, "다"), block(10.0, 11.0, "가"), block(20.0, 21.0, "나"),
        ])
        self.assertEqual([c["text"] for c in cues], ["가", "나", "다"])

    def test_repeated_line_after_a_break_becomes_two_cues(self) -> None:
        """같은 문장을 나중에 다시 말하면 별개 cue 로 남아야 한다."""
        cues = fetch_youtube.collapse_rolling_lines([
            block(10.0, 11.0, "네"), block(11.0, 12.0, "다른 말"), block(60.0, 61.0, "네"),
        ])
        starts = sorted(c["start"] for c in cues if c["text"] == "네")
        self.assertEqual(len(starts), 2)
        self.assertAlmostEqual(starts[1], 60.0)

    def test_duplicate_lines_inside_one_block_collapse(self) -> None:
        cues = fetch_youtube.collapse_rolling_lines([block(1.0, 2.0, "가", "가")])
        self.assertEqual(len(cues), 1)


class FixtureRegressionTests(unittest.TestCase):
    """CONTRACT.md 4절 검증 기준을 고정한다."""

    def setUp(self) -> None:
        self.blocks = fetch_youtube.parse_srt(FIXTURE)
        self.cues = fetch_youtube.collapse_rolling_lines(self.blocks)

    def test_raw_line_count(self) -> None:
        self.assertEqual(sum(len(b.lines) for b in self.blocks), 1800)

    def test_collapsed_cue_count(self) -> None:
        self.assertEqual(len(self.cues), 603)

    def test_collapsed_word_count(self) -> None:
        self.assertEqual(sum(len(c["text"].split()) for c in self.cues), 2752)

    def test_self_supervised_line_start(self) -> None:
        hits = [c for c in self.cues if c["text"] == "했을까요? self"]
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(float(hits[0]["start"]), 207.68)

    def test_english_terms_survive_collapse(self) -> None:
        joined = " ".join(c["text"] for c in self.cues)
        for term in ("self", "supervised", "learning"):
            self.assertIn(term, joined)


class ParserRobustnessTests(unittest.TestCase):
    def test_blocks_without_timing_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "s.srt"
            path.write_text(
                "쓰레기 줄\n\n1\n00:00:01,000 --> 00:00:02,000\n가\n",
                encoding="utf-8")
            blocks = fetch_youtube.parse_srt(path)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0].lines, ("가",))

    def test_crlf_and_bom_are_handled(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "s.srt"
            path.write_bytes(
                "﻿1\r\n00:00:01,000 --> 00:00:02,000\r\n가\r\n".encode("utf-8"))
            blocks = fetch_youtube.parse_srt(path)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0].lines, ("가",))


if __name__ == "__main__":
    unittest.main()
