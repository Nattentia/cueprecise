"""MCP 도구 표면 테스트. 네트워크와 Gemini API 를 쓰지 않는다."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
            "cueprecise_register", "cueprecise_status", "cueprecise_outline", "cueprecise_query",
            "cueprecise_excerpt", "cueprecise_frames", "cueprecise_purge", "cueprecise_set_chapter_titles",
            "cueprecise_summary", "cueprecise_set_summary"})

    def test_every_tool_declares_an_input_schema(self) -> None:
        for tool in mcp_server.TOOLS:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("required", tool["inputSchema"])
            self.assertTrue(tool["description"].strip())


MODERN_META = {"_meta": {mcp_server.PROTOCOL_VERSION_KEY:
                         mcp_server.MODERN_PROTOCOL_VERSION}}


class ProtocolTests(unittest.TestCase):
    def test_initialize_returns_protocol_version(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            bundle_root=Path("data"))
        self.assertEqual(reply["result"]["protocolVersion"],
                         mcp_server.LEGACY_PROTOCOL_VERSION)
        self.assertEqual(reply["result"]["serverInfo"]["name"], "cueprecise")

    def test_initialize_echoes_a_version_we_support(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": mcp_server.MODERN_PROTOCOL_VERSION}},
            bundle_root=Path("data"))
        self.assertEqual(reply["result"]["protocolVersion"],
                         mcp_server.MODERN_PROTOCOL_VERSION)

    def test_initialize_falls_back_when_the_asked_version_is_unknown(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "1900-01-01"}},
            bundle_root=Path("data"))
        self.assertEqual(reply["result"]["protocolVersion"],
                         mcp_server.LEGACY_PROTOCOL_VERSION)


class ModernEraTests(unittest.TestCase):
    """스펙 2026-07-28: 악수 없이 요청마다 판을 싣고 온다."""

    def test_discover_reports_both_eras_and_identity(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "server/discover",
             "params": dict(MODERN_META)},
            bundle_root=Path("data"))
        result = reply["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertIn(mcp_server.MODERN_PROTOCOL_VERSION, result["supportedVersions"])
        self.assertIn(mcp_server.LEGACY_PROTOCOL_VERSION, result["supportedVersions"])
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["_meta"][mcp_server.SERVER_INFO_KEY]["name"], "cueprecise")

    def test_discover_works_without_any_handshake(self) -> None:
        """신식 클라이언트의 첫 요청이 이것이다. initialize 가 앞설 수 없다."""
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
            bundle_root=Path("data"))
        self.assertIn("result", reply)

    def test_tools_list_is_served_with_modern_metadata(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
             "params": dict(MODERN_META)},
            bundle_root=Path("data"))
        self.assertEqual(len(reply["result"]["tools"]), len(mcp_server.TOOLS))

    def test_unsupported_version_is_rejected_with_the_supported_list(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list",
             "params": {"_meta": {mcp_server.PROTOCOL_VERSION_KEY: "1900-01-01"}}},
            bundle_root=Path("data"))
        error = reply["error"]
        self.assertEqual(error["code"], mcp_server.UNSUPPORTED_VERSION_CODE)
        self.assertEqual(error["data"]["requested"], "1900-01-01")
        self.assertEqual(error["data"]["supported"],
                         list(mcp_server.SUPPORTED_PROTOCOL_VERSIONS))

    def test_a_request_without_metadata_is_not_version_checked(self) -> None:
        """구식 요청에는 판이 실리지 않는다. 없다고 끊으면 지금 쓰는 앱이 죽는다."""
        self.assertIsNone(mcp_server.requested_protocol_version(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bundle_root=Path("data"))
        self.assertIn("result", reply)

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
             "params": {"name": "cueprecise_outline", "arguments": {"video_id": "없음"}}},
            bundle_root=Path("data"))
        self.assertTrue(reply["result"]["isError"])

    def test_tool_failure_never_returns_the_api_key(self) -> None:
        key = "AIza" + "q" * 36
        with mock.patch("mcp_server.dispatch",
                        side_effect=RuntimeError(f"provider rejected {key}")):
            reply = mcp_server.handle(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "cueprecise_status", "arguments": {}}},
                bundle_root=Path("data"), api_key=key)
        text = reply["result"]["content"][0]["text"]
        self.assertNotIn(key, text)
        self.assertIn("***", text)

    def test_serve_reads_and_writes_json_lines(self) -> None:
        stream_in = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
            + "깨진 줄\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
        stream_out = io.StringIO()
        mcp_server.serve(stream_in, stream_out, bundle_root=Path("data"))
        replies = [json.loads(line) for line in stream_out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(len(replies[0]["result"]["tools"]), 10)


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


class SummaryToolTests(BundleFixture):
    def test_summary_is_on_demand_and_has_local_fallback(self) -> None:
        path = self.bundle / "derived" / "summary.md"
        self.assertFalse(path.exists())
        result = mcp_server.tool_summary(self.root, video_id="vid")
        self.assertFalse(path.exists(), "요약은 별도 파일을 만들지 않는다")
        self.assertTrue(result["stored"])
        self.assertEqual(result["generation"], "local-extractive")
        self.assertTrue(result["needs_host_summary"])
        self.assertTrue(result["packet"])
        self.assertIn("그대로 답해도", result["summary_action"])


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

    def test_excerpt_does_not_echo_word_objects(self) -> None:
        """text 가 이미 같은 내용을 담는다. words 를 실으면 응답이 폭주한다."""
        result = mcp_server.tool_excerpt(self.root, video_id="vid",
                                         start=0.0, end=10_000.0)
        self.assertNotIn("words", result)
        self.assertEqual(result["word_count"], 9)

    def test_long_excerpt_is_truncated_and_says_so(self) -> None:
        words = [word("word%d" % i, float(i)) for i in range(4000)]
        (self.bundle / "derived" / "merged.json").write_text(json.dumps(
            {"source": "merged", "video_id": "vid", "words": words},
            ensure_ascii=False), encoding="utf-8")
        result = mcp_server.tool_excerpt(self.root, video_id="vid",
                                         start=0.0, end=5000.0)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"]), mcp_server.MAX_EXCERPT_CHARS)
        self.assertEqual(result["word_count"], 4000)
        self.assertIn("start", result["note"])

    def test_short_excerpt_has_no_truncation_flag(self) -> None:
        result = mcp_server.tool_excerpt(self.root, video_id="vid",
                                         start=207.0, end=211.0)
        self.assertNotIn("truncated", result)


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
                   "params": {"name": "cueprecise_outline", "arguments": {"video_id": "vid"}}}
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


class ArgumentValidationTests(unittest.TestCase):
    """도구를 부르는 쪽은 모델이다. 무엇이 빠졌는지 말해 주면 스스로 고쳐 부른다."""

    def _error(self, name: str, arguments: dict) -> str:
        with self.assertRaises(mcp_server.ToolError) as caught:
            mcp_server.dispatch(name, arguments, bundle_root=Path("data"))
        return str(caught.exception)

    def test_missing_required_value_names_the_field_and_the_tool(self) -> None:
        message = self._error("cueprecise_status", {})
        self.assertIn("video_id", message)
        self.assertIn("cueprecise_status", message)

    def test_every_missing_field_is_listed_at_once(self) -> None:
        message = self._error("cueprecise_set_summary", {"video_id": "a"})
        self.assertIn("fingerprint", message)
        self.assertIn("content", message)

    def test_a_value_of_the_wrong_type_names_the_field(self) -> None:
        message = self._error("cueprecise_excerpt",
                              {"video_id": "a", "start": "처음", "end": 10})
        self.assertIn("start", message)

    def test_a_boolean_is_not_accepted_as_a_number(self) -> None:
        """파이썬에서 True 는 int 다. 그냥 두면 max_entries=true 가 1 로 통과한다."""
        message = self._error("cueprecise_outline",
                              {"video_id": "a", "max_entries": True})
        self.assertIn("max_entries", message)

    def test_optional_values_may_be_omitted(self) -> None:
        mcp_server.validate_arguments("cueprecise_outline", {"video_id": "a"})

    def test_a_valid_call_passes_validation(self) -> None:
        mcp_server.validate_arguments(
            "cueprecise_excerpt", {"video_id": "a", "start": 0, "end": 1.5})

    def test_an_unknown_tool_still_reports_the_tool_name(self) -> None:
        self.assertIn("cueprecise_nope", self._error("cueprecise_nope", {}))

    def test_the_failure_reaches_the_caller_as_an_error_result(self) -> None:
        reply = mcp_server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "cueprecise_status", "arguments": {}}},
            bundle_root=Path("data"))
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("video_id", reply["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()


class FramesOnDemandTests(unittest.TestCase):
    """cueprecise_frames 는 영상이 없으면 그때 받아온다 (작업 A)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "vid"
        (self.bundle / "derived").mkdir(parents=True)
        (self.bundle / "raw").mkdir(parents=True)
        (self.bundle / "job.json").write_text(json.dumps({
            "schema_version": 1, "video_id": "vid",
            "input": {"source": "https://www.youtube.com/watch?v=vid",
                      "fingerprint": "sha256:x"},
            "config": {}, "status": "complete", "chunks": [],
        }, ensure_ascii=False), encoding="utf-8")
        (self.bundle / "derived/merged.json").write_text(json.dumps({
            "schema_version": 1, "video_id": "vid",
            "words": [{"text": t, "start": 10.0 + i, "end": 10.5 + i, "speaker": "spk:0"}
                      for i, t in enumerate(["여기", "보시면", "그림이", "있습니다"])],
        }, ensure_ascii=False), encoding="utf-8")
        self.original = mcp_server.pipeline._download
        self.calls: list[str] = []

        def download(url, fmt, raw, stem, names):
            self.calls.append(url)
            target = Path(raw) / (stem + ".mp4")
            target.write_bytes(b"fake-video")
            return target, ""

        mcp_server.pipeline._download = download

    def tearDown(self) -> None:
        mcp_server.pipeline._download = self.original
        self.tmp.cleanup()

    def test_frames_acquires_video_when_missing(self) -> None:
        result = mcp_server.dispatch("cueprecise_frames", {"video_id": "vid"},
                                     bundle_root=self.root)
        self.assertEqual(self.calls, ["https://www.youtube.com/watch?v=vid"])
        self.assertIn("candidates_considered", result)

    def test_frames_honours_max_frames(self) -> None:
        seen: dict[str, int] = {}
        original_build = mcp_server.pipeline.visual.build

        def build(bundle, *, at=None, max_frames=40):
            seen["max_frames"] = max_frames
            return original_build(bundle, at=at, max_frames=max_frames)

        mcp_server.pipeline.visual.build = build
        try:
            mcp_server.dispatch("cueprecise_frames", {"video_id": "vid", "max_frames": 3},
                                bundle_root=self.root)
        finally:
            mcp_server.pipeline.visual.build = original_build
        self.assertEqual(seen.get("max_frames"), 3, "max_frames 가 전달되지 않았다")

    def test_frames_schema_exposes_max_frames(self) -> None:
        tool = [t for t in mcp_server.TOOLS if t["name"] == "cueprecise_frames"][0]
        self.assertIn("max_frames", tool["inputSchema"]["properties"])
