"""On-disk meeting store: one folder per meeting, metadata in meeting.json."""
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import unicodedata
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
ID_RE = re.compile(r"^[a-z0-9-]+$")
MAX_SLUG = 60


@dataclass
class Meeting:
    id: str
    title: str
    date: str
    status: str = "queued"  # queued|downloading|converting|transcribing|done|error
    progress: float = 0.0
    error: str | None = None
    keywords: str = ""
    duration: float | None = None
    model: str | None = None
    original_name: str = ""
    created_at: str = ""
    source_url: str | None = None  # YouTube link the audio was downloaded from
    language: str = "vi"  # spoken language chosen when adding: vi | en | auto
    detected_language: str | None = None  # what Whisper found when language is "auto"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Meeting":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def slugify(title: str, date: str) -> str:
    text = unicodedata.normalize("NFD", title.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:MAX_SLUG].strip("-")
    return f"{date}-{text or 'cuoc-hop'}"


def write_text_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _write_atomic(path: Path, data: Any) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


class MeetingStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()  # guards meeting.json + transcript.md rewrites

    def dir(self, meeting_id: str) -> Path:
        if not ID_RE.match(meeting_id or ""):
            raise KeyError(meeting_id)
        return self.root / meeting_id

    def create(self, *, title: str, date: str, keywords: str, original_name: str,
               source_url: str | None = None, duration: float | None = None, language: str = "vi") -> Meeting:
        base = slugify(title, date)
        meeting_id, n = base, 1
        while (self.root / meeting_id).exists():
            n += 1
            meeting_id = f"{base}-{n}"
        (self.root / meeting_id).mkdir()
        meeting = Meeting(id=meeting_id, title=title, date=date, keywords=keywords,
                          original_name=original_name, source_url=source_url, duration=duration, language=language,
                          created_at=datetime.now().isoformat(timespec="microseconds"))
        self.save(meeting)
        return meeting

    def get(self, meeting_id: str) -> Meeting:
        path = self.dir(meeting_id) / "meeting.json"
        if not path.exists():
            raise KeyError(meeting_id)
        return Meeting.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, meeting: Meeting) -> None:
        _write_atomic(self.dir(meeting.id) / "meeting.json", meeting.to_dict())

    def update(self, meeting_id: str, **changes: Any) -> Meeting:
        """Re-read, apply changes and save under a lock, so the worker and API never clobber each other."""
        with self.lock:
            meeting = self.get(meeting_id)
            for key, value in changes.items():
                if not hasattr(meeting, key):
                    raise AttributeError(key)
                setattr(meeting, key, value)
            self.save(meeting)
            return meeting

    def list(self) -> list[Meeting]:
        meetings = []
        for p in self.root.iterdir():
            if not (p.is_dir() and ID_RE.match(p.name) and (p / "meeting.json").exists()):
                continue
            try:
                meetings.append(self.get(p.name))
            except (KeyError, ValueError, TypeError):
                log.exception("skipping unreadable meeting %s", p.name)
        return sorted(meetings, key=lambda m: (m.date, m.created_at), reverse=True)

    def delete(self, meeting_id: str) -> None:
        self.get(meeting_id)
        shutil.rmtree(self.dir(meeting_id))

    def read_json(self, meeting_id: str, name: str) -> Any:
        return json.loads((self.dir(meeting_id) / name).read_text(encoding="utf-8"))

    def write_json(self, meeting_id: str, name: str, data: Any) -> None:
        _write_atomic(self.dir(meeting_id) / name, data)
