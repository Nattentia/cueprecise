from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import context


class ContextIndexTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "video-1"
        derived = bundle / "derived"
        derived.mkdir(parents=True)
        transcript = {
            "source": "merged",
            "video_id": "video-1",
            "words": [
                {"text": "self", "start": 1.0, "end": 1.2, "speaker": "spk:0"},
                {"text": "supervised", "start": 1.2, "end": 1.7, "speaker": "spk:0"},
                {"text": "learning", "start": 1.7, "end": 2.1, "speaker": "spk:0"},
                {"text": "검색", "start": 35.0, "end": 35.4, "speaker": "spk:1"},
                {"text": "증강", "start": 35.4, "end": 35.8, "speaker": "spk:1"},
            ],
        }
        (derived / "merged.json").write_text(json.dumps(transcript), encoding="utf-8")
        (derived / "chapters.json").write_text(json.dumps({
            "chapters": [{"start": 0, "end": 20, "title": "학습 방식"}]
        }), encoding="utf-8")
        (derived / "frames.json").write_text(json.dumps({
            "frames": [{"timestamp": 1.5, "ocr_text": "self supervised learning", "confidence": 0.8}]
        }), encoding="utf-8")
        return bundle

    def test_build_persists_all_evidence_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            index = context.build_index(bundle)
            connection = sqlite3.connect(index)
            try:
                kinds = {row[0] for row in connection.execute("SELECT DISTINCT source_kind FROM evidence")}
                video_id = connection.execute("SELECT value FROM metadata WHERE key='video_id'").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(kinds, {"transcript", "chapter", "frame"})
            self.assertEqual(video_id, "video-1")

    def test_search_returns_timestamped_provenance_after_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            index = context.build_index(bundle)
            results = context.search(index, "self supervised learning")
            self.assertGreaterEqual(len(results), 2)
            self.assertEqual(results[0]["video_id"], "video-1")
            self.assertIn(results[0]["source_kind"], {"transcript", "frame"})
            self.assertIn("source_path", results[0])
            self.assertLessEqual(results[0]["start"], 1.5)

    def test_rebuild_replaces_stale_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            index = context.build_index(bundle)
            self.assertTrue(context.search(index, "검색"))
            payload = json.loads((bundle / "derived" / "merged.json").read_text(encoding="utf-8"))
            payload["words"] = payload["words"][:3]
            (bundle / "derived" / "merged.json").write_text(json.dumps(payload), encoding="utf-8")
            context.build_index(bundle)
            self.assertEqual(context.search(index, "검색"), [])

    def test_missing_transcript_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                context.build_index(Path(directory))


if __name__ == "__main__":
    unittest.main()
