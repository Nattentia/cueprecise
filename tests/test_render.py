"""render 무손실 규약 테스트 (CONTRACT.md 3절). API 호출 없음."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import render

FIXTURE = Path(__file__).parent / "fixtures" / "jcBDSLSeud4.gemini-auto.json"


def word(text: str, start: float, end: float, speaker: str = "spk:0") -> dict:
    return {"text": text, "start": start, "end": end, "speaker": speaker}


class LosslessTests(unittest.TestCase):
    """초기 비교 도구가 lines[:2]로 초과분을 버린 실패를 재발하지 않는지 본다."""

    def test_long_run_without_pause_keeps_every_word(self) -> None:
        words = [word("단어%d" % i, i * 0.1, i * 0.1 + 0.09) for i in range(400)]
        cues = render.build_cues(words, 20)
        flat = [w for cue in cues for w in cue.words]
        self.assertEqual(len(flat), 400)
        self.assertEqual([w["text"] for w in flat], [w["text"] for w in words])

    def test_overflow_moves_to_next_cue_not_truncated(self) -> None:
        words = [word("가나다라마바사", i * 0.1, i * 0.1 + 0.09) for i in range(12)]
        cues = render.build_cues(words, 20)
        self.assertGreater(len(cues), 1, "넘치는 텍스트가 새 큐로 가지 않았다")
        self.assertEqual(sum(len(c.words) for c in cues), 12)

    def test_fixture_words_survive_render(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        words = [w for w in payload["words"] if str(w["text"]).strip()]
        cues = render.build_cues(payload["words"], 20)
        flat = [w for cue in cues for w in cue.words]
        self.assertEqual(len(flat), len(words))

    def test_single_token_longer_than_two_lines_is_kept_intact(self) -> None:
        long_token = "가" * 120
        cues = render.build_cues([word(long_token, 0.0, 1.0)], 20)
        joined = " ".join(line for cue in cues for line in cue.lines)
        self.assertIn(long_token, joined.replace(" ", ""))


class CueConstraintTests(unittest.TestCase):
    def _words(self) -> list[dict]:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        return payload["words"]

    def test_cues_respect_line_and_duration_limits(self) -> None:
        cues = render.build_cues(self._words(), 20)
        over_lines = [c for c in cues if len(c.lines) > render.MAX_LINES]
        over_duration = [c for c in cues if c.end - c.start > render.MAX_DURATION + 1e-9]
        over_width = [line for c in cues for line in c.lines if len(line) > 20]
        self.assertEqual(over_lines, [])
        self.assertEqual(over_duration, [])
        self.assertEqual(over_width, [])

    def test_speaker_change_splits_cue(self) -> None:
        words = [word("가", 0.0, 0.2, "spk:0"), word("나", 0.2, 0.4, "spk:1")]
        self.assertEqual(len(render.build_cues(words, 20)), 2)

    def test_silence_gap_splits_cue(self) -> None:
        words = [word("가", 0.0, 0.2), word("나", 5.0, 5.2)]
        self.assertEqual(len(render.build_cues(words, 20)), 2)

    def test_width_argument_changes_wrapping(self) -> None:
        words = [word("abcde", i * 0.1, i * 0.1 + 0.09) for i in range(6)]
        self.assertGreater(len(render.build_cues(words, 10)),
                           len(render.build_cues(words, 60)))

    def test_zero_width_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            render.build_cues([word("가", 0.0, 0.2)], 0)


class OutputFormatTests(unittest.TestCase):
    def test_srt_is_well_formed_and_txt_matches_word_count(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "in.json"
            source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            srt_path, txt_path = render.render(source, root / "out", 20)

            srt = srt_path.read_text(encoding="utf-8")
            blocks = [b for b in re.split(r"\n\s*\n", srt.strip()) if b.strip()]
            self.assertGreater(len(blocks), 0)
            for index, block in enumerate(blocks, 1):
                rows = block.splitlines()
                self.assertEqual(rows[0], str(index), "SRT 번호가 연속이 아니다")
                self.assertRegex(
                    rows[1], r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")

            words = [w for w in payload["words"] if str(w["text"]).strip()]
            self.assertEqual(len(txt_path.read_text(encoding="utf-8").split()), len(words))

    def test_missing_words_array_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "in.json"
            source.write_text(json.dumps({"source": "gemini"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                render.render(source, root / "out", 20)

    def test_merged_json_with_origin_renders_unchanged(self) -> None:
        """render 는 origin 필드를 무시하고 transcript/merged 를 같게 처리한다."""
        plain = [word("가", 0.0, 0.2), word("나", 0.3, 0.5)]
        tagged = [{**w, "origin": "gemini"} for w in plain]
        self.assertEqual(
            [c.lines for c in render.build_cues(plain, 20)],
            [c.lines for c in render.build_cues(tagged, 20)])


if __name__ == "__main__":
    unittest.main()
