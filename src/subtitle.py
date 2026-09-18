"""번역 자막 코어 — 문장 분할, 교차 대조, phase 진행 (CONTRACT.md 16절).

owner: claude

영어 강연을 한국어 자막으로 옮긴다. 전사는 기존 Gemini 경로를 쓰고, 번역은
호스트 에이전트(Claude/Codex)가 MCP 도구로 오간다. 이 모듈은 순수 함수
위주다 — 서버(`mcp_server.py`)는 요청 사이에 상태를 두지 않으므로, 다음
묶음은 매 호출마다 `translations/<lang>.json`과 전사에서 다시 계산한다.

phase 는 `terms` → `translate` → `review` → `done` 순서로 진행한다. 각
phase 의 패킷·검증 규칙은 CONTRACT.md 16절과 이 파일의 각 섹션 주석을
참고한다.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import chapters
import locking
import pipeline
import visual

# --------------------------------------------------------------------- 상수

SCHEMA_VERSION = 1
READ_CPS = 13
"""분당 아니라 초당. 한국어 기준, 공백 제외 글자 수."""
MAX_LINES = 2
LINE_CHARS = 18
MIN_CUE_SECS = 1.5
BATCH_CHARS = 5000
CONTEXT_SENTENCES = 4
MAX_REJECTS = 2
MAX_PACKET_CHARS = 12000
MAX_NEW_OCR_CAPTURES = 30
MAX_SHAPED_TOKEN_CANDIDATES = 60
SENTENCE_GAP_SECS = 1.2
"""`chapters._sentences` 와 같은 문장 경계 공백 기준."""
FORCED_CUT_WORDS = 35
FORCED_CUT_WINDOW = 10
BREATH_GAP_SECS = 0.3
BREATH_PUNCTUATION = (",", ";", ":")
BATCH_BREAK_GAP_SECS = 2.0

SUPPORTED_LANGS = ("ko",)

_END_RE = chapters._END_RE  # 문장 끝 판정 규칙을 그대로 쓴다 (명세 4절 참고).
_NORMALIZE_RE = re.compile(r"[^0-9a-z.]+")
_BRACKET_CUE_RE = re.compile(r"^\[[^\[\]]*\]$")
_HANGUL_RE = re.compile(r"[가-힣]")
_DIGIT_RE = re.compile(r"[0-9]")
_WORD_SPLIT_RE = re.compile(r"\S+")
_EDGE_PUNCT = ".,;:!?\"'()[]"


class SubtitleError(ValueError):
    """자막 작업 검증 실패. 호출자(서버)가 그대로 전달한다."""


# ----------------------------------------------------------------- JSON io

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- 전사

def _load_words(bundle: Path) -> list[dict[str, Any]]:
    for name in ("merged.json", "transcript.json"):
        path = bundle / "derived" / name
        if path.exists():
            payload = _read_json(path)
            return payload.get("words") or []
    raise SubtitleError("전사가 없다. 먼저 cueprecise_register 로 전사하라.")


# -------------------------------------------------------------- 문장 분할

def _forced_cut_index(words: list[dict[str, Any]]) -> int:
    """`FORCED_CUT_WORDS` 강제 절단 시 마지막 `FORCED_CUT_WINDOW` 단어 안에서
    가장 긴 공백 뒤를 자른다. 단어 중간에서 뜻이 끊기는 것을 피한다."""
    n = len(words)
    lo = max(1, n - FORCED_CUT_WINDOW)
    best_index, best_gap = lo, -1.0
    for index in range(lo, n):
        gap = float(words[index]["start"]) - float(words[index - 1]["end"])
        if gap > best_gap:
            best_gap = gap
            best_index = index
    return best_index


def _sentence_word_groups(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        if current and float(word["start"]) - float(current[-1]["end"]) > SENTENCE_GAP_SECS:
            result.append(current)
            current = []
        current.append(word)
        if _END_RE.search(str(word.get("text", ""))):
            result.append(current)
            current = []
        elif len(current) >= FORCED_CUT_WORDS:
            cut = _forced_cut_index(current)
            result.append(current[:cut])
            current = current[cut:]
    if current:
        result.append(current)
    return [group for group in result if group]


def build_sentences(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """단어 목록에서 문장을 만든다. `chapters._sentences` 와 경계 규칙은 같지만
    단어 인덱스(시각)를 보존한다 — 호흡 지점·자막 넘김 계산에 필요하다."""
    groups = _sentence_word_groups(words)
    sentences = []
    for group in groups:
        sentence_words = [{"text": str(word["text"]), "start": float(word["start"]),
                           "end": float(word["end"])} for word in group]
        text = " ".join(word["text"] for word in sentence_words)
        sentences.append({
            "start": sentence_words[0]["start"],
            "end": sentence_words[-1]["end"],
            "text": text,
            "words": sentence_words,
        })
    return sentences


def sentence_key(start: float, end: float, text: str) -> str:
    digest = hashlib.sha1(f"{start:.2f}|{end:.2f}|{text}".encode("utf-8")).hexdigest()
    return "s-" + digest[:12]


def sentences_fingerprint(keys: list[str]) -> str:
    encoded = "\n".join(keys).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _has_breath(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    gap = float(current["start"]) - float(previous["end"])
    if gap >= BREATH_GAP_SECS:
        return True
    return str(previous["text"]).rstrip().endswith(BREATH_PUNCTUATION)


def breath_points(sentence_words: list[dict[str, Any]]) -> list[float]:
    """문장 안 숨 지점의 시각(다음 단어 start) 목록. 1번부터 순서대로다."""
    points = []
    for index in range(1, len(sentence_words)):
        if _has_breath(sentence_words[index - 1], sentence_words[index]):
            points.append(float(sentence_words[index]["start"]))
    return points


def char_budget(duration_sec: float) -> int:
    return max(8, round(duration_sec * READ_CPS))


class Indexed:
    """전사에서 매번 다시 계산하는 문장 색인. 서버 상태를 두지 않기 위해서다."""

    def __init__(self, words: list[dict[str, Any]]) -> None:
        sentences = build_sentences(words)
        self.words = words
        self.sentences: list[dict[str, Any]] = []
        keys = []
        for number, sentence in enumerate(sentences, start=1):
            key = sentence_key(sentence["start"], sentence["end"], sentence["text"])
            entry = {**sentence, "no": number, "key": key,
                     "breaths": breath_points(sentence["words"])}
            self.sentences.append(entry)
            keys.append(key)
        self.fingerprint = sentences_fingerprint(keys)
        self.by_no = {item["no"]: item for item in self.sentences}
        self.by_key = {item["key"]: item for item in self.sentences}

    def sentence_no_at(self, t: float) -> int:
        if not self.sentences:
            return 0
        for item in self.sentences:
            if item["start"] <= t <= item["end"]:
                return item["no"]
        # 창 경계 밖(예: 마지막 문장 이후)이면 가장 가까운 문장으로 붙인다.
        before = [item for item in self.sentences if item["start"] <= t]
        return (before[-1] if before else self.sentences[0])["no"]


def index_sentences(bundle: Path) -> Indexed:
    return Indexed(_load_words(bundle))


# ---------------------------------------------------------------- 정규화

def _normalize(text: str) -> str:
    """비교용 정규화: 소문자, 영숫자와 `.` 만 남긴다.

    끝에 붙는 문장부호(마침표·쉼표 등)는 먼저 벗겨낸다. 그렇지 않으면
    Gemini 는 "ago", YouTube 자막은 "ago." 로 적었을 뿐인 완전히 같은 단어가
    끝 마침표 하나 때문에 의심으로 잡힌다. `comnet.js` 처럼 단어 안쪽의 점은
    `_strip_edges` 가 건드리지 않으므로 그대로 남는다.
    """
    return _NORMALIZE_RE.sub("", _strip_edges(text).lower())


def _strip_edges(text: str) -> str:
    return text.strip(_EDGE_PUNCT)


def _is_bracket_cue(text: str) -> bool:
    return bool(_BRACKET_CUE_RE.fullmatch(text.strip()))


def _interesting_shape(token: str) -> bool:
    """대문자 섞인 토큰 / 숫자+점 포함 토큰 / CamelCase."""
    stripped = _strip_edges(token)
    if len(stripped) < 2:
        return False
    if any(ch.isupper() for ch in stripped[1:]):
        return True
    if stripped[:1].isupper() and stripped[1:].islower() is False and any(
            ch.isalpha() for ch in stripped):
        return True
    if _DIGIT_RE.search(stripped) and "." in stripped:
        return True
    return False


# 영어 기능어·흔한 단어. 의심 쌍 양쪽이 전부 이 목록에만 속하면(예: the/that,
# all/really) 의미 있는 용어가 아니라 잡음으로 보고 버린다. `chapters._STOP`
# (영어 부분)을 밑돌로 삼고 대명사·전치사·접속사·조동사·흔한 동사/부사를 더했다.
_COMMON_WORDS: frozenset[str] = frozenset({
    word for word in chapters._STOP if word.isascii() and word.isalpha()
} | {
    "a", "an", "the", "this", "that", "these", "those", "i", "me", "my", "mine",
    "myself", "you", "your", "yours", "yourself", "he", "him", "his", "himself",
    "she", "her", "hers", "herself", "it", "its", "itself", "we", "us", "our",
    "ours", "ourselves", "they", "them", "their", "theirs", "themselves", "who",
    "whom", "whose", "which", "am", "is", "are", "was", "were", "be", "been",
    "being", "do", "does", "did", "done", "have", "has", "had", "having", "will",
    "would", "shall", "should", "can", "could", "may", "might", "must", "in",
    "on", "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "to", "from",
    "up", "down", "over", "under", "again", "further", "once", "off", "out",
    "of", "near", "and", "but", "or", "nor", "so", "yet", "although", "though",
    "because", "since", "unless", "while", "whereas", "if", "very", "really",
    "just", "only", "also", "too", "quite", "rather", "almost", "always",
    "never", "often", "sometimes", "still", "even", "ever", "go", "going",
    "went", "gone", "get", "got", "gotten", "make", "made", "take", "took",
    "taken", "come", "came", "see", "saw", "seen", "know", "knew", "known",
    "think", "thought", "want", "wanted", "look", "looked", "use", "used",
    "find", "found", "give", "gave", "tell", "told", "ask", "asked", "work",
    "worked", "seem", "seemed", "feel", "felt", "try", "tried", "leave",
    "left", "call", "called", "one", "two", "three", "first", "second", "no",
    "not", "yes", "ok", "okay", "well", "now", "here", "there", "then", "than",
    "such", "same", "own", "other", "another", "each", "every", "any", "some",
    "few", "more", "most", "much", "many",
})

# 축약형 -> 전개형 어미. "there's" == "there is" 같은 짝을 같은 것으로 본다.
_CONTRACTION_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("n't", " not"), ("'re", " are"), ("'ve", " have"), ("'ll", " will"),
    ("'s", " is"), ("'m", " am"), ("'d", " would"),
)
_LOOSE_STRIP_RE = re.compile(r"[\s\-']+")


def _expand_contractions(phrase: str) -> str:
    words = []
    for word in phrase.split():
        lower = word.lower()
        expanded = word
        for suffix, expansion in _CONTRACTION_SUFFIXES:
            if lower.endswith(suffix) and len(lower) > len(suffix):
                expanded = word[: -len(suffix)] + expansion
                break
        words.append(expanded)
    return " ".join(words)


def _loose_forms(phrase: str) -> set[str]:
    """공백·하이픈·아포스트로피를 지우고, 흔한 축약형은 전개해서도 만들어 본다."""
    variants = {phrase, _expand_contractions(phrase)}
    return {_LOOSE_STRIP_RE.sub("", variant.lower()) for variant in variants}


def _is_noise_pair(gemini: str, youtube: str) -> bool:
    """의미 없는 의심 쌍(표기·축약·복수/시제·기능어 차이뿐)이면 참."""
    gemini_forms = _loose_forms(gemini)
    youtube_forms = _loose_forms(youtube)
    if gemini_forms & youtube_forms:
        return True  # 공백·하이픈·아포스트로피(또는 흔한 축약) 차이뿐이다.
    for left in gemini_forms:
        for right in youtube_forms:
            shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
            if shorter and longer.startswith(shorter) and len(longer) - len(shorter) <= 3:
                return True  # 접두사 관계 + 복수/시제 정도의 차이.
    gemini_tokens = [_strip_edges(t).lower() for t in gemini.split()]
    youtube_tokens = [_strip_edges(t).lower() for t in youtube.split()]
    if gemini_tokens and youtube_tokens \
            and all(t in _COMMON_WORDS for t in gemini_tokens) \
            and all(t in _COMMON_WORDS for t in youtube_tokens):
        return True  # 양쪽 모두 기능어·흔한 단어뿐이다.
    return False


def _has_keep_signal(suspect: dict[str, Any]) -> bool:
    """모양이 특이하거나(대문자·숫자·점·CamelCase) 슬라이드 근거가 있으면 우선한다.

    'the'/'that' 처럼 짧은 흔한 단어는 슬라이드에 우연히 나와도 근거로 치지
    않는다 — 토큰 길이가 4 이상일 때만 슬라이드 근거를 신호로 인정한다.
    """
    if _interesting_shape(suspect["gemini"]) or _interesting_shape(suspect["youtube"]):
        return True
    if suspect.get("slide") and min(len(_strip_edges(suspect["gemini"])),
                                    len(_strip_edges(suspect["youtube"]))) >= 4:
        return True
    return False


# --------------------------------------------------------- 1단계: 교차 대조

def _load_captions(bundle: Path) -> list[dict[str, Any]]:
    path = bundle / "raw" / "captions.json"
    if not path.exists():
        return []
    try:
        payload = _read_json(path)
    except (OSError, ValueError):
        return []
    return [cue for cue in payload.get("cues") or [] if not _is_bracket_cue(str(cue.get("text", "")))]


def _gemini_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [w for w in words if w.get("origin", "gemini") == "gemini"]


def _window_candidates(window_words: list[dict[str, Any]]) -> dict[str, tuple[str, float]]:
    """시각 창 안 Gemini 단어에서 만들 수 있는 후보 표기. 정규화 -> (원형, 시각)."""
    candidates: dict[str, tuple[str, float]] = {}
    for word in window_words:
        norm = _normalize(str(word["text"]))
        if norm and norm not in candidates:
            candidates[norm] = (str(word["text"]), float(word["start"]))
    for index in range(len(window_words) - 1):
        left, right = window_words[index], window_words[index + 1]
        norm = _normalize(str(left["text"])) + _normalize(str(right["text"]))
        if norm and norm not in candidates:
            display = str(left["text"]) + " " + str(right["text"])
            candidates[norm] = (display, float(left["start"]))
    return candidates


def _find_suspects(words: list[dict[str, Any]], cues: list[dict[str, Any]],
                    indexed: Indexed) -> list[dict[str, Any]]:
    gemini_words = _gemini_words(words)
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for cue in cues:
        start, end = float(cue.get("start", 0.0)), float(cue.get("end", 0.0))
        window = [w for w in gemini_words
                  if float(w["start"]) <= end + 0.5 and float(w["end"]) >= start - 0.5]
        if not window:
            continue
        single_norms = {_normalize(str(w["text"])) for w in window}
        candidates = _window_candidates(window)
        for raw_token in _WORD_SPLIT_RE.findall(str(cue.get("text", ""))):
            token_norm = _normalize(raw_token)
            if len(token_norm) < 4 or token_norm in single_norms:
                continue
            best_norm, best_ratio = None, 0.0
            for candidate_norm in candidates:
                ratio = difflib.SequenceMatcher(None, token_norm, candidate_norm).ratio()
                if ratio > best_ratio:
                    best_ratio, best_norm = ratio, candidate_norm
            if best_norm is None or best_ratio < 0.55:
                continue
            gemini_display, t = candidates[best_norm]
            youtube_display = _strip_edges(raw_token)
            gemini_display = _strip_edges(gemini_display)
            if _is_noise_pair(gemini_display, youtube_display):
                continue  # 표기·축약·복수/시제·기능어 차이뿐인 잡음.
            pair_key = (gemini_display, youtube_display)
            existing = merged.get(pair_key)
            if existing is None:
                merged[pair_key] = {"gemini": gemini_display, "youtube": youtube_display,
                                    "t": t, "sentence_no": indexed.sentence_no_at(t),
                                    "times": [t]}
            else:
                existing["times"].append(t)
    for item in merged.values():
        item["times"] = sorted(set(item["times"]))
    # id 는 아직 붙이지 않는다 — slide 근거가 붙은 뒤에야 정렬(유지 신호 우선,
    # 그다음 시각순)을 확정할 수 있다 (`_finalize_suspects`).
    return sorted(merged.values(), key=lambda item: item["t"])


def _finalize_suspects(suspects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """slide 근거를 붙인 뒤 정렬하고 S번호를 매긴다."""
    ordered = sorted(suspects, key=lambda item: (not _has_keep_signal(item), item["t"]))
    return [{"id": f"S{position}", **item} for position, item in enumerate(ordered, start=1)]


def _restored_terms(words: list[dict[str, Any]], indexed: Indexed) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for word in words:
        if word.get("origin") != "youtube":
            continue
        text = _strip_edges(str(word["text"]))
        if not text:
            continue
        t = float(word["start"])
        entry = seen.setdefault(text, {"text": text, "t": t, "times": []})
        entry["times"].append(t)
        entry["t"] = min(entry["t"], t)
    ordered = sorted(seen.values(), key=lambda item: item["t"])
    for item in ordered:
        item["times"] = sorted(set(item["times"]))
    return ordered


def _capture_ocr_evidence(bundle: Path, suspects: list[dict[str, Any]]
                          ) -> tuple[list[dict[str, Any]], bool, str | None]:
    """의심 시각의 새 프레임을 OCR 한다. `derived/frames.json` 은 건드리지 않는다."""
    existing_frames: list[dict[str, Any]] = []
    frames_path = bundle / "derived" / "frames.json"
    if frames_path.exists():
        try:
            existing_frames = _read_json(frames_path).get("frames") or []
        except (OSError, ValueError):
            existing_frames = []
    ocr_entries = [{"timestamp": float(f["timestamp"]), "text": f["ocr_text"]}
                   for f in existing_frames if f.get("ocr_text")]

    times: list[float] = sorted({t for item in suspects for t in item["times"]})[:MAX_NEW_OCR_CAPTURES]
    if not times:
        return ocr_entries, True, None

    video = visual.source_video(bundle)
    if video is None:
        try:
            video = pipeline.ensure_video(bundle)
        except Exception:  # noqa: BLE001 - 실패해도 조용히 건너뛴다.
            video = None
    if video is None:
        return ocr_entries, False, "영상을 구할 수 없어 새 프레임을 캡처하지 못했다."

    try:
        candidates = [{"timestamp": t, "reason": "suspect"} for t in times]
        new_frames = visual.extract_frames(video, bundle, candidates,
                                           language=visual.ocr_language(bundle))
    except Exception as error:  # noqa: BLE001
        return ocr_entries, False, f"프레임 캡처 실패: {error}"

    for frame in new_frames:
        if frame.get("ocr_text"):
            ocr_entries.append({"timestamp": float(frame["timestamp"]), "text": frame["ocr_text"]})
    return ocr_entries, True, None


def _attach_slides(suspects: list[dict[str, Any]], ocr_entries: list[dict[str, Any]]) -> None:
    ocr_tokens: list[str] = []
    for entry in ocr_entries:
        ocr_tokens.extend(_WORD_SPLIT_RE.findall(str(entry["text"])))
    normalized_tokens = [(token, _normalize(token)) for token in ocr_tokens if _normalize(token)]
    for suspect in suspects:
        best_token, best_ratio = None, 0.0
        for target in (suspect["gemini"], suspect["youtube"]):
            target_norm = _normalize(target)
            if not target_norm:
                continue
            for original, norm in normalized_tokens:
                ratio = difflib.SequenceMatcher(None, target_norm, norm).ratio()
                if ratio > best_ratio:
                    best_ratio, best_token = ratio, original
        suspect["slide"] = _strip_edges(best_token) if best_ratio >= 0.8 and best_token else None


def _shaped_gemini_candidates(words: list[dict[str, Any]]) -> list[tuple[str, float, int]]:
    counts: dict[str, int] = {}
    first_seen: dict[str, float] = {}
    for word in _gemini_words(words):
        text = _strip_edges(str(word["text"]))
        if not text or not _interesting_shape(text):
            continue
        counts[text] = counts.get(text, 0) + 1
        first_seen.setdefault(text, float(word["start"]))
    ranked = sorted(counts, key=lambda text: (-counts[text], first_seen[text]))
    return [(text, first_seen[text], counts[text]) for text in ranked[:MAX_SHAPED_TOKEN_CANDIDATES]]


def _shaped_ocr_candidates(ocr_entries: list[dict[str, Any]]) -> list[tuple[str, float]]:
    seen: dict[str, float] = {}
    for entry in ocr_entries:
        for token in _WORD_SPLIT_RE.findall(str(entry["text"])):
            text = _strip_edges(token)
            if text and _interesting_shape(text):
                seen.setdefault(text, float(entry["timestamp"]))
    return sorted(seen.items(), key=lambda pair: pair[1])


MAX_TERM_CANDIDATES = 120


def _build_term_candidates(words: list[dict[str, Any]], suspects: list[dict[str, Any]],
                           restored: list[dict[str, Any]],
                           ocr_entries: list[dict[str, Any]]
                           ) -> tuple[list[dict[str, Any]], int]:
    """restored + 모양 특이 토큰에서 용어 후보를 뽑는다.

    의심(S) 목록과 겹치는 표기는 이미 다른 목록에 보이므로 여기서 뺀다. 빈도
    1회이면서 대문자·숫자·점 같은 특이한 모양도 없는 토큰은 잡음으로 보고
    버린다. 최종 목록은 상한(`MAX_TERM_CANDIDATES`)으로 자르고, 잘린 개수를
    돌려준다(패킷에 그대로 밝힌다).
    """
    suspect_forms = {s["gemini"] for s in suspects} | {s["youtube"] for s in suspects}
    pool: dict[str, dict[str, float]] = {}

    def _add(text: str, t: float, freq: int) -> None:
        if not text or text in suspect_forms:
            return
        entry = pool.setdefault(text, {"t": t, "freq": 0})
        entry["t"] = min(entry["t"], t)
        entry["freq"] += freq

    for item in restored:
        _add(item["text"], item["t"], len(item["times"]))
    for text, t, freq in _shaped_gemini_candidates(words):
        _add(text, t, freq)
    for text, t in _shaped_ocr_candidates(ocr_entries):
        _add(text, t, 1)

    survivors = [(text, entry["t"]) for text, entry in pool.items()
                if entry["freq"] > 1 or _interesting_shape(text)]
    survivors.sort(key=lambda pair: pair[1])
    dropped = max(0, len(survivors) - MAX_TERM_CANDIDATES)
    kept = survivors[:MAX_TERM_CANDIDATES]
    return ([{"id": f"C{index}", "text": text, "t": t}
             for index, (text, t) in enumerate(kept, start=1)], dropped)


def build_evidence(bundle: Path, words: list[dict[str, Any]], indexed: Indexed) -> dict[str, Any]:
    """교차 대조 근거를 한 번 만든다 (명세 "1단계 준비"). Gemini 호출 없음."""
    cues = _load_captions(bundle)
    raw_suspects = _find_suspects(words, cues, indexed)
    restored = _restored_terms(words, indexed)
    ocr_entries, captured, capture_note = _capture_ocr_evidence(bundle, raw_suspects)
    _attach_slides(raw_suspects, ocr_entries)
    suspects = _finalize_suspects(raw_suspects)
    candidates, candidates_dropped = _build_term_candidates(words, suspects, restored, ocr_entries)
    transcript_text = " ".join(str(w["text"]) for w in _gemini_words(words))
    return {
        "suspects": suspects,
        "restored": restored,
        "candidates": candidates,
        "candidates_dropped": candidates_dropped,
        "ocr": ocr_entries,
        "captured": captured,
        "capture_note": capture_note,
        "transcript_text": transcript_text,
    }


# ------------------------------------------------------------------ 저장

def ko_path(bundle: Path, lang: str) -> Path:
    return bundle / "translations" / f"{lang}.json"


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _default_state(*, video_id: str, lang: str, fingerprint: str,
                   evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "lang": lang, "video_id": video_id,
        "sentences_fingerprint": fingerprint, "evidence": evidence,
        "terms_done": False, "terms": [], "lines": {}, "rejects": {},
        "terms_cursor": {"suspects": 0, "candidates": 0}, "terms_pending_retry": [],
        "terms_decided": [], "terms_stall_count": 0, "terms_auto_skipped": 0,
        "consistency_done": False, "updated_at": _now_iso(),
    }


def load_state(bundle: Path, lang: str) -> dict[str, Any] | None:
    path = ko_path(bundle, lang)
    if not path.exists():
        return None
    state = _read_json(path)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise SubtitleError(
            "translations/%s.json 의 schema_version(%r)을 알 수 없다. 새로 만들지 않는다."
            % (lang, state.get("schema_version")))
    return state


def _get_or_init_state(bundle: Path, *, video_id: str, lang: str,
                       words: list[dict[str, Any]], indexed: Indexed) -> dict[str, Any]:
    """상태를 읽거나, 없으면 `build_evidence` 로 한 번 만들어 저장한다."""
    path = ko_path(bundle, lang)
    with locking.file_lock(_lock_path(path)):
        if path.exists():
            state = _read_json(path)
            if state.get("schema_version") != SCHEMA_VERSION:
                raise SubtitleError(
                    "translations/%s.json 의 schema_version(%r)을 알 수 없다."
                    % (lang, state.get("schema_version")))
            if state.get("sentences_fingerprint") != indexed.fingerprint:
                return state
            changed = False
            if (state.get("terms_done") and _all_translated(state, indexed)
                    and not state.get("consistency_done")):
                _run_consistency_checks(state, indexed)
                changed = True
            if state.get("flags_revision") != FLAGS_REVISION and state.get("lines"):
                _reevaluate_flags(state, indexed)
                changed = True
            if changed:
                _write_json_atomic(path, state)
            return state
        evidence = build_evidence(bundle, words, indexed)
        state = _default_state(video_id=video_id, lang=lang,
                               fingerprint=indexed.fingerprint, evidence=evidence)
        _write_json_atomic(path, state)
        return state


def _mutate_state(bundle: Path, lang: str, mutate: Any) -> dict[str, Any]:
    """읽기 -> 병합 -> 원자적 쓰기. `mutate(state) -> state` 는 잠금 안에서 돈다."""
    path = ko_path(bundle, lang)
    with locking.file_lock(_lock_path(path)):
        if not path.exists():
            raise SubtitleError("먼저 cueprecise_subtitle 을 불러 작업을 시작하라.")
        state = _read_json(path)
        if state.get("schema_version") != SCHEMA_VERSION:
            raise SubtitleError(
                "translations/%s.json 의 schema_version(%r)을 알 수 없다."
                % (lang, state.get("schema_version")))
        state = mutate(state)
        state["updated_at"] = _now_iso()
        # `_last_result` 는 이번 호출의 응답(accepted/rejected)을 돌려주는 용도의
        # 임시 값이다. 파일에 남기지 않는다 — 다음 읽기에서 stale 값이 보이면
        # 안 된다.
        _write_json_atomic(path, {k: v for k, v in state.items() if k != "_last_result"})
        return state


# --------------------------------------------------------------- 진행률

def _line_states(state: dict[str, Any], indexed: Indexed) -> dict[str, str]:
    lines = state.get("lines") or {}
    return {item["key"]: lines[item["key"]]["state"]
            for item in indexed.sentences if item["key"] in lines}


def _progress(state: dict[str, Any], indexed: Indexed) -> dict[str, int]:
    states = _line_states(state, indexed)
    translated = sum(1 for value in states.values() if value in {"ok", "flagged"})
    flagged = sum(1 for value in states.values() if value == "flagged")
    untranslated = sum(1 for value in states.values() if value == "untranslated")
    return {"sentences": len(indexed.sentences), "translated": translated,
            "flagged": flagged, "untranslated": untranslated}


def _current_phase(state: dict[str, Any], indexed: Indexed) -> str:
    if not state.get("terms_done"):
        return "terms"
    states = _line_states(state, indexed)
    if len(states) < len(indexed.sentences):
        return "translate"
    flagged_unreviewed = [item for item in indexed.sentences
                          if states.get(item["key"]) == "flagged"
                          and not (state.get("lines", {}).get(item["key"], {}).get("reviewed"))]
    if not state.get("consistency_done") or flagged_unreviewed:
        return "review"
    return "done"


# ------------------------------------------------------------ 패킷 렌더

def _render_sentence(sentence: dict[str, Any]) -> str:
    tokens: list[str] = []
    breaths = sentence["breaths"]
    breath_index = 0
    for position, word in enumerate(sentence["words"]):
        tokens.append(str(word["text"]))
        if position + 1 < len(sentence["words"]) and breath_index < len(breaths):
            next_start = float(sentence["words"][position + 1]["start"])
            if breaths[breath_index] == next_start:
                breath_index += 1
                tokens.append(f"^{breath_index}")
    return " ".join(tokens)


def _video_meta(bundle: Path) -> tuple[str, str]:
    path = bundle / "raw" / "metadata.json"
    if path.exists():
        try:
            payload = _read_json(path)
            return str(payload.get("title") or bundle.name), str(payload.get("channel") or "-")
        except (OSError, ValueError):
            pass
    return bundle.name, "-"


def _enforce_packet_limit(text: str) -> str:
    """패킷은 절대 조용히 자르지 않는다. 잘릴 상황은 각 phase 가 묶음을 나눠
    미리 피해야 한다 — 여기까지 넘어오면 나누는 로직 자체의 버그다."""
    if len(text) > MAX_PACKET_CHARS:
        raise SubtitleError(
            "패킷이 %d자 상한을 넘었다(%d자). 묶음을 더 잘게 나누지 못한 버그다."
            % (MAX_PACKET_CHARS, len(text)))
    return text


def _base_packet(*, video_id: str, phase: str, fingerprint: str, progress: dict[str, int],
                 instructions: str, packet_text: str, response_format: str,
                 viewer_url: str | None = None) -> dict[str, Any]:
    return {
        "video_id": video_id, "phase": phase, "fingerprint": fingerprint,
        "progress": progress, "viewer_url": viewer_url,
        "instructions": instructions,
        "packet": _enforce_packet_limit(packet_text),
        "response_format": response_format,
    }


_TERMS_RULES: tuple[str, ...] = (
    "", "[규칙]",
    "이 패킷에 실린 의심(S)·후보(C) 전부에 결정을 내려야 한다 — 하나도 빠짐없이. 결정 없는 응답(빈 "
    "응답 포함)은 커서를 전진시키지 않고 같은 항목을 다시 보여준다.",
    "한 줄: T<n>|원어|번역어|짧은해설(<=16자, 없으면 -)|긴설명(<=50자, 없으면 -)|근거(S3,C5 또는 -)",
    "근거에 적은 S번호·C번호는 그 항목에 대한 결정으로 처리된다.",
    "용어로 만들 게 아니면 S<n>|- 또는 C<n>|- 한 줄로 그 항목을 건너뛴다고 밝혀라.",
    "용어집에는 고유명사(사람·기관·제품 이름)와 전문용어만 넣는다. is, times, probably, map 같은 일상 "
    "단어는 표기가 달라도 용어가 아니다 — S<n>|- 로 건너뛴다.",
    "gemini 와 youtube/slide 가 다른 의심(S)이 고유명사·전문용어의 전사 오류면 교정 용어로 만들고 근거에 "
    "그 S번호를 적어라.",
    "원어가 Gemini 원문 표기와 다르면(=교정) 근거에 S번호가 반드시 있어야 한다.",
    "번역어를 모르면 ? 를 쓴다 (원문 유지, uncertain 표시).",
    "짧은해설이 있으면 긴설명도 있어야 한다.",
    "짧은해설(<=16자)·긴설명은 비전공자가 모를 전문용어에만 적는다. 농담·문화 해설은 만들지 않는다.",
)


def _render_terms_body(title: str, channel: str, evidence: dict[str, Any],
                       cursor: dict[str, int], suspect_chunk: list[dict[str, Any]],
                       candidate_chunk: list[dict[str, Any]]) -> str:
    suspects, candidates = evidence["suspects"], evidence["candidates"]
    s_start, c_start = cursor["suspects"], cursor["candidates"]
    lines = [f"제목: {title}", f"채널: {channel}"]
    if s_start or c_start:
        lines.append("(이어서 보여준다 — 이전에 본 항목은 다시 나오지 않는다)")
    lines.append("")

    s_end = s_start + len(suspect_chunk)
    lines.append("[의심 목록] (%d-%d / %d)" %
                 (s_start + 1 if suspect_chunk else s_start, s_end, len(suspects)))
    for suspect in suspect_chunk:
        slide = suspect.get("slide") or "-"
        lines.append(f"{suspect['id']} | {suspect['t']:.1f}s | gemini:{suspect['gemini']} "
                     f"| youtube:{suspect['youtube']} | slide:{slide}")
    if not suspects:
        lines.append("(없음)")
    elif not suspect_chunk:
        lines.append("(이번 패킷에는 없음 — 다음 호출에서 이어진다)")

    dropped = evidence.get("candidates_dropped", 0)
    c_end = c_start + len(candidate_chunk)
    dropped_note = (" / 상한 초과로 %d건 생략" % dropped) if dropped else ""
    lines += ["", "[용어 후보] (%d-%d / %d%s)" %
             (c_start + 1 if candidate_chunk else c_start, c_end, len(candidates), dropped_note)]
    for candidate in candidate_chunk:
        lines.append(f"{candidate['id']} | {candidate['t']:.1f}s | {candidate['text']}")
    if not candidates:
        lines.append("(없음)")
    elif not candidate_chunk:
        lines.append("(이번 패킷에는 없음 — 다음 호출에서 이어진다)")

    lines.extend(_TERMS_RULES)
    return "\n".join(lines)


def _select_terms_chunk(title: str, channel: str, evidence: dict[str, Any],
                        cursor: dict[str, int]
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """다음에 보여줄 의심·후보 묶음을 12,000자 상한 안에서 고른다.

    남은 항목이 있으면 최소 하나는 반드시 담아 진행이 멈추지 않게 한다. 단일
    항목이 상한을 혼자 넘는 병적인 경우는 `_base_packet` 의 최종 검사가
    ToolError 로 잡는다 — 여기서 조용히 자르지 않는다.
    """
    suspects, candidates = evidence["suspects"], evidence["candidates"]

    def _fits(s_chunk: list[dict[str, Any]], c_chunk: list[dict[str, Any]]) -> bool:
        return len(_render_terms_body(title, channel, evidence, cursor, s_chunk, c_chunk)
                  ) <= MAX_PACKET_CHARS

    suspect_chunk: list[dict[str, Any]] = []
    index = cursor["suspects"]
    while index < len(suspects):
        trial = suspect_chunk + [suspects[index]]
        if not _fits(trial, []):
            break
        suspect_chunk = trial
        index += 1
    if not suspect_chunk and cursor["suspects"] < len(suspects):
        suspect_chunk = [suspects[cursor["suspects"]]]

    candidate_chunk: list[dict[str, Any]] = []
    index = cursor["candidates"]
    while index < len(candidates):
        trial = candidate_chunk + [candidates[index]]
        if not _fits(suspect_chunk, trial):
            break
        candidate_chunk = trial
        index += 1
    if not suspect_chunk and not candidate_chunk and cursor["candidates"] < len(candidates):
        candidate_chunk = [candidates[cursor["candidates"]]]

    return suspect_chunk, candidate_chunk


def _packet_terms_retry(state: dict[str, Any], indexed: Indexed, video_id: str) -> dict[str, Any]:
    pending = state.get("terms_pending_retry") or []
    lines = ["[재시도 — 마지막 기회] 아래 줄이 거절됐다. 같은 T번호로 고쳐서 다시 보내라:"]
    for item in pending:
        lines.append(f"{item['line']} | 사유: {item['reason']}")
    instructions = ("거절된 줄만 고쳐 같은 T<n> 번호로 다시 보내라. 이번에도 거절되면 그 용어는 "
                   "포기하고 다음 묶음으로 넘어간다(재시도는 한 번뿐).")
    return _base_packet(video_id=video_id, phase="terms", fingerprint=state["sentences_fingerprint"],
                        progress=_progress(state, indexed), instructions=instructions,
                        packet_text="\n".join(lines),
                        response_format="T<n>|원어|번역어|짧은해설|긴설명|근거 (거절된 줄만)")


def _packet_terms(bundle: Path, state: dict[str, Any], indexed: Indexed,
                  video_id: str) -> dict[str, Any]:
    if state.get("terms_pending_retry"):
        return _packet_terms_retry(state, indexed, video_id)
    title, channel = _video_meta(bundle)
    evidence = state["evidence"]
    cursor = state.get("terms_cursor") or {"suspects": 0, "candidates": 0}
    suspect_chunk, candidate_chunk = _select_terms_chunk(title, channel, evidence, cursor)
    packet_text = _render_terms_body(title, channel, evidence, cursor, suspect_chunk, candidate_chunk)
    instructions = ("의심 목록과 후보를 보고 용어집을 만들어라. 이 패킷에 실린 의심(S)·후보(C) 전부에 "
                   "결정이 있어야 한다. 각 줄을 T<n>|원어|번역어|짧은해설|긴설명|근거 형식으로 만들거나, "
                   "용어로 만들지 않을 항목은 S<n>|- 또는 C<n>|- 로 cueprecise_set_subtitle 에 보내라. "
                   "결정이 빠진 항목이 있으면(빈 응답 포함) 같은 항목을 다시 보여준다. "
                   "용어집에는 고유명사와 전문용어만 넣고, 일상 단어는 건너뛴다.")
    return _base_packet(video_id=video_id, phase="terms", fingerprint=state["sentences_fingerprint"],
                        progress=_progress(state, indexed), instructions=instructions,
                        packet_text=packet_text,
                        response_format="T<n>|원어|번역어|짧은해설|긴설명|근거 또는 S<n>|- / C<n>|-")


def _translate_batch_for(bundle: Path, state: dict[str, Any], indexed: Indexed
                         ) -> list[dict[str, Any]]:
    lines = state.get("lines") or {}
    start = None
    for position, item in enumerate(indexed.sentences):
        if item["key"] not in lines:
            start = position
            break
    if start is None:
        return []
    # 저장된(이미 번역된) 문장은 건너뛰고 미저장 문장만 모은다 — 거절돼 혼자
    # 남은 한 줄이 다음 미번역 구간과 합쳐져 한 패킷이 된다(왕복 감소). 건너뛴
    # 구간은 `_render_translate_body` 가 "-- N~M번 이미 번역됨(생략) --" 로
    # 패킷에 남겨 번역자가 앞뒤 문장이 바로 이어진다고 오해하지 않게 한다.
    unsaved = [item for item in indexed.sentences[start:] if item["key"] not in lines]
    if not unsaved:
        return []

    chapters_path = bundle / "derived" / "chapters.json"
    chapter_starts: list[float] = []
    if chapters_path.exists():
        try:
            chapter_starts = [float(item["start"])
                              for item in _read_json(chapters_path).get("chapters") or []]
        except (OSError, ValueError, KeyError):
            chapter_starts = []

    total_chars = len(unsaved[0]["text"])
    batch = [unsaved[0]]
    for position in range(1, len(unsaved)):
        item = unsaved[position]
        projected = total_chars + len(item["text"])
        if projected > BATCH_CHARS:
            break
        gap = item["start"] - batch[-1]["end"]
        natural_break = gap >= BATCH_BREAK_GAP_SECS or any(
            abs(item["start"] - boundary) < 0.05 for boundary in chapter_starts)
        batch.append(item)
        total_chars = projected
        if natural_break and total_chars >= BATCH_CHARS * 0.5:
            break

    # 용어집·앞 문맥까지 합친 실제 패킷이 12,000자를 넘으면(용어가 아주 많을
    # 때) 조용히 자르지 않고 묶음 자체를 줄인다. `_packet_translate` 와
    # `_apply_translate` 가 같은 함수를 써서 같은 배치를 본다.
    while len(batch) > 1 and len(_render_translate_body(state, indexed, batch)) > MAX_PACKET_CHARS:
        batch = batch[:-1]
    return batch


def _render_translate_body(state: dict[str, Any], indexed: Indexed,
                           batch: list[dict[str, Any]]) -> str:
    lines = list(_glossary_lines(state))
    context = _context_lines(state, indexed, batch)
    if context:
        lines.append("[앞 문맥] " + "  ".join(context))
    lines.append("[원고] 번호|길이|글자상한|원문(^k 표시)")
    previous_no: int | None = None
    for item in batch:
        if previous_no is not None and item["no"] != previous_no + 1:
            skip_start, skip_end = previous_no + 1, item["no"] - 1
            span = f"{skip_start}" if skip_start == skip_end else f"{skip_start}~{skip_end}"
            lines.append(f"-- {span}번 이미 번역됨(생략) --")
        duration = item["end"] - item["start"]
        lines.append(f"{item['no']}|{duration:.1f}s|{char_budget(duration)}|{_render_sentence(item)}")
        previous_no = item["no"]
    return "\n".join(lines)


def _context_lines(state: dict[str, Any], indexed: Indexed, batch: list[dict[str, Any]]) -> list[str]:
    if not batch:
        return []
    first_no = batch[0]["no"]
    context_items = [item for item in indexed.sentences
                     if first_no - CONTEXT_SENTENCES <= item["no"] < first_no]
    lines_state = state.get("lines") or {}
    rendered = []
    for item in context_items:
        stored = lines_state.get(item["key"])
        ko = stored["ko"] if stored and stored.get("ko") else None
        text = f"{item['no']}|en {item['text']}"
        if ko:
            text += f" || ko {ko}"
        rendered.append(text)
    return rendered


def _glossary_lines(state: dict[str, Any]) -> list[str]:
    parts = []
    for term in state.get("terms") or []:
        tgt = "원문 유지" if term.get("status") == "uncertain" else term.get("tgt")
        entry = f"{term['src']}={tgt}"
        heard = term.get("heard") or []
        if heard:
            # 원고(Gemini 전사)에는 이 틀린 표기로 나온다 — 호스트가 원문
            # 문장 안에서 용어를 못 알아보는 것을 막는다.
            entry += f" (원고 표기: {', '.join(heard)})"
        parts.append(entry)
    return ["[용어집] " + " | ".join(parts)] if parts else []


def _packet_translate(bundle: Path, state: dict[str, Any], indexed: Indexed,
                      video_id: str) -> dict[str, Any]:
    batch = _translate_batch_for(bundle, state, indexed)
    packet_text = _render_translate_body(state, indexed, batch)
    instructions = ("각 줄을 <번호>|번역 형식으로 답하라. 원문의 ^k 자리에서 자막을 넘기려면 "
                   "번역문에 /k 를 넣어라 (예: 17|... /2 ...). 자신 없으면 줄 끝에 ' ?'를 붙여라. "
                   "문장이 중간에 끊겨 다음 번호와 합쳐야만 옮길 수 있으면 다음 번호를 "
                   "<번호>|= 로 답하고(예: 12|=, 연쇄 가능 11<-12<-13) 그 내용을 앞 번역에 "
                   "포함시켜라 — 병합된 문장의 시작은 /+1, /+2, 병합된 문장 안의 숨 지점은 /+1.2 처럼 써라. "
                   "합쇼체로 쓴다. "
                   "/k, /+n 번호는 항상 오름차순으로 써라 — 한국어 어순이 뒤집혀도 표시는 원문 시간순이다. "
                   "원문이 기호·숫자뿐이어도 번역문의 한글 비율은 30% 이상이어야 한다(기호를 그대로 "
                   "베끼지 말고 한국어로 풀어써라). "
                   "/k 로 나눈 구간도 각각 초당 약 13자를 넘기지 마라(문장 전체 글자상한과 별개다). "
                   "'-- N~M번 이미 번역됨(생략) --' 표시는 그 사이 문장이 이미 처리됐다는 뜻이다 — "
                   "표시 앞뒤 문장이 바로 이어진다고 보지 마라.")
    return _base_packet(video_id=video_id, phase="translate",
                        fingerprint=state["sentences_fingerprint"],
                        progress=_progress(state, indexed), instructions=instructions,
                        packet_text=packet_text,
                        response_format="<번호>|번역(/k 또는 /+n, 필요하면 ' ?') 또는 <번호>|=")


def _flagged_unreviewed(state: dict[str, Any], indexed: Indexed) -> list[dict[str, Any]]:
    lines = state.get("lines") or {}
    result = []
    for item in indexed.sentences:
        stored = lines.get(item["key"])
        if stored and stored.get("state") == "flagged" and not stored.get("reviewed"):
            result.append(item)
    return result


def _render_stored_translation(stored: dict[str, Any]) -> str:
    """저장된 번역을 넘김 표시(/k, /+n)까지 되살린다. 그대로 다시 보내면 같은 결과가 된다."""
    segments = stored.get("segments") or []
    breaks = stored.get("breaks") or []
    if not segments:
        return stored.get("ko") or "-"
    parts = [segments[0]["text"]]
    for token, segment in zip(breaks, segments[1:]):
        parts.append(f"/{token} {segment['text']}")
    return " ".join(part for part in parts if part)


def _packet_review(bundle: Path, state: dict[str, Any], indexed: Indexed,
                   video_id: str) -> dict[str, Any]:
    candidates = _flagged_unreviewed(state, indexed)
    lines_state = state.get("lines") or {}
    lines: list[str] = []
    total = 0
    batch: list[dict[str, Any]] = []
    for item in candidates:
        stored = lines_state.get(item["key"], {})
        neighbors = [n for n in indexed.sentences
                    if item["no"] - 2 <= n["no"] <= item["no"] + 2 and n["no"] != item["no"]]
        block = [f"-- {item['no']} flags:{','.join(stored.get('flags') or [])} --"]
        for neighbor in neighbors:
            neighbor_stored = lines_state.get(neighbor["key"], {})
            block.append(f"{neighbor['no']}|en {neighbor['text']} || ko "
                         f"{neighbor_stored.get('ko') or '-'}")
        duration = item["end"] - item["start"]
        block.append(f"{item['no']}|{duration:.1f}s|{char_budget(duration)}|"
                     f"{_render_sentence(item)}")
        block.append(f"(현재 번역) {_render_stored_translation(stored)}")
        block_text = "\n".join(block)
        # 블록 사이에 붙는 빈 줄(두 글자)도 상한에 센다.
        added = len(block_text) + (2 if lines else 0)
        if batch and total + added > MAX_PACKET_CHARS:
            break
        batch.append(item)
        lines.append(block_text)
        total += added
    instructions = ("flag 이유를 보고 필요하면 번역을 고쳐 translate 와 같은 형식(<번호>|번역)으로 "
                   "다시 보내라. 문제가 없으면 (현재 번역)을 넘김 표시까지 그대로 다시 보내도 된다. "
                   "병합된 문장이 있는 줄은 /+n, /+n.k 를 쓸 수 있다.")
    return _base_packet(video_id=video_id, phase="review",
                        fingerprint=state["sentences_fingerprint"],
                        progress=_progress(state, indexed), instructions=instructions,
                        packet_text="\n\n".join(lines) if lines else "(재검토할 줄이 없다)",
                        response_format="<번호>|번역(/k, 필요하면 ' ?')")


def _cosmetic_key(text: str) -> str:
    # 복수형 s 도 표기 차이로 본다(ResNets -> ResNet).
    return re.sub(r"[^0-9a-z가-힣]", "", text.casefold()).removesuffix("s")


def _report_data(state: dict[str, Any], indexed: Indexed) -> dict[str, Any]:
    """`done` 패킷과 뷰어 품질 탭이 공유하는 구조화 보고 데이터."""
    lines_state = state.get("lines") or {}
    suspects_by_id = {s["id"]: s for s in state["evidence"]["suspects"]}
    corrections = []
    for term in state.get("terms") or []:
        sid = next((e for e in term.get("evidence") or [] if e.startswith("S")), None)
        if sid and sid in suspects_by_id:
            original = suspects_by_id[sid]["gemini"]
            # 대소문자·띄어쓰기·문장부호만 다른 표기는 교정으로 보고하지 않는다.
            if _cosmetic_key(original) != _cosmetic_key(term["src"]):
                corrections.append({"from": original, "to": term["src"], "evidence": sid})
    uncertain_terms = [term["src"] for term in state.get("terms") or []
                       if term.get("status") == "uncertain"]
    paced = [key for key, value in lines_state.items() if "pace" in (value.get("flags") or [])]
    translated_lines = [v for v in lines_state.values() if v.get("state") in {"ok", "flagged"}]
    pace_ratio = (len(paced) / len(translated_lines)) if translated_lines else 0.0
    untranslated_nos = sorted(item["no"] for item in indexed.sentences
                              if (lines_state.get(item["key"]) or {}).get("state") == "untranslated")
    terms_count = len(state.get("terms") or [])
    terms_auto_skipped = int(state.get("terms_auto_skipped") or 0)
    evidence = state.get("evidence") or {}
    suspects_and_candidates = len(evidence.get("suspects") or []) + len(evidence.get("candidates") or [])
    warnings: list[str] = []
    if terms_count == 0 and suspects_and_candidates > 0:
        warnings.append(
            "의심(%d건)·후보(%d건)가 있는데 용어가 하나도 만들어지지 않았다 — terms 단계 응답을 확인하라."
            % (len(evidence.get("suspects") or []), len(evidence.get("candidates") or [])))
    return {"corrections": corrections, "uncertain": uncertain_terms,
            "untranslated": untranslated_nos, "speed_over_ratio": round(pace_ratio, 4),
            "terms_count": terms_count, "terms_auto_skipped": terms_auto_skipped,
            "warnings": warnings}


def _packet_done(state: dict[str, Any], indexed: Indexed, video_id: str) -> dict[str, Any]:
    progress = _progress(state, indexed)
    report = _report_data(state, indexed)
    corrections_text = "; ".join(f"{c['from']} -> {c['to']} ({c['evidence']})"
                                 for c in report["corrections"]) or "(없음)"
    lines = [
        f"문장 수: {progress['sentences']}",
        f"번역됨: {progress['translated']} / 표시됨: {progress['flagged']} / "
        f"영어로 남음: {progress['untranslated']}",
        f"용어 수: {report['terms_count']}" +
        (f" (자동 건너뛴 항목 {report['terms_auto_skipped']}건)" if report["terms_auto_skipped"] else ""),
        "교정 목록: " + corrections_text,
        "uncertain 용어: " + (", ".join(report["uncertain"]) if report["uncertain"] else "(없음)"),
        f"읽기 속도 초과 비율: {report['speed_over_ratio']:.1%}",
    ]
    if report["warnings"]:
        lines.append("경고: " + " / ".join(report["warnings"]))
    return _base_packet(video_id=video_id, phase="done", fingerprint=state["sentences_fingerprint"],
                        progress=progress, instructions="번역이 끝났다. 추가로 부를 필요 없다.",
                        packet_text="\n".join(lines), response_format="(없음)")


def build_packet(bundle: Path, *, video_id: str, lang: str,
                 viewer_url: str | None = None) -> dict[str, Any]:
    """`viewer_url` 은 1단계 범위 밖 서버(viewer.py)가 채워 주는 값을 그대로
    싣는 자리다. 여기서는 서버를 켜지 않는다 — 주입만 받는다(테스트 용이)."""
    if lang not in SUPPORTED_LANGS:
        raise SubtitleError("lang 은 현재 ko 만 지원한다.")
    words = _load_words(bundle)
    if not words:
        raise SubtitleError("전사에 단어가 없다.")
    indexed = index_sentences(bundle)
    state = _get_or_init_state(bundle, video_id=video_id, lang=lang, words=words, indexed=indexed)
    phase = _current_phase(state, indexed)
    if phase == "terms":
        packet = _packet_terms(bundle, state, indexed, video_id)
    elif phase == "translate":
        packet = _packet_translate(bundle, state, indexed, video_id)
    elif phase == "review":
        packet = _packet_review(bundle, state, indexed, video_id)
    else:
        packet = _packet_done(state, indexed, video_id)
    packet["viewer_url"] = viewer_url
    if viewer_url:
        # Claude Desktop 등 인앱 브라우저 패널에는 전체화면 API가 아예 없거나
        # 막혀 있을 수 있다 — 의사 전체화면으로 대체는 하지만, 그 패널 크기
        # 안에서만 채워진다. 진짜 전체화면을 보려면 기본 웹 브라우저로 열어야
        # 한다는 것을 호스트에게 알린다.
        packet["instructions"] = (
            packet["instructions"] + " 뷰어 링크(viewer_url)는 앱 내장 브라우저 패널이 아니라 "
            "기본 웹 브라우저에서 열어야 전체화면이 제대로 된다 — 인앱 패널 안에서는 전체화면이 "
            "그 패널 크기로 제한된다."
        )
    return packet


# ---------------------------------------------------------- 응답 검증·저장

_TERM_LINE_RE = re.compile(r"^T(\d+)\s*\|\s*(.*)$")
_SKIP_LINE_RE = re.compile(r"^([SC]\d+)\s*\|\s*-\s*$")
_CODE_FENCE_RE = re.compile(r"^`{3,}")
_TABLE_SEP_RE = re.compile(r"^\|?[\s:|-]+\|?$")
_LIST_MARKER_RE = re.compile(r"^(?:[-*•]|\d+\.)\s+")


def _strip_markdown_decoration(raw_line: str) -> str | None:
    """마크다운 장식(목록 기호·굵게·인라인 코드·표 칸)을 벗긴다.

    무시해도 되는 줄(빈 줄, 코드펜스 구분선 ```` ``` ````, 표 구분선 `|---|`)이면
    `None`. 그 외에는 장식만 벗긴 내용을 돌려준다 — 벗기고 나서도 형식이 안 맞으면
    호출부가 그 사실을 거절로 보고할 수 있도록, 여기서는 조용히 버리지 않는다.
    """
    line = raw_line.strip()
    if not line:
        return None
    if _CODE_FENCE_RE.match(line):
        return None
    if "-" in line and _TABLE_SEP_RE.match(line):
        return None
    line = _LIST_MARKER_RE.sub("", line, count=1).strip()
    line = line.replace("**", "").strip()
    line = line.strip("`").strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return line.strip()


def _parse_terms_response(text: str) -> tuple[list[tuple[int, list[str]]], list[str], list[str]]:
    """T<n>|원어|번역어|짧은해설|긴설명|근거 줄과 S<n>|- / C<n>|- 건너뛰기 줄을 관대하게 파싱한다.

    목록 기호(`-`,`*`,`•`,`1.`)·굵게(`**`)·인라인 코드(백틱)·표 칸(양끝 `|`)은
    벗기고 읽는다. 코드펜스 구분선과 표 구분선(`|---|`)은 무시한다(거절이
    아니다). 그 외 비어 있지 않은 줄이 그래도 둘 다로 안 읽히면 셋째
    반환값(`unmatched`)에 원본 형태로 담아 호출부가 거절로 보고하게 한다.
    """
    parsed: list[tuple[int, list[str]]] = []
    skip_ids: list[str] = []
    unmatched: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_markdown_decoration(raw_line)
        if line is None:
            continue
        term_match = _TERM_LINE_RE.match(line)
        if term_match:
            fields = [field.strip() for field in term_match.group(2).split("|")]
            parsed.append((int(term_match.group(1)), fields))
            continue
        skip_match = _SKIP_LINE_RE.match(line)
        if skip_match:
            skip_ids.append(skip_match.group(1))
            continue
        if line:
            unmatched.append(line)
    return parsed, skip_ids, unmatched


# 용어집에 들어오면 안 되는 흔한 영어 단어. 실측에서 is/times/that's/probably/map 이
# 용어로 들어와 번역 검사(terms 플래그)가 문장 절반을 잘못 표시했다.
_EVERYDAY_WORDS = frozenset("""
a an the is are was were be been being am do does did done have has had having
it its it's this that that's these those there here what what's which who whom whose
i you he she we they me him her us them my your his our their
and or but so if then than because as of in on at to for from by with about into over
not no yes can could will would shall should may might must
time times thing things way ways day days year years people person
probably necessarily actually really just very also only even still maybe perhaps
map maps fare
get got make made take took see saw say said know knew go went come came look looked
""".split())


def _is_everyday_word(src: str) -> bool:
    words = re.findall(r"[A-Za-z']+", src)
    if not words or src != src.lower():
        return False  # 대문자가 섞이면 고유명사일 수 있다.
    return all(word in _EVERYDAY_WORDS for word in words)


def _mentions(text: str, form: str) -> bool:
    """단어 경계에서 대소문자 무시로 찾는다 ('is' 가 'this' 에 걸리지 않게)."""
    return re.search(r"(?<![A-Za-z])" + re.escape(form) + r"(?![A-Za-z])", text, re.I) is not None


MAX_SHORT_EXPLANATION_CHARS = 16
MAX_NOTE_CHARS = 50


def _gemini_word_pairs(words: list[dict[str, Any]]) -> list[tuple[str, float]]:
    return [(_strip_edges(str(w["text"])), float(w["start"])) for w in _gemini_words(words)]


def _first_occurrence_time(pairs: list[tuple[str, float]], phrase: str) -> float | None:
    """`phrase` 가 Gemini 전사에 처음 등장하는 시각. 단어 경계로 찾는다."""
    tokens = [_strip_edges(token) for token in phrase.split() if _strip_edges(token)]
    if not tokens:
        return None
    texts = [text for text, _ in pairs]
    for start in range(len(texts) - len(tokens) + 1):
        if all(texts[start + offset].casefold() == tokens[offset].casefold()
              for offset in range(len(tokens))):
            return pairs[start][1]
    return None


def _contains_term(transcript_text: str, phrase: str) -> bool:
    """`phrase` 가 전사에 있는지 대소문자 무시 + 단어 경계로 본다.

    "NanoGPT" 를 원문 그대로 썼는데도 대소문자가 다르다는 이유만으로
    교정(=근거 필요)으로 오판되는 것을 막는다.
    """
    if not phrase.strip():
        return False
    try:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        return re.search(pattern, transcript_text, re.IGNORECASE) is not None
    except re.error:
        return phrase.casefold() in transcript_text.casefold()


def _apply_terms(bundle: Path, state: dict[str, Any], words: list[dict[str, Any]],
                 text: str) -> dict[str, Any]:
    known_suspects = {s["id"]: s for s in state["evidence"]["suspects"]}
    known_candidates = {c["id"] for c in state["evidence"]["candidates"]}
    transcript_text = state["evidence"].get("transcript_text", "")
    gemini_pairs = _gemini_word_pairs(words)
    is_retry_round = bool(state.get("terms_pending_retry"))
    # 구 상태 파일 호환: `terms_decided` 가 없으면 아무것도 결정되지 않은 것으로 본다.
    decided_before = set(state.get("terms_decided") or [])
    decided = set(decided_before)
    accepted, rejected = [], []
    terms = list(state.get("terms") or [])
    parsed_terms, skip_ids, unmatched_lines = _parse_terms_response(text)
    for number, fields in parsed_terms:
        term_id = f"T{number}"
        if len(fields) < 5:
            rejected.append({"line": term_id, "reason": "필드 수가 부족하다"})
            continue
        src, tgt, short, note, evidence_raw = (fields + ["-"])[:5]
        src, tgt, short, note = src.strip(), tgt.strip(), short.strip(), note.strip()
        evidence_ids = [e.strip() for e in evidence_raw.split(",") if e.strip() and e.strip() != "-"]
        if _is_everyday_word(src):
            rejected.append({"line": term_id,
                             "reason": "일상 단어는 용어가 아니다 — S<n>|- 로 건너뛰어라"})
            continue
        if not src or not tgt:
            rejected.append({"line": term_id, "reason": "원어 또는 번역어가 비었다"})
            continue
        short_clean = None if short in ("-", "") else short
        note_clean = None if note in ("-", "") else note
        if short_clean and len(short_clean) > MAX_SHORT_EXPLANATION_CHARS:
            rejected.append({"line": term_id,
                             "reason": "짧은해설이 %d자를 넘는다" % MAX_SHORT_EXPLANATION_CHARS})
            continue
        if note_clean and len(note_clean) > MAX_NOTE_CHARS:
            rejected.append({"line": term_id, "reason": "긴설명이 %d자를 넘는다" % MAX_NOTE_CHARS})
            continue
        if short_clean and not note_clean:
            rejected.append({"line": term_id, "reason": "짧은해설이 있으면 긴설명도 있어야 한다"})
            continue
        is_correction = not _contains_term(transcript_text, src)
        evidence_suspect_ids = [e for e in evidence_ids if e.startswith("S") and e in known_suspects]
        evidence_suspect_id = evidence_suspect_ids[0] if evidence_suspect_ids else None
        if is_correction and evidence_suspect_id is None:
            has_candidate_only = any(e.startswith("C") and e in known_candidates
                                     for e in evidence_ids)
            reason = ("원문 음성에 나오지 않는 용어" if has_candidate_only
                     else "교정인데 근거(S번호)가 없다")
            rejected.append({"line": term_id, "reason": reason})
            continue
        heard = sorted({known_suspects[sid]["gemini"] for sid in evidence_suspect_ids
                        if _is_heard_form(known_suspects[sid]["gemini"], src)})
        if evidence_suspect_id is not None:
            # 교정된 용어는 교정 전(Gemini 원형) 표기로 첫 등장 시각을 찾는다.
            first_t = known_suspects[evidence_suspect_id]["t"]
        else:
            first_t = _first_occurrence_time(gemini_pairs, src)
        status = "uncertain" if tgt == "?" else "confirmed"
        final_tgt = src if status == "uncertain" else tgt
        terms.append({"id": term_id, "src": src, "tgt": final_tgt,
                      "short": short_clean, "note": note_clean, "heard": heard,
                      "evidence": evidence_ids, "status": status, "first_t": first_t})
        accepted.append(term_id)
        # 이 용어의 근거에 적힌 S/C 번호는 그 항목에 대한 결정으로 처리한다.
        decided.update(e for e in evidence_ids if e in known_suspects or e in known_candidates)
    state["terms"] = terms

    for sid in skip_ids:
        if sid in known_suspects or sid in known_candidates:
            decided.add(sid)
            accepted.append(sid)
        else:
            rejected.append({"line": sid, "reason": "알 수 없는 의심·후보 번호다"})

    for line in unmatched_lines:
        # 형식이 아예 안 맞아 T<n> 도 S<n>|-/C<n>|- 도 못 읽은 줄 — 조용히 버리지
        # 않고 거절로 보고한다. id 가 없으니 원본 줄 자체를 식별자로 쓴다.
        rejected.append({"line": line[:120],
                         "reason": "형식이 맞지 않는다: T<n>|원어|번역어|짧은해설|긴설명|근거 또는 "
                                  "S<n>|- / C<n>|-"})

    # 응답에 뭔가 글자는 있었는데(공백뿐이 아니었는데) 결정으로 읽히는 줄이 하나도
    # 없었다 — 형식이 전부 어긋난 응답이다. 개별 줄 재시도(terms_pending_retry)를
    # 만들지 않는다 — 고칠 T번호 자체를 하나도 못 건졌기 때문이다.
    stalled = (not is_retry_round) and bool(text.strip()) and not parsed_terms and not skip_ids

    if is_retry_round:
        # 재시도는 한 번뿐이다 — 이번에도 거절되면 그냥 포기하고 넘어간다. 그
        # 항목은 결정되지 않은 채 남고, 명시적 건너뛰기나 3회 정체 후 자동
        # 건너뛰기로만 해소된다.
        state["terms_pending_retry"] = []
    elif stalled:
        state["terms_pending_retry"] = []
    elif rejected:
        # 거절된 줄만 다음 패킷에 다시 보여준다(딱 한 번).
        state["terms_pending_retry"] = rejected
    else:
        state["terms_pending_retry"] = []

    # 커서는 이제 "한 번 보여줌"이 아니라 "결정됨"을 기준으로 움직인다: 이번
    # 호출이 보여준 것과 같은 묶음(커서 위치가 안 바뀌었으므로 다시 골라도 같은
    # 항목이다)에서, 맨 앞부터 이어지는 연속 구간이 전부 결정됐을 때만 그만큼
    # 전진한다. 중간에 결정 안 된 항목이 있으면 그 뒤는 다음 호출에도 다시 보인다.
    title, channel = _video_meta(bundle)
    evidence = state["evidence"]
    cursor = dict(state.get("terms_cursor") or {"suspects": 0, "candidates": 0})
    suspect_chunk, candidate_chunk = _select_terms_chunk(title, channel, evidence, cursor)
    chunk_ids = [item["id"] for item in suspect_chunk] + [item["id"] for item in candidate_chunk]

    def _decided_prefix(items: list[dict[str, Any]]) -> int:
        length = 0
        for item in items:
            if item["id"] not in decided:
                break
            length += 1
        return length

    suspect_prefix = _decided_prefix(suspect_chunk)
    candidate_prefix = _decided_prefix(candidate_chunk)
    fully_decided_this_chunk = (suspect_prefix == len(suspect_chunk)
                                and candidate_prefix == len(candidate_chunk))
    newly_decided = decided - decided_before

    # 무한 루프 방지: 같은 묶음이 진전 없이(이번 호출에서 그 묶음의 항목이 하나도
    # 새로 결정되지 않고) 3회 연속되면 남은 항목을 전부 건너뛴 것으로 표시한다.
    auto_skipped_now = 0
    if fully_decided_this_chunk or not chunk_ids or (set(chunk_ids) & newly_decided):
        state["terms_stall_count"] = 0
    else:
        stall_count = int(state.get("terms_stall_count") or 0) + 1
        if stall_count >= 3:
            for item in suspect_chunk + candidate_chunk:
                if item["id"] not in decided:
                    decided.add(item["id"])
                    auto_skipped_now += 1
            suspect_prefix, candidate_prefix = len(suspect_chunk), len(candidate_chunk)
            fully_decided_this_chunk = True
            state["terms_stall_count"] = 0
        else:
            state["terms_stall_count"] = stall_count

    cursor["suspects"] += suspect_prefix
    cursor["candidates"] += candidate_prefix
    state["terms_cursor"] = cursor
    state["terms_decided"] = sorted(decided)
    if auto_skipped_now:
        state["terms_auto_skipped"] = int(state.get("terms_auto_skipped") or 0) + auto_skipped_now

    exhausted = (cursor["suspects"] >= len(evidence["suspects"])
                and cursor["candidates"] >= len(evidence["candidates"]))
    # 자동 건너뛰기로 완결된 경우엔 이번 응답 자체가 정체(stalled)였더라도 막지 않는다.
    blocking_stall = stalled and not auto_skipped_now
    state["terms_done"] = exhausted and not state["terms_pending_retry"] and not blocking_stall

    undecided_ids = [cid for cid in chunk_ids if cid not in decided]
    note = None
    if auto_skipped_now:
        note = "같은 항목이 3회 연속 결정되지 않아 남은 %d건을 자동으로 건너뛰었다." % auto_skipped_now
    elif undecided_ids:
        note = ("다음 항목에 아직 결정이 없다: %s — 각 항목을 T<n>|원어|번역어|짧은해설|긴설명|근거 로 "
                "용어로 만들거나, 만들지 않으려면 S<n>|- / C<n>|- 로 건너뛴다고 밝혀라." %
                ", ".join(undecided_ids))
    elif text.strip() and not accepted:
        note = "이번 응답에서 저장된 용어가 없다(accepted 0건) — rejected 사유를 확인하라."
    state["_last_result"] = {"accepted": accepted, "rejected": rejected, "note": note}
    return state


def _is_heard_form(heard: str, src: str) -> bool:
    """근거 의심의 Gemini 표기가 이 용어를 잘못 들은 표기인가.

    의심 목록은 단어 단위라, 용어의 일부 단어만 걸린 경우가 있다('gradient' for
    'gradient descent', 'Bengio' for 'Yoshua Bengio'). 그걸 들린 표기로 넣으면
    원고에 그 단어만 나와도 용어 번역어를 요구하게 된다. 그래서 용어의 한 단어와
    같은 표기는 버리고, 용어 전체나 한 단어와 충분히 닮은 표기만 받는다.
    """
    heard_norm = re.sub(r"[^0-9a-z]", "", heard.lower())
    src_words = [re.sub(r"[^0-9a-z]", "", word.lower()) for word in src.split()]
    if not heard_norm or heard_norm == "".join(src_words):
        return False
    if len(src_words) > 1 and heard_norm in src_words:
        return False
    whole = difflib.SequenceMatcher(None, heard_norm, "".join(src_words)).ratio()
    best_word = max((difflib.SequenceMatcher(None, heard_norm, word).ratio()
                     for word in src_words if word), default=0.0)
    return whole >= 0.6 or (len(src_words) > 1 and best_word >= 0.7)


_NUMBER_RE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")


def _numbers_of(text: str) -> set[str]:
    return {token.replace(",", "") for token in _NUMBER_RE.findall(text)}


_LATIN_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.\-]*")


def _hangul_ratio(text: str) -> float:
    """영문 용어(라틴 문자 연속)를 뺀 나머지에서 한글 비율을 잰다."""
    stripped = _LATIN_RUN_RE.sub("", text)
    letters = [ch for ch in stripped if ch.isalpha()]
    if not letters:
        # 영문 용어를 뺀 나머지에 글자가 하나도 없다 — 한글 문장이 아니라는
        # 뜻이므로 통과시키지 않는다 (전부 영어인 답을 0/0 으로 봐주지 않는다).
        return 0.0
    hangul = sum(1 for ch in letters if _HANGUL_RE.match(ch))
    return hangul / len(letters)


_BREAK_TOKEN_RE = re.compile(r"\s*/(\+\d+(?:\.\d+)?|\d+)\s*")


def _parse_translate_line(rest: str) -> tuple[list[str], list[str], bool]:
    """줄을 조각과 넘김 표시로 나눈다.

    넘김 표시는 두 갈래다: `k`(정수) 는 그 문장 자신의 숨 지점, `+n` 은 병합된
    다음 n 번째 문장이 시작하는 시각(문장이 중간에 끊겨 이어지는 경우, 명세
    16절의 병합 표기). 시각으로 바꾸는 것은 호출부(`_validate_and_store_line`)
    가 한다 — 여기서는 원본 토큰 문자열만 그대로 돌려준다.
    """
    # 자신 없음 표시는 공백 뒤에 홀로 선 '?' 뿐이다. 붙은 '?' 는 의문문의 물음표다.
    stripped = rest.rstrip()
    uncertain = stripped.endswith(" ?") or stripped == "?"
    if uncertain:
        rest = stripped[:-1].rstrip()
    parts = _BREAK_TOKEN_RE.split(rest)
    segments = [parts[index].strip() for index in range(0, len(parts), 2)]
    breaks = [parts[index] for index in range(1, len(parts), 2)]
    return segments, breaks, uncertain


def _resolve_break_times(break_tokens: list[str], item: dict[str, Any],
                         merged: list[dict[str, Any]]) -> tuple[list[float] | None, str | None]:
    """`k`/`+n`/`+n.k` 넘김 토큰을 실제 시각으로 바꾼다. 증가 순서를 강제한다.

    `merged` 는 이 문장 뒤로 병합된 문장들(순서대로)이다. `+n` 은 n번째 병합
    문장의 시작, `+n.k` 는 그 문장의 k번째 숨 지점이다.
    """
    times: list[float] = []
    previous = item["start"]
    for token in break_tokens:
        if token.startswith("+"):
            head, _, sub = token[1:].partition(".")
            index = int(head)
            if index < 1 or index > len(merged):
                return None, "/+n 이 병합된 문장 범위를 벗어났다"
            target = merged[index - 1]
            if sub:
                breath = int(sub)
                if breath < 1 or breath > len(target["breaths"]):
                    return None, "/+n.k 숨 지점이 범위를 벗어났다"
                candidate = target["breaths"][breath - 1]
            else:
                candidate = target["start"]
        else:
            index = int(token)
            if index < 1 or index > len(item["breaths"]):
                return None, "/k 숨 지점이 범위를 벗어났다"
            candidate = item["breaths"][index - 1]
        if candidate <= previous:
            return None, "/k 숨 지점이 증가 순서가 아니다"
        times.append(candidate)
        previous = candidate
    return times, None


def _validate_and_store_line(state: dict[str, Any], indexed: Indexed, item: dict[str, Any],
                             rest: str, *, merged: list[dict[str, Any]] | None = None,
                             extended_end: float | None = None
                             ) -> tuple[bool, str | None, list[str]]:
    """구조 검증 후 저장한다. 실패면 (False, 이유, []); 성공이면 (True, None, flags).

    `merged` 는 이 문장 뒤로 병합된 문장들(순서대로) — `/+n`, `/+n.k` 넘김
    표기가 가리키는 자리다. `extended_end` 가 있으면 마지막
    조각의 끝을 이 문장의 끝이 아니라 병합 사슬의 마지막 문장 끝으로 늘린다
    (명세 16절, 문장이 중간에 끊겨 다음 문장과 합쳐 옮겨야 하는 경우).
    """
    segments, break_tokens, uncertain = _parse_translate_line(rest)
    text = " ".join(segment for segment in segments if segment)
    if not text:
        return False, "번역이 비었다", []
    times, error = _resolve_break_times(break_tokens, item, merged or [])
    if error:
        return False, error, []

    if _hangul_ratio(text) < 0.30:
        return False, "한글 비율이 30% 미만이다", []

    flags: list[str] = []
    if _numbers_of(item["text"]) - _numbers_of(text):
        flags.append("numbers")
    for term in state.get("terms") or []:
        if term.get("status") == "uncertain":
            continue
        source_forms = [term["src"], *(term.get("heard") or [])]
        if any(_mentions(item["text"], form) for form in source_forms) and term["tgt"] not in text:
            flags.append("terms")
            break
    # 넘김 구간 경계: 문장 시작 -> 실제로 쓴 숨 지점(들)의 시각 -> 문장(또는
    # 병합 사슬의 마지막 문장) 끝. 전체 breaths 가 아니라 응답이 실제로 넘긴
    # 지점만 쓴다 — 아니면 일부만 쓰였을 때 시간 배분이 어긋난다.
    final_end = extended_end if extended_end is not None else item["end"]
    edges = [item["start"], *times, final_end]
    sentence_secs = final_end - item["start"]
    segment_records: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        start_edge = edges[index] if index < len(edges) - 1 else edges[-2]
        end_edge = edges[index + 1] if index < len(edges) - 1 else edges[-1]
        segment_records.append({"text": segment, "start": start_edge, "end": end_edge})
        if _pace_exceeded(segment, end_edge - start_edge, sentence_secs) and "pace" not in flags:
            flags.append("pace")
    if uncertain:
        flags.append("uncertain")

    lines = state.setdefault("lines", {})
    lines[item["key"]] = {"ko": text, "breaks": break_tokens, "segments": segment_records,
                          "flags": flags,
                          "rejects": (state.get("rejects") or {}).get(item["key"], 0),
                          "reviewed": False, "state": "flagged" if flags else "ok"}
    return True, None, flags


PACE_SLACK_CHARS = 3
"""글자 상한을 이만큼까지 넘는 건 표시하지 않는다. 뷰어가 짧은 큐를 합치고 늘린다."""


def _pace_exceeded(segment: str, duration: float, sentence_secs: float) -> bool:
    """읽기 속도 초과인가. 1.5초 미만 발화는 뷰어가 MIN_CUE 로 늘리므로 보지 않는다."""
    if sentence_secs < MIN_CUE_SECS or duration <= 0:
        return False
    chars = len("".join(segment.split()))
    return chars > duration * READ_CPS * 1.2 + PACE_SLACK_CHARS


_LINE_NO_RE = re.compile(r"^(\d+)\s*\|\s*(.*)$")


def _parse_numbered_lines(text: str) -> dict[int, str]:
    """<번호>|번역 줄을 읽는다. 목록 기호·굵게·인라인 코드·표 칸 장식은 벗기고 읽는다
    (`_strip_markdown_decoration`, terms 단계와 같은 완화 규칙). 문법 자체(꺾쇠 표시
    `/k`, `/+n`, ' ?')는 바꾸지 않는다 — 여전히 안 읽히는 줄은 조용히 버린다(번호가
    응답에 없는 문장은 다음 패킷에 다시 나오므로 여기서 거절을 만들 필요가 없다)."""
    result = {}
    for raw_line in text.splitlines():
        line = _strip_markdown_decoration(raw_line)
        if not line:
            continue
        match = _LINE_NO_RE.match(line)
        if match:
            result[int(match.group(1))] = match.group(2)
    return result


def _reject_line(state: dict[str, Any], rejects_map: dict[str, int], rejected: list[dict[str, Any]],
                 number: int, key: str, reason: str) -> None:
    count = rejects_map.get(key, 0) + 1
    rejects_map[key] = count
    if count >= MAX_REJECTS:
        state.setdefault("lines", {})[key] = {
            "ko": None, "breaks": [], "segments": None, "flags": ["max_rejects"], "rejects": count,
            "reviewed": False, "state": "untranslated"}
        rejected.append({"line": number, "reason": f"{reason} (거절 {count}회, 영어로 표시)"})
    else:
        rejected.append({"line": number, "reason": reason})


def _apply_translate(bundle: Path, state: dict[str, Any], indexed: Indexed, text: str) -> dict[str, Any]:
    batch = _translate_batch_for(bundle, state, indexed)
    by_no = {item["no"]: item for item in batch}
    responses = _parse_numbered_lines(text)
    accepted, rejected = [], []
    rejects_map = dict(state.get("rejects") or {})
    lines_state = state.get("lines") or {}

    # 1차: 병합 선언("<번호>|=")을 오름차순으로 훑어 사슬을 만든다. "11 <- 12
    # <- 13" 처럼 연쇄될 수 있으므로 번호 순서대로 봐야 앞 번호가 먼저
    # 해결된다. 대상(바로 앞 번호)이 이번 응답에도 없고 이미 저장돼 있지도
    # 않으면 구조 거절이다 — 다른 구조 거절과 같은 규칙(MAX_REJECTS)을 쓴다.
    merge_target: dict[int, int] = {}
    for number in sorted(responses):
        rest = responses[number]
        if rest.strip() != "=":
            continue
        item = by_no.get(number)
        if item is None:
            continue  # 묶음에 없는 번호는 조용히 무시한다.
        target_no = number - 1
        target_item = indexed.by_no.get(target_no)
        target_present = target_no in responses
        target_stored = bool(target_item and lines_state.get(target_item["key"], {}).get("state")
                             in {"ok", "flagged"})
        if target_item is None or not (target_present or target_stored):
            _reject_line(state, rejects_map, rejected, number, item["key"],
                        f"{target_no}번에 번역이 없어 병합할 수 없다")
            continue
        merge_target[number] = target_no

    def _chain_terminal_no(root_no: int) -> int:
        terminal = root_no
        while merge_target.get(terminal + 1) == terminal:
            terminal += 1
        return terminal

    # 2차: 병합 선언이 아닌 일반 번역 줄을 저장한다. 병합 사슬의 뿌리라면
    # 마지막 조각의 끝을 사슬의 마지막 문장 끝까지 늘리고, `/+n` 이 가리킬 수
    # 있는 병합 문장 시작 시각 목록을 넘긴다.
    for number in sorted(responses):
        if number in merge_target:
            continue
        rest = responses[number]
        item = by_no.get(number)
        if item is None:
            continue  # 묶음에 없는 번호는 조용히 무시한다 (거절 누적 아님).
        terminal_no = _chain_terminal_no(number)
        extended_end = indexed.by_no[terminal_no]["end"] if terminal_no != number else None
        merged = [indexed.by_no[n] for n in range(number + 1, terminal_no + 1)]
        ok, reason, _flags = _validate_and_store_line(
            state, indexed, item, rest, merged=merged, extended_end=extended_end)
        if ok:
            accepted.append(number)
            rejects_map.pop(item["key"], None)
            state.setdefault("lines", {})[item["key"]]["rejects"] = rejects_map.get(item["key"], 0)
            continue
        _reject_line(state, rejects_map, rejected, number, item["key"], reason)

    # 3차: 이제 병합 선언들을 실제로 저장한다(뿌리가 정상 저장됐는지와 무관하게
    # — 뿌리가 거절돼 다음 기회를 기다리는 중이어도 병합 표시 자체는 유효하다).
    for number, target_no in merge_target.items():
        item = by_no[number]
        target_item = indexed.by_no[target_no]
        state.setdefault("lines", {})[item["key"]] = {
            "ko": None, "breaks": [], "segments": None, "flags": [],
            "rejects": rejects_map.get(item["key"], 0),
            "reviewed": False, "state": "ok", "merged_into": target_item["key"]}
        accepted.append(number)

    state["rejects"] = rejects_map
    if _all_translated(state, indexed) and not state.get("consistency_done"):
        # review 패킷을 만들기 전에 일관성 표시가 모두 붙어 있어야 한다.
        _run_consistency_checks(state, indexed)
    state["_last_result"] = {"accepted": accepted, "rejected": rejected}
    return state


def _run_consistency_checks(state: dict[str, Any], indexed: Indexed) -> None:
    lines = state.get("lines") or {}
    # (a) 같은 용어 src 에 서로 다른 tgt.
    by_src: dict[str, set[str]] = {}
    for term in state.get("terms") or []:
        by_src.setdefault(term["src"], set()).add(term["tgt"])
    inconsistent_srcs = {src for src, targets in by_src.items() if len(targets) > 1}
    if inconsistent_srcs:
        for item in indexed.sentences:
            stored = lines.get(item["key"])
            if stored and stored.get("state") in {"ok", "flagged"} and any(
                    src in item["text"] for src in inconsistent_srcs):
                if "term_consistency" not in stored["flags"]:
                    stored["flags"].append("term_consistency")
                stored["state"] = "flagged"

    _flag_style(state, indexed)
    state["consistency_done"] = True


_QUOTED_RE = re.compile(r'"[^"]*"|“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’')


def _speech_style(ko: str) -> str | None:
    """번역 줄의 말투. 인용문·의문문·'~죠' 류는 판정하지 않는다(None).

    합쇼체 강연 번역에서도 "왜일까요?", "그렇죠" 같은 어미는 자연스럽다. 해요체는
    인용 밖 평서문이 '요' 로 끝나고 합쇼체 구어 어미가 아닐 때만 본다.
    """
    text = _QUOTED_RE.sub("", ko).strip()
    if not text or text.endswith("?"):
        return None
    tail = text.rstrip(".!…, ").rstrip()
    if tail.endswith("니다"):
        return "formal"
    if tail.endswith(("죠", "까요", "세요", "네요")):
        return None
    if tail.endswith("요"):
        return "informal"
    return None


def _flag_style(state: dict[str, Any], indexed: Indexed) -> None:
    """합쇼체/해요체 혼용 — 소수 쪽에 flag."""
    lines = state.get("lines") or {}
    endings: dict[str, str] = {}
    for item in indexed.sentences:
        stored = lines.get(item["key"])
        if not stored or stored.get("state") not in {"ok", "flagged"} or not stored.get("ko"):
            continue
        style = _speech_style(stored["ko"])
        if style:
            endings[item["key"]] = style
    formal = sum(1 for value in endings.values() if value == "formal")
    informal = len(endings) - formal
    if not formal or not informal or formal == informal:
        return
    minority = "informal" if informal < formal else "formal"
    for key, style in endings.items():
        if style == minority:
            stored = lines[key]
            if "style" not in stored["flags"]:
                stored["flags"].append("style")
            stored["state"] = "flagged"


FLAGS_REVISION = 2
"""저장된 표시(pace/style)를 만든 규칙의 판. 규칙이 바뀌면 올리고 재평가한다."""


def _merge_chain(state: dict[str, Any], indexed: Indexed, item: dict[str, Any]
                 ) -> list[dict[str, Any]]:
    """저장 상태에서 이 문장 뒤로 병합된 문장들을 순서대로 찾는다."""
    lines = state.get("lines") or {}
    chain: list[dict[str, Any]] = []
    current = item
    while True:
        following = indexed.by_no.get(current["no"] + 1)
        if not following or (lines.get(following["key"]) or {}).get("merged_into") != item["key"]:
            return chain
        chain.append(following)
        current = following


def _reevaluate_flags(state: dict[str, Any], indexed: Indexed) -> None:
    """옛 규칙으로 붙은 pace/style 표시를 현재 규칙으로 다시 매긴다."""
    lines = state.get("lines") or {}
    for item in indexed.sentences:
        stored = lines.get(item["key"])
        if not stored or stored.get("state") not in {"ok", "flagged"} or not stored.get("segments"):
            continue
        chain = _merge_chain(state, indexed, item)
        final_end = chain[-1]["end"] if chain else item["end"]
        sentence_secs = final_end - item["start"]
        flags = [f for f in stored.get("flags") or [] if f not in {"pace", "style"}]
        if any(_pace_exceeded(seg["text"], seg["end"] - seg["start"], sentence_secs)
               for seg in stored["segments"]):
            flags.append("pace")
        stored["flags"] = flags
        if not flags and stored.get("state") == "flagged":
            stored["state"] = "ok"
    if state.get("consistency_done"):
        _flag_style(state, indexed)
    state["flags_revision"] = FLAGS_REVISION


def _all_translated(state: dict[str, Any], indexed: Indexed) -> bool:
    return len(_line_states(state, indexed)) >= len(indexed.sentences)


def _apply_review(state: dict[str, Any], indexed: Indexed, text: str) -> dict[str, Any]:
    if not state.get("consistency_done"):
        # 옛 상태 파일 호환. 새 흐름에서는 translate 가 끝날 때 이미 돌았다.
        _run_consistency_checks(state, indexed)
    candidates = {item["no"]: item for item in _flagged_unreviewed(state, indexed)}
    responses = _parse_numbered_lines(text)
    accepted, rejected = [], []
    for number in sorted(set(responses) - set(candidates)):
        rejected.append({"line": number,
                         "reason": "재검토 대상이 아니다(이미 재검토됐거나 표시가 없다)"})
    for number, item in candidates.items():
        rest = responses.get(number)
        stored = state["lines"][item["key"]]
        if rest is None:
            continue  # 응답하지 않은 줄은 다음 패킷에 다시 나온다.
        chain = _merge_chain(state, indexed, item)
        ok, reason, flags = _validate_and_store_line(
            state, indexed, item, rest, merged=chain,
            extended_end=chain[-1]["end"] if chain else None)
        if ok:
            state["lines"][item["key"]]["reviewed"] = True
            accepted.append(number)
        else:
            # 재검토는 통과든 아니든 reviewed=true. 이전 번역은 남긴다.
            stored["reviewed"] = True
            if "review_parse_error" not in stored["flags"]:
                stored["flags"].append("review_parse_error")
            rejected.append({"line": number, "reason": reason})
    state["_last_result"] = {"accepted": accepted, "rejected": rejected}
    return state


def apply_response(bundle: Path, *, video_id: str, phase: str, fingerprint: str,
                   lang: str = "ko", text: str, viewer_url: str | None = None) -> dict[str, Any]:
    if lang not in SUPPORTED_LANGS:
        raise SubtitleError("lang 은 현재 ko 만 지원한다.")
    words = _load_words(bundle)
    indexed = index_sentences(bundle)
    if fingerprint != indexed.fingerprint:
        raise SubtitleError("전사가 바뀌었다. cueprecise_subtitle 을 다시 부르라.")

    def _mutate(state: dict[str, Any]) -> dict[str, Any]:
        if state["sentences_fingerprint"] != indexed.fingerprint:
            raise SubtitleError("전사가 바뀌었다. cueprecise_subtitle 을 다시 부르라.")
        current_phase = _current_phase(state, indexed)
        if current_phase != phase:
            raise SubtitleError(
                "phase 가 최신 상태(%s)와 다르다. cueprecise_subtitle 을 다시 불러 최신 패킷을 받으라."
                % current_phase)
        if phase == "terms":
            return _apply_terms(bundle, state, words, text)
        if phase == "translate":
            return _apply_translate(bundle, state, indexed, text)
        if phase == "review":
            return _apply_review(state, indexed, text)
        raise SubtitleError("phase %r 에는 응답을 보낼 수 없다." % phase)

    state = _mutate_state(bundle, lang, _mutate)
    result = state.pop("_last_result", {"accepted": [], "rejected": []})
    next_packet = build_packet(bundle, video_id=video_id, lang=lang, viewer_url=viewer_url)
    return {
        "video_id": video_id, "phase": phase, "accepted": result.get("accepted", []),
        "rejected": result.get("rejected", []), "note": result.get("note"),
        "progress": _progress(state, indexed),
        "next": next_packet,
    }


# ------------------------------------------------------------ 뷰어(2단계)

def viewer_payload(bundle: Path, lang: str = "ko") -> dict[str, Any]:
    """뷰어(HTML/JS)가 그대로 그릴 수 있는 JSON. 전사가 없으면 SubtitleError.

    번역이 아직 없어도(작업을 시작하지 않았어도) 동작한다 — 모든 문장이
    `pending`(영어 표시)으로 나간다. 전사가 바뀌어 키를 잃은 번역은 내보내지
    않는다(현재 문장 목록에 없는 키는 그냥 조회되지 않는다).
    """
    if lang not in SUPPORTED_LANGS:
        raise SubtitleError("lang 은 현재 ko 만 지원한다.")
    words = _load_words(bundle)
    if not words:
        raise SubtitleError("전사에 단어가 없다.")
    indexed = index_sentences(bundle)
    state = load_state(bundle, lang)
    title, _channel = _video_meta(bundle)
    video_id = str(bundle.name)

    lines_state = (state.get("lines") if state else None) or {}
    terms_state = (state.get("terms") if state else None) or []

    sentences_out = []
    for item in indexed.sentences:
        stored = lines_state.get(item["key"])
        words_triples = [[w["start"], w["end"], w["text"]] for w in item["words"]]
        merged_into = stored.get("merged_into") if stored else None
        if merged_into:
            # 문장이 중간에 끊겨 앞 문장 번역에 합쳐졌다(명세 16절). ko 는
            # 내보내지 않는다 — 목록에서 앞 문장과 묶어 보여주는 것은 프런트가
            # `merged_into` 로 판단한다.
            ko, segments, state_label = None, None, "ok"
            flags = stored.get("flags") or []
        elif stored and stored.get("state") == "untranslated":
            ko, segments, state_label = None, None, "untranslated"
            flags = stored.get("flags") or []
        elif stored and stored.get("ko"):
            ko = stored["ko"]
            segments = [{"text": seg["text"], "start": seg["start"], "end": seg["end"]}
                       for seg in stored.get("segments") or []]
            state_label = stored.get("state") or "ok"
            flags = stored.get("flags") or []
        else:
            ko, segments, state_label, flags = None, None, "pending", []
        sentences_out.append({
            "no": item["no"], "key": item["key"], "start": item["start"], "end": item["end"],
            "en": item["text"], "words": words_triples, "breaths": item["breaths"],
            "ko": ko, "segments": segments, "state": state_label, "flags": flags,
            "merged_into": merged_into,
        })

    terms_out = [{"src": t["src"], "tgt": t["tgt"], "short": t.get("short"),
                 "note": t.get("note"), "first_t": t.get("first_t"), "status": t["status"]}
                for t in terms_state]

    if state is not None:
        progress = _progress(state, indexed)
        complete = _current_phase(state, indexed) == "done"
        report = _report_data(state, indexed)
    else:
        progress = {"sentences": len(indexed.sentences), "translated": 0,
                   "flagged": 0, "untranslated": 0}
        complete = False
        report = {"corrections": [], "uncertain": [], "untranslated": [], "speed_over_ratio": 0.0,
                  "terms_count": 0, "terms_auto_skipped": 0, "warnings": []}

    return {
        "video_id": video_id, "title": title, "complete": complete, "progress": progress,
        "sentences": sentences_out, "terms": terms_out, "report": report,
    }
