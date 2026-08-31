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


RANGE_SLACK_SECS = 5.0
"""오디오 길이를 이만큼 넘어서면 그 timestamp 는 응답 오류로 본다."""


def _repair(entries: list[dict[str, Any]], *, audio_secs: float | None = None
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """계약을 어기는 timestamp 를 이웃 단어로 복구한다. 단어는 지우지 않는다.

    **단어가 시간순으로 온다고 가정하지 않는다.** 여러 사람이 말을 겹쳐 하면
    맞장구("네.", "음.")가 앞 단어보다 이른 시각에 오는 것이 정상이다. 실측
    54분 대담(`AMvF8VrTXWg`)에서 4,156단어 중 393건이 이런 겹침이었다. 이것을
    손상으로 처리하면 정상 응답이 통째로 거부된다.

    그래서 각 단어를 **그 단어만 놓고** 본다: offset 이 있는가, 음수가 아닌가,
    `end >= start` 인가, 오디오 길이 안에 있는가. 순서는 마지막에 start 로
    정렬해 맞춘다.

    범위 검사가 중요한 이유가 하나 더 있다. 실측에서 단어 하나가 `start`
    22,979초(오디오는 1,633초)로 왔다. 예전 구현은 그 값을 다음 단어의 기준으로
    삼았고, **그 뒤 3,602단어가 전부 "앞 단어보다 이르다"로 걸렸다.** 잘못된
    값 하나가 응답 전체를 손상으로 만들면 안 된다. 여기서는 이상치를 그 자리
    에서 고치고 기준으로 삼지 않는다.
    """
    cap = _duration_cap(entries)
    limit = (audio_secs + RANGE_SLACK_SECS) if audio_secs else None
    words: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    previous_end = 0.0

    def out_of_range(value: float | None) -> bool:
        return limit is not None and value is not None and value > limit

    for index, entry in enumerate(entries):
        start, end = entry["start"], entry["end"]
        repaired_fields: list[str] = []

        if start is None or start < 0 or out_of_range(start):
            original, start = start, previous_end
            repairs.append({"index": index, "text": entry["text"], "field": "start",
                            "from": original, "to": start})
            repaired_fields.append("start")

        if end is None or end < start or out_of_range(end):
            original = end
            end = round(start + cap, 3)
            repairs.append({"index": index, "text": entry["text"], "field": "end",
                            "from": original, "to": end})
            repaired_fields.append("end")

        word: dict[str, Any] = {"text": entry["text"], "start": start,
                                "end": end, "speaker": entry["speaker"]}
        if repaired_fields:
            word["timestamp_repaired"] = repaired_fields
        words.append(word)
        # 고친 값은 기준으로 삼지 않는다. 하나의 오류가 뒤를 오염시키지 않는다.
        if not repaired_fields:
            previous_end = max(previous_end, end)

    # 겹쳐 말한 구간을 시간순으로 돌려놓는다. 같은 시각이면 받은 순서를 지킨다.
    words.sort(key=lambda item: item["start"])
    return _drop_duplicates(words), repairs


def _drop_duplicates(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 시각의 같은 말을 한 번만 남긴다.

    모델이 같은 구간을 두 번 뱉는 일이 있다. 실측 54분 대담(`AMvF8VrTXWg`)에서
    8,176단어 중 148단어(1.8%)가 그랬다 — timestamp 와 글자는 똑같고 화자
    라벨만 달랐다(`spk:3` 과 `spk:0`). 정렬하면 나란히 붙어 "일단 일단 어 어"
    처럼 읽힌다.

    같은 시각에 같은 글자가 두 번 나올 수는 없으므로 안전한 규칙이다. 화자가
    갈리는 것은 diarization 의 실수이고, 먼저 온 쪽을 남긴다.
    """
    kept: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()
    for word in words:
        key = (word["start"], word["end"], word["text"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(word)
    return kept


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
              video_id: str | None = None, audio_secs: float | None = None
              ) -> dict[str, Any]:
    """응답 원문 -> transcript.json 페이로드. API 를 호출하지 않는다.

    `audio_secs` 를 주면 그 길이를 벗어난 timestamp 를 오류로 보고 고친다.
    """
    entries = _annotations(raw)
    words, repairs = _repair(entries, audio_secs=audio_secs)
    duplicates = len(entries) - len(words)
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
    if duplicates:
        payload["duplicate_words_removed"] = duplicates
    return payload


def _write_raw(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _audio_seconds(audio: str) -> float | None:
    """전사한 오디오의 길이. 못 재면 None (범위 검사를 건너뛴다)."""
    try:
        import audio as audio_mod

        return audio_mod.probe_duration(Path(audio))
    except Exception:
        return None


def from_raw(raw_path: Path) -> dict[str, Any]:
    """저장된 응답 원문으로 결과를 다시 만든다. Gemini 호출 0회."""
    stored = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    start, end = stored.get("chunk_start"), stored.get("chunk_end")
    audio_secs = (float(end) - float(start)) if start is not None and end is not None else None
    return parse_raw(stored.get("response"), stored.get("language_codes"),
                     audio_secs=audio_secs)


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


def transcribe(audio: str, langs: str | None, raw_path: Path | None = None,
               meta: dict[str, Any] | None = None) -> dict:
    """`meta` 는 이 응답이 어느 입력에 대한 것인지 적어 두는 꼬리표다.

    저장된 응답을 나중에 재사용할 때, 그 사이 청크 경계나 입력 오디오가
    바뀌었으면 재사용하면 안 된다. 그 판단에 쓸 근거를 함께 남긴다.
    """
    raw, codes = request_raw(audio, langs)
    if raw_path is not None:
        # 파싱 전에 저장한다. 파싱이 실패해도 소모한 호출이 날아가지 않는다.
        payload: dict[str, Any] = {"model": MODEL, "requested_langs": langs or "auto",
                                   "language_codes": codes, "response": raw}
        payload.update(meta or {})
        _write_raw(Path(raw_path), payload)
    return parse_raw(raw, codes, audio_secs=_audio_seconds(audio))


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
