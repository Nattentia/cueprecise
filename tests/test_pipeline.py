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
                       "language_codes": None},
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

    def test_failure_does_not_store_or_raise_the_api_key(self) -> None:
        key = "AIza" + "r" * 36

        def failing(_path, _langs):
            raise RuntimeError(f"provider echoed {key}")

        with self.assertRaises(pipeline.StageError) as caught:
            pipeline.stage_transcribe(
                self.bundle, self.job, ledger=self.ledger, api_key=key,
                daily_limit=25, rpm_limit=None, request_interval=0.0,
                transcriber=failing)

        saved = (self.bundle / "job.json").read_text(encoding="utf-8")
        self.assertNotIn(key, saved)
        self.assertNotIn(key, str(caught.exception))
        self.assertIn("***", saved)

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

    def _write_raw(self, index: int, langs: str, text: str, **override) -> Path:
        path = self.bundle / ("raw/transcripts/chunk-%03d.raw.json" % index)
        path.parent.mkdir(parents=True, exist_ok=True)
        chunk = self.job["chunks"][index]
        tags = {"requested_langs": langs,
                "fingerprint": self.job["input"]["fingerprint"],
                "chunk_index": chunk["index"], "chunk_start": chunk["start"],
                "chunk_end": chunk["end"], **override}
        path.write_text(json.dumps({
            "model": "fake", "language_codes": None, **tags,
            "response": {"steps": [{"content": [{"annotations": [
                {"type": "word_info", "text": text,
                 "start_offset": "0.0s", "end_offset": "0.4s", "speaker": "spk:0"},
            ]}]}]},
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def test_stored_raw_response_is_reused_without_calling(self) -> None:
        self._write_raw(0, "auto", "저장됨")
        fake = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        self._run(fake)
        self.assertEqual(fake.calls, ["chunk-001.mp3"], "저장된 응답이 있는데 호출했다")
        first = json.loads(
            (self.bundle / "raw/transcripts/chunk-000.json").read_text(encoding="utf-8"))
        self.assertEqual([w["text"] for w in first["words"]], ["저장됨"])

    def test_reused_raw_does_not_count_as_an_attempt(self) -> None:
        self._write_raw(0, "auto", "저장됨")
        self._write_raw(1, "auto", "저장됨2")
        fake = FakeTranscriber({})
        self._run(fake)
        self.assertEqual(fake.calls, [])
        self.assertEqual(usage.get_usage(self.ledger, "k")["attempts"], 0)

    def test_raw_from_a_different_language_request_is_ignored(self) -> None:
        self._write_raw(0, "en-US", "저장됨")   # job 설정은 auto 다
        fake = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        self._run(fake)
        self.assertEqual(fake.calls, ["chunk-000.mp3", "chunk-001.mp3"])

    def test_force_ignores_stored_raw(self) -> None:
        self._write_raw(0, "auto", "저장됨")
        fake = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        pipeline.stage_transcribe(self.bundle, self.job, ledger=self.ledger, api_key="k",
                                  daily_limit=25, rpm_limit=None, request_interval=0.0,
                                  transcriber=fake, force=True)
        self.assertEqual(fake.calls, ["chunk-000.mp3", "chunk-001.mp3"])

    def test_unusable_stored_raw_reports_how_to_recover(self) -> None:
        """꼬리표는 맞는데 내용을 파싱할 수 없으면 임의로 호출하지 않는다."""
        path = self._write_raw(0, "auto", "저장됨")
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["response"] = {"steps": []}          # word_info 가 없다
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

        fake = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        with self.assertRaises(pipeline.StageError) as caught:
            self._run(fake)
        self.assertIn("삭제", str(caught.exception))
        self.assertEqual(fake.calls, [], "복구 실패인데 임의로 호출했다")

    def test_raw_from_a_different_chunk_plan_is_not_reused(self) -> None:
        """--force 나 --chunk-max-secs 변경으로 경계가 옮겨가면 옛 응답을 쓰면 안 된다.

        언어만 비교하면 다른 구간의 전사를 그대로 붙여 타임스탬프가 조용히
        어긋난다. 호출을 한 번 더 쓰더라도 다시 받는 편이 낫다.
        """
        self._write_raw(0, "auto", "저장됨", chunk_start=999.0)
        fake = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        self._run(fake)
        self.assertIn("chunk-000.mp3", fake.calls, "경계가 달라진 응답을 재사용했다")

    def test_raw_from_a_different_input_audio_is_not_reused(self) -> None:
        self._write_raw(0, "auto", "저장됨", fingerprint="sha256:다른오디오")
        fake = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        self._run(fake)
        self.assertIn("chunk-000.mp3", fake.calls, "다른 오디오의 응답을 재사용했다")

    def test_untagged_legacy_raw_is_not_reused(self) -> None:
        path = self.bundle / "raw/transcripts/chunk-000.raw.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"requested_langs": "auto",
                                    "response": {"steps": []}}), encoding="utf-8")
        fake = FakeTranscriber({"chunk-000.mp3": ["a"], "chunk-001.mp3": ["b"]})
        self._run(fake)
        self.assertIn("chunk-000.mp3", fake.calls, "꼬리표 없는 응답을 재사용했다")


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

    def test_visual_runs_offline_and_reports_missing_video(self) -> None:
        pipeline.stage_merge(self.bundle)
        result = pipeline.stage_visual(self.bundle)
        self.assertEqual(result["frames"], [], "영상이 없는데 프레임이 나왔다")
        self.assertIn("영상", result["note"])
        self.assertTrue((self.bundle / "derived/frames.json").exists())

    def test_visual_does_not_treat_audio_as_video(self) -> None:
        (self.bundle / "raw/source.mp3").write_bytes(b"fake-audio")
        pipeline.stage_merge(self.bundle)
        result = pipeline.stage_visual(self.bundle)
        self.assertEqual(result["frames"], [], "오디오로 프레임을 뽑으려 했다")

    def test_visual_stage_sits_between_render_and_index(self) -> None:
        self.assertEqual(pipeline.STAGES[-3:], ("render", "visual", "index"))

    def test_index_picks_up_frames(self) -> None:
        pipeline.stage_merge(self.bundle)
        (self.bundle / "derived/frames.json").write_text(json.dumps({
            "schema_version": 1, "video_id": "vid",
            "frames": [{"timestamp": 3.0, "path": "raw/frames/000003000.jpg",
                        "reason": "screen-reference", "ocr_text": "self supervised",
                        "confidence": 0.8}],
        }, ensure_ascii=False), encoding="utf-8")
        index = pipeline.stage_index(self.bundle)
        import sqlite3
        connection = sqlite3.connect(index)
        try:
            rows = connection.execute(
                "SELECT source_kind, text FROM evidence WHERE source_kind='frame'").fetchall()
        finally:
            connection.close()  # Windows 는 열린 파일을 지우지 못한다
        self.assertEqual(rows, [("frame", "self supervised")])

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

    def test_status_marks_optional_artifacts(self) -> None:
        """선택 산출물이 없다고 해서 실패로 보이면 안 된다."""
        info = pipeline.status(self.bundle)
        expected = sorted(name for stage in pipeline.OPTIONAL_STAGES
                          for name in pipeline.STAGE_ARTIFACTS[stage])
        self.assertEqual(sorted(info["optional_artifacts"]), expected)
        for name in info["optional_artifacts"]:
            self.assertIn(name, info["artifacts"],
                          "선택 산출물 이름이 artifacts 에 없다")

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

    def _seed_chunks(self) -> None:
        chunk = self.bundle / "raw/audio/chunk-000.mp3"
        chunk.parent.mkdir(parents=True, exist_ok=True)
        chunk.write_bytes(b"fake-chunk")
        (self.bundle / "raw/source.mp3").write_bytes(b"fake-audio")

    def test_purge_chunks_keeps_source_and_transcripts(self) -> None:
        self._seed_chunks()
        transcript = self.bundle / "raw/transcripts/chunk-000.json"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("{}", encoding="utf-8")

        removed = pipeline.purge(self.bundle, scope="chunks")

        self.assertFalse((self.bundle / "raw/audio").exists(), "청크가 남았다")
        self.assertTrue((self.bundle / "raw/source.mp3").exists(), "원본을 지웠다")
        self.assertTrue(transcript.exists(), "전사를 지웠다")
        self.assertTrue((self.bundle / "raw/captions.json").exists(), "자막을 지웠다")
        self.assertEqual(len(removed), 1)

    def test_purge_chunks_refuses_when_source_audio_is_gone(self) -> None:
        chunk = self.bundle / "raw/audio/chunk-000.mp3"
        chunk.parent.mkdir(parents=True, exist_ok=True)
        chunk.write_bytes(b"fake-chunk")  # source.mp3 없음
        with self.assertRaises(ValueError) as caught:
            pipeline.purge(self.bundle, scope="chunks")
        self.assertIn("유일한 오디오", str(caught.exception))
        self.assertTrue(chunk.exists(), "되돌릴 수 없는데 지웠다")

    def test_new_plan_config_has_no_diarization_key(self) -> None:
        """새로 세운 계획에는 죽은 `diarization` 키가 없다."""
        self._seed_chunks()
        pipeline.audio.file_fingerprint = lambda path: "sha256:x"
        original = pipeline.audio.extract_chunks
        pipeline.audio.extract_chunks = lambda src, root, chunks: None
        probe = pipeline.audio.probe_duration
        pipeline.audio.probe_duration = lambda path: 10.0
        try:
            job = pipeline.stage_plan(self.bundle, "u", chunk_max_secs=1790.0,
                                      overlap_secs=10.0, language_codes=None, force=True)
        finally:
            pipeline.audio.extract_chunks = original
            pipeline.audio.probe_duration = probe
        self.assertNotIn("diarization", job["config"])

    def test_legacy_diarization_key_does_not_force_replan(self) -> None:
        """옛 job.json 의 `diarization` 키는 계획 재사용을 막지 않는다.

        막으면 이미 전사를 끝낸 청크를 Gemini 에 다시 부른다.
        """
        self._seed_chunks()
        (self.bundle / "raw/transcripts").mkdir(parents=True, exist_ok=True)
        job = {"input": {"source": "u", "fingerprint": "sha256:x"},
               "config": {"chunk_max_secs": 1790.0, "overlap_secs": 10.0,
                          "language_codes": None, "diarization": True},
               "chunks": [{"index": 0, "start": 0.0, "end": 10.0,
                           "path": "raw/audio/chunk-000.mp3", "status": "complete",
                           "attempts": 1,
                           "transcript_path": "raw/transcripts/chunk-000.json",
                           "error": None}]}
        (self.bundle / "job.json").write_text(json.dumps(job), encoding="utf-8")
        pipeline.audio.file_fingerprint = lambda path: "sha256:x"
        replanned = []
        original = pipeline.audio.create_job
        pipeline.audio.create_job = lambda *a, **k: replanned.append(1)
        try:
            reused = pipeline.stage_plan(self.bundle, "u", chunk_max_secs=1790.0,
                                         overlap_secs=10.0, language_codes=None)
        finally:
            pipeline.audio.create_job = original
        self.assertEqual(replanned, [], "옛 키 하나 때문에 계획을 다시 세웠다")
        self.assertEqual(reused["chunks"][0]["status"], "complete")

    def test_replan_keeps_finished_chunk_records(self) -> None:
        """설정이 바뀌어 다시 계획해도 끝낸 전사의 기록은 살린다.

        기록이 날아가면 status 가 0/N 을 보고하고 사람은 분석이 안 된 줄 안다.
        """
        self._seed_chunks()
        (self.bundle / "raw/transcripts").mkdir(parents=True, exist_ok=True)
        (self.bundle / "raw/transcripts/chunk-000.json").write_text("{}", encoding="utf-8")
        job = {"input": {"source": "u", "fingerprint": "sha256:x"},
               "config": {"chunk_max_secs": 1790.0, "overlap_secs": 10.0,
                          "language_codes": None},
               "status": "complete",
               "chunks": [{"index": 0, "start": 0.0, "end": 10.0,
                           "path": "raw/audio/chunk-000.mp3", "status": "complete",
                           "attempts": 1,
                           "transcript_path": "raw/transcripts/chunk-000.json",
                           "error": None}]}
        (self.bundle / "job.json").write_text(json.dumps(job), encoding="utf-8")
        pipeline.audio.file_fingerprint = lambda path: "sha256:x"
        extract = pipeline.audio.extract_chunks
        probe = pipeline.audio.probe_duration
        pipeline.audio.extract_chunks = lambda src, root, chunks: None
        pipeline.audio.probe_duration = lambda path: 10.0
        try:
            # 언어를 바꾼다. 경계는 그대로다.
            new_job = pipeline.stage_plan(self.bundle, "u", chunk_max_secs=1790.0,
                                          overlap_secs=10.0, language_codes=["ko-KR"])
        finally:
            pipeline.audio.extract_chunks = extract
            pipeline.audio.probe_duration = probe
        self.assertEqual(new_job["chunks"][0]["status"], "complete")
        self.assertEqual(new_job["chunks"][0]["attempts"], 1)
        self.assertEqual(new_job["config"]["language_codes"], ["ko-KR"])
        saved = json.loads((self.bundle / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["chunks"][0]["status"], "complete")

    def test_replan_drops_records_when_boundaries_move(self) -> None:
        """경계가 옮겨가면 옛 전사는 시각이 어긋나므로 기록을 넘기지 않는다."""
        self._seed_chunks()
        (self.bundle / "raw/transcripts").mkdir(parents=True, exist_ok=True)
        (self.bundle / "raw/transcripts/chunk-000.json").write_text("{}", encoding="utf-8")
        old = {"index": 0, "start": 0.0, "end": 99.0,
               "path": "raw/audio/chunk-000.mp3", "status": "complete", "attempts": 1,
               "transcript_path": "raw/transcripts/chunk-000.json", "error": None}
        new = {**old, "start": 0.0, "end": 10.0, "status": "planned", "attempts": 0}
        self.assertEqual(pipeline._carry_chunk_progress({"chunks": [old]},
                                                        {"chunks": [new]}, self.bundle), 0)
        self.assertEqual(new["status"], "planned")

    def test_status_reports_transcripts_ahead_of_the_ledger(self) -> None:
        """장부가 초기화된 옛 번들에서도 전사가 있다는 사실이 보여야 한다."""
        (self.bundle / "raw/transcripts").mkdir(parents=True, exist_ok=True)
        (self.bundle / "raw/transcripts/chunk-000.json").write_text("{}", encoding="utf-8")
        (self.bundle / "job.json").write_text(json.dumps({
            "status": "planned",
            "chunks": [{"index": 0, "status": "planned",
                        "transcript_path": "raw/transcripts/chunk-000.json"}],
        }), encoding="utf-8")
        info = pipeline.status(self.bundle)
        self.assertEqual(info["chunks"]["complete"], 0)
        self.assertEqual(info["chunks"]["transcripts_on_disk"], 1)
        self.assertIn("transcribe", info["chunks"]["note"])

    def test_status_never_reports_null_translation_guard(self) -> None:
        """검사하고 통과한 것과 검사 자체를 안 한 것은 구분돼야 한다."""
        def guard_for(job: dict) -> str:
            (self.bundle / "job.json").write_text(json.dumps(job), encoding="utf-8")
            return pipeline.status(self.bundle)["translation_guard"]

        chunk_done = {"index": 0, "status": "complete"}
        chunk_todo = {"index": 0, "status": "planned"}
        self.assertEqual(guard_for({"status": "planned", "chunks": [chunk_todo]}), "not-run")
        self.assertEqual(guard_for({"status": "complete", "chunks": [chunk_done]}), "unknown")
        self.assertEqual(guard_for({"status": "complete", "chunks": [chunk_done],
                                    "translation_guard": "skipped"}), "skipped")
        self.assertEqual(guard_for({"status": "complete", "chunks": [chunk_done],
                                    "translation_guard": "active"}), "active")

    def test_plan_rebuilds_purged_chunks(self) -> None:
        """청크를 지워도 source.mp3 가 있으면 계획 단계가 다시 뽑는다.

        job.json 은 `diarization` 키가 있던 시절의 것이다. 그 키 하나 때문에
        계획을 통째로 다시 세우면 안 된다.
        """
        self._seed_chunks()
        job = {"input": {"fingerprint": "sha256:x"},
               "config": {"chunk_max_secs": 1790.0, "overlap_secs": 10.0,
                          "language_codes": None, "diarization": True},
               "chunks": [{"index": 0, "start": 0.0, "end": 10.0,
                           "path": "raw/audio/chunk-000.mp3", "status": "planned",
                           "attempts": 0,
                           "transcript_path": "raw/transcripts/chunk-000.json",
                           "error": None}]}
        (self.bundle / "job.json").write_text(json.dumps(job), encoding="utf-8")
        pipeline.purge(self.bundle, scope="chunks")

        calls: list = []
        original = pipeline.audio.extract_chunks
        pipeline.audio.extract_chunks = lambda src, root, chunks: calls.append(len(chunks))
        pipeline.audio.file_fingerprint = lambda path: "sha256:x"
        try:
            pipeline.stage_plan(self.bundle, "u", chunk_max_secs=1790.0, overlap_secs=10.0,
                                language_codes=None)
        finally:
            pipeline.audio.extract_chunks = original
        self.assertEqual(calls, [1], "없어진 청크를 다시 뽑지 않았다")



class ForceRefetchTests(unittest.TestCase):
    """--force 로 다시 받을 때 옛 원본이 남으면 용량만 두 배가 된다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "raw").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _touch(self, name: str) -> Path:
        path = self.bundle / "raw" / name
        path.write_bytes(b"x")
        return path

    def test_stale_audio_of_another_format_is_removed(self) -> None:
        self._touch("source.mp3")
        self._touch("source.webm")
        removed = pipeline._remove_stale_sources(self.bundle, pipeline.audio.AUDIO_NAMES)
        self.assertEqual(sorted(removed), ["source.mp3", "source.webm"])
        self.assertIsNone(pipeline.audio.source_audio(self.bundle))

    def test_stale_video_including_legacy_name_is_removed(self) -> None:
        self._touch("source.mp4")            # 구 bundle 이름
        self._touch("source_video.mp4")
        pipeline._remove_stale_sources(self.bundle, pipeline.visual.VIDEO_NAMES)
        self.assertIsNone(pipeline.visual.source_video(self.bundle))

    def test_removal_leaves_other_raw_files_alone(self) -> None:
        self._touch("source.mp3")
        captions = self._touch("captions.json")
        pipeline._remove_stale_sources(self.bundle, pipeline.audio.AUDIO_NAMES)
        self.assertTrue(captions.exists(), "자막을 지웠다")

    def test_removal_is_silent_when_nothing_is_there(self) -> None:
        self.assertEqual(
            pipeline._remove_stale_sources(self.bundle, pipeline.audio.AUDIO_NAMES), [])

    def test_newly_downloaded_file_is_kept(self) -> None:
        self._touch("source.mp3")
        self._touch("source.webm")
        removed = pipeline._remove_stale_sources(
            self.bundle, pipeline.audio.AUDIO_NAMES, keep="source.webm")
        self.assertEqual(removed, ["source.mp3"])
        self.assertEqual(pipeline.audio.source_audio(self.bundle).name, "source.webm")


class DownloadSafetyTests(unittest.TestCase):
    """다운로드가 실패해도 기존 원본을 잃으면 안 된다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        self.raw = self.bundle / "raw"
        self.raw.mkdir(parents=True)
        self.real_run = pipeline.subprocess.run

    def tearDown(self) -> None:
        pipeline.subprocess.run = self.real_run
        self.tmp.cleanup()

    def _fake_yt_dlp(self, *, ok: bool, ext: str = "webm"):
        class Result:
            returncode = 0 if ok else 1
            stderr = "" if ok else "ERROR: 모의 다운로드 실패"
            stdout = ""

        def fake(command, **kwargs):
            if "--dump-json" in command:
                return pipeline.subprocess.CompletedProcess(
                    command, 0,
                    json.dumps({"id": "x", "title": "t", "language": "ko",
                                "automatic_captions": {"ko-orig": []}}), "")
            if ok:
                target = Path(command[command.index("-o") + 1].replace(".%(ext)s", "." + ext))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"new-media")
            return Result()

        return fake

    def test_failed_download_leaves_previous_source_intact(self) -> None:
        previous = self.raw / "source.mp3"
        previous.write_bytes(b"original-audio")
        pipeline.subprocess.run = self._fake_yt_dlp(ok=False)
        with self.assertRaises(pipeline.StageError):
            pipeline.stage_fetch(self.bundle, "https://y/watch?v=x",
                                 force=True, video=False)
        self.assertTrue(previous.exists(), "다운로드 실패인데 원본을 지웠다")
        self.assertEqual(previous.read_bytes(), b"original-audio")

    def test_successful_download_replaces_the_old_format(self) -> None:
        (self.raw / "source.mp3").write_bytes(b"original-audio")
        (self.raw / "captions.json").write_text(
            json.dumps({"source": "youtube-ko-orig", "video_id": "x", "cues": []}),
            encoding="utf-8")
        pipeline.subprocess.run = self._fake_yt_dlp(ok=True, ext="webm")
        pipeline.stage_fetch(self.bundle, "https://y/watch?v=x", force=True, video=False)
        self.assertFalse((self.raw / "source.mp3").exists(), "옛 형식이 남았다")
        self.assertEqual((self.raw / "source.webm").read_bytes(), b"new-media")
        self.assertFalse((self.raw / ".download").exists(), "임시 디렉터리가 남았다")


class ForceAndMissingJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        self.bundle.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_job_explains_which_stage_to_run(self) -> None:
        with self.assertRaises(pipeline.StageError) as caught:
            pipeline.run("jcBDSLSeud4", bundle_root=self.bundle.parent,
                         stages=("assemble",))
        self.assertIn("plan", str(caught.exception))

    def test_force_on_complete_chunks_warns_instead_of_silence(self) -> None:
        transcript = self.bundle / "raw/transcripts/chunk-000.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"words": []}), encoding="utf-8")
        job = {
            "schema_version": 1, "video_id": "vid",
            "input": {"source": "u", "fingerprint": "sha256:x"},
            "config": {"chunk_max_secs": 1790.0, "overlap_secs": 10.0,
                       "language_codes": None},
            "status": "complete",
            "chunks": [{"index": 0, "start": 0.0, "end": 10.0,
                        "path": "raw/audio/chunk-000.mp3", "status": "complete",
                        "attempts": 1,
                        "transcript_path": "raw/transcripts/chunk-000.json",
                        "error": None}],
        }
        messages: list[str] = []
        real_log, pipeline._log = pipeline._log, messages.append
        try:
            pipeline.stage_transcribe(
                self.bundle, job, ledger=self.bundle / "usage.json", api_key="k",
                daily_limit=25, rpm_limit=None, request_interval=0.0, force=True)
        finally:
            pipeline._log = real_log
        self.assertTrue(any("plan" in message for message in messages),
                        "--force 가 조용히 아무것도 안 했다")


if __name__ == "__main__":
    unittest.main()


class StageSelectionTests(unittest.TestCase):
    """유효 단계 목록과 기본 실행 목록의 분리 (LIGHTWEIGHT_HANDOFF 작업 D)."""

    def test_default_stages_are_valid_and_ordered(self) -> None:
        for stage in pipeline.DEFAULT_STAGES:
            self.assertIn(stage, pipeline.STAGES, "기본 단계가 유효 목록에 없다")
        order = [s for s in pipeline.STAGES if s in pipeline.DEFAULT_STAGES]
        self.assertEqual(list(pipeline.DEFAULT_STAGES), order,
                         "기본 단계 순서가 파이프라인 순서와 다르다")

    def test_optional_stages_are_the_difference(self) -> None:
        remainder = tuple(s for s in pipeline.STAGES if s not in pipeline.DEFAULT_STAGES)
        self.assertEqual(remainder, pipeline.OPTIONAL_STAGES,
                         "OPTIONAL_STAGES 가 STAGES - DEFAULT_STAGES 와 다르다")

    def test_resolve_stages_defaults_and_all(self) -> None:
        self.assertEqual(pipeline.resolve_stages(None), pipeline.DEFAULT_STAGES)
        self.assertEqual(pipeline.resolve_stages(()), pipeline.DEFAULT_STAGES)
        self.assertEqual(pipeline.resolve_stages(["all"]), pipeline.STAGES)
        self.assertEqual(pipeline.resolve_stages("all"), pipeline.STAGES)

    def test_resolve_stages_keeps_explicit_selection(self) -> None:
        self.assertEqual(pipeline.resolve_stages(["merge", "index"]), ("merge", "index"))
        self.assertEqual(pipeline.resolve_stages("merge, index"), ("merge", "index"))

    def test_resolve_stages_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            pipeline.resolve_stages(["nope"])

    def test_resolve_stages_validates_against_full_list(self) -> None:
        """명시적 입력은 기본 목록이 아니라 STAGES 전체에서 검증한다."""
        for stage in pipeline.OPTIONAL_STAGES:
            self.assertEqual(pipeline.resolve_stages([stage]), (stage,))


class OnDemandVideoTests(unittest.TestCase):
    """영상은 기본으로 받지 않고, 프레임이 필요할 때 그때 받는다 (작업 A)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "raw").mkdir(parents=True)
        self.original_download = pipeline._download
        self.calls: list[str] = []

    def tearDown(self) -> None:
        pipeline._download = self.original_download
        self.tmp.cleanup()

    def _job(self, source: str = "https://www.youtube.com/watch?v=vid") -> None:
        (self.bundle / "job.json").write_text(json.dumps({
            "schema_version": 1, "video_id": "vid",
            "input": {"source": source, "fingerprint": "sha256:x"},
            "config": {}, "status": "complete", "chunks": [],
        }, ensure_ascii=False), encoding="utf-8")

    def _fake_download(self, *, ok: bool = True):
        def download(url, fmt, raw, stem, names):
            self.calls.append(url)
            if not ok:
                return None, "네트워크 없음"
            target = Path(raw) / (stem + ".mp4")
            target.write_bytes(b"fake-video")
            return target, ""
        return download

    def test_visual_stays_in_default_stages(self) -> None:
        """화면 정보를 캡처로 가져오는 것이 이 도구의 목적 절반이다."""
        self.assertIn("visual", pipeline.STAGES)
        self.assertIn("visual", pipeline.DEFAULT_STAGES)
        self.assertNotIn("visual", pipeline.OPTIONAL_STAGES)

    def test_fetch_downloads_video_by_default(self) -> None:
        import inspect
        signature = inspect.signature(pipeline.stage_fetch)
        self.assertIs(signature.parameters["video"].default, True)
        signature = inspect.signature(pipeline.run)
        self.assertIs(signature.parameters["video"].default, True)

    def test_ensure_video_downloads_using_job_source(self) -> None:
        self._job()
        pipeline._download = self._fake_download()
        found = pipeline.ensure_video(self.bundle)
        self.assertIsNotNone(found, "영상을 받지 못했다")
        self.assertEqual(self.calls, ["https://www.youtube.com/watch?v=vid"])
        self.assertIsNotNone(pipeline.visual.source_video(self.bundle))

    def test_ensure_video_reuses_existing_and_does_not_download(self) -> None:
        self._job()
        (self.bundle / "raw/source_video.mp4").write_bytes(b"already-here")
        pipeline._download = self._fake_download()
        found = pipeline.ensure_video(self.bundle)
        self.assertIsNotNone(found)
        self.assertEqual(self.calls, [], "이미 있는 영상을 다시 받았다")
        self.assertEqual((self.bundle / "raw/source_video.mp4").read_bytes(), b"already-here")

    def test_ensure_video_without_job_manifest_returns_none(self) -> None:
        pipeline._download = self._fake_download()
        self.assertIsNone(pipeline.ensure_video(self.bundle))
        self.assertEqual(self.calls, [], "URL 도 모르면서 받으려 했다")

    def test_ensure_video_survives_download_failure(self) -> None:
        self._job()
        pipeline._download = self._fake_download(ok=False)
        self.assertIsNone(pipeline.ensure_video(self.bundle))

    def test_visual_stage_acquires_video_then_extracts(self) -> None:
        self._job()
        (self.bundle / "derived").mkdir(parents=True, exist_ok=True)
        (self.bundle / "derived/merged.json").write_text(json.dumps({
            "schema_version": 1, "video_id": "vid",
            "words": _words(["여기", "보시면", "그림이", "있습니다"], 10.0),
        }, ensure_ascii=False), encoding="utf-8")
        pipeline._download = self._fake_download()
        result = pipeline.stage_visual(self.bundle)
        self.assertEqual(self.calls, ["https://www.youtube.com/watch?v=vid"],
                         "프레임 요청인데 영상을 받지 않았다")
        self.assertTrue(result["candidates_considered"] >= 1)

    def test_visual_stage_reports_when_video_cannot_be_had(self) -> None:
        self._job()
        (self.bundle / "derived").mkdir(parents=True, exist_ok=True)
        (self.bundle / "derived/merged.json").write_text(json.dumps({
            "schema_version": 1, "video_id": "vid",
            "words": _words(["여기", "보시면", "그림이"], 10.0),
        }, ensure_ascii=False), encoding="utf-8")
        pipeline._download = self._fake_download(ok=False)
        result = pipeline.stage_visual(self.bundle)
        self.assertEqual(result["frames"], [])
        self.assertTrue(result["note"], "조용히 빈 결과만 돌려줬다")


class OptionalRenderTests(unittest.TestCase):
    """SRT/TXT 는 요청할 때만 만든다 (작업 C). 기능은 그대로 둔다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "derived").mkdir(parents=True)
        self.texts = ["첫", "번째", "문장", "그리고", "두", "번째", "문장"]
        (self.bundle / "derived/merged.json").write_text(json.dumps({
            "schema_version": 1, "video_id": "vid", "words": _words(self.texts, 0.0),
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_render_is_optional_and_not_in_default_stages(self) -> None:
        self.assertIn("render", pipeline.STAGES)
        self.assertNotIn("render", pipeline.DEFAULT_STAGES)
        self.assertIn("render", pipeline.OPTIONAL_STAGES)

    def test_default_stages_keep_evidence_path_intact(self) -> None:
        """요약과 원문 검색에 필요한 단계는 기본에 남아 있어야 한다."""
        for stage in ("fetch", "plan", "transcribe", "assemble", "merge",
                      "chapters", "index"):
            self.assertIn(stage, pipeline.DEFAULT_STAGES, "%s 가 기본에서 빠졌다" % stage)

    def test_render_still_runs_when_asked_and_preserves_words(self) -> None:
        srt, txt = pipeline.stage_render(self.bundle, width=42)
        self.assertTrue(srt.exists() and txt.exists())
        rendered = txt.read_text(encoding="utf-8").split()
        self.assertEqual(rendered, self.texts, "렌더에서 단어가 사라졌다")

    def test_status_calls_srt_and_txt_optional(self) -> None:
        info = pipeline.status(self.bundle)
        self.assertIn("srt", info["optional_artifacts"])
        self.assertIn("txt", info["optional_artifacts"])
        self.assertNotIn("frames", info["optional_artifacts"],
                         "프레임은 선택이 아니다")


class TranslationGuardTests(unittest.TestCase):
    """Gemini 가 받아적기 대신 번역문을 돌려주는 경우를 잡는다."""

    def test_script_share_counts_only_letters(self) -> None:
        self.assertGreater(pipeline._script_share("안녕하세요 여러분", "hangul"), 0.9)
        self.assertEqual(pipeline._script_share("Hello everyone, 2026", "hangul"), 0.0)
        self.assertEqual(pipeline._script_share("   ", "hangul"), 0.0)

    def test_detects_translation_against_captions(self) -> None:
        korean = "안녕하세요 오늘은 자기지도학습에 대해 말씀드리겠습니다"
        english = "Hello, today I will talk about self supervised learning"
        self.assertTrue(pipeline._looks_translated(
            english, captions=korean, captions_language="ko-orig", langs="auto"))
        self.assertFalse(pipeline._looks_translated(
            korean, captions=korean, captions_language="ko-orig", langs="auto"))

    def test_detects_translation_from_requested_language_alone(self) -> None:
        """자막이 없어도 ko 를 요청했는데 한글이 없으면 번역이다."""
        english = "Hello, today I will talk about self supervised learning"
        self.assertTrue(pipeline._looks_translated(english, captions="", langs="ko-KR"))
        self.assertFalse(pipeline._looks_translated(english, captions="", langs="en-US"))
        self.assertFalse(pipeline._looks_translated(english, captions="", langs="auto"))

    def test_english_video_with_english_captions_is_not_flagged(self) -> None:
        english = "Large language models get the hype but the work is data"
        self.assertFalse(pipeline._looks_translated(
            english, captions=english, captions_language="en-orig", langs="en-US"))

    def test_bilingual_korean_lecture_is_not_flagged(self) -> None:
        """영어 용어가 섞인 한국어 강의를 오탐하면 안 된다."""
        mixed = ("self supervised learning 이라는 방법을 오늘 설명드리겠습니다 "
                 "transformer 구조와 attention 을 함께 봅니다")
        self.assertFalse(pipeline._looks_translated(
            mixed, captions=mixed, captions_language="ko-orig", langs="ko-KR"))

    def test_detects_translation_for_newly_covered_scripts(self) -> None:
        """표에 새로 넣은 언어도 요청 언어만으로 번역문을 잡는다."""
        english = "Hello, today I will talk about self supervised learning"
        for langs in ("bn-BD", "ta-IN", "km-KH", "am-ET", "my-MM", "si-LK",
                      "lo-LA", "te-IN", "ml-IN", "kn-IN", "pa-IN", "gu-IN"):
            with self.subTest(langs=langs):
                self.assertTrue(
                    pipeline._looks_translated(english, captions="", langs=langs))

    def test_new_scripts_are_recognised_in_their_own_text(self) -> None:
        """새 문자 체계의 정상 전사를 번역문으로 오판하지 않는다."""
        samples = {
            "bn-BD": "আমি আজ স্বয়ংক্রিয় শিক্ষার বিষয়ে কথা বলব বন্ধুরা সবাই",
            "ta-IN": "நான் இன்று இயந்திர கற்றல் பற்றி பேசுவேன் நண்பர்களே வணக்கம்",
            "km-KH": "ថ្ងៃនេះខ្ញុំនឹងនិយាយអំពីការរៀនរបស់ម៉ាស៊ីនជាមួយអ្នកទាំងអស់គ្នា",
            "am-ET": "ዛሬ ስለ ማሽን ትምህርት እናገራለሁ ጓደኞቼ ሁላችሁም እንኳን ደህና መጣችሁ",
        }
        for langs, text in samples.items():
            with self.subTest(langs=langs):
                self.assertFalse(
                    pipeline._looks_translated(text, captions="", langs=langs))

    def test_dominant_script_reads_new_blocks(self) -> None:
        self.assertEqual(
            pipeline._dominant_script("আমি বাংলা ভাষায় কথা বলি প্রতিদিন"), "bengali")
        self.assertEqual(
            pipeline._dominant_script("ជំរាបសួរ អ្នកទាំងអស់គ្នា"), "khmer")

    def test_hangul_and_kana_blocks_do_not_collide(self) -> None:
        """호환 자모(ㄱ~ㆎ)와 가나 블록이 서로를 삼키지 않는다."""
        self.assertEqual(pipeline._script_counts("ㄱㄴㄷ")["hangul"], 3)
        self.assertEqual(pipeline._script_counts("ひらがな")["japanese"], 4)

    def test_short_captions_do_not_trigger_on_their_own(self) -> None:
        """자막이 몇 글자뿐이면 근거로 쓰지 않는다."""
        self.assertFalse(pipeline._looks_translated(
            "Hello there friends", captions="안녕", captions_language="ko-orig",
            langs="auto"))


class MetadataLanguageEvidenceTests(unittest.TestCase):
    """자막이 없을 때 영상 메타데이터가 원어 근거를 대신한다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "raw").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, payload: dict) -> None:
        (self.bundle / "raw" / pipeline.METADATA_NAME).write_text(
            json.dumps(payload), encoding="utf-8")

    def test_declared_language_gives_the_script(self) -> None:
        self._write({"language": "ko", "auto_caption_langs": []})
        self.assertEqual(pipeline._metadata_script(self.bundle), "hangul")

    def test_auto_caption_track_is_used_when_language_is_missing(self) -> None:
        self._write({"language": None, "auto_caption_langs": ["ko-orig"]})
        self.assertEqual(pipeline._metadata_script(self.bundle), "hangul")

    def test_region_suffix_is_tolerated(self) -> None:
        self._write({"language": "en-US", "auto_caption_langs": []})
        self.assertEqual(pipeline._metadata_script(self.bundle), "latin")

    def test_unknown_or_missing_metadata_is_silent(self) -> None:
        self.assertIsNone(pipeline._metadata_script(self.bundle))
        self._write({"language": "xx", "auto_caption_langs": []})
        self.assertIsNone(pipeline._metadata_script(self.bundle))
        (self.bundle / "raw" / pipeline.METADATA_NAME).write_text("{", encoding="utf-8")
        self.assertIsNone(pipeline._metadata_script(self.bundle))

    def test_title_script_is_never_used(self) -> None:
        """영어 제목의 한국어 강의를 영어 영상으로 오판하면 안 된다."""
        self._write({"title": "Self-Supervised Learning Lecture",
                     "language": None, "auto_caption_langs": []})
        self.assertIsNone(pipeline._metadata_script(self.bundle))

    def test_metadata_closes_the_blind_spot(self) -> None:
        """자막도 지정 언어도 없던 사각지대를 메타데이터가 메운다."""
        english = "Hello, today I will talk about self supervised learning"
        self.assertFalse(pipeline._looks_translated(english, captions="", langs="auto"))
        self.assertTrue(pipeline._looks_translated(
            english, captions="", langs="auto", metadata_script="hangul"))

    def test_metadata_does_not_flag_a_matching_transcript(self) -> None:
        korean = "안녕하세요 오늘은 자기지도학습에 대해 말씀드리겠습니다 여러분"
        self.assertFalse(pipeline._looks_translated(
            korean, captions="", langs="auto", metadata_script="hangul"))

    def test_requested_language_outranks_metadata(self) -> None:
        """사람이 지정한 언어가 YouTube 판정보다 앞선다."""
        english = "Hello, today I will talk about self supervised learning"
        self.assertFalse(pipeline._looks_translated(
            english, captions="", langs="en-US", metadata_script="hangul"))

    def test_original_captions_outrank_metadata(self) -> None:
        korean_captions = "안녕하세요 오늘은 자기지도학습에 대해 말씀드리겠습니다"
        self.assertEqual(
            pipeline._expected_script(captions=korean_captions, langs="auto",
                                      captions_language="ko-orig",
                                      metadata_script="latin"),
            "hangul")


class TranslationGuardInPipelineTests(unittest.TestCase):
    """번역문이 오면 첫 청크에서 멈춰 남은 호출을 아낀다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "raw/audio").mkdir(parents=True)
        (self.bundle / "raw/transcripts").mkdir(parents=True)
        (self.bundle / "raw/captions.json").write_text(json.dumps({
            "source": "youtube-ko-orig", "video_id": "vid",
            "cues": [{"start": 0.0, "end": 5.0,
                      "text": "안녕하세요 오늘은 자기지도학습에 대해 말씀드리겠습니다"}],
        }, ensure_ascii=False), encoding="utf-8")
        self.job = {
            "schema_version": 1, "video_id": "vid",
            "input": {"source": "u", "fingerprint": "sha256:x"},
            "config": {"chunk_max_secs": 600.0, "overlap_secs": 10.0,
                       "language_codes": None},
            "status": "planned",
            "chunks": [
                {"index": i, "start": i * 100.0, "end": (i + 1) * 100.0,
                 "path": "raw/audio/chunk-%03d.mp3" % i, "status": "pending",
                 "attempts": 0,
                 "transcript_path": "raw/transcripts/chunk-%03d.json" % i,
                 "error": None}
                for i in range(3)
            ],
        }
        for chunk in self.job["chunks"]:
            (self.bundle / chunk["path"]).write_bytes(b"x")
        (self.bundle / "job.json").write_text(json.dumps(self.job, ensure_ascii=False),
                                              encoding="utf-8")
        self.ledger = Path(self.tmp.name) / "usage.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, transcriber):
        return pipeline.stage_transcribe(
            self.bundle, self.job, ledger=self.ledger, api_key="k",
            daily_limit=25, rpm_limit=None, request_interval=0.0,
            transcriber=transcriber)

    def test_translation_stops_after_first_chunk(self) -> None:
        calls: list[str] = []

        def transcriber(path, langs):
            calls.append(path)
            return {"words": [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.3,
                               "speaker": "spk:0"}
                              for i, t in enumerate(
                                  "Hello today I will talk about self supervised "
                                  "learning methods".split())]}

        with self.assertRaises(pipeline.StageError) as caught:
            self._run(transcriber)
        self.assertEqual(len(calls), 1, "번역문을 받고도 나머지 청크를 계속 불렀다")
        self.assertIn("번역", str(caught.exception))

    def test_korean_transcription_passes(self) -> None:
        def transcriber(path, langs):
            return {"words": [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.3,
                               "speaker": "spk:0"}
                              for i, t in enumerate(
                                  "안녕하세요 오늘은 자기지도학습에 대해 "
                                  "말씀드리겠습니다".split())]}

        job = self._run(transcriber)
        self.assertEqual(job["status"], "complete")


class TranslationGuardBasisTests(TranslationGuardInPipelineTests):
    """검사 근거가 있었는지를 job 에 남긴다."""

    def _korean(self, path, langs):
        return {"words": [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.3,
                           "speaker": "spk:0"}
                          for i, t in enumerate(
                              "안녕하세요 오늘은 자기지도학습에 대해 "
                              "말씀드리겠습니다".split())]}

    def _blind(self) -> None:
        """자막도 지정 언어도 메타데이터도 없는 상태로 만든다."""
        (self.bundle / "raw/captions.json").write_text(
            json.dumps({"source": "youtube", "language": None, "original": False,
                        "video_id": "vid", "cues": []}), encoding="utf-8")

    def test_guard_records_active_when_captions_exist(self) -> None:
        job = self._run(self._korean)
        self.assertEqual(job["translation_guard"], "active")

    def test_guard_records_skipped_without_any_evidence(self) -> None:
        self._blind()
        job = self._run(self._korean)
        self.assertEqual(job["translation_guard"], "skipped")

    def test_metadata_alone_keeps_the_guard_active(self) -> None:
        self._blind()
        (self.bundle / "raw" / pipeline.METADATA_NAME).write_text(
            json.dumps({"language": "ko", "auto_caption_langs": ["ko-orig"]}),
            encoding="utf-8")
        job = self._run(self._korean)
        self.assertEqual(job["translation_guard"], "active")

    def test_metadata_alone_blocks_a_translation(self) -> None:
        """자막이 없어도 메타데이터만으로 번역문을 잡는다."""
        self._blind()
        (self.bundle / "raw" / pipeline.METADATA_NAME).write_text(
            json.dumps({"language": "ko", "auto_caption_langs": []}),
            encoding="utf-8")
        calls: list[str] = []

        def transcriber(path, langs):
            calls.append(path)
            return {"words": [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.3,
                               "speaker": "spk:0"}
                              for i, t in enumerate(
                                  "Hello today I will talk about self supervised "
                                  "learning methods".split())]}

        with self.assertRaises(pipeline.StageError):
            self._run(transcriber)
        self.assertEqual(len(calls), 1)

    def test_status_reports_the_guard(self) -> None:
        self._blind()
        self._run(self._korean)
        info = pipeline.status(self.bundle)
        self.assertEqual(info["translation_guard"], "skipped")
        self.assertIsNone(info["metadata_script"])


class TranslationGuardRerunTests(TranslationGuardInPipelineTests):
    """번역문으로 멈춘 뒤 이어가는 경로."""

    def test_same_settings_rerun_costs_no_call(self) -> None:
        """저장된 응답을 다시 읽어 같은 판정을 낸다. 호출은 쓰지 않는다."""
        calls: list[str] = []
        english = ("Hello today I will talk about self supervised learning "
                   "methods and their applications")

        def transcriber(path, langs):
            calls.append(path)
            words = [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.3,
                      "speaker": "spk:0"} for i, t in enumerate(english.split())]
            # 실제 경로와 같게 응답 원문을 먼저 남긴다.
            name = Path(path).name
            chunk = next(c for c in self.job["chunks"]
                         if Path(c["path"]).name == name)
            raw = pipeline._raw_path(self.bundle, chunk)
            raw.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(pipeline._raw_meta(self.job, chunk, langs))
            payload["response"] = {"words": words}
            raw.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return {"words": words}

        with self.assertRaises(pipeline.StageError):
            self._run(transcriber)
        self.assertEqual(len(calls), 1)

        original = pipeline.transcribe_mod.from_raw
        pipeline.transcribe_mod.from_raw = lambda path: {
            "words": json.loads(Path(path).read_text(encoding="utf-8"))["response"]["words"]}
        try:
            with self.assertRaises(pipeline.StageError):
                self._run(transcriber)
        finally:
            pipeline.transcribe_mod.from_raw = original
        self.assertEqual(len(calls), 1, "재실행이 Gemini 를 다시 불렀다")

    def test_error_message_explains_how_to_continue(self) -> None:
        def transcriber(path, langs):
            return {"words": [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.3,
                               "speaker": "spk:0"}
                              for i, t in enumerate(
                                  "Hello today I will talk about self supervised "
                                  "learning".split())]}

        with self.assertRaises(pipeline.StageError) as caught:
            self._run(transcriber)
        message = str(caught.exception)
        self.assertIn("--language", message)
        self.assertIn("--from-raw", message)


class CaptionTrustTests(unittest.TestCase):
    """번역된 자막 트랙을 원어 근거로 쓰면 안 된다."""

    def test_original_track_is_trusted(self) -> None:
        self.assertTrue(pipeline._captions_are_original("ko-orig"))
        self.assertTrue(pipeline._captions_are_original("en-orig"))

    def test_translated_track_is_not_trusted(self) -> None:
        self.assertFalse(pipeline._captions_are_original("ko"))
        self.assertFalse(pipeline._captions_are_original("en"))
        self.assertFalse(pipeline._captions_are_original(None))

    def test_guard_ignores_translated_captions(self) -> None:
        """영어 영상에 한국어 번역 자막이 붙어도 전사를 막지 않는다."""
        english = "Large language models get the hype but the work is data"
        korean_sub = "대규모 언어 모델이 주목받지만 실제 일은 데이터입니다"
        self.assertFalse(
            pipeline._looks_translated(english, captions=korean_sub,
                                       captions_language="ko", langs="auto"),
            "번역 자막을 원어 근거로 썼다")
        self.assertTrue(
            pipeline._looks_translated(english, captions=korean_sub,
                                       captions_language="ko-orig", langs="auto"))

    def test_fallback_captions_are_not_used_as_evidence(self) -> None:
        """original=False 로 표시된 자막은 원어 근거에서 빠진다."""
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "vid"
            (bundle / "raw").mkdir(parents=True)
            (bundle / "raw/captions.json").write_text(json.dumps({
                "source": "youtube-ko", "language": "ko", "original": False,
                "video_id": "vid",
                "cues": [{"start": 0.0, "end": 3.0,
                          "text": "대규모 언어 모델이 주목받지만 실제 일은 데이터입니다"}],
            }, ensure_ascii=False), encoding="utf-8")
            text, language = pipeline._captions_evidence(bundle)
            self.assertTrue(text)
            self.assertIsNone(language, "번역 자막을 원어 근거로 넘겼다")


class ReviewFixTests(unittest.TestCase):
    """코드 리뷰에서 나온 네 건 (2026-08-31)."""

    VIDEO_ID = "abcdefghijk"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / self.VIDEO_ID
        (self.bundle / "derived").mkdir(parents=True)
        (self.bundle / "raw").mkdir(parents=True)
        (self.bundle / "job.json").write_text(json.dumps({
            "input": {"source": "https://www.youtube.com/watch?v=abcdefghijk"},
        }), encoding="utf-8")
        self.calls: list[str] = []
        self.original = pipeline._download

        def download(url, fmt, raw, stem, names):
            self.calls.append(url)
            target = Path(raw) / (stem + ".mp4")
            target.write_bytes(b"fake")
            return target, ""

        pipeline._download = download

    def tearDown(self) -> None:
        pipeline._download = self.original
        self.tmp.cleanup()

    def _merged(self) -> None:
        (self.bundle / "derived/merged.json").write_text(json.dumps({
            "video_id": self.VIDEO_ID,
            "words": _words(["여기", "보시면", "그림이"], 10.0),
        }, ensure_ascii=False), encoding="utf-8")

    def test_skip_video_is_honoured_by_visual_stage(self) -> None:
        """--skip-video 로 껐는데 visual 이 곧바로 받아오면 무의미하다."""
        self._merged()
        pipeline.run("https://www.youtube.com/watch?v=abcdefghijk",
                     bundle_root=Path(self.tmp.name), stages=("visual",), video=False)
        self.assertEqual(self.calls, [], "--skip-video 인데 영상을 받았다")

    def test_visual_still_acquires_when_video_not_skipped(self) -> None:
        self._merged()
        pipeline.run("https://www.youtube.com/watch?v=abcdefghijk",
                     bundle_root=Path(self.tmp.name), stages=("visual",), video=True)
        self.assertEqual(len(self.calls), 1)

    def test_no_download_when_transcript_is_missing(self) -> None:
        """전사가 없으면 어차피 실패한다. 영상부터 받고 버리지 않는다."""
        with self.assertRaises(Exception):
            pipeline.stage_visual(self.bundle)
        self.assertEqual(self.calls, [], "쓰지도 못할 영상을 먼저 받았다")


class ChunkRangeGuardTests(unittest.TestCase):
    """번역문 판정은 그 청크의 시간대 자막과 대조한다."""

    CUES = [
        {"start": 0.0, "end": 100.0, "text": "안녕하세요 오늘은 자기지도학습을 다룹니다"},
        {"start": 100.0, "end": 200.0, "text": "이어서 초청 연사의 발표가 있겠습니다"},
        {"start": 200.0, "end": 300.0,
         "text": "thanks everyone I will present our recent results today"},
    ]

    def test_range_text_picks_overlapping_cues(self) -> None:
        text = pipeline._captions_in_range(self.CUES, 200.0, 300.0)
        self.assertIn("thanks everyone", text)
        self.assertNotIn("안녕하세요", text)

    def test_english_guest_segment_is_not_flagged(self) -> None:
        """한국어 강의 안의 영어 발표 구간을 막으면 안 된다."""
        english = " ".join(c["text"] for c in self.CUES[2:])
        self.assertFalse(pipeline._looks_translated(
            english, captions=pipeline._captions_in_range(self.CUES, 200.0, 300.0),
            captions_language="ko-orig", langs="auto"))

    def test_korean_segment_translated_is_flagged(self) -> None:
        english = "Hello everyone today we cover self supervised learning in depth"
        self.assertTrue(pipeline._looks_translated(
            english, captions=pipeline._captions_in_range(self.CUES, 0.0, 100.0),
            captions_language="ko-orig", langs="auto"))


class ScriptGuardTests(unittest.TestCase):
    """번역문 판정은 문자 종류로 한다. 한국어를 특별 취급하지 않는다."""

    KO = "안녕하세요 오늘은 자기지도학습에 대해 말씀드리겠습니다 여러분"
    EN = "Hello everyone today I will talk about self supervised learning"
    JA = "こんにちは今日は自己教師あり学習についてお話しします皆さん"
    ZH = "大家好今天我要讲的是自监督学习的最新进展和应用"

    def test_script_profile_names_the_dominant_script(self) -> None:
        self.assertEqual(pipeline._dominant_script(self.KO), "hangul")
        self.assertEqual(pipeline._dominant_script(self.EN), "latin")
        self.assertEqual(pipeline._dominant_script(self.JA), "japanese")
        self.assertEqual(pipeline._dominant_script(self.ZH), "han")
        self.assertIsNone(pipeline._dominant_script("2026 :: 12.5 %"))

    def test_translation_detected_for_any_source_language(self) -> None:
        for name, captions in (("한국어", self.KO), ("일본어", self.JA),
                               ("중국어", self.ZH)):
            with self.subTest(source=name):
                self.assertTrue(pipeline._looks_translated(
                    self.EN, captions=captions, captions_language="xx-orig",
                    langs="auto"), "%s 영상의 영어 번역문을 놓쳤다" % name)

    def test_matching_script_passes(self) -> None:
        for name, text in (("한국어", self.KO), ("영어", self.EN),
                           ("일본어", self.JA), ("중국어", self.ZH)):
            with self.subTest(source=name):
                self.assertFalse(pipeline._looks_translated(
                    text, captions=text, captions_language="xx-orig", langs="auto"))

    def test_english_terms_in_korean_lecture_still_pass(self) -> None:
        mixed = ("self supervised learning 이라는 방법을 오늘 설명드리겠습니다 "
                 "transformer 구조와 attention 을 함께 봅니다")
        self.assertFalse(pipeline._looks_translated(
            mixed, captions=mixed, captions_language="ko-orig", langs="ko-KR"))

    def test_requested_language_rule_covers_more_than_korean(self) -> None:
        """자막이 없어도 요청 언어의 문자 종류와 어긋나면 잡는다."""
        self.assertTrue(pipeline._looks_translated(
            self.EN, captions="", captions_language=None, langs="ja-JP"))
        self.assertTrue(pipeline._looks_translated(
            self.EN, captions="", captions_language=None, langs="ko-KR"))
        self.assertFalse(pipeline._looks_translated(
            self.EN, captions="", captions_language=None, langs="en-US"))
        self.assertFalse(pipeline._looks_translated(
            self.EN, captions="", captions_language=None, langs="auto"))

    def test_unknown_requested_language_does_not_block(self) -> None:
        """문자 종류를 모르는 언어 코드는 근거로 쓰지 않는다."""
        self.assertFalse(pipeline._looks_translated(
            self.EN, captions="", captions_language=None, langs="sw-KE"))


class CallPacingTests(unittest.TestCase):
    """분당 한도에 여유가 있으면 기다리지 않고 바로 보낸다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "raw/audio").mkdir(parents=True)
        (self.bundle / "raw/transcripts").mkdir(parents=True)
        self.job = {
            "schema_version": 1, "video_id": "vid",
            "input": {"source": "u", "fingerprint": "sha256:x"},
            "config": {"chunk_max_secs": 600.0, "overlap_secs": 10.0,
                       "language_codes": None},
            "status": "planned",
            "chunks": [
                {"index": i, "start": i * 100.0, "end": (i + 1) * 100.0,
                 "path": "raw/audio/chunk-%03d.mp3" % i, "status": "pending",
                 "attempts": 0,
                 "transcript_path": "raw/transcripts/chunk-%03d.json" % i,
                 "error": None}
                for i in range(3)
            ],
        }
        for chunk in self.job["chunks"]:
            (self.bundle / chunk["path"]).write_bytes(b"x")
        self.ledger = Path(self.tmp.name) / "usage.json"
        self.slept: list[float] = []
        self.original_sleep = pipeline.time.sleep
        pipeline.time.sleep = self.slept.append

    def tearDown(self) -> None:
        pipeline.time.sleep = self.original_sleep
        self.tmp.cleanup()

    def _transcriber(self, path, langs):
        return {"words": [{"text": "안녕하세요", "start": 0.0, "end": 0.4,
                           "speaker": "spk:0"}]}

    def _run(self, *, rpm_limit, request_interval=30.0):
        return pipeline.stage_transcribe(
            self.bundle, self.job, ledger=self.ledger, api_key="k",
            daily_limit=25, rpm_limit=rpm_limit, request_interval=request_interval,
            transcriber=self._transcriber)

    def test_only_waits_when_the_minute_window_is_full(self) -> None:
        """분당 2회면 앞의 두 번은 즉시 나가고 세 번째만 기다린다.

        옛 방식은 청크마다 무조건 30초를 쉬어 3청크에 두 번(60초) 쉬었다.
        실제 호출은 한 번에 60~70초가 걸리므로 창이 저절로 비어, 실행 중에는
        이 대기마저 거의 0 이 된다.
        """
        self._run(rpm_limit=2)
        self.assertEqual(len(self.slept), 1,
                         "대기 횟수가 1회가 아니다: %s" % self.slept)
        self.assertLessEqual(self.slept[0], 61.0)

    def test_no_wait_at_all_when_limit_is_generous(self) -> None:
        self._run(rpm_limit=10)
        self.assertEqual(self.slept, [], "여유가 있는데 기다렸다")

    def test_fixed_interval_still_used_without_rpm_limit(self) -> None:
        self._run(rpm_limit=None, request_interval=7.0)
        self.assertEqual(self.slept, [7.0, 7.0],
                         "RPM 한도가 없으면 고정 간격을 써야 한다")


class SingleFetchCallTests(unittest.TestCase):
    """소리·영상·자막을 yt-dlp 한 번으로 받는다."""

    SRT = ("1\n00:00:01,000 --> 00:00:03,000\n안녕하세요 여러분\n\n"
           "2\n00:00:03,000 --> 00:00:05,000\n오늘은 자기지도학습입니다\n")

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "raw").mkdir(parents=True)
        self.commands: list[list[str]] = []
        self.original_run = pipeline.subprocess.run
        self.produce = {"audio": True, "video": True, "captions": True}

        def run(command, capture_output=True, text=True, **kwargs):
            self.commands.append(list(command))
            if command[0] == "ffprobe":
                target = Path(command[-1])
                has_video = "video" in target.name
                return pipeline.subprocess.CompletedProcess(
                    command, 0, "video\n" if has_video else "", "")
            out = Path(command[command.index("-o") + 1]).parent
            out.mkdir(parents=True, exist_ok=True)
            if self.produce["audio"]:
                (out / "media.f251.audio.webm").write_bytes(b"audio-bytes")
            if self.produce["video"] and "," in command[command.index("-f") + 1]:
                (out / "media.f134.video.mp4").write_bytes(b"video-bytes")
            if self.produce["captions"] and "--sub-langs" in command:
                (out / "media.ko-orig.srt").write_text(self.SRT, encoding="utf-8")
            if "--write-info-json" in command:
                # 실물 yt-dlp 처럼 미디어와 같은 디렉터리에 떨군다. 크기가 커서
                # "가장 큰 파일" 규칙에 걸리면 오디오로 오인된다.
                (out / "media.f251.info.json").write_text(
                    json.dumps({"id": "abcdefghijk", "title": "제목",
                                "channel": "채널", "language": "ko",
                                "automatic_captions": {"ko-orig": [], "en": []},
                                "subtitles": {}, "thumbnails": ["x"] * 400}),
                    encoding="utf-8")
            return pipeline.subprocess.CompletedProcess(command, 0, "", "")

        pipeline.subprocess.run = run

    def tearDown(self) -> None:
        pipeline.subprocess.run = self.original_run
        self.tmp.cleanup()

    def _yt_dlp_calls(self) -> list[list[str]]:
        return [c for c in self.commands if c and c[0] == "yt-dlp"]

    def test_metadata_rides_along_and_is_trimmed(self) -> None:
        pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk")
        self.assertEqual(len(self._yt_dlp_calls()), 1, "메타데이터 때문에 호출이 늘었다")
        saved = json.loads(
            (self.bundle / "raw" / pipeline.METADATA_NAME).read_text(encoding="utf-8"))
        self.assertEqual(saved["language"], "ko")
        self.assertEqual(saved["auto_caption_langs"], ["ko-orig"])
        self.assertNotIn("thumbnails", saved)
        self.assertEqual(pipeline._metadata_script(self.bundle), "hangul")

    def test_info_json_is_not_mistaken_for_media(self) -> None:
        """info.json 이 media 목록에 새면 가장 큰 파일 규칙에 걸려 오디오가 된다."""
        pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk")
        audio_path = Path(pipeline.audio.source_audio(self.bundle))
        self.assertEqual(audio_path.suffix, ".webm")
        self.assertEqual(audio_path.read_bytes(), b"audio-bytes")

    def test_captions_carry_the_bundle_video_id(self) -> None:
        """통합 취득은 파일을 media.<format>.srt 로 받는다. 그 이름이 video_id 가
        되면 merge 가 "video_id 가 다릅니다" 로 멈춘다."""
        pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk")
        payload = json.loads(
            (self.bundle / "raw" / "captions.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["video_id"], "vid")
        self.assertEqual(payload["language"], "ko-orig")

    def test_one_call_produces_all_three(self) -> None:
        result = pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk")
        self.assertEqual(len(self._yt_dlp_calls()), 1,
                         "yt-dlp 를 여러 번 불렀다: %d" % len(self._yt_dlp_calls()))
        self.assertIsNotNone(pipeline.audio.source_audio(self.bundle))
        self.assertIsNotNone(pipeline.visual.source_video(self.bundle))
        self.assertEqual(result["cues"], 2)

    def test_captions_keep_language_and_original_flag(self) -> None:
        pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk")
        payload = json.loads((self.bundle / "raw/captions.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["language"], "ko-orig")
        self.assertTrue(payload["original"])

    def test_skip_video_asks_for_audio_only(self) -> None:
        pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk", video=False)
        fmt = self._yt_dlp_calls()[0]
        fmt_value = fmt[fmt.index("-f") + 1]
        self.assertNotIn(",", fmt_value, "영상을 껐는데 영상 포맷을 함께 요청했다")
        self.assertIsNone(pipeline.visual.source_video(self.bundle))

    def test_missing_audio_is_fatal(self) -> None:
        self.produce["audio"] = False
        with self.assertRaises(pipeline.StageError):
            pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk")

    def test_missing_video_is_only_a_warning(self) -> None:
        self.produce["video"] = False
        result = pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk")
        self.assertIsNotNone(pipeline.audio.source_audio(self.bundle))
        self.assertIsNone(result["video"])

    def test_existing_files_are_reused_without_any_call(self) -> None:
        (self.bundle / "raw/source.webm").write_bytes(b"a")
        (self.bundle / "raw/source_video.mp4").write_bytes(b"v")
        (self.bundle / "raw/captions.json").write_text(json.dumps({
            "source": "youtube-ko-orig", "language": "ko-orig", "original": True,
            "video_id": "vid", "cues": []}), encoding="utf-8")
        pipeline.stage_fetch(self.bundle, "https://youtu.be/abcdefghijk")
        self.assertEqual(self._yt_dlp_calls(), [], "받아둔 것이 있는데 다시 불렀다")


class VideoReleaseTests(unittest.TestCase):
    """프레임을 뽑고 나면 영상은 자리만 차지한다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "derived").mkdir(parents=True)
        (self.bundle / "raw/frames").mkdir(parents=True)
        (self.bundle / "raw/source_video.mp4").write_bytes(b"video-bytes")
        (self.bundle / "raw/source.webm").write_bytes(b"audio-bytes")
        (self.bundle / "job.json").write_text(json.dumps({
            "input": {"source": "https://www.youtube.com/watch?v=abcdefghijk"},
        }), encoding="utf-8")
        (self.bundle / "derived/merged.json").write_text(json.dumps({
            "video_id": "vid", "words": _words(["여기", "보시면", "그림이"], 10.0),
        }, ensure_ascii=False), encoding="utf-8")
        self.frames: list[dict] = [{"timestamp": 10.5, "path": "raw/frames/000010500.jpg",
                                    "reason": "screen-reference", "ocr_text": None,
                                    "confidence": 0.5}]
        self.original_build = pipeline.visual.build

        def build(bundle, *, at=None, max_frames=40):
            payload = {"schema_version": 1, "video_id": "vid", "frames": list(self.frames),
                       "candidates_considered": len(self.frames),
                       "candidates_dropped": 0, "note": None}
            pipeline._write_json(bundle / "derived" / "frames.json", payload)
            return payload

        pipeline.visual.build = build

    def tearDown(self) -> None:
        pipeline.visual.build = self.original_build
        self.tmp.cleanup()

    def test_video_is_released_after_frames_are_extracted(self) -> None:
        pipeline.stage_visual(self.bundle)
        self.assertIsNone(pipeline.visual.source_video(self.bundle),
                          "프레임을 다 뽑고도 영상을 들고 있다")
        self.assertTrue((self.bundle / "raw/frames").exists(), "프레임을 지웠다")
        self.assertTrue((self.bundle / "raw/source.webm").exists(), "오디오를 지웠다")

    def test_video_is_kept_when_no_frames_came_out(self) -> None:
        """프레임이 0장이면 다시 시도할 수 있게 영상을 남긴다."""
        self.frames = []
        pipeline.stage_visual(self.bundle)
        self.assertIsNotNone(pipeline.visual.source_video(self.bundle))

    def test_video_is_kept_without_a_source_url(self) -> None:
        """원본 주소를 모르면 다시 받을 수 없으므로 지우지 않는다."""
        (self.bundle / "job.json").unlink()
        pipeline.stage_visual(self.bundle)
        self.assertIsNotNone(pipeline.visual.source_video(self.bundle))

    def test_keep_video_option_wins(self) -> None:
        pipeline.stage_visual(self.bundle, keep_video=True)
        self.assertIsNotNone(pipeline.visual.source_video(self.bundle))

    def test_purge_scope_video_removes_only_the_video(self) -> None:
        removed = pipeline.purge(self.bundle, scope="video")
        self.assertTrue(removed)
        self.assertIsNone(pipeline.visual.source_video(self.bundle))
        self.assertTrue((self.bundle / "raw/source.webm").exists())
        self.assertTrue((self.bundle / "derived/merged.json").exists())


class CaptionHealthTests(unittest.TestCase):
    """자막이 0개일 때, 원래 없는 것과 취득이 고장난 것을 구분한다."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name) / "vid"
        (self.bundle / "raw").mkdir(parents=True)
        (self.bundle / "derived").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _captions(self, payload: dict) -> None:
        (self.bundle / "raw/captions.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_status_reports_caption_language_and_count(self) -> None:
        self._captions({"source": "youtube-ko-orig", "language": "ko-orig",
                        "original": True, "video_id": "vid",
                        "cues": [{"start": 0.0, "end": 1.0, "text": "안녕"}]})
        info = pipeline.status(self.bundle)
        self.assertEqual(info["captions"]["language"], "ko-orig")
        self.assertEqual(info["captions"]["cues"], 1)
        self.assertTrue(info["captions"]["original"])

    def test_status_flags_a_failed_fetch(self) -> None:
        """취득 실패로 남은 빈 자막은 그렇다고 말해야 한다."""
        self._captions({"source": "youtube", "language": None, "original": False,
                        "video_id": "vid", "cues": [], "error": "yt-dlp 실패"})
        info = pipeline.status(self.bundle)
        self.assertEqual(info["captions"]["cues"], 0)
        self.assertTrue(info["captions"]["failed"], "고장을 정상으로 보고했다")

    def test_video_without_captions_is_not_a_failure(self) -> None:
        """자막이 원래 없는 영상도 있다. 그건 고장이 아니다."""
        self._captions({"source": "youtube-en-orig", "language": "en-orig",
                        "original": True, "video_id": "vid", "cues": []})
        info = pipeline.status(self.bundle)
        self.assertFalse(info["captions"]["failed"])

    def test_missing_captions_file_is_reported(self) -> None:
        info = pipeline.status(self.bundle)
        self.assertIsNone(info["captions"])
