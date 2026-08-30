from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

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
