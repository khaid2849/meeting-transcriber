"""Turn cleaned Whisper segments into transcript paragraphs and Markdown."""
from app.config import PARAGRAPH_MAX_GAP, PARAGRAPH_MAX_LEN
from app.storage import Meeting


def to_utterances(segments: list[dict]) -> list[dict]:
    out = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in segments]
    # Whisper can emit out-of-order timestamps on noisy audio.
    return sorted((u for u in out if u["text"]), key=lambda u: u["start"])


def group_paragraphs(utterances: list[dict], max_gap: float, max_len: float) -> list[dict]:
    paragraphs: list[dict] = []
    for u in utterances:
        last = paragraphs[-1] if paragraphs else None
        if last and u["start"] - last["end"] <= max_gap and u["start"] - last["start"] < max_len:
            last["end"] = u["end"]
            last["text"] += " " + u["text"]
        else:
            paragraphs.append({"start": u["start"], "end": u["end"], "text": u["text"]})
    return paragraphs


def format_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def render_markdown(meeting: Meeting, utterances: list[dict]) -> str:
    info = [f"Ngày: {meeting.date}"]
    if meeting.duration:
        info.append(f"Thời lượng: {format_ts(meeting.duration)}")
    lines = [f"# {meeting.title}", " · ".join(info), ""]
    for p in group_paragraphs(utterances, PARAGRAPH_MAX_GAP, PARAGRAPH_MAX_LEN):
        lines += [f"**[{format_ts(p['start'])}]** {p['text']}", ""]
    return "\n".join(lines)
