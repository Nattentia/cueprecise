"""Gemini 전사 -> transcript.json (CONTRACT.md 2절 준수).

owner: claude

사용법:
    python src/transcribe.py <audio.mp3> <out.json> [language_codes]
    python src/transcribe.py --from-raw <raw.json> <out.json>

language_codes 는 콤마 구분. 생략하거나 "auto" 면 자동 감지로 호출한다.
    python src/transcribe.py a.mp3 data/t.json ko-KR
    python src/transcribe.py a.mp3 data/t.json ko-KR,en-US
    python src/transcribe.py a.mp3 data/t.json auto

호출 응답 원문은 파싱 전에 `<out>.raw.json` 으로 저장한다. 파싱이 실패해도
응답이 남으므로 `--from-raw` 로 API 호출 없이 다시 만들 수 있다.

전제: GEMINI_API_KEY 환경변수. 오디오 30분 이하 (무료 티어 호출당 상한).
`google-genai` 는 실제 호출 경로에서만 필요하다. 파싱·복구 경로는 SDK 없이
동작한다.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Any

MODEL = "gemini-3.5-transcribe"

# Gemini 는 긴 오디오에서 드물게 단어 하나의 offset 을 손상시킨다 (관측 사례:
# end_offset 1120.3 -> 120.3, 앞자리 누락). 단어 5,000개 중 1건 때문에 청크
# 전체를 버리면 이미 소모한 호출까지 날아간다. 이웃 단어로 복구하되, 손상이
# 아래 비율을 넘으면 응답 자체가 망가진 것으로 보고 기존처럼 중단한다.
MAX_REPAIR_RATIO = 0.005
MIN_REPAIR_ALLOWANCE = 3
FALLBACK_WORD_SECS = 0.5


class TranscriptionResultError(ValueError):
    """The API completed but did not return the contracted word annotations."""


def _offset(v: Any) -> float | None:
    """'12.34s' 또는 12.34 -> 12.34"""
    if v is None:
        return None
    match = re.fullmatch(r"(?:([0-9]+(?:\.[0-9]+)?)s?)", str(v).strip())
    return float(match.group(1)) if match else None


def _annotations(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """응답에서 word_info 를 순서대로 뽑는다. 검증·복구는 하지 않는다."""
    if not isinstance(raw, dict):
        raise TranscriptionResultError("Gemini 응답이 JSON 객체가 아닙니다.")
    entries: list[dict[str, Any]] = []
    for step in raw.get("steps") or []:
        for content in step.get("content") or []:
            for a in content.get("annotations") or []:
                if a.get("type") != "word_info":
                    continue
                text = (a.get("text") or "").strip()
                if not text:
                    continue
                entries.append({
                    "text": text,
                    "start": _offset(a.get("start_offset")),
                    "end": _offset(a.get("end_offset")),
                    "speaker": a.get("speaker"),
                })
    return entries


def _duration_cap(entries: list[dict[str, Any]]) -> float:
    """정상 단어들의 길이 분포(p99)로 복구 상한을 잡는다. 응답마다 자체 보정."""
    durations = sorted(
        entry["end"] - entry["start"]
        for entry in entries
        if entry["start"] is not None and entry["end"] is not None
        and entry["start"] >= 0 and entry["end"] >= entry["start"]
    )
    if not durations:
        return FALLBACK_WORD_SECS
    index = min(int(len(durations) * 0.99), len(durations) - 1)
    return max(durations[index], FALLBACK_WORD_SECS)


def _repair(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """계약을 어기는 timestamp 를 이웃 단어로 복구한다. 단어는 지우지 않는다.

    복구 대상은 기존 검증이 치명으로 처리하던 것과 정확히 같다: offset 누락,
    음수 start, `end < start`, 앞 단어보다 이른 start. 판정 기준은 그대로 두고
    대응만 '중단'에서 '복구+기록'으로 바꾼다.
    """
    cap = _duration_cap(entries)
    words: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    previous_start = -1.0
    previous_end = 0.0

    for index, entry in enumerate(entries):
        start, end = entry["start"], entry["end"]
        repaired_fields: list[str] = []

        if start is None or start < 0 or start < previous_start:
            original, start = start, previous_end
            repairs.append({"index": index, "text": entry["text"], "field": "start",
                            "from": original, "to": start})
            repaired_fields.append("start")

        following = entries[index + 1]["start"] if index + 1 < len(entries) else None
        if end is None or end < start:
            original = end
            limit = round(start + cap, 3)
            end = min(following, limit) if following is not None and following >= start else limit
            repairs.append({"index": index, "text": entry["text"], "field": "end",
                            "from": original, "to": end})
            repaired_fields.append("end")

        word: dict[str, Any] = {"text": entry["text"], "start": start,
                                "end": end, "speaker": entry["speaker"]}
        if repaired_fields:
            word["timestamp_repaired"] = repaired_fields
        words.append(word)
        previous_start, previous_end = start, end

    return words, repairs


def _check(words: list[dict[str, Any]], repairs: list[dict[str, Any]]) -> None:
    if not words:
        raise TranscriptionResultError(
            "Gemini 응답에 word_info가 없습니다. verbatim/word timestamp 설정과 "
            "오디오 내용을 확인하세요."
        )
    allowance = max(MIN_REPAIR_ALLOWANCE, int(len(words) * MAX_REPAIR_RATIO))
    if len(repairs) > allowance:
        sample = "; ".join(
            f"[{item['index']}] {item['text']!r} {item['field']}={item['from']}"
            for item in repairs[:5]
        )
        raise TranscriptionResultError(
            f"timestamp 이상이 {len(words)}단어 중 {len(repairs)}건으로 허용치"
            f"({allowance})를 넘었습니다. 응답 전체가 손상됐을 수 있습니다. "
            f"예: {sample}"
        )


def _extract_words(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """호환용. 단어 배열만 필요할 때 쓴다."""
    entries = _annotations(raw)
    words, repairs = _repair(entries)
    _check(words, repairs)
    return words


def parse_raw(raw: dict[str, Any], language_codes: list[str] | None = None,
              video_id: str | None = None) -> dict[str, Any]:
    """응답 원문 -> transcript.json 페이로드. API 를 호출하지 않는다."""
    entries = _annotations(raw)
    words, repairs = _repair(entries)
    _check(words, repairs)
    payload: dict[str, Any] = {
        "source": "gemini",
        "model": MODEL,
        "language_codes": language_codes,
        "video_id": video_id,
        "words": words,
    }
    if repairs:
        payload["timestamp_repairs"] = repairs
    return payload


def _write_raw(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def from_raw(raw_path: Path) -> dict[str, Any]:
    """저장된 응답 원문으로 결과를 다시 만든다. Gemini 호출 0회."""
    stored = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    return parse_raw(stored.get("response"), stored.get("language_codes"))


def request_raw(audio: str, langs: str | None) -> tuple[dict[str, Any], list[str] | None]:
    """Gemini 를 호출해 응답 원문을 그대로 돌려준다. 검증하지 않는다."""
    if not os.path.isfile(audio):
        raise FileNotFoundError(f"오디오 파일이 없습니다: {audio}")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")

    from google import genai  # SDK 는 실제 호출 경로에서만 필요하다.

    client = genai.Client(api_key=api_key)
    uploaded = client.files.upload(file=audio)
    codes = None
    try:
        cfg: dict = {"mode": {
            "type": "verbatim",
            "diarization_mode": "speaker",
            "timestamp_granularities": ["word"],
        }}
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

    return json.loads(interaction.model_dump_json(exclude_none=True)), codes


def transcribe(audio: str, langs: str | None, raw_path: Path | None = None) -> dict:
    raw, codes = request_raw(audio, langs)
    if raw_path is not None:
        # 파싱 전에 저장한다. 파싱이 실패해도 소모한 호출이 날아가지 않는다.
        _write_raw(Path(raw_path), {"model": MODEL, "requested_langs": langs or "auto",
                                    "language_codes": codes, "response": raw})
    return parse_raw(raw, codes)


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--from-raw":
        if len(argv) < 3:
            print(__doc__)
            return 2
        out = argv[2]
        result = from_raw(Path(argv[1]))
    else:
        if len(argv) < 2:
            print(__doc__)
            return 2
        audio, out = argv[0], argv[1]
        langs = argv[2] if len(argv) > 2 else "auto"
        result = transcribe(audio, langs, raw_path=Path(out).with_suffix(".raw.json"))

    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    words = result["words"]
    latin = re.findall(r"[A-Za-z][A-Za-z\-]*",
                       " ".join(w["text"] for w in words))
    span = words[-1]["end"] if words else 0.0
    print(f"{out}: words={len(words)} span={span:.1f}s "
          f"latin={len(latin)} codes={result['language_codes']} "
          f"repairs={len(result.get('timestamp_repairs', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
