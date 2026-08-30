from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import chapters
import summary


class SummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        derived = self.bundle / "derived"
        raw = self.bundle / "raw"
        derived.mkdir(parents=True)
        raw.mkdir()
        words = [{"text": "retrieval" if second < 300 else "evaluation.",
                  "start": float(second), "end": float(second) + 0.4}
                 for second in range(0, 600, 5)]
        (derived / "transcript.json").write_text(json.dumps(
            {"video_id": "vid", "words": words}), encoding="utf-8")
        (raw / "youtube.json").write_text(json.dumps({"title": "Test video"}),
                                           encoding="utf-8")
        chapters.build(self.bundle)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_local_summary_is_created_only_when_requested(self) -> None:
        path = self.bundle / "derived" / "summary.md"
        self.assertFalse(path.exists())
        result = summary.build(self.bundle)
        self.assertTrue(path.exists())
        self.assertEqual(result["generation"], "local-extractive")
        self.assertIn("# Test video", result["summary"])
        self.assertTrue(result["packet"])

    def test_current_host_summary_is_reused(self) -> None:
        local = summary.build(self.bundle)
        items = json.loads((self.bundle / "derived" / "chapters.json").read_text(
            encoding="utf-8"))["chapters"]
        content = {
            "overview": "검색과 평가를 설명한다.",
            "key_points": [{"text": "검색을 다룬다.", "chapter_ids": [items[0]["id"]]}],
            "chapter_summaries": [
                {"id": item["id"], "bullets": ["이 구간의 핵심 설명이다."]}
                for item in items
            ],
            "terms": [{"term": "retrieval", "meaning": "정보 검색",
                       "chapter_ids": [items[0]["id"]]}],
        }
        saved = summary.set_host_summary(self.bundle, fingerprint=local["fingerprint"],
                                         content=content)
        reused = summary.build(self.bundle)
        self.assertEqual(saved["summary"], reused["summary"])
        self.assertEqual(reused["generation"], "host-llm")
        self.assertFalse(reused["needs_host_summary"])
        self.assertIsNone(reused["packet"])

    def test_host_cannot_invent_chapter_or_omit_one(self) -> None:
        local = summary.build(self.bundle)
        with self.assertRaises(ValueError):
            summary.set_host_summary(self.bundle, fingerprint=local["fingerprint"], content={
                "overview": "개요",
                "key_points": [{"text": "핵심", "chapter_ids": ["missing"]}],
                "chapter_summaries": [], "terms": [],
            })

    def test_changed_chapter_title_invalidates_saved_summary(self) -> None:
        local = summary.build(self.bundle)
        chapter_payload = json.loads((self.bundle / "derived" / "chapters.json").read_text(
            encoding="utf-8"))
        chapters.set_titles(
            self.bundle, fingerprint=chapter_payload["transcript_fingerprint"],
            titles=[{"id": chapter_payload["chapters"][0]["id"], "title": "새 제목"}],
        )
        rebuilt = summary.build(self.bundle)
        self.assertNotEqual(local["fingerprint"], rebuilt["fingerprint"])
        self.assertEqual(rebuilt["generation"], "local-extractive")
        self.assertIn("새 제목", rebuilt["summary"])


if __name__ == "__main__":
    unittest.main()
