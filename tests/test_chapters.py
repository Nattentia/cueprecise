from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import chapters


class ChapterTests(unittest.TestCase):
    def _bundle(self, root: Path, duration: int = 1200) -> Path:
        bundle = root / "vid"
        derived, raw = bundle / "derived", bundle / "raw"
        derived.mkdir(parents=True)
        raw.mkdir()
        words = []
        for second in range(0, duration, 5):
            suffix = "." if second % 30 == 25 else ""
            words.append({"text": "retrieval" if second < duration / 2 else "evaluation" + suffix,
                          "start": float(second), "end": float(second) + 0.4})
        (derived / "transcript.json").write_text(json.dumps(
            {"video_id": "vid", "words": words}), encoding="utf-8")
        return bundle

    def test_local_fallback_covers_video_with_bounded_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            result = chapters.build(bundle)
            items = result["chapters"]
            self.assertEqual(items[0]["start"], 0.0)
            self.assertEqual(items[-1]["end"], 1195.4)
            self.assertTrue(all(0 < item["end"] - item["start"] <= chapters.MAX_SECS
                                for item in items))
            self.assertTrue(all(left["end"] == right["start"]
                                for left, right in zip(items, items[1:])))
            self.assertTrue(all(item["title"] for item in items))
            self.assertTrue(all(item["needs_title"] for item in items))

    def test_native_title_is_kept_but_long_native_span_is_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            (bundle / "raw" / "youtube.json").write_text(json.dumps({"chapters": [
                {"start_time": 0, "end_time": 300, "title": "Existing title"},
                {"start_time": 300, "end_time": 1200, "title": "Q&A"},
            ]}), encoding="utf-8")
            result = chapters.build(bundle)
            self.assertEqual(result["chapters"][0]["title"], "Existing title")
            self.assertEqual(result["chapters"][0]["title_source"], "youtube")
            self.assertGreater(len(result["chapters"]), 2)
            self.assertTrue(all(item["title"] != "Q&A" for item in result["chapters"][1:]))

    def test_fetch_metadata_is_reused_for_native_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory), 300)
            (bundle / "raw" / "metadata.json").write_text(json.dumps({"chapters": [
                {"start_time": 0, "end_time": 300, "title": "Cached title"},
            ]}), encoding="utf-8")
            with mock.patch.object(chapters.subprocess, "run") as run:
                result = chapters.build(bundle, url="https://example.com/watch?v=vid")
            run.assert_not_called()
            self.assertEqual(result["chapters"][0]["title"], "Cached title")
            self.assertEqual(result["chapters"][0]["title_source"], "youtube")

    def test_blocked_metadata_command_falls_back_to_local_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory), 300)
            with mock.patch.object(chapters.subprocess, "run",
                                   side_effect=OSError(4551, "blocked")):
                result = chapters.build(bundle, url="https://example.com/watch?v=vid")
            self.assertTrue(result["chapters"])
            self.assertTrue(all(item["title_source"] == "local-keywords"
                                for item in result["chapters"]))

    def test_host_title_commit_validates_fingerprint_and_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory), 300)
            result = chapters.build(bundle)
            chapter_id = result["chapters"][0]["id"]
            updated = chapters.set_titles(
                bundle, fingerprint=result["transcript_fingerprint"],
                titles=[{"id": chapter_id, "title": "Host title"}],
            )
            self.assertEqual(updated["chapters"][0]["title"], "Host title")
            self.assertEqual(updated["chapters"][0]["title_source"], "host-llm")
            rebuilt = chapters.build(bundle)
            self.assertEqual(rebuilt["chapters"][0]["title"], "Host title")
            self.assertEqual(rebuilt["chapters"][0]["title_source"], "host-llm")
            with self.assertRaises(ValueError):
                chapters.set_titles(bundle, fingerprint="stale", titles=[])
            with self.assertRaises(ValueError):
                chapters.set_titles(
                    bundle, fingerprint=result["transcript_fingerprint"],
                    titles=[{"id": "missing", "title": "Nope"}],
                )


if __name__ == "__main__":
    unittest.main()


class TitleClampTests(unittest.TestCase):
    def test_long_youtube_title_is_shortened_at_a_boundary(self) -> None:
        long_title = ("이물 반응으로 인한 흉터 조직 형성 및 신호 감도 감소 문제 해결책 "
                      "🧪 데이트 중 진심을 읽는다? 뉴로마케팅과 일반인 타깃 비침습 뇌파 모자의 대중화 "
                      "대한민국 반도체 인프라를 활용한 추격: BCI 사업단장 공모와 국가 과제의 시작 "
                      "빌 게이츠, 제프 베이조스, 샘 올트먼의 BCI 투자 경쟁")
        clamped = chapters._clamp_title(long_title)
        self.assertLessEqual(len(clamped), chapters.MAX_TITLE_CHARS + 1)
        self.assertTrue(clamped.startswith("이물 반응으로"))
        self.assertTrue(clamped.endswith("…"))
        self.assertNotIn("  ", clamped)

    def test_short_title_is_left_alone(self) -> None:
        self.assertEqual(chapters._clamp_title("생물학적 신경망과 컴퓨터의 결합"),
                         "생물학적 신경망과 컴퓨터의 결합")

    def test_newlines_are_collapsed(self) -> None:
        self.assertEqual(chapters._clamp_title("앞줄\n\n뒷줄"), "앞줄 뒷줄")
