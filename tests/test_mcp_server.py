"""MCP 도구 표면 테스트. 네트워크와 Gemini API 를 쓰지 않는다."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import context
import mcp_server
import pipeline


def word(text: str, start: float, **extra) -> dict:
    return {"text": text, "start": start, "end": start + 0.3,
            "speaker": "spk:0", "origin": "gemini", **extra}


class BundleFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "vid"
        (self.bundle / "derived").mkdir(parents=True)
        words = (
            [word("안녕하세요.", 0.0), word("시작합니다.", 0.5)]
            + [word("이", 100.0), word("그림을", 100.5), word("보시면", 101.0)]
            + [word("했을까요?", 207.6),
               word("self", 208.0, origin="youtube"),
               word("supervised", 208.9, origin="youtube"),
               word("라는", 210.8)]
        )
        (self.bundle / "derived" / "merged.json").write_text(json.dumps(
            {"source": "merged", "video_id": "vid", "words": words},
            ensure_ascii=False), encoding="utf-8")
        (self.bundle / "derived" / "frames.json").write_text(json.dumps(
            {"schema_version": 1, "video_id": "vid", "frames": [
                {"timestamp": 208.0, "path": "raw/frames/000208000.jpg",
                 "reason": "restored-term", "ocr_text": "self supervised learning",
                 "confidence": 0.82}]},
            ensure_ascii=False), encoding="utf-8")
        context.build_index(self.bundle)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class ToolSurfaceTests(unittest.TestCase):
    def test_contract_required_tools_exist(self) -> None:
        names = {t["name"] for t in mcp_server.TOOLS}
        self.assertEqual(names, {
            "ytx_register", "ytx_status", "ytx_outline", "ytx_query",
            "ytx_excerpt", "ytx_frames", "ytx_purge", "ytx_set_chapter_titles"})

    def test_every_tool_declares_an_input_schema(self) -> None:
        for tool in mcp_server.TOOLS:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("required", tool["inputSchema"])
            self.assertTrue(tool["description"].strip())


class ProtocolTests(unittest.TestCase):
    def test_initialize_returns_protocol_version(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            bundle_root=Path("data"))
        self.assertEqual(reply["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSION)
        self.assertEqual(reply["result"]["serverInfo"]["name"], "ytx")

    def test_notifications_get_no_reply(self) -> None:
        self.assertIsNone(mcp_server.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            bundle_root=Path("data")))

    def test_unknown_method_returns_jsonrpc_error(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 9, "method": "nope"}, bundle_root=Path("data"))
        self.assertEqual(reply["error"]["code"], -32601)

    def test_tool_failure_is_reported_as_iserror_not_crash(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "ytx_outline", "arguments": {"video_id": "없음"}}},
            bundle_root=Path("data"))
        self.assertTrue(reply["result"]["isError"])

    def test_serve_reads_and_writes_json_lines(self) -> None:
        stream_in = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
            + "깨진 줄\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
        stream_out = io.StringIO()
        mcp_server.serve(stream_in, stream_out, bundle_root=Path("data"))
        replies = [json.loads(line) for line in stream_out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(len(replies[0]["result"]["tools"]), 8)


class OutlineTests(BundleFixture):
    def test_outline_reports_terms_and_timecodes(self) -> None:
        result = mcp_server.tool_outline(self.root, video_id="vid")
        self.assertEqual(result["video_id"], "vid")
        self.assertIn("self", result["restored_terms"])
        self.assertIn("supervised", result["restored_terms"])
        self.assertGreaterEqual(len(result["outline"]), 1, "장 경계를 찾지 못했다")
        self.assertIn("needs_titles", result)
        self.assertIn("transcript_fingerprint", result)
        for entry in result["outline"]:
            self.assertRegex(entry["timecode"], r"^\d{2}:\d{2}:\d{2}$")

    def test_max_entries_is_respected(self) -> None:
        result = mcp_server.tool_outline(self.root, video_id="vid", max_entries=2)
        self.assertLessEqual(len(result["outline"]), 2)

    def test_unresolved_speaker_is_not_presented_as_confirmed(self) -> None:
        path = self.bundle / "derived" / "merged.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload["words"]:
            item["speaker_global"] = "speaker:7"
            item["speaker_status"] = "unresolved"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        result = mcp_server.tool_outline(self.root, video_id="vid")
        self.assertEqual(result["speakers"], [])
        self.assertEqual(result["unresolved_speaker_candidates"], ["speaker:7"])
        self.assertEqual(result["unresolved_speaker_words"], len(payload["words"]))

    def test_host_can_set_title_without_changing_boundary(self) -> None:
        outline = mcp_server.tool_outline(self.root, video_id="vid")
        before = outline["outline"][0]
        result = mcp_server.tool_set_chapter_titles(
            self.root, video_id="vid", fingerprint=outline["transcript_fingerprint"],
            titles=[{"id": before["id"], "title": "직접 지은 챕터 제목"}],
        )
        after = result["outline"][0]
        self.assertEqual(after["title"], "직접 지은 챕터 제목")
        self.assertEqual((after["start"], after["end"]), (before["start"], before["end"]))
        self.assertEqual(after["title_source"], "host-llm")


class QueryTests(BundleFixture):
    def test_query_returns_evidence_with_timestamps(self) -> None:
        result = mcp_server.tool_query(self.root, video_id="vid", query="supervised")
        self.assertTrue(result["evidence"])
        first = result["evidence"][0]
        for field in ("start", "end", "text", "source_path", "source_kind"):
            self.assertIn(field, first)
        self.assertRegex(first["timecode"], r"^\d{2}:\d{2}:\d{2}$")

    def test_query_attaches_frames_near_the_span(self) -> None:
        result = mcp_server.tool_query(self.root, video_id="vid", query="supervised")
        self.assertTrue(any(hit["frames"] for hit in result["evidence"]))

    def test_no_match_says_so_instead_of_guessing(self) -> None:
        result = mcp_server.tool_query(
            self.root, video_id="vid", query="존재하지않는용어xyz")
        self.assertEqual(result["evidence"], [])
        self.assertIn("근거", result["answer"])

    def test_missing_index_is_reported(self) -> None:
        with self.assertRaises(mcp_server.ToolError):
            mcp_server.tool_query(self.root, video_id="없는영상", query="가")


class ExcerptTests(BundleFixture):
    def test_excerpt_returns_text_and_frames_in_range(self) -> None:
        result = mcp_server.tool_excerpt(self.root, video_id="vid",
                                         start=207.0, end=211.0)
        self.assertIn("self", result["text"])
        self.assertEqual(len(result["frames"]), 1)
        self.assertEqual(result["timecode"], "00:03:27 - 00:03:31")

    def test_reversed_range_is_rejected(self) -> None:
        with self.assertRaises(mcp_server.ToolError):
            mcp_server.tool_excerpt(self.root, video_id="vid", start=10.0, end=5.0)


class PurgeTests(BundleFixture):
    def test_purge_derived_reports_removed_paths(self) -> None:
        result = mcp_server.tool_purge(self.root, video_id="vid", scope="derived")
        self.assertTrue(result["removed"])
        self.assertFalse((self.bundle / "derived").exists())
        self.assertIn("재생성", result["note"])


class StdoutIsProtocolOnlyTests(BundleFixture):
    """stdout 은 JSON-RPC 전용이다. 진행 로그가 섞이면 클라이언트가 끊긴다."""

    def test_pipeline_progress_log_goes_to_stderr(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            pipeline._log("[render]")
        self.assertEqual(out.getvalue(), "", "진행 로그가 stdout 을 오염시켰다")
        self.assertIn("[render]", err.getvalue())

    def test_tool_call_writes_only_json_lines(self) -> None:
        captured = io.StringIO()
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "ytx_outline", "arguments": {"video_id": "vid"}}}
        stream_in = io.StringIO(json.dumps(request) + "\n")
        with contextlib.redirect_stdout(captured):
            mcp_server.serve(stream_in, captured, bundle_root=self.root)
        lines = [line for line in captured.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        for line in lines:
            json.loads(line)  # 하나라도 JSON 이 아니면 여기서 터진다


class NarrowConsoleEncodingTests(unittest.TestCase):
    """stdio 가 파이프이고 로케일이 cp949 여도 죽지 않아야 한다.

    cp949 는 한글은 되지만 `—` `’` 를 못 쓴다. 전사와 OCR 결과에 흔한
    문자라 응답 한 건에 서버가 통째로 죽던 자리다.
    """

    def _roundtrip(self, encoding: str) -> dict:
        import os
        import subprocess

        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "없는—도구’", "arguments": {}}}
        environment = {**os.environ, "PYTHONIOENCODING": encoding}
        environment.pop("PYTHONUTF8", None)
        process = subprocess.run(
            [sys.executable, str(Path(__file__).parents[1] / "src" / "mcp_server.py")],
            input=(json.dumps(request) + "\n").encode("utf-8"),
            capture_output=True, env=environment, timeout=60,
        )
        self.assertEqual(process.returncode, 0,
                         "서버가 죽었다: " + process.stderr.decode("utf-8", "replace"))
        lines = [line for line in process.stdout.decode("utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, "응답이 한 줄이 아니다")
        return json.loads(lines[0])

    def test_cp949_pipe_survives_non_cp949_characters(self) -> None:
        response = self._roundtrip("cp949")
        text = response["result"]["content"][0]["text"]
        self.assertIn("—", text, "특수문자가 응답에서 사라졌다")
        self.assertIn("’", text)

    def test_utf8_pipe_is_unaffected(self) -> None:
        response = self._roundtrip("utf-8")
        self.assertIn("—", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
