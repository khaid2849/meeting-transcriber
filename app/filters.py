"""Pure helpers around Whisper: group VAD speech regions into chunks, strip hallucinations."""
import re
import unicodedata

OUTRO_PATTERNS = ("subscribe", "ghiền mì gõ", "đăng ký kênh", "like và share",
                  "cảm ơn các bạn đã theo dõi", "hẹn gặp lại các bạn trong")
OUTRO_MAX_WORDS = 25
REPEAT_RUN = 3  # identical consecutive segments at or above this count collapse to one
LOOP_MIN = 4  # an n-gram repeated this many times in a row collapses to one
LOOP_MAX_N = 4
MAX_WORDS_PER_SEC = 10  # faster than any human speaker; Whisper invents these at chunk edges
FAST_MIN_WORDS = 5


def speech_chunks(regions: list[tuple[float, float]], max_len: float, pad: float = 0.2,
                  total: float | None = None, max_gap: float = 1.5) -> list[tuple[float, float]]:
    """Merge nearby speech regions into chunks no longer than max_len (before padding)."""
    pieces: list[tuple[float, float]] = []
    for start, end in regions:
        while end - start > max_len:
            pieces.append((start, start + max_len))
            start += max_len
        pieces.append((start, end))

    chunks: list[list[float]] = []
    for start, end in pieces:
        if chunks and start - chunks[-1][1] <= max_gap and end - chunks[-1][0] <= max_len:
            chunks[-1][1] = end
        else:
            chunks.append([start, end])

    upper = total if total is not None else float("inf")
    return [(max(0.0, s - pad), min(upper, e + pad)) for s, e in chunks]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _collapse_loops(tokens: list[str]) -> list[int]:
    """Return indexes of tokens to keep after collapsing repeated n-gram loops."""
    keys = [_norm(t) for t in tokens]
    keep: list[int] = []
    i = 0
    while i < len(keys):
        for n in range(1, LOOP_MAX_N + 1):
            gram = keys[i:i + n]
            count = 1
            while keys[i + count * n:i + (count + 1) * n] == gram:
                count += 1
            if count >= LOOP_MIN:
                keep.extend(range(i, i + n))
                i += n * count
                break
        else:
            keep.append(i)
            i += 1
    return keep


def _clean_one(seg: dict) -> dict | None:
    norm = _norm(seg["text"])
    if not norm:
        return None
    if any(p in norm for p in OUTRO_PATTERNS) and len(norm.split()) <= OUTRO_MAX_WORDS:
        return None
    n_words = len(norm.split())
    duration = seg["end"] - seg["start"]
    if n_words >= FAST_MIN_WORDS and n_words > MAX_WORDS_PER_SEC * max(duration, 0.01):
        return None
    if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0:
        return None

    words = seg.get("words") or []
    tokens = [w["word"] for w in words] if words else seg["text"].split()
    keep = _collapse_loops(tokens)
    if len(keep) == len(tokens):
        return seg
    seg = dict(seg)
    if words:
        seg["words"] = [words[i] for i in keep]
        seg["text"] = "".join(w["word"] for w in seg["words"])
    else:
        seg["text"] = " " + " ".join(tokens[i] for i in keep)
    return seg


def clean_segments(segments: list[dict]) -> list[dict]:
    cleaned = [c for c in (_clean_one(s) for s in segments) if c]
    out: list[dict] = []
    i = 0
    while i < len(cleaned):
        j = i
        while j + 1 < len(cleaned) and _norm(cleaned[j + 1]["text"]) == _norm(cleaned[i]["text"]):
            j += 1
        run = cleaned[i:j + 1]
        out.extend(run[:1] if len(run) >= REPEAT_RUN else run)
        i = j + 1
    return out
