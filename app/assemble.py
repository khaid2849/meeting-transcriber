"""Turn Whisper segments + diarization turns into speaker utterances and transcript Markdown."""
from app.config import PARAGRAPH_MAX_GAP, PARAGRAPH_MAX_LEN
from app.storage import Meeting

Turn = tuple[float, float, int]


def _speaker_for(start: float, end: float, turns: list[Turn]) -> str:
    def overlap(t: Turn) -> float:
        return min(end, t[1]) - max(start, t[0])

    def distance(t: Turn) -> float:
        return max(0.0, t[0] - end, start - t[1])

    best = max(turns, key=overlap)
    if overlap(best) <= 0:
        best = min(turns, key=distance)
    return f"S{best[2] + 1}"


def assign_speakers(segments: list[dict], turns: list[Turn] | None) -> list[dict]:
    """Return utterances [{start, end, speaker, text}]; a segment is split where the speaker changes."""
    out: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        if not turns:
            out.append({"start": seg["start"], "end": seg["end"], "speaker": None,
                        "text": seg["text"].strip()})
            continue
        if not words:
            out.append({"start": seg["start"], "end": seg["end"],
                        "speaker": _speaker_for(seg["start"], seg["end"], turns),
                        "text": seg["text"].strip()})
            continue
        current: dict | None = None
        for word in words:
            speaker = _speaker_for(word["start"], word["end"], turns)
            if current and current["speaker"] == speaker:
                current["end"] = word["end"]
                current["text"] += word["word"]
            else:
                current = {"start": word["start"], "end": word["end"], "speaker": speaker,
                           "text": word["word"]}
                out.append(current)
    for u in out:
        u["text"] = u["text"].strip()
    return [u for u in out if u["text"]]


def speaker_label(speaker: str | None, names: dict[str, str]) -> str | None:
    if speaker is None:
        return None
    name = (names.get(speaker) or "").strip()
    return name or f"Người {speaker[1:]}"


def group_paragraphs(utterances: list[dict], max_gap: float, max_len: float) -> list[dict]:
    paragraphs: list[dict] = []
    for u in utterances:
        last = paragraphs[-1] if paragraphs else None
        if (last and last["speaker"] == u["speaker"] and u["start"] - last["end"] <= max_gap
                and u["start"] - last["start"] < max_len):
            last["end"] = u["end"]
            last["text"] += " " + u["text"]
        else:
            paragraphs.append(dict(u))
    return paragraphs


def format_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def render_markdown(meeting: Meeting, utterances: list[dict]) -> str:
    paragraphs = group_paragraphs(utterances, PARAGRAPH_MAX_GAP, PARAGRAPH_MAX_LEN)
    speakers = list(dict.fromkeys(p["speaker"] for p in paragraphs if p["speaker"]))
    info = [f"Ngày: {meeting.date}"]
    if meeting.duration:
        info.append(f"Thời lượng: {format_ts(meeting.duration)}")
    if speakers:
        info.append("Người nói: " + ", ".join(speaker_label(s, meeting.speakers) for s in speakers))
    lines = [f"# {meeting.title}", " · ".join(info), ""]
    for p in paragraphs:
        label = speaker_label(p["speaker"], meeting.speakers)
        head = f"**[{format_ts(p['start'])}] {label}:**" if label else f"**[{format_ts(p['start'])}]**"
        lines += [f"{head} {p['text']}", ""]
    return "\n".join(lines)
