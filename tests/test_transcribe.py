"""transcribe 응답 파싱·복구 테스트. Gemini API 를 호출하지 않는다.

`transcribe.py` 는 google-genai 를 실제 호출 경로 안에서만 임포트하므로
파싱·복구 테스트는 SDK 없이도 전부 돈다.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

try:
    import transcribe
except ImportError:  # pragma: no cover - 임포트 실패는 회귀다
    transcribe = None


def annotation(text: str, start: str, end: str, speaker: str | None = "spk:0") -> dict:
    payload = {"type": "word_info", "text": text,
               "start_offset": start, "end_offset": end}
    if speaker is not None:
        payload["speaker"] = speaker
    return payload


def response(*annotations: dict) -> dict:
    return {"steps": [{"type": "model_output",
                       "content": [{"type": "text", "annotations": list(annotations)}]}]}


@unittest.skipIf(transcribe is None, "transcribe 임포트 실패")
class OffsetParsingTests(unittest.TestCase):
    def test_seconds_suffix_is_stripped(self) -> None:
        self.assertAlmostEqual(transcribe._offset("12.34s"), 12.34)

    def test_bare_number_is_accepted(self) -> None:
        self.assertAlmostEqual(transcribe._offset("7"), 7.0)

    def test_none_stays_none(self) -> None:
        self.assertIsNone(transcribe._offset(None))


@unittest.skipIf(transcribe is None, "transcribe 임포트 실패")
class WordExtractionTests(unittest.TestCase):
    def test_word_info_becomes_contract_shape(self) -> None:
        words = transcribe._extract_words(response(
            annotation("안녕하세요", "0.9s", "1.6s"),
            annotation("반갑습니다", "1.7s", "2.4s"),
        ))
        self.assertEqual(len(words), 2)
        self.assertEqual(sorted(words[0]), ["end", "speaker", "start", "text"])
        self.assertAlmostEqual(words[0]["start"], 0.9)
        self.assertEqual(words[0]["speaker"], "spk:0")

    def test_missing_speaker_is_allowed(self) -> None:
        words = transcribe._extract_words(
            response(annotation("가", "0.0s", "0.3s", speaker=None)))
        self.assertIsNone(words[0]["speaker"])

    def test_empty_text_is_dropped(self) -> None:
        words = transcribe._extract_words(response(
            annotation("  ", "0.0s", "0.3s"), annotation("가", "0.4s", "0.7s")))
        self.assertEqual([w["text"] for w in words], ["가"])

    def test_response_without_word_info_is_rejected(self) -> None:
        with self.assertRaises(transcribe.TranscriptionResultError):
            transcribe._extract_words({"steps": [
                {"type": "model_output", "content": [{"type": "text", "annotations": []}]}]})

    def test_non_object_response_is_rejected(self) -> None:
        with self.assertRaises(transcribe.TranscriptionResultError):
            transcribe._extract_words([])  # type: ignore[arg-type]

    def test_error_message_points_at_configuration(self) -> None:
        try:
            transcribe._extract_words({"steps": []})
        except transcribe.TranscriptionResultError as error:
            self.assertIn("word", str(error).lower())
        else:
            self.fail("빈 응답인데 오류가 나지 않았다")


@unittest.skipIf(transcribe is None, "transcribe 임포트 실패")
class TimestampRepairTests(unittest.TestCase):
    """단어 하나가 깨졌다고 청크 전체를 버리지 않는다. 실측 사례 기준.

    Gemini 가 'language' 의 end_offset 1120.3 을 120.3 으로 보냈다
    (앞자리 누락). 다음 단어 'model' 이 1120.3 에서 시작하므로 이웃으로
    정확히 복원할 수 있다.
    """

    def test_broken_end_is_restored_from_next_word(self) -> None:
        payload = transcribe.parse_raw(response(
            annotation("but", "1119.6s", "1119.8s"),
            annotation("language", "1119.8s", "120.3s"),   # end < start
            annotation("model", "1120.3s", "1120.6s"),
        ))
        broken = payload["words"][1]
        self.assertAlmostEqual(broken["start"], 1119.8)
        self.assertAlmostEqual(broken["end"], 1120.3)
        self.assertEqual(broken["timestamp_repaired"], ["end"])
        self.assertEqual(len(payload["words"]), 3, "단어를 버렸다")

    def test_repair_is_recorded_with_before_and_after(self) -> None:
        payload = transcribe.parse_raw(response(
            annotation("a", "1119.8s", "120.3s"),
            annotation("b", "1120.3s", "1120.6s"),
        ))
        record = payload["timestamp_repairs"][0]
        self.assertEqual(record["field"], "end")
        self.assertAlmostEqual(record["from"], 120.3)
        self.assertAlmostEqual(record["to"], 1120.3)

    def test_clean_response_records_no_repairs(self) -> None:
        payload = transcribe.parse_raw(response(
            annotation("가", "0.0s", "0.3s"), annotation("나", "0.4s", "0.7s")))
        self.assertNotIn("timestamp_repairs", payload)
        self.assertNotIn("timestamp_repaired", payload["words"][0])

    def test_broken_last_word_falls_back_to_duration_cap(self) -> None:
        payload = transcribe.parse_raw(response(
            annotation("가", "0.0s", "0.4s"),
            annotation("나", "10.0s", "1.0s"),   # 뒤에 이웃이 없다
        ))
        last = payload["words"][-1]
        self.assertGreaterEqual(last["end"], last["start"])
        self.assertLessEqual(last["end"] - last["start"], 1.0)

    def test_reversed_start_is_pulled_back_to_previous_end(self) -> None:
        payload = transcribe.parse_raw(response(
            annotation("가", "10.0s", "10.4s"),
            annotation("나", "1.5s", "10.8s"),   # 앞 단어보다 이른 start
        ))
        second = payload["words"][1]
        self.assertAlmostEqual(second["start"], 10.4)
        self.assertEqual(second["timestamp_repaired"], ["start"])

    def test_missing_offsets_are_repaired_not_fatal(self) -> None:
        payload = transcribe.parse_raw(response(
            annotation("가", "0.0s", "0.4s"),
            {"type": "word_info", "text": "나", "speaker": "spk:0"},
        ))
        self.assertEqual(len(payload["words"]), 2)
        self.assertEqual(payload["words"][1]["timestamp_repaired"], ["start", "end"])

    def test_widespread_corruption_still_aborts(self) -> None:
        # 10단어 중 4건 손상. 허용치(max(3, 0.5%))를 넘으므로 중단해야 한다.
        annotations = [annotation("ok%d" % i, "%d.0s" % i, "%d.4s" % i) for i in range(6)]
        annotations += [annotation("bad%d" % i, "100.0s", "1.0s") for i in range(4)]
        with self.assertRaises(transcribe.TranscriptionResultError) as caught:
            transcribe.parse_raw(response(*annotations))
        self.assertIn("손상", str(caught.exception))

    def test_monotonic_order_holds_after_repair(self) -> None:
        payload = transcribe.parse_raw(response(
            annotation("가", "0.0s", "0.4s"),
            annotation("나", "0.4s", "0.1s"),
            annotation("다", "0.9s", "1.2s"),
        ))
        words = payload["words"]
        for previous, following in zip(words, words[1:]):
            self.assertLessEqual(previous["start"], following["start"])
            self.assertLessEqual(previous["start"], previous["end"])


@unittest.skipIf(transcribe is None, "transcribe 임포트 실패")
class RawRoundTripTests(unittest.TestCase):
    """저장된 응답 원문으로 호출 없이 결과를 다시 만든다."""

    def test_from_raw_rebuilds_payload_without_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chunk-000.raw.json"
            path.write_text(json.dumps({
                "model": transcribe.MODEL, "requested_langs": "en-US",
                "language_codes": ["en-US"],
                "response": response(annotation("hello", "0.0s", "0.5s")),
            }, ensure_ascii=False), encoding="utf-8")
            payload = transcribe.from_raw(path)
        self.assertEqual([w["text"] for w in payload["words"]], ["hello"])
        self.assertEqual(payload["language_codes"], ["en-US"])

    def test_from_raw_repairs_the_same_way(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            path.write_text(json.dumps({
                "requested_langs": "auto", "language_codes": None,
                "response": response(annotation("a", "1119.8s", "120.3s"),
                                     annotation("b", "1120.3s", "1120.6s")),
            }, ensure_ascii=False), encoding="utf-8")
            payload = transcribe.from_raw(path)
        self.assertAlmostEqual(payload["words"][0]["end"], 1120.3)


@unittest.skipIf(transcribe is None, "transcribe 임포트 실패")
class GuardTests(unittest.TestCase):
    def test_missing_audio_file_is_rejected_before_any_call(self) -> None:
        with self.assertRaises(FileNotFoundError):
            transcribe.transcribe("존재하지-않는-파일.mp3", "auto")


if __name__ == "__main__":
    unittest.main()
