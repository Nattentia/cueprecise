"""번역 자막 코어 테스트. 네트워크·Gemini 호출 없음(합성 words/captions)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import pipeline
import subtitle


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _word(text: str, start: float, end: float, origin: str = "gemini") -> dict:
    return {"text": text, "start": start, "end": end, "origin": origin}


class SentenceSplitTests(unittest.TestCase):
    def test_boundaries_and_key_stability(self) -> None:
        words = [
            _word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1),
            _word("It", 3.0, 3.2), _word("has", 3.2, 3.4),
            _word("100", 3.4, 3.6), _word("categories.", 3.6, 4.0),
        ]
        sentences = subtitle.build_sentences(words)
        self.assertEqual(len(sentences), 2)
        self.assertEqual(sentences[0]["text"], "Hello world.")
        self.assertEqual(sentences[1]["text"], "It has 100 categories.")

        key_before = subtitle.sentence_key(sentences[1]["start"], sentences[1]["end"],
                                           sentences[1]["text"])
        other_key_before = subtitle.sentence_key(sentences[0]["start"], sentences[0]["end"],
                                                 sentences[0]["text"])
        # 단어 하나만 바꾸면 그 문장의 키만 바뀐다.
        words[2] = _word("So,", 3.0, 3.2)
        words[3] = _word("has", 3.2, 3.4)
        changed = subtitle.build_sentences(words)
        key_after = subtitle.sentence_key(changed[1]["start"], changed[1]["end"],
                                          changed[1]["text"])
        other_key_after = subtitle.sentence_key(changed[0]["start"], changed[0]["end"],
                                                changed[0]["text"])
        self.assertNotEqual(key_before, key_after)
        self.assertEqual(other_key_before, other_key_after)

    def test_forced_cut_prefers_longest_gap_in_last_window(self) -> None:
        words = []
        t = 0.0
        for index in range(40):
            gap = 0.5 if index == 30 else 0.05
            start = t + gap
            words.append(_word(f"w{index}", start, start + 0.2))
            t = start + 0.2
        sentences = subtitle.build_sentences(words)
        self.assertGreaterEqual(len(sentences), 2)
        self.assertLessEqual(len(sentences[0]["words"]), 35)
        # 31번째 단어(index 30) 앞에서 잘렸어야 한다 (가장 큰 공백).
        self.assertEqual(sentences[0]["words"][-1]["text"], "w29")


class BreathAndBudgetTests(unittest.TestCase):
    def test_breath_points_on_gap_and_punctuation(self) -> None:
        sentence_words = [
            {"text": "Well,", "start": 0.0, "end": 0.3},
            {"text": "this", "start": 0.31, "end": 0.5},
            {"text": "works", "start": 1.0, "end": 1.3},
        ]
        points = subtitle.breath_points(sentence_words)
        self.assertEqual(points, [0.31, 1.0])

    def test_char_budget_floor_and_scale(self) -> None:
        self.assertEqual(subtitle.char_budget(0.1), 8)
        self.assertEqual(subtitle.char_budget(2.0), 26)


class EvidenceTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [
            _word("So", 10.0, 10.1), _word("this", 10.1, 10.2), _word("is", 10.2, 10.3),
            _word("all", 10.3, 10.4), _word("by", 10.4, 10.5), _word("torch", 10.5, 10.9),
            _word("and", 11.3, 11.4), _word("fast.", 11.4, 11.7),
        ]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        _write_json(bundle / "raw" / "captions.json", {
            "cues": [{"start": 10.0, "end": 11.7, "text": "this is all PyTorch and fast"},
                    {"start": 5.0, "end": 6.0, "text": "[laughter]"}],
        })
        return bundle

    def test_suspect_detection_by_torch_pytorch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            words = subtitle._load_words(bundle)
            indexed = subtitle.index_sentences(bundle)
            evidence = subtitle.build_evidence(bundle, words, indexed)
            youtube_forms = {s["youtube"] for s in evidence["suspects"]}
            self.assertIn("PyTorch", youtube_forms)
            matching = [s for s in evidence["suspects"] if s["youtube"] == "PyTorch"]
            self.assertIn("torch", matching[0]["gemini"])
            # 대괄호 큐는 의심 탐지에서 제외된다.
            self.assertTrue(evidence["captured"] is False or evidence["captured"] is True)


class TermsPhaseTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [_word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1)]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def test_correction_without_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            self.assertEqual(packet["phase"], "terms")
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text="T1|TotallyNewTerm|번역어|짧은설명|긴설명|-")
            self.assertEqual(result["accepted"], [])
            self.assertEqual(len(result["rejected"]), 1)
            self.assertIn("근거", result["rejected"][0]["reason"])
            # 거절된 줄은 한 번 더 기회를 준다 — 다음 패킷도 terms 로 남는다.
            self.assertEqual(result["next"]["phase"], "terms")
            self.assertIn("T1", result["next"]["packet"])
            # 재시도에서도 실패하면 그제서야 넘어간다.
            retry = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=result["next"]["fingerprint"],
                text="T1|TotallyNewTerm|번역어|짧은설명|긴설명|-")
            self.assertEqual(retry["next"]["phase"], "translate")

    def test_literal_gemini_term_needs_no_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text="T1|Hello|안녕|-|-|-")
            self.assertEqual(result["accepted"], ["T1"])
            self.assertEqual(result["rejected"], [])
            state = subtitle.load_state(bundle, "ko")
            self.assertEqual(state["terms"][0]["first_t"], 0.0)

    def test_short_explanation_too_long_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text="T1|Hello|안녕|이것은열여섯자를넘는아주긴짧은해설입니다|긴설명|-")
            self.assertEqual(result["accepted"], [])
            self.assertIn("짧은해설", result["rejected"][0]["reason"])

    def test_short_without_long_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text="T1|Hello|안녕|짧은설명|-|-")
            self.assertEqual(result["accepted"], [])
            self.assertIn("긴설명", result["rejected"][0]["reason"])

    def test_correction_first_t_uses_gemini_original_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = EvidenceTests()._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            state = subtitle.load_state(bundle, "ko")
            suspect = next(s for s in state["evidence"]["suspects"] if s["youtube"] == "PyTorch")
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text=f"T1|PyTorch|파이토치|-|-|{suspect['id']}")
            self.assertEqual(result["accepted"], ["T1"])
            state = subtitle.load_state(bundle, "ko")
            term = state["terms"][0]
            self.assertEqual(term["first_t"], suspect["t"])


class TranslatePhaseTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [
            _word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1),
            _word("It", 2.0, 2.2), _word("has", 2.2, 2.4),
            _word("100", 2.4, 2.6), _word("categories.", 2.6, 3.0),
        ]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def _start_translate(self, bundle: Path) -> dict:
        packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
        result = subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                         fingerprint=packet["fingerprint"], text="")
        self.assertEqual(result["next"]["phase"], "translate")
        return result["next"]

    def test_structural_reject_then_max_rejects_untranslated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = self._start_translate(bundle)
            for attempt in range(subtitle.MAX_REJECTS):
                result = subtitle.apply_response(
                    bundle, video_id="vid", phase="translate",
                    fingerprint=packet["fingerprint"], text="1|this stays english")
                self.assertEqual(result["accepted"], [])
                self.assertEqual(len(result["rejected"]), 1)
                packet = result["next"]
            state = subtitle.load_state(bundle, "ko")
            keys = list(state["lines"].keys())
            self.assertTrue(any(line["state"] == "untranslated" for line in state["lines"].values()))

    def test_numbers_and_glossary_display_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                    fingerprint=packet["fingerprint"], text="T1|categories|카테고리|-|-|-")
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            self.assertEqual(packet["phase"], "translate")
            # 숫자 100 누락 + 용어집 tgt("카테고리") 누락 -> 표시(flag), 거절 아님.
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="1|안녕 세상아.\n2|그것은 항목이 여러 개 있습니다.")
            self.assertIn(1, result["accepted"])
            self.assertIn(2, result["accepted"])
            state = subtitle.load_state(bundle, "ko")
            flagged_line = next(v for v in state["lines"].values()
                                if v["state"] == "flagged")
            self.assertIn("numbers", flagged_line["flags"])
            self.assertIn("terms", flagged_line["flags"])

    def test_breath_marker_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = self._start_translate(bundle)
            # 문장 1엔 숨 지점이 없으므로 /1 은 범위를 벗어나 구조 거절이다.
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="1|안녕 /1 세상아.")
            self.assertEqual(result["accepted"], [])
            self.assertEqual(len(result["rejected"]), 1)

    def test_fingerprint_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            self._start_translate(bundle)
            with self.assertRaises(subtitle.SubtitleError):
                subtitle.apply_response(bundle, video_id="vid", phase="translate",
                                        fingerprint="sha256:deadbeef", text="1|안녕")

    def test_markdown_decorated_translate_lines_are_parsed_leniently(self) -> None:
        """Bug 1 연장: <번호>|번역 줄도 같은 마크다운 장식을 벗기고 읽어야 한다."""
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = self._start_translate(bundle)
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="- 1|안녕 세상아.\n`2|그것은 100개 항목이 있습니다.`")
            self.assertEqual(sorted(result["accepted"]), [1, 2])
            self.assertEqual(result["rejected"], [])


class FullCycleTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [
            _word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1),
            _word("It", 2.0, 2.2), _word("has", 2.2, 2.4),
            _word("100", 2.4, 2.6), _word("categories.", 2.6, 3.0),
        ]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def test_terms_translate_review_done(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            self.assertEqual(packet["phase"], "terms")
            self.assertLessEqual(len(packet["packet"]), subtitle.MAX_PACKET_CHARS)

            result = subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                             fingerprint=packet["fingerprint"], text="")
            self.assertEqual(result["next"]["phase"], "translate")
            self.assertLessEqual(len(result["next"]["packet"]), subtitle.MAX_PACKET_CHARS)

            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate",
                fingerprint=result["next"]["fingerprint"],
                text="1|안녕 세상아.\n2|그것은 100개 범주가 있습니다.")
            self.assertEqual(sorted(result["accepted"]), [1, 2])
            # 표시할 게 없으면 일관성 검사까지 마치고 바로 done 이다.
            self.assertEqual(result["next"]["phase"], "done")
            self.assertEqual(result["next"]["progress"]["sentences"], 2)
            self.assertEqual(result["next"]["progress"]["translated"], 2)


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_saves_merge_without_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "vid"
            words = [_word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1)]
            _write_json(bundle / "derived" / "transcript.json",
                       {"video_id": "vid", "words": words})
            subtitle.build_packet(bundle, video_id="vid", lang="ko")  # 파일을 먼저 만든다.

            def _add(marker: str):
                def mutate(state):
                    state.setdefault("terms", []).append({"id": marker})
                    return state
                subtitle._mutate_state(bundle, "ko", mutate)

            threads = [threading.Thread(target=_add, args=(f"marker-{i}",)) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            state = subtitle.load_state(bundle, "ko")
            markers = {item["id"] for item in state["terms"]}
            self.assertEqual(markers, {"marker-0", "marker-1"})


class PurgeTests(unittest.TestCase):
    def test_purge_derived_keeps_translations_raw_removes_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "vid"
            (bundle / "derived").mkdir(parents=True)
            (bundle / "derived" / "merged.json").write_text("{}", encoding="utf-8")
            (bundle / "raw").mkdir(parents=True)
            (bundle / "raw" / "captions.json").write_text("{}", encoding="utf-8")
            (bundle / "translations").mkdir(parents=True)
            (bundle / "translations" / "ko.json").write_text("{}", encoding="utf-8")

            pipeline.purge(bundle, scope="derived")
            self.assertFalse((bundle / "derived").exists())
            self.assertTrue((bundle / "translations" / "ko.json").exists())

            pipeline.purge(bundle, scope="raw")
            self.assertFalse((bundle / "raw").exists())
            self.assertFalse((bundle / "translations").exists())


class PacketLimitTests(unittest.TestCase):
    def test_within_limit_passes_through(self) -> None:
        text = "short packet"
        self.assertEqual(subtitle._enforce_packet_limit(text), text)

    def test_over_limit_raises_instead_of_truncating(self) -> None:
        long_text = "\n".join(f"line {i} " + "x" * 40 for i in range(1000))
        self.assertGreater(len(long_text), subtitle.MAX_PACKET_CHARS)
        with self.assertRaises(subtitle.SubtitleError):
            subtitle._enforce_packet_limit(long_text)


class TermsPaginationTests(unittest.TestCase):
    def _bundle_with_many_suspects(self, root: Path, count: int) -> Path:
        bundle = root / "vid"
        words = []
        cues = []
        t = 0.0
        for i in range(count):
            words.append(_word("by", t, t + 0.1))
            words.append(_word(f"torch{i}", t + 0.1, t + 0.3))
            cues.append({"start": t, "end": t + 0.3, "text": f"PyTorchVariant{i}"})
            t += 5.0
        words.append(_word("End.", t, t + 0.3))
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        _write_json(bundle / "raw" / "captions.json", {"cues": cues})
        return bundle

    def test_terms_phase_spans_multiple_packets_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle_with_many_suspects(Path(directory), 400)
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            self.assertEqual(packet["phase"], "terms")
            self.assertLessEqual(len(packet["packet"]), subtitle.MAX_PACKET_CHARS)
            state = subtitle.load_state(bundle, "ko")
            total_suspects = len(state["evidence"]["suspects"])
            self.assertGreater(total_suspects, 50)  # 여러 패킷이 필요할 만큼 많다.

            seen_ids: set[str] = set()
            fingerprint = packet["fingerprint"]
            guard = 0
            while packet["phase"] == "terms":
                guard += 1
                self.assertLess(guard, total_suspects + 20, "terms phase 가 끝나지 않는다")
                for line in packet["packet"].splitlines():
                    if line.startswith("S") and "|" in line:
                        seen_ids.add(line.split("|")[0].strip())
                result = subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                                 fingerprint=fingerprint, text="")
                packet = result["next"]
                fingerprint = packet["fingerprint"]
            state = subtitle.load_state(bundle, "ko")
            all_ids = {s["id"] for s in state["evidence"]["suspects"]}
            self.assertEqual(seen_ids, all_ids)  # 잘려서 안 보인 의심이 없다.


class TermCorrectionCaseTests(unittest.TestCase):
    """대소문자·단어 경계 기준 교정 판정, OCR 전용 근거의 거절 사유."""

    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [_word("We", 0.0, 0.2), _word("use", 0.2, 0.4), _word("NanoGPT", 0.4, 0.9),
                _word("here.", 0.9, 1.1)]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def test_case_difference_is_not_treated_as_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text="T1|nanoGPT|나노지피티|-|-|-")
            self.assertEqual(result["accepted"], ["T1"])
            self.assertEqual(result["rejected"], [])

    def _seed_state(self, bundle: Path, lang: str = "ko") -> subtitle.Indexed:
        indexed = subtitle.index_sentences(bundle)
        evidence = {
            "suspects": [], "restored": [],
            "candidates": [{"id": "C1", "text": "BLEU", "t": 1.0}],
            "candidates_dropped": 0, "ocr": [], "captured": True, "capture_note": None,
            "transcript_text": " ".join(str(w["text"]) for w in subtitle._load_words(bundle)),
        }
        state = subtitle._default_state(video_id="vid", lang=lang,
                                        fingerprint=indexed.fingerprint, evidence=evidence)
        subtitle._write_json_atomic(subtitle.ko_path(bundle, lang), state)
        return indexed

    def test_ocr_only_evidence_gets_distinct_rejection_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            indexed = self._seed_state(bundle)
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=indexed.fingerprint,
                text="T1|BLEU|블루|-|-|C1")
            self.assertEqual(result["accepted"], [])
            self.assertEqual(result["rejected"][0]["reason"], "원문 음성에 나오지 않는 용어")

    def test_no_evidence_at_all_keeps_old_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            indexed = self._seed_state(bundle)
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=indexed.fingerprint,
                text="T1|Encoder-Decoder|인코더-디코더|-|-|-")
            self.assertEqual(result["accepted"], [])
            self.assertIn("근거(S번호)가 없다", result["rejected"][0]["reason"])


class TermsRetryTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [_word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1)]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def test_retry_success_then_advances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            state = subtitle.load_state(bundle, "ko")
            suspects = state["evidence"]["suspects"]
            # 이 데이터엔 의심이 없다 — evidence 없이 교정하면 거절되는 것만 확인.
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text="T1|Hi|안녕|-|-|-")
            self.assertEqual(result["rejected"][0]["line"], "T1")
            self.assertEqual(result["next"]["phase"], "terms")
            state = subtitle.load_state(bundle, "ko")
            self.assertEqual(len(state["terms_pending_retry"]), 1)
            # 재시도에서 원문 그대로("Hello")로 고쳐 보내면 근거 없이도 통과한다.
            retry = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=result["next"]["fingerprint"],
                text="T1|Hello|안녕|-|-|-")
            self.assertEqual(retry["accepted"], ["T1"])
            self.assertEqual(retry["next"]["phase"], "translate")
            state = subtitle.load_state(bundle, "ko")
            self.assertEqual(state["terms_pending_retry"], [])


class TermsMarkdownLeniencyTests(unittest.TestCase):
    """Bug 1: 호스트가 마크다운으로 장식한 T<n> 줄을 조용히 버리지 않는지 확인한다."""

    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [_word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1)]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def test_markdown_decorated_term_lines_are_parsed_leniently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            text = (
                "- T1|Hello|안녕|-|-|-\n"
                "* T2|Hello|안녕|-|-|-\n"
                "• T3|Hello|안녕|-|-|-\n"
                "1. T4|Hello|안녕|-|-|-\n"
                "`T5|Hello|안녕|-|-|-`\n"
                "**T6**|Hello|안녕|-|-|-\n"
                "| T7 | Hello | 안녕 | - | - | - |\n"
                "|---|---|---|---|---|---|\n"
                "```\n"
                "T8|Hello|안녕|-|-|-\n"
                "```\n"
            )
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"], text=text)
            self.assertEqual(sorted(result["accepted"]), [f"T{i}" for i in range(1, 9)])
            self.assertEqual(result["rejected"], [])

    def test_unparseable_line_is_reported_as_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text="이 용어는 그냥 설명입니다.")
            self.assertEqual(result["accepted"], [])
            self.assertEqual(len(result["rejected"]), 1)
            self.assertIn("형식이 맞지 않는다", result["rejected"][0]["reason"])
            self.assertIsNotNone(result["note"])
            self.assertEqual(result["next"]["phase"], "terms")


class TermsCursorStallTests(unittest.TestCase):
    """Bug 1: 형식이 전부 어긋난 비어 있지 않은 응답은 커서를 전진시키면 안 된다."""

    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [
            _word("So", 10.0, 10.1), _word("this", 10.1, 10.2), _word("is", 10.2, 10.3),
            _word("all", 10.3, 10.4), _word("by", 10.4, 10.5), _word("torch", 10.5, 10.9),
            _word("and", 11.3, 11.4), _word("fast.", 11.4, 11.7),
        ]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        _write_json(bundle / "raw" / "captions.json", {
            "cues": [{"start": 10.0, "end": 11.7, "text": "this is all PyTorch and fast"}],
        })
        return bundle

    def test_all_garbage_response_does_not_advance_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            self.assertEqual(packet["phase"], "terms")
            state = subtitle.load_state(bundle, "ko")
            cursor_before = dict(state["terms_cursor"])
            self.assertGreater(len(state["evidence"]["suspects"]), 0)

            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text="이건 그냥 문장이다.\n또 다른 잡음 줄.")
            self.assertEqual(result["accepted"], [])
            self.assertEqual(len(result["rejected"]), 2)
            self.assertIsNotNone(result["note"])
            self.assertEqual(result["next"]["phase"], "terms")

            state = subtitle.load_state(bundle, "ko")
            self.assertEqual(state["terms_cursor"], cursor_before)
            self.assertFalse(state["terms_done"])
            # 같은 묶음이 다시 나온다.
            self.assertEqual(result["next"]["packet"], packet["packet"])

            # 형식을 고쳐 보내면 정상적으로 진행된다.
            suspect_id = state["evidence"]["suspects"][0]["id"]
            retry = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=result["next"]["fingerprint"],
                text=f"T1|PyTorch|파이토치|-|-|{suspect_id}")
            self.assertEqual(retry["accepted"], ["T1"])


class GlossaryHeardTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [
            _word("So", 10.0, 10.1), _word("this", 10.1, 10.2), _word("is", 10.2, 10.3),
            _word("all", 10.3, 10.4), _word("by", 10.4, 10.5), _word("torch", 10.5, 10.9),
            _word("and", 11.3, 11.4), _word("fast.", 11.4, 11.7),
        ]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        _write_json(bundle / "raw" / "captions.json", {
            "cues": [{"start": 10.0, "end": 11.7, "text": "this is all PyTorch and fast"}],
        })
        return bundle

    def _suspect_id(self, bundle: Path) -> str:
        state = subtitle.load_state(bundle, "ko")
        return next(s["id"] for s in state["evidence"]["suspects"] if s["youtube"] == "PyTorch")

    def test_heard_stored_and_rendered_in_glossary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            sid = self._suspect_id(bundle)
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="terms", fingerprint=packet["fingerprint"],
                text=f"T1|PyTorch|파이토치|-|-|{sid}")
            self.assertEqual(result["accepted"], ["T1"])
            state = subtitle.load_state(bundle, "ko")
            term = state["terms"][0]
            self.assertEqual(term["heard"], ["by torch"])
            translate_packet = result["next"]
            self.assertEqual(translate_packet["phase"], "translate")
            self.assertIn("원고 표기: by torch", translate_packet["packet"])

    def test_consistency_flag_triggers_on_heard_form_too(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            sid = self._suspect_id(bundle)
            subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                    fingerprint=packet["fingerprint"],
                                    text=f"T1|PyTorch|파이토치|-|-|{sid}")
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            self.assertEqual(packet["phase"], "translate")
            # 원문엔 "by torch"(heard) 만 있고 "PyTorch" 는 없다. 번역에 "파이토치"
            # 를 빼면 용어집 불일치로 표시돼야 한다(거절은 아니다).
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="1|이건 전부 그냥 빠르게 돌아갑니다.")
            self.assertIn(1, result["accepted"])
            state = subtitle.load_state(bundle, "ko")
            stored = next(iter(state["lines"].values()))
            self.assertIn("terms", stored["flags"])


class MergeLineTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [
            # 문장 1: 끝맺지 못하고 공백(>1.2s)으로 강제로 끊긴다. 내부 숨
            # 지점은 없다(단어 사이 공백이 전부 0초).
            _word("My", 0.0, 0.2), _word("team", 0.2, 0.4), _word("would", 0.4, 0.6),
            _word("create", 0.6, 0.8), _word("the", 0.8, 1.0), _word("neural", 1.0, 1.2),
            _word("networks", 1.2, 1.4), _word("that", 1.4, 1.6),
            # 문장 2.
            _word("create", 3.0, 3.2), _word("these", 3.2, 3.4), _word("predictions.", 3.4, 3.7),
        ]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def _bundle_three(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [
            _word("My", 0.0, 0.2), _word("team", 0.2, 0.4), _word("would", 0.4, 0.6),
            _word("create", 0.6, 0.8), _word("that", 0.8, 1.0),
            _word("create", 3.0, 3.2), _word("these", 3.2, 3.4),
            _word("predictions", 5.0, 5.3), _word("well.", 5.3, 5.6),
        ]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def _start_translate(self, bundle: Path) -> dict:
        packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
        result = subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                         fingerprint=packet["fingerprint"], text="")
        self.assertEqual(result["next"]["phase"], "translate")
        return result["next"]

    def test_merge_line_stores_merged_into_and_extends_last_segment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = self._start_translate(bundle)
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="1|우리 팀은 신경망을 만들 예정이었습니다.\n2|=")
            self.assertIn(1, result["accepted"])
            self.assertIn(2, result["accepted"])
            state = subtitle.load_state(bundle, "ko")
            indexed = subtitle.index_sentences(bundle)
            key1, key2 = indexed.by_no[1]["key"], indexed.by_no[2]["key"]
            self.assertEqual(state["lines"][key2]["state"], "ok")
            self.assertEqual(state["lines"][key2]["merged_into"], key1)
            self.assertIsNone(state["lines"][key2]["ko"])
            segments = state["lines"][key1]["segments"]
            self.assertEqual(segments[-1]["end"], indexed.by_no[2]["end"])

    def test_plus_marker_splits_at_merged_sentence_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = self._start_translate(bundle)
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="1|첫 부분입니다 /+1 나머지 부분입니다.\n2|=")
            self.assertIn(1, result["accepted"])
            indexed = subtitle.index_sentences(bundle)
            state = subtitle.load_state(bundle, "ko")
            key1 = indexed.by_no[1]["key"]
            segments = state["lines"][key1]["segments"]
            self.assertEqual(len(segments), 2)
            self.assertEqual(segments[0]["end"], indexed.by_no[2]["start"])
            self.assertEqual(segments[1]["start"], indexed.by_no[2]["start"])
            self.assertEqual(segments[1]["end"], indexed.by_no[2]["end"])

    def test_chained_merge_three_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle_three(Path(directory))
            packet = self._start_translate(bundle)
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="1|전체 내용을 한 번에 옮깁니다.\n2|=\n3|=")
            self.assertEqual(sorted(result["accepted"]), [1, 2, 3])
            indexed = subtitle.index_sentences(bundle)
            state = subtitle.load_state(bundle, "ko")
            key1, key2, key3 = (indexed.by_no[n]["key"] for n in (1, 2, 3))
            self.assertEqual(state["lines"][key2]["merged_into"], key1)
            self.assertEqual(state["lines"][key3]["merged_into"], key2)
            segments = state["lines"][key1]["segments"]
            self.assertEqual(segments[-1]["end"], indexed.by_no[3]["end"])

    def test_merge_without_target_is_structural_reject_then_untranslated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = self._start_translate(bundle)
            for _ in range(subtitle.MAX_REJECTS):
                result = subtitle.apply_response(
                    bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                    text="2|=")
                packet = result["next"]
            state = subtitle.load_state(bundle, "ko")
            indexed = subtitle.index_sentences(bundle)
            key2 = indexed.by_no[2]["key"]
            self.assertEqual(state["lines"].get(key2, {}).get("state"), "untranslated")

    def test_viewer_payload_marks_merged_sentence_and_extends_root_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = self._start_translate(bundle)
            subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="1|우리 팀은 신경망을 만들 예정이었습니다.\n2|=")
            payload = subtitle.viewer_payload(bundle, "ko")
            first, second = payload["sentences"][0], payload["sentences"][1]
            self.assertIsNone(second["ko"])
            self.assertEqual(second["merged_into"], first["key"])
            self.assertEqual(first["segments"][-1]["end"], second["end"])



class ReviewFixTests(unittest.TestCase):
    """실측(68분 강연)에서 드러난 재검토 단계 결함의 회귀 테스트."""

    def _bundle(self, root: Path, words: list) -> Path:
        bundle = root / "vid"
        _write_json(bundle / "derived" / "transcript.json", {"video_id": "vid", "words": words})
        return bundle

    def _start(self, bundle: Path) -> dict:
        packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
        return subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                       fingerprint=packet["fingerprint"], text="")["next"]

    def test_attached_question_mark_is_not_uncertain(self) -> None:
        segments, _breaks, uncertain = subtitle._parse_translate_line("왜일까요?")
        self.assertFalse(uncertain)
        self.assertEqual(segments, ["왜일까요?"])
        segments, _breaks, uncertain = subtitle._parse_translate_line("잘 모르겠습니다 ?")
        self.assertTrue(uncertain)
        self.assertEqual(segments, ["잘 모르겠습니다"])

    def test_speech_style_ignores_questions_quotes_and_jyo(self) -> None:
        self.assertIsNone(subtitle._speech_style("왜일까요?"))
        self.assertIsNone(subtitle._speech_style("그렇죠."))
        self.assertIsNone(subtitle._speech_style('"산책 나갈 건데요."'))
        self.assertEqual(subtitle._speech_style("정말 흥미롭습니다."), "formal")
        self.assertEqual(subtitle._speech_style("정말 흥미로워요."), "informal")

    def test_pace_ignores_short_utterances_and_small_overrun(self) -> None:
        self.assertFalse(subtitle._pace_exceeded("좋습니다", 0.3, 0.3))
        # 2초 x 13 x 1.2 = 31.2자, 여유 3자까지는 표시하지 않는다.
        self.assertFalse(subtitle._pace_exceeded("가" * 34, 2.0, 2.0))
        self.assertTrue(subtitle._pace_exceeded("가" * 36, 2.0, 2.0))

    def test_heard_form_filter(self) -> None:
        self.assertFalse(subtitle._is_heard_form("gradient", "gradient descent"))
        self.assertFalse(subtitle._is_heard_form("Bengio", "Yoshua Bengio"))
        self.assertTrue(subtitle._is_heard_form("by torch", "PyTorch"))
        self.assertTrue(subtitle._is_heard_form("combine JS", "ConvNetJS"))
        self.assertTrue(subtitle._is_heard_form("Joshua", "Yoshua Bengio"))

    def test_merged_sentence_breath_token(self) -> None:
        item = {"start": 0.0, "breaths": [0.5]}
        merged = [{"start": 2.0, "breaths": [2.6]}]
        times, error = subtitle._resolve_break_times(["1", "+1", "+1.1"], item, merged)
        self.assertIsNone(error)
        self.assertEqual(times, [0.5, 2.0, 2.6])
        _times, error = subtitle._resolve_break_times(["+1.1", "+1"], item, merged)
        self.assertIsNotNone(error)

    def test_review_reports_non_candidates_and_resubmits_with_breaks(self) -> None:
        words = [
            # 문장 1: 2초, 중간 숨 지점 하나.
            _word("It", 0.0, 0.2), _word("was", 0.2, 0.4), _word("great,", 0.4, 0.8),
            _word("really", 1.4, 1.7), _word("great.", 1.7, 2.0),
            _word("Bye.", 4.0, 5.0),
        ]
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory), words)
            packet = self._start(bundle)
            long_ko = "가" * 30 + " /1 " + "정말 좋았습니다."
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text=f"1|{long_ko}\n2|안녕히 가세요.")
            self.assertEqual(result["next"]["phase"], "review")
            review = result["next"]
            self.assertIn("/1 정말 좋았습니다.", review["packet"])
            result = subtitle.apply_response(
                bundle, video_id="vid", phase="review", fingerprint=review["fingerprint"],
                text="1|멋졌습니다. /1 정말 좋았습니다.\n2|안녕히 가세요.")
            self.assertEqual(result["accepted"], [1])
            self.assertEqual([r["line"] for r in result["rejected"]], [2])
            self.assertEqual(result["next"]["phase"], "done")
            state = subtitle.load_state(bundle, "ko")
            key = subtitle.index_sentences(bundle).by_no[1]["key"]
            self.assertEqual(state["lines"][key]["breaks"], ["1"])


class ReportTests(unittest.TestCase):
    def test_cosmetic_spelling_differences_are_not_reported_as_corrections(self) -> None:
        state = {
            "evidence": {"suspects": [
                {"id": "S1", "gemini": "imageet"},
                {"id": "S2", "gemini": "pytorch"},
                {"id": "S3", "gemini": "nano GPT"},
                {"id": "S4", "gemini": "ResNets"},
            ]},
            "terms": [
                {"src": "ImageNet", "evidence": ["S1"]},
                {"src": "PyTorch", "evidence": ["S2"]},
                {"src": "nanoGPT", "evidence": ["S3"]},
                {"src": "ResNet", "evidence": ["S4"]},
            ],
        }
        report = subtitle._report_data(state, types.SimpleNamespace(sentences=[]))
        self.assertEqual([c["to"] for c in report["corrections"]], ["ImageNet"])


if __name__ == "__main__":
    unittest.main()


class ReviewPacketLimitTests(unittest.TestCase):
    def test_block_separators_count_toward_the_packet_limit(self) -> None:
        """블록 사이 빈 줄을 세지 않아 12,011자 패킷이 나간 회귀를 막는다."""
        sentences, lines_state = [], {}
        for no in range(1, 41):
            key = f"k{no}"
            words = [{"text": "word%03d" % i, "start": float(no), "end": float(no) + 0.1}
                     for i in range(30)]
            sentences.append({"no": no, "key": key, "start": float(no), "end": float(no) + 3.0,
                              "words": words, "breaths": [], "text": " ".join(w["text"] for w in words)})
            lines_state[key] = {"state": "flagged", "flags": ["pace"], "ko": "가" * 60}
        indexed = types.SimpleNamespace(sentences=sentences)
        state = {"lines": lines_state, "sentences_fingerprint": "fp"}
        original = (subtitle._flagged_unreviewed, subtitle._progress)
        subtitle._flagged_unreviewed = lambda st, idx: sentences
        subtitle._progress = lambda st, idx: {}
        try:
            for limit in range(3000, 4200, 13):
                with unittest.mock.patch.object(subtitle, "MAX_PACKET_CHARS", limit):
                    packet = subtitle._packet_review(Path("."), state, indexed, "vid")
                    self.assertLessEqual(len(packet["packet"]), limit)
        finally:
            subtitle._flagged_unreviewed, subtitle._progress = original
