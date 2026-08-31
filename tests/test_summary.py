from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import chapters
import context
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
        self.assertFalse(path.exists(), "요약은 별도 파일을 만들지 않는다")
        self.assertTrue(result["stored"], "요약이 색인에 저장되지 않았다")
        self.assertEqual(result["generation"], "local-extractive")
        self.assertIn("# Test video", result["summary"])
        self.assertTrue(result["packet"])

    def test_summary_survives_reindex(self) -> None:
        """재색인은 DB 를 통째로 갈아끼운다. 요약이 딸려 나가면 안 된다."""
        local = summary.build(self.bundle)
        context.build_index(self.bundle)
        self.assertEqual(context.read_summary(self.bundle), local["summary"])
        self.assertEqual(summary.build(self.bundle)["summary"], local["summary"])

    def test_legacy_summary_file_is_absorbed_and_removed(self) -> None:
        """파일로 보관하던 옛 요약은 색인으로 옮기고 파일을 지운다."""
        local = summary.build(self.bundle)
        legacy = self.bundle / "derived" / "summary.md"
        legacy.write_text(local["summary"], encoding="utf-8")
        context.write_summary(self.bundle, "옛 색인 값")
        self.assertEqual(summary._stored_summary(self.bundle), "옛 색인 값")
        self.assertTrue(legacy.exists(), "색인에 값이 있으면 파일을 건드리지 않는다")

        legacy_only = self.bundle / "index.sqlite3"
        legacy_only.unlink()
        context.build_index(self.bundle)
        self.assertIsNone(context.read_summary(self.bundle))
        moved = summary._stored_summary(self.bundle)
        self.assertEqual(moved, local["summary"])
        self.assertFalse(legacy.exists(), "옮긴 뒤에도 파일이 남았다")

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
