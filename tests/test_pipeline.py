"""pipeline 오케스트레이션 테스트. Gemini API 를 호출하지 않는다."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import pipeline
import usage


def _words(texts: list[str], base: float) -> list[dict]:
    return [{"text": t, "start": round(base + i * 0.5, 3),
             "end": round(base + i * 0.5 + 0.3, 3), "speaker": "spk:0"}
            for i, t in enumerate(texts)]


class FakeTranscriber:
    """청크 오디오를 읽지 않고 정해진 결과를 돌려준다. 호출 횟수를 센다."""

    def __init__(self, per_chunk: dict[str, list[str]], fail_on: set[str] | None = None):
        self.per_chunk = per_chunk
        self.fail_on = fail_on or set()
        self.calls: list[str] = []

    def __call__(self, audio_path: str, langs: str) -> dict:
        name = Path(audio_path).name
        self.calls.append(name)
        if name in self.fail_on:
            raise RuntimeError("모의 API 실패: %s" % name)
        return {"source": "gemini", "model": "fake", "language_codes": None,
                "video_id": None, "words": _words(self.per_chunk[name], 0.0)}


class PipelineHelperTests(unittest.TestCase):
    def test_video_id_from_various_url_shapes(self) -> None:
        for url in (
            "https://www.youtube.com/watch?v=jcBDSLSeud4",
            "https://www.youtube.com/watch?v=jcBDSLSeud4&list=PL0&index=3",
            "https://youtu.be/jcBDSLSeud4",
            "https://www.youtube.com/embed/jcBDSLSeud4",
            "jcBDSLSeud4",
        ):
            self.assertEqual(pipeline.video_id_from_url(url), "jcBDSLSeud4")

    def test_unknown_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pipeline.video_id_from_url("https://example.com/video")

    def test_unknown_stage_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pipeline.run("jcBDSLSeud4", stages=("nope",))


class TranscribeResumeTests(unittest.TestCase):
    """완료 청크를 재호출하지 않는지, 실패 후 이어서 가는지 확인한다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "vid"
        self.ledger = self.root / "usage.json"
        for index in (0, 1):
            chunk = self.bundle / ("raw/audio/chunk-%03d.mp3" % index)
            chunk.parent.mkdir(parents=True, exist_ok=True)
            chunk.write_bytes(b"fake-audio")
        self.job = {
            "schema_version": 1, "video_id": "vid",
            "input": {"source": "u", "fingerprint": "sha256:x"},
            "config": {"chunk_max_secs": 1790.0, "overlap_secs": 10.0,
                       "language_codes": None, "diarization": True},
            "status": "planned",
            "chunks": [
                {"index": 0, "start": 0.0, "end": 10.0,
                 "path": "raw/audio/chunk-000.mp3", "status": "planned", "attempts": 0,
                 "transcript_path": "raw/transcripts/chunk-000.json", "error": None},
                {"index": 1, "start": 10.0, "end": 20.0,
                 "path": "raw/audio/chunk-001.mp3", "status": "planned", "attempts": 0,
                 "transcript_path": "raw/transcripts/chunk-001.json", "error": None},
            ],
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, transcriber) -> dict:
        return pipeline.stage_transcribe(
            self.bundle, self.job, ledger=self.ledger, api_key="k",
            daily_limit=25, rpm_limit=None, request_interval=0.0,
            transcriber=transcriber)

    def test_absolute_timestamps_use_chunk_offset(self) -> None:
        fake = FakeTranscriber({"chunk-000.mp3": ["a", "b"], "chunk-001.mp3": ["c", "d"]})
        self._run(fake)
        second = json.loads(
            (self.bundle / "raw/transcripts/chunk-001.json").read_text(encoding="utf-8"))
        self.assertEqual(second["chunk_index"], 1)
        # 청크 로컬 0.0 이 절대 10.0 으로 바뀌어야 한다.
        self.assertAlmostEqual(second["words"][0]["start"], 10.0)
        self.assertAlmostEqual(second["words"][1]["start"], 10.5)

    def test_completed_chunks_are_not_called_again(self) -> None:
        first = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        self._run(first)
        self.assertEqual(len(first.calls), 2)

        second = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        self._run(second)
        self.assertEqual(second.calls, [], "완료 청크를 다시 호출했다")

    def test_failure_keeps_completed_chunks_and_resumes(self) -> None:
        failing = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]},
                                  fail_on={"chunk-001.mp3"})
        with self.assertRaises(pipeline.StageError):
            self._run(failing)

        job = json.loads((self.bundle / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["chunks"][0]["status"], "complete")
        self.assertEqual(job["chunks"][1]["status"], "failed")
        self.assertIsNotNone(job["chunks"][1]["error"])

        # 재개 시 완료된 0번은 건너뛰고 1번만 호출한다.
        recovered = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        pipeline.stage_transcribe(self.bundle, job, ledger=self.ledger, api_key="k",
                                  daily_limit=25, rpm_limit=None, request_interval=0.0,
                                  transcriber=recovered)
        self.assertEqual(recovered.calls, ["chunk-001.mp3"])
        self.assertEqual(
            json.loads((self.bundle / "job.json").read_text(encoding="utf-8"))["status"],
            "complete")

    def test_failed_attempt_still_counts_in_ledger(self) -> None:
        failing = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]},
                                  fail_on={"chunk-000.mp3"})
        with self.assertRaises(pipeline.StageError):
            self._run(failing)
        self.assertEqual(usage.get_usage(self.ledger, "k")["attempts"], 1)

    def test_preflight_blocks_when_estimate_exceeds_remaining(self) -> None:
        fake = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        with self.assertRaises(usage.UsageLimitExceeded):
            pipeline.stage_transcribe(self.bundle, self.job, ledger=self.ledger,
                                      api_key="k", daily_limit=1, rpm_limit=None,
                                      request_interval=0.0, transcriber=fake)
        self.assertEqual(fake.calls, [], "한도 초과인데 호출했다")


class OfflineStageTests(unittest.TestCase):
    """merge/render/index 는 Gemini SDK 없이 동작해야 한다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "derived").mkdir(parents=True)
        (self.bundle / "raw").mkdir(parents=True)
        transcript = {
            "source": "gemini", "video_id": "vid",
            "words": _words(["안녕하세요.", "했을까요?"], 0.0) +
                     _words(["라는", "방식으로"], 6.0),
        }
        (self.bundle / "derived/transcript.json").write_text(
            json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        (self.bundle / "raw/captions.json").write_text(json.dumps(
            {"source": "youtube-ko-orig", "video_id": "vid",
             "cues": [{"start": 1.0, "end": 6.2, "text": "했을까요? self supervised"}]},
            ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_merge_render_index_run_without_api(self) -> None:
        merged = pipeline.stage_merge(self.bundle)
        gemini = [w for w in merged["words"] if w["origin"] == "gemini"]
        self.assertEqual(len(gemini), 4, "Gemini 단어가 보존되지 않았다")

        srt, txt = pipeline.stage_render(self.bundle, width=20)
        self.assertTrue(srt.exists() and txt.exists())

        index = pipeline.stage_index(self.bundle)
        self.assertTrue(index.exists())

    def test_render_reports_zero_word_loss(self) -> None:
        pipeline.stage_merge(self.bundle)
        pipeline.stage_render(self.bundle, width=20)
        merged = json.loads(
            (self.bundle / "derived/merged.json").read_text(encoding="utf-8"))
        rendered = (self.bundle / "derived/output.txt").read_text(encoding="utf-8")
        self.assertEqual(len(rendered.split()), len(merged["words"]))

    def test_assemble_requires_chunk_transcripts(self) -> None:
        job = {"video_id": "vid", "chunks": [
            {"index": 0, "transcript_path": "raw/transcripts/chunk-000.json"}]}
        with self.assertRaises(pipeline.StageError):
            pipeline.stage_assemble(self.bundle, job)


class StatusAndPurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "derived").mkdir(parents=True)
        (self.bundle / "raw").mkdir(parents=True)
        (self.bundle / "derived/output.srt").write_text("1\n", encoding="utf-8")
        (self.bundle / "raw/captions.json").write_text("{}", encoding="utf-8")
        (self.bundle / "index.sqlite3").write_bytes(b"")
        (self.bundle / "job.json").write_text(json.dumps({
            "status": "partial",
            "chunks": [{"index": 0, "status": "complete"},
                       {"index": 1, "status": "failed"}],
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_status_reports_chunk_progress_and_artifacts(self) -> None:
        info = pipeline.status(self.bundle)
        self.assertEqual(info["status"], "partial")
        self.assertEqual(info["chunks"]["complete"], 1)
        self.assertEqual(info["chunks"]["failed"], [1])
        self.assertTrue(info["artifacts"]["srt"])
        self.assertFalse(info["artifacts"]["merged"])

    def test_purge_derived_keeps_raw(self) -> None:
        pipeline.purge(self.bundle, scope="derived")
        self.assertFalse((self.bundle / "derived").exists())
        self.assertFalse((self.bundle / "index.sqlite3").exists())
        self.assertTrue((self.bundle / "raw/captions.json").exists(),
                        "derived 삭제가 raw 를 지웠다")

    def test_purge_all_removes_job_manifest(self) -> None:
        pipeline.purge(self.bundle, scope="all")
        self.assertFalse((self.bundle / "raw").exists())
        self.assertFalse((self.bundle / "job.json").exists())

    def test_purge_rejects_unknown_scope(self) -> None:
        with self.assertRaises(ValueError):
            pipeline.purge(self.bundle, scope="everything")


if __name__ == "__main__":
    unittest.main()
