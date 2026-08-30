from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import speakers


def word(text: str, start: float, speaker: str) -> dict:
    return {"text": text, "start": start, "end": start + 0.3, "speaker": speaker}


class SpeakerReconciliationTests(unittest.TestCase):
    def test_swapped_local_labels_follow_overlap_evidence(self) -> None:
        chunks = [
            {"chunk_index": 0, "video_id": "v", "words": [
                word("alpha", 8.0, "spk:0"), word("beta", 8.5, "spk:0"),
                word("gamma", 9.0, "spk:1"), word("delta", 9.5, "spk:1"),
            ]},
            {"chunk_index": 1, "video_id": "v", "words": [
                word("alpha", 8.0, "spk:1"), word("beta", 8.5, "spk:1"),
                word("gamma", 9.0, "spk:0"), word("delta", 9.5, "spk:0"),
                word("later-a", 10.0, "spk:1"), word("later-b", 10.5, "spk:0"),
            ]},
        ]
        result = speakers.reconcile_chunks(chunks)
        by_text = {item["text"]: item for item in result["words"]}
        self.assertEqual(by_text["later-a"]["speaker_global"], "speaker:0")
        self.assertEqual(by_text["later-b"]["speaker_global"], "speaker:1")
        self.assertEqual(by_text["later-a"]["speaker_raw"], "spk:1")
        self.assertEqual(by_text["later-a"]["speaker_evidence"], "overlap")
        self.assertEqual(result["speaker_mapping"]["duplicates_removed"], 4)

    def test_insufficient_evidence_stays_unresolved(self) -> None:
        chunks = [
            {"chunk_index": 0, "words": [word("same", 8.0, "spk:0")]},
            {"chunk_index": 1, "words": [
                word("same", 8.0, "spk:1"), word("later", 10.0, "spk:1"),
            ]},
        ]
        result = speakers.reconcile_chunks(chunks)
        later = next(item for item in result["words"] if item["text"] == "later")
        self.assertIsNone(later["speaker_global"])
        self.assertEqual(later["speaker_status"], "unresolved")
        self.assertEqual(later["speaker"], "spk:1")

    def test_two_local_labels_cannot_claim_one_global_speaker(self) -> None:
        chunks = [
            {"chunk_index": 0, "words": [
                word("one", 8.0, "spk:0"), word("two", 8.5, "spk:0"),
            ]},
            {"chunk_index": 1, "words": [
                word("one", 8.0, "spk:4"), word("two", 8.5, "spk:4"),
                word("one", 8.0, "spk:5"), word("two", 8.5, "spk:5"),
                word("later-4", 10.0, "spk:4"), word("later-5", 10.5, "spk:5"),
            ]},
        ]
        result = speakers.reconcile_chunks(chunks)
        later = [item for item in result["words"] if item["text"].startswith("later")]
        self.assertEqual(sum(item["speaker_global"] == "speaker:0" for item in later), 1)
        self.assertEqual(sum(item["speaker_status"] == "unresolved" for item in later), 1)

    def test_raw_labels_are_never_overwritten(self) -> None:
        result = speakers.reconcile_chunks([
            {"chunk_index": 0, "words": [word("hello", 0.0, "spk:7")]}
        ])
        self.assertEqual(result["words"][0]["speaker_raw"], "spk:7")
        self.assertEqual(result["words"][0]["speaker_global"], "speaker:0")

    def test_missing_speaker_remains_unresolved(self) -> None:
        item = {"text": "hello", "start": 0.0, "end": 0.2, "speaker": None}
        result = speakers.reconcile_chunks([{"chunk_index": 0, "words": [item]}])
        self.assertIsNone(result["words"][0]["speaker_raw"])
        self.assertEqual(result["words"][0]["speaker_status"], "unresolved")


if __name__ == "__main__":
    unittest.main()
