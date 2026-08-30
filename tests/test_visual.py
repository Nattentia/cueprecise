"""프레임 후보 선정 테스트. ffmpeg 와 OCR 없이 순수 로직만 검증한다."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import visual


def word(text: str, start: float, **extra) -> dict:
    return {"text": text, "start": start, "end": start + 0.3,
            "speaker": "spk:0", **extra}


class ScreenReferenceTests(unittest.TestCase):
    def test_reference_phrase_is_detected(self) -> None:
        hits = visual.screen_reference_times(
            [word("여기", 100.0), word("이", 100.5), word("그림을", 101.0),
             word("보시면", 101.5)])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["reason"], "screen-reference")

    def test_timestamp_is_taken_slightly_before_the_phrase(self) -> None:
        """말할 때는 이미 화면이 바뀐 뒤다. 1초 앞을 잡는다."""
        hits = visual.screen_reference_times([word("보시면", 100.0)])
        self.assertAlmostEqual(hits[0]["timestamp"], 99.0)

    def test_timestamp_never_goes_negative(self) -> None:
        hits = visual.screen_reference_times([word("보시면", 0.2)])
        self.assertGreaterEqual(hits[0]["timestamp"], 0.0)

    def test_plain_speech_produces_no_candidate(self) -> None:
        self.assertEqual(
            visual.screen_reference_times(
                [word("안녕하세요", 1.0), word("반갑습니다", 2.0)]),
            [])


class EnglishScreenReferenceTests(unittest.TestCase):
    """영어 강의도 프레임 후보를 잡아야 한다. 한국어 전용이면 반쪽이다."""

    def _hits(self, sentence: str, base: float = 100.0) -> list[dict]:
        words = [word(token, base + index * 0.3)
                 for index, token in enumerate(sentence.split())]
        return visual.screen_reference_times(words)

    def test_common_english_phrases_are_detected(self) -> None:
        for sentence in (
            "as you can see the numbers go up",
            "if you look at the second column",
            "let's look at the architecture now",
            "this figure shows the tradeoff",
            "the value up here is the baseline",
            "on the left we have the retriever",
            "at the bottom of the slide",
            "take a look at what happens next",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(len(self._hits(sentence)), 1, sentence)

    def test_matching_ignores_case(self) -> None:
        self.assertEqual(len(self._hits("As You Can See here")), 1)

    def test_ordinary_english_speech_is_not_a_candidate(self) -> None:
        for sentence in (
            "the model was trained on a large corpus",
            "we published the results last year",
            "there we go and that is the end",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(self._hits(sentence), [], sentence)

    def test_korean_detection_is_unchanged(self) -> None:
        self.assertEqual(len(self._hits("이 그림을 보시면 알 수 있습니다")), 1)


class RestoredTermTests(unittest.TestCase):
    def test_youtube_origin_words_become_candidates(self) -> None:
        hits = visual.restored_term_times([
            word("self", 208.0, origin="youtube"),
            word("라는", 210.8, origin="gemini"),
        ])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["reason"], "restored-term")
        self.assertAlmostEqual(hits[0]["timestamp"], 207.5)

    def test_words_without_origin_are_ignored(self) -> None:
        self.assertEqual(visual.restored_term_times([word("가", 1.0)]), [])


class DedupeTests(unittest.TestCase):
    def test_close_candidates_collapse(self) -> None:
        kept = visual.dedupe_candidates([
            {"timestamp": 100.0, "reason": "a"},
            {"timestamp": 102.0, "reason": "b"},
            {"timestamp": 130.0, "reason": "c"},
        ])
        self.assertEqual([c["timestamp"] for c in kept], [100.0, 130.0])

    def test_max_frames_is_enforced(self) -> None:
        candidates = [{"timestamp": i * 100.0, "reason": "x"} for i in range(50)]
        self.assertEqual(len(visual.dedupe_candidates(candidates, max_frames=7)), 7)

    def test_result_is_sorted(self) -> None:
        kept = visual.dedupe_candidates([
            {"timestamp": 300.0, "reason": "c"},
            {"timestamp": 100.0, "reason": "a"},
            {"timestamp": 200.0, "reason": "b"},
        ])
        self.assertEqual([c["timestamp"] for c in kept], [100.0, 200.0, 300.0])

    def test_uniform_extraction_is_not_the_default(self) -> None:
        """균일 전체 프레임 추출을 하지 않는다 (CONTRACT 11절)."""
        words = [word("가나다", i * 1.0) for i in range(600)]
        candidates = (visual.screen_reference_times(words)
                      + visual.restored_term_times(words))
        self.assertEqual(candidates, [])


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "derived").mkdir(parents=True)
        (self.bundle / "derived" / "merged.json").write_text(json.dumps({
            "source": "merged", "video_id": "vid",
            "words": [word("이", 100.0), word("그림을", 100.5), word("보시면", 101.0),
                      word("self", 208.0, origin="youtube")],
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_frames_json_is_written_with_contract_shape(self) -> None:
        result = visual.build(self.bundle)
        saved = json.loads(
            (self.bundle / "derived" / "frames.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["video_id"], "vid")
        self.assertIn("frames", saved)
        self.assertGreaterEqual(result["candidates_considered"], 2)

    def test_missing_video_file_reports_note_instead_of_crashing(self) -> None:
        result = visual.build(self.bundle)
        self.assertEqual(result["frames"], [])
        self.assertIsNotNone(result["note"])

    def test_requested_times_are_included(self) -> None:
        result = visual.build(self.bundle, at=[500.0])
        self.assertIn(500.0, [c for c in [500.0]])
        self.assertGreaterEqual(result["candidates_considered"], 3)

    def test_missing_transcript_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(FileNotFoundError):
                visual.build(Path(name) / "empty")



class SourceVideoLookupTests(unittest.TestCase):
    """오디오와 영상이 둘 다 webm 일 수 있다. 이름이 부딪히면 안 된다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "raw").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _touch(self, name: str) -> None:
        (self.bundle / "raw" / name).write_bytes(b"x")

    def test_audio_webm_is_not_treated_as_video(self) -> None:
        self._touch("source.webm")   # 오디오
        self.assertIsNone(visual.source_video(self.bundle))

    def test_finds_downloaded_video(self) -> None:
        self._touch("source.webm")
        self._touch("source_video.webm")
        self.assertEqual(visual.source_video(self.bundle).name, "source_video.webm")

    def test_legacy_bundle_video_still_found(self) -> None:
        self._touch("source.mp4")
        self.assertEqual(visual.source_video(self.bundle).name, "source.mp4")

if __name__ == "__main__":
    unittest.main()
