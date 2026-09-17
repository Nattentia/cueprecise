"""뷰어(viewer.py) 테스트. 브라우저 없이, urllib 로 실제 소켓만 연다."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import mcp_server
import subtitle
import viewer


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _word(text: str, start: float, end: float) -> dict:
    return {"text": text, "start": start, "end": end}


class ViewerPayloadTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "vid"
        words = [
            _word("Well,", 0.0, 0.3), _word("this", 0.31, 0.5), _word("works.", 0.5, 0.8),
            _word("It", 2.0, 2.2), _word("has", 2.2, 2.4),
            _word("100", 2.4, 2.6), _word("categories.", 2.6, 3.0),
        ]
        _write_json(bundle / "derived" / "transcript.json",
                   {"video_id": "vid", "words": words})
        return bundle

    def test_no_transcript_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(subtitle.SubtitleError):
                subtitle.viewer_payload(Path(directory) / "missing", "ko")

    def test_untranslated_bundle_is_all_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            payload = subtitle.viewer_payload(bundle, "ko")
            self.assertEqual(len(payload["sentences"]), 2)
            self.assertTrue(all(s["state"] == "pending" for s in payload["sentences"]))
            self.assertTrue(all(s["ko"] is None for s in payload["sentences"]))
            self.assertFalse(payload["complete"])

    def test_segments_follow_breath_times(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                    fingerprint=packet["fingerprint"], text="")
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            self.assertEqual(packet["phase"], "translate")
            subtitle.apply_response(
                bundle, video_id="vid", phase="translate", fingerprint=packet["fingerprint"],
                text="1|이건 /1 됩니다.\n2|그것은 100개 범주가 있습니다.")
            payload = subtitle.viewer_payload(bundle, "ko")
            first = payload["sentences"][0]
            self.assertEqual(first["ko"], "이건 됩니다.")
            self.assertIsNotNone(first["segments"])
            self.assertEqual(len(first["segments"]), 2)
            # 숨 지점(0.31s)이 두 조각의 경계 시각이어야 한다.
            self.assertAlmostEqual(first["segments"][0]["end"], first["breaths"][0])
            self.assertAlmostEqual(first["segments"][1]["start"], first["breaths"][0])
            self.assertEqual(first["segments"][0]["start"], first["start"])
            self.assertEqual(first["segments"][-1]["end"], first["end"])

    def test_lost_key_is_excluded_and_uncertain_included(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self._bundle(Path(directory))
            packet = subtitle.build_packet(bundle, video_id="vid", lang="ko")
            subtitle.apply_response(bundle, video_id="vid", phase="terms",
                                    fingerprint=packet["fingerprint"], text="T1|Well|?|-|-|-")
            state = subtitle.load_state(bundle, "ko")
            self.assertEqual(state["terms"][0]["status"], "uncertain")
            # 전사를 바꿔 문장 키를 하나 잃게 만든다.
            words = subtitle._load_words(bundle)
            words[0] = _word("Hmm,", 0.0, 0.3)
            _write_json(bundle / "derived" / "transcript.json",
                       {"video_id": "vid", "words": words})
            payload = subtitle.viewer_payload(bundle, "ko")
            self.assertEqual(len(payload["sentences"]), 2)  # 잃은 키는 안 보인다.
            self.assertEqual(payload["terms"][0]["status"], "uncertain")


class _ServerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "AAAAAAAAAAA"
        words = [_word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1)]
        _write_json(self.bundle / "derived" / "transcript.json",
                   {"video_id": "AAAAAAAAAAA", "words": words})
        self.base_url, reason = viewer.ensure_server(self.root)
        self.assertIsNone(reason)
        self.assertIsNotNone(self.base_url)

    def tearDown(self) -> None:
        viewer.stop_server()
        self.tmp.cleanup()

    def _get(self, path: str, *, host: str | None = None):
        url = self.base_url + path
        request = urllib.request.Request(url, method="GET")
        if host is not None:
            request.add_header("Host", host)
        return urllib.request.urlopen(request, timeout=5)

    def _status(self, path: str, *, host: str | None = None, method: str = "GET") -> int:
        url = self.base_url + path
        request = urllib.request.Request(url, method=method)
        if host is not None:
            request.add_header("Host", host)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code


class ServerTests(_ServerFixture):
    def test_index_page_serves_html_with_only_iframe_api_external_script(self) -> None:
        with self._get("/") as response:
            self.assertEqual(response.status, 200)
            body = response.read().decode("utf-8")
        self.assertIn("<html", body)
        srcs = [line for line in body.splitlines() if "<script src=" in line]
        self.assertEqual(len(srcs), 1)
        self.assertIn("youtube.com/iframe_api", srcs[0])

    def test_api_subtitle_ok(self) -> None:
        with self._get("/api/subtitle?v=AAAAAAAAAAA&lang=ko") as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["video_id"], "AAAAAAAAAAA")
        self.assertEqual(len(payload["sentences"]), 1)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_invalid_video_id_is_400(self) -> None:
        self.assertEqual(self._status("/api/subtitle?v=short&lang=ko"), 400)

    def test_path_traversal_video_id_is_400(self) -> None:
        self.assertEqual(self._status("/api/subtitle?v=" + "..%2f..%2f..%2fetc" + "&lang=ko"), 400)

    def test_bad_host_header_is_403(self) -> None:
        self.assertEqual(self._status("/", host="evil.example.com"), 403)

    def test_post_is_405(self) -> None:
        self.assertEqual(self._status("/", method="POST"), 405)

    def test_health(self) -> None:
        with self._get("/__cueprecise/health") as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["app"], "cueprecise-viewer")
        self.assertIn("bundle_root_hash", payload)

    def test_ensure_server_reuses_address_for_same_bundle_root(self) -> None:
        url_again, reason = viewer.ensure_server(self.root)
        self.assertIsNone(reason)
        self.assertEqual(url_again, self.base_url)


class McpServerViewerUrlTests(unittest.TestCase):
    """viewer_url 이 채워지는지 확인한다 — 실제 서버는 mock 으로 대신한다."""

    def test_viewer_url_injected_without_starting_a_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle_root = Path(directory)
            bundle = bundle_root / "AAAAAAAAAAA"
            words = [_word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1)]
            _write_json(bundle / "derived" / "transcript.json",
                       {"video_id": "AAAAAAAAAAA", "words": words})
            with mock.patch.object(mcp_server.viewer, "ensure_server",
                                   return_value=("http://127.0.0.1:9999", None)) as ensure:
                result = mcp_server.tool_subtitle(bundle_root, video_id="AAAAAAAAAAA")
                ensure.assert_called_once()
            self.assertEqual(result["viewer_url"], "http://127.0.0.1:9999/?v=AAAAAAAAAAA")

    def test_viewer_url_none_when_server_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle_root = Path(directory)
            bundle = bundle_root / "AAAAAAAAAAA"
            words = [_word("Hello", 0.0, 0.5), _word("world.", 0.6, 1.1)]
            _write_json(bundle / "derived" / "transcript.json",
                       {"video_id": "AAAAAAAAAAA", "words": words})
            with mock.patch.object(mcp_server.viewer, "ensure_server",
                                   return_value=(None, "포트를 못 열었다")):
                result = mcp_server.tool_subtitle(bundle_root, video_id="AAAAAAAAAAA")
            self.assertIsNone(result["viewer_url"])


class TermsToggleNotPersistedTests(unittest.TestCase):
    """용어 해설 on/off 는 항상 꺼진 채로 시작하고 localStorage 에 저장하지 않는다."""

    def test_page_html_never_persists_terms_toggle(self) -> None:
        html = viewer.PAGE_HTML
        self.assertNotIn("JSON.stringify(prefs)", html)  # 통짜 저장이면 terms 도 같이 샌다.
        self.assertIn("def.terms = false", html)
        self.assertIn("PERSISTED_KEYS", html)
        # 저장 목록 선언 자체에 "terms" 가 없어야 한다.
        start = html.index("const PERSISTED_KEYS")
        line_end = html.index("\n", start)
        persisted_line = html[start:line_end]
        self.assertNotIn('"terms"', persisted_line)


if __name__ == "__main__":
    unittest.main()
