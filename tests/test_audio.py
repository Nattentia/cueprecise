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


if __name__ == "__main__":
    unittest.main()
