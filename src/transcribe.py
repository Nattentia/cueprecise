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
import warnings
from typing import Any

from google import genai

MODEL = "gemini-3.5-transcribe"


class TranscriptionResultError(ValueError):
    """The API completed but did not return the contracted word annotations."""


def _offset(v: Any) -> float | None:
    """'12.34s' 또는 12.34 -> 12.34"""
    if v is None:
        return None
    match = re.fullmatch(r"(?:([0-9]+(?:\.[0-9]+)?)s?)", str(v).strip())
    return float(match.group(1)) if match else None


def _extract_words(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise TranscriptionResultError("Gemini 응답이 JSON 객체가 아닙니다.")
    words: list[dict[str, Any]] = []
    for step in raw.get("steps") or []:
        for content in step.get("content") or []:
            for a in content.get("annotations") or []:
                if a.get("type") != "word_info":
                    continue
                text = (a.get("text") or "").strip()
                if not text:
                    continue
                start = _offset(a.get("start_offset"))
                end = _offset(a.get("end_offset"))
                position = len(words)
                if start is None or end is None:
                    raise TranscriptionResultError(
                        f"word_info[{position}] {text!r}에 start/end timestamp가 없습니다."
                    )
                if start < 0 or end < start:
                    raise TranscriptionResultError(
                        f"word_info[{position}] {text!r}의 timestamp가 비정상입니다: "
                        f"start={start}, end={end}"
                    )
                if words and start < words[-1]["start"]:
                    raise TranscriptionResultError(
                        f"word_info[{position}] {text!r}의 start가 앞 단어보다 이릅니다: "
                        f"{start} < {words[-1]['start']}"
                    )
                words.append({
                    "text": text,
                    "start": start,
                    "end": end,
                    "speaker": a.get("speaker"),
                })
    if not words:
        raise TranscriptionResultError(
            "Gemini 응답에 word_info가 없습니다. verbatim/word timestamp 설정과 "
            "오디오 내용을 확인하세요."
        )
    return words


def transcribe(audio: str, langs: str | None) -> dict:
    if not os.path.isfile(audio):
        raise FileNotFoundError(f"오디오 파일이 없습니다: {audio}")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
    client = genai.Client(api_key=api_key)
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
                    "mime_type": uploaded.mime_type or "audio/mpeg"}],
            generation_config={"transcription_config": cfg},
        )
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception as error:  # 전사 결과는 살리되 서버 잔존 가능성을 알린다.
            warnings.warn(
                f"업로드 파일 정리에 실패했습니다 ({uploaded.name}): {error}",
                RuntimeWarning,
                stacklevel=2,
            )

    raw = json.loads(interaction.model_dump_json(exclude_none=True))
    words = _extract_words(raw)
    return {
        "source": "gemini",
        "model": MODEL,
        "language_codes": codes,
        "video_id": None,
        "words": words,
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
