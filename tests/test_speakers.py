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

    def test_single_chunk_keeps_adjacent_repeats(self) -> None:
        # 더듬음·열거로 같은 단어가 0.75초 안에 다시 나온다. 단일 청크에는
        # overlap 이 없으므로 하나도 지우면 안 된다.
        chunks = [{"chunk_index": 0, "chunk_start": 0.0, "chunk_end": 20.0, "words": [
            word("의사", 8.0, "spk:0"), word("A", 8.4, "spk:0"),
            word("의사", 8.9, "spk:0"), word("B", 9.3, "spk:0"),
            word("메드", 12.0, "spk:0"), word("팜", 12.3, "spk:0"),
            word("메드", 12.6, "spk:0"), word("팜", 12.9, "spk:0"),
        ]}]
        result = speakers.reconcile_chunks(chunks)
        self.assertEqual(len(result["words"]), 8)
        self.assertEqual(result["speaker_mapping"]["duplicates_removed"], 0)

    def test_repeats_outside_overlap_survive_but_overlap_dupes_go(self) -> None:
        # 청크 경계는 30.0. overlap 재전사(28~30초)는 지우고, 청크 고유
        # 구간의 인접 반복어는 보존한다.
        chunks = [
            {"chunk_index": 0, "chunk_start": 0.0, "chunk_end": 30.0, "words": [
                word("몇", 10.0, "spk:0"), word("년", 10.4, "spk:0"),
                word("몇", 10.8, "spk:0"), word("월", 11.2, "spk:0"),
                word("tail", 29.0, "spk:0"),
            ]},
            {"chunk_index": 1, "chunk_start": 28.0, "chunk_end": 60.0, "words": [
                word("tail", 29.0, "spk:0"),            # 진짜 overlap 중복
                word("또", 40.0, "spk:0"), word("또", 40.4, "spk:0"),  # 고유 반복어
            ]},
        ]
        result = speakers.reconcile_chunks(chunks)
        texts = [w["text"] for w in result["words"]]
        self.assertEqual(result["speaker_mapping"]["duplicates_removed"], 1)
        self.assertEqual(texts.count("tail"), 1)
        self.assertEqual(texts.count("몇"), 2)
        self.assertEqual(texts.count("또"), 2)


class ScaleTests(unittest.TestCase):
    """긴 영상에서 이어붙이기가 전수 대조로 돌아가면 안 된다.

    수정 전에는 앞 청크 전체 × 현재 청크 전체를 대조해 3청크 15,900단어에
    170초가 걸렸다. 텍스트 버킷 조회로 바꾼 뒤 0.1초다. 넉넉한 상한을 둬서
    전수 대조로 되돌아가는 회귀만 잡는다.
    """

    @staticmethod
    def _chunk(index: int, start: float, count: int) -> dict:
        # 어휘가 좁아 같은 텍스트가 대량으로 겹친다 (버킷 조회의 최악 조건).
        vocab = ["그리고", "이제", "모델", "학습", "데이터"]
        return {
            "chunk_index": index, "chunk_start": start, "chunk_end": start + count * 0.3,
            "words": [
                word(vocab[i % len(vocab)], start + i * 0.3, "spk:%d" % (i % 2))
                for i in range(count)
            ],
        }

    def test_many_chunks_do_not_degrade(self) -> None:
        import time

        chunks = [self._chunk(i, i * 590.0, 2000) for i in range(3)]
        started = time.monotonic()
        result = speakers.reconcile_chunks(chunks)
        elapsed = time.monotonic() - started
        self.assertGreater(len(result["words"]), 5000)
        self.assertLess(elapsed, 5.0, "이어붙이기가 전수 대조로 되돌아갔다")


if __name__ == "__main__":
    unittest.main()
