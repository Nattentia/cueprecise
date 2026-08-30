"""transcribe 응답 파싱과 검증 테스트. Gemini API 를 호출하지 않는다.

`transcribe.py` 는 google-genai 를 임포트한다. SDK 가 없는 환경에서는
모듈 전체를 건너뛴다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

try:
    import transcribe
except ImportError:  # pragma: no cover - SDK 미설치 환경
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


@unittest.skipIf(transcribe is None, "google-genai 미설치")
class OffsetParsingTests(unittest.TestCase):
    def test_seconds_suffix_is_stripped(self) -> None:
        self.assertAlmostEqual(transcribe._offset("12.34s"), 12.34)

    def test_bare_number_is_accepted(self) -> None:
        self.assertAlmostEqual(transcribe._offset("7"), 7.0)

    def test_none_stays_none(self) -> None:
        self.assertIsNone(transcribe._offset(None))


@unittest.skipIf(transcribe is None, "google-genai 미설치")
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


@unittest.skipIf(transcribe is None, "google-genai 미설치")
class GuardTests(unittest.TestCase):
    def test_missing_audio_file_is_rejected_before_any_call(self) -> None:
        with self.assertRaises(FileNotFoundError):
            transcribe.transcribe("존재하지-않는-파일.mp3", "auto")


if __name__ == "__main__":
    unittest.main()
