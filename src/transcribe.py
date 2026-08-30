"""Gemini 전사 -> transcript.json (CONTRACT.md 2절 준수).

owner: claude

사용법:
    python src/transcribe.py <audio.mp3> <out.json> [language_codes]

language_codes 는 콤마 구분. 생략하거나 "auto" 면 자동 감지로 호출한다.
    python src/transcribe.py a.mp3 data/t.json ko-KR
    python src/transcribe.py a.mp3 data/t.json ko-KR,en-US
    python src/transcribe.py a.mp3 data/t.json auto

전제: GEMINI_API_KEY 환경변수. 오디오 30분 이하 (무료 티어 호출당 상한).
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

from google import genai
from google.genai._gaos.types import interactions as GI

MODEL = "gemini-3.5-transcribe"


def _offset(v) -> float | None:
    """'12.34s' 또는 12.34 -> 12.34"""
    if v is None:
        return None
    m = re.match(r"([0-9.]+)s?", str(v))
    return float(m.group(1)) if m else None


def _extract_words(raw: dict) -> list[dict]:
    words = []
    for step in raw.get("steps") or []:
        for content in step.get("content") or []:
            for a in content.get("annotations") or []:
                if a.get("type") != "word_info":
                    continue
                words.append({
                    "text": (a.get("text") or "").strip(),
                    "start": _offset(a.get("start_offset")),
                    "end": _offset(a.get("end_offset")),
                    "speaker": a.get("speaker"),
                })
    return [w for w in words if w["text"] and w["start"] is not None]


def transcribe(audio: str, langs: str | None) -> dict:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    uploaded = client.files.upload(file=audio)
    try:
        cfg: dict = {"mode": {
            "type": "verbatim",
            "diarization_mode": "speaker",
            "timestamp_granularities": ["word"],
        }}
        codes = None
        if langs and langs.lower() != "auto":
            codes = [s.strip() for s in langs.split(",") if s.strip()]
            cfg["language_codes"] = codes

        interaction = client.interactions.create(
            model=MODEL,
            input=[{"type": "audio", "uri": uploaded.uri,
                    "mime_type": "audio/mpeg"}],
            generation_config=GI.GenerationConfig(
                transcription_config=GI.TranscriptionConfig(**cfg)),
        )
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:  # 삭제 실패는 전사 결과에 영향 없음
            pass

    raw = json.loads(interaction.model_dump_json(exclude_none=True))
    return {
        "source": "gemini",
        "model": MODEL,
        "language_codes": codes,
        "video_id": None,
        "words": _extract_words(raw),
        "_raw": raw,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    audio, out = sys.argv[1], sys.argv[2]
    langs = sys.argv[3] if len(sys.argv) > 3 else "auto"

    result = transcribe(audio, langs)
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    words = result["words"]
    latin = re.findall(r"[A-Za-z][A-Za-z\-]*",
                       " ".join(w["text"] for w in words))
    span = words[-1]["end"] if words else 0.0
    print(f"{out}: words={len(words)} span={span:.1f}s "
          f"latin={len(latin)} codes={result['language_codes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
