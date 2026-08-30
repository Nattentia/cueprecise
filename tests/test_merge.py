"""merge 삽입 판정과 타임스탬프 배분 테스트. Gemini API 를 호출하지 않는다."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import merge

FIXTURE = Path(__file__).parent / "fixtures" / "jcBDSLSeud4.gemini-auto.json"


def word(text: str, start: float, end: float, speaker: str = "spk:0") -> dict:
    return {"text": text, "start": start, "end": end, "speaker": speaker}


def cue(text: str, start: float, end: float) -> dict:
    return {"start": start, "end": end, "text": text}


def run(words: list[dict], cues: list[dict]) -> dict:
    return merge.merge_payloads(
        {"source": "gemini", "video_id": "v", "words": words},
        {"source": "youtube-ko-orig", "video_id": "v", "cues": cues},
    )


class InsertionPolicyTests(unittest.TestCase):
    def test_gap_with_dangling_particle_restores_english(self) -> None:
        result = run(
            [word("했을까요?", 207.6, 208.0), word("라는", 210.8, 211.0)],
            [cue("했을까요? self", 207.68, 210.2), cue("supervised", 209.4, 211.8)],
        )
        restored = [w["text"] for w in result["words"] if w["origin"] == "youtube"]
        self.assertEqual(restored, ["self", "supervised"])

    def test_gap_without_particle_inserts_nothing(self) -> None:
        """공백만으로는 삽입하지 않는다. 정상 무음까지 후보가 되면 안 된다."""
        result = run(
            [word("있습니다.", 200.0, 200.4), word("그러면", 206.0, 206.2)],
            [cue("있습니다. self supervised", 200.0, 206.5)],
        )
        self.assertEqual([w for w in result["words"] if w["origin"] == "youtube"], [])

    def test_short_gap_with_particle_inserts_nothing(self) -> None:
        """조사만으로도 삽입하지 않는다. 두 근거가 모두 필요하다."""
        result = run(
            [word("것을", 100.0, 100.4), word("라고", 100.9, 101.2)],
            [cue("것을 retrieval 라고", 100.0, 101.5)],
        )
        self.assertEqual([w for w in result["words"] if w["origin"] == "youtube"], [])

    def test_token_already_in_gemini_is_not_duplicated(self) -> None:
        result = run(
            [word("RAG의", 100.0, 100.4), word("것을", 101.0, 101.4),
             word("라고", 104.0, 104.3)],
            [cue("것을 RAG 라고", 101.0, 104.5)],
        )
        self.assertEqual([w for w in result["words"] if w["origin"] == "youtube"], [])

    def test_cue_outside_gap_is_not_pulled_in(self) -> None:
        """CAPTION_LOOKAHEAD 회귀 방지. 공백과 겹치지 않는 cue 는 쓰지 않는다."""
        result = run(
            [word("것을", 678.3, 678.7), word("라고", 680.7, 681.0)],
            [cue("retriever먼트 제이션이라고", 679.2, 683.23),
             cue("RG의 장점이 뭐냐?", 683.24, 688.83)],
        )
        restored = [w["text"] for w in result["words"] if w["origin"] == "youtube"]
        self.assertEqual(restored, ["retriever"])
        self.assertNotIn("RG", restored)

    def test_one_cue_token_is_consumed_only_once(self) -> None:
        """한 cue 가 인접한 두 공백에 걸쳐도 중복 삽입하지 않는다."""
        result = run(
            [word("것을", 678.3, 678.7), word("라고", 680.7, 681.0),
             word("합니다.", 681.1, 681.4), word("의", 683.1, 683.4)],
            [cue("retriever먼트 제이션이라고", 679.2, 683.23)],
        )
        restored = [w["text"] for w in result["words"] if w["origin"] == "youtube"]
        self.assertEqual(restored.count("retriever"), 1)


class OrderingTests(unittest.TestCase):
    def test_tokens_keep_source_order_across_overlapping_cues(self) -> None:
        """롤링 자막은 cue 구간이 겹친다. 그래도 토큰 순서가 유지돼야 한다."""
        result = run(
            [word("말이냐면", 266.3, 266.7), word("이라는", 269.5, 269.8)],
            [cue("말이냐면 medicine promots", 266.3, 268.6),
             cue("health and", 267.8, 269.0),
             cue("treats illnesses", 268.4, 269.6)],
        )
        restored = [w["text"] for w in result["words"] if w["origin"] == "youtube"]
        self.assertEqual(
            restored, ["medicine", "promots", "health", "and", "treats", "illnesses"])

    def test_inserted_spans_are_monotonic_and_inside_gap(self) -> None:
        result = run(
            [word("했을까요?", 207.6, 208.0), word("라는", 210.8, 211.0)],
            [cue("했을까요? self", 207.68, 210.2), cue("supervised learning", 209.4, 211.8)],
        )
        inserted = [w for w in result["words"] if w["origin"] == "youtube"]
        for previous, current in zip(inserted, inserted[1:]):
            self.assertLessEqual(previous["end"], current["start"] + 1e-6)
        for item in inserted:
            self.assertGreaterEqual(item["start"], 208.0 - 1e-6)
            self.assertLessEqual(item["end"], 210.8 + 1e-6)


class PreservationTests(unittest.TestCase):
    def test_gemini_words_are_never_dropped_or_reordered(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        source = [w for w in payload["words"] if str(w["text"]).strip()]
        result = merge.merge_payloads(
            payload,
            {"source": "youtube-ko-orig", "video_id": payload["video_id"], "cues": []})
        kept = [w for w in result["words"] if w["origin"] == "gemini"]
        self.assertEqual(len(kept), len(source))
        self.assertEqual([w["text"] for w in kept], [w["text"] for w in source])
        self.assertEqual([w["start"] for w in kept], [float(w["start"]) for w in source])

    def test_output_is_sorted_by_start(self) -> None:
        result = run(
            [word("했을까요?", 207.6, 208.0), word("라는", 210.8, 211.0),
             word("합니다.", 211.5, 211.9)],
            [cue("했을까요? self supervised", 207.68, 210.5)],
        )
        starts = [w["start"] for w in result["words"]]
        self.assertEqual(starts, sorted(starts))

    def test_every_word_carries_origin(self) -> None:
        result = run([word("a", 0.0, 0.3), word("의", 3.0, 3.2)],
                     [cue("a token 의", 0.0, 3.5)])
        self.assertTrue(all(w.get("origin") in {"gemini", "youtube"}
                            for w in result["words"]))


class ValidationTests(unittest.TestCase):
    def test_non_monotonic_words_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run([word("b", 5.0, 5.3), word("a", 1.0, 1.3)], [])

    def test_missing_words_array_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            merge.merge_payloads({"source": "gemini"}, {"cues": []})

    def test_merge_files_writes_contract_shape(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            transcript = root / "t.json"
            captions = root / "c.json"
            output = root / "m.json"
            transcript.write_text(json.dumps(
                {"source": "gemini", "video_id": "v",
                 "words": [word("했을까요?", 207.6, 208.0), word("라는", 210.8, 211.0)]},
                ensure_ascii=False), encoding="utf-8")
            captions.write_text(json.dumps(
                {"source": "youtube-ko-orig", "video_id": "v",
                 "cues": [cue("했을까요? self", 207.68, 210.2)]},
                ensure_ascii=False), encoding="utf-8")
            merge.merge_files(transcript, captions, output)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["source"], "merged")
            self.assertEqual(saved["video_id"], "v")
            self.assertIn("origin", saved["words"][0])


if __name__ == "__main__":
    unittest.main()
