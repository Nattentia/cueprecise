from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import audio


class ChunkPlanTests(unittest.TestCase):
    def test_single_short_chunk(self) -> None:
        self.assertEqual(len(audio.plan_chunks(100.0)), 1)

    def test_equal_plan_has_no_tiny_tail(self) -> None:
        chunks = audio.plan_chunks(3600.0)
        self.assertEqual(len(chunks), 3)
        core_ends = [chunks[0]["end"]]
        core_ends.extend(chunk["end"] for chunk in chunks[1:])
        self.assertEqual(core_ends, [1200.0, 2400.0, 3600.0])
        self.assertTrue(all(c["end"] - c["start"] <= 1790.0 for c in chunks))

    def test_overlap_can_require_one_more_chunk(self) -> None:
        chunks = audio.plan_chunks(3570.0)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(c["end"] - c["start"] <= 1790.0 for c in chunks))

    def test_invalid_overlap_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            audio.plan_chunks(10.0, chunk_max_secs=10.0, overlap_secs=10.0)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class AudioExtractionTests(unittest.TestCase):
    def test_create_job_extracts_chunks_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=3", "-y", str(source),
            ], check=True)
            bundle = root / "bundle"
            job = audio.create_job(
                source, bundle, "sample", "fixture",
                chunk_max_secs=2.0, overlap_secs=0.25,
            )
            self.assertEqual(job["schema_version"], 1)
            self.assertEqual(job["status"], "planned")
            self.assertTrue(job["input"]["fingerprint"].startswith("sha256:"))
            self.assertGreater(len(job["chunks"]), 1)
            for chunk in job["chunks"]:
                self.assertLessEqual(chunk["end"] - chunk["start"], 2.0)
                self.assertTrue((bundle / chunk["path"]).is_file())
            stored = json.loads((bundle / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, job)



class SourceAudioLookupTests(unittest.TestCase):
    """오디오는 받은 형식 그대로 둔다. 확장자가 여러 개일 수 있다."""

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

    def test_returns_none_without_audio(self) -> None:
        self.assertIsNone(audio.source_audio(self.bundle))

    def test_finds_native_download(self) -> None:
        self._touch("source.webm")
        self.assertEqual(audio.source_audio(self.bundle).name, "source.webm")

    def test_legacy_mp3_bundle_still_works(self) -> None:
        self._touch("source.mp3")
        self.assertEqual(audio.source_audio(self.bundle).name, "source.mp3")

    def test_native_format_wins_over_legacy_mp3(self) -> None:
        self._touch("source.mp3")
        self._touch("source.m4a")
        self.assertEqual(audio.source_audio(self.bundle).name, "source.m4a")

    def test_video_is_never_mistaken_for_audio(self) -> None:
        self._touch("source_video.mp4")
        self._touch("source.mp4")
        self.assertIsNone(audio.source_audio(self.bundle),
                          "영상 파일을 오디오로 골랐다")

if __name__ == "__main__":
    unittest.main()
