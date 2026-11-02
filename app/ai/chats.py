"""Saved AI conversations: one JSON file per chat under data/chats/."""
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.storage import write_text_atomic

ID_RE = re.compile(r"^[a-f0-9]{12}$")
DEFAULT_TITLE = "Cuộc trò chuyện mới"
TITLE_MAX = 60


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


class ChatStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, chat_id: str) -> Path:
        if not ID_RE.match(chat_id or ""):
            raise KeyError(chat_id)
        return self.root / f"{chat_id}.json"

    def _save(self, chat: dict) -> None:
        write_text_atomic(self._path(chat["id"]), json.dumps(chat, ensure_ascii=False, indent=2))

    def get(self, chat_id: str) -> dict:
        path = self._path(chat_id)
        if not path.exists():
            raise KeyError(chat_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def create(self, *, meeting_ids: list[str], provider_id: str | None) -> dict:
        now = _now()
        chat = {"id": uuid.uuid4().hex[:12], "title": DEFAULT_TITLE, "meeting_ids": list(meeting_ids),
                "provider_id": provider_id, "created_at": now, "updated_at": now, "messages": []}
        self._save(chat)
        return chat

    def update(self, chat_id: str, **changes) -> dict:
        allowed = {"title", "meeting_ids", "provider_id"}
        with self.lock:
            chat = self.get(chat_id)
            for key, value in changes.items():
                if key not in allowed:
                    raise AttributeError(key)
                chat[key] = value
            chat["updated_at"] = _now()
            self._save(chat)
            return chat

    def append(self, chat_id: str, message: dict) -> dict:
        with self.lock:
            chat = self.get(chat_id)
            chat["messages"].append({**message, "created_at": _now()})
            if chat["title"] == DEFAULT_TITLE and message["role"] == "user":
                text = " ".join(message["content"].split())
                chat["title"] = text if len(text) <= TITLE_MAX else text[:TITLE_MAX].rstrip() + "…"
            chat["updated_at"] = _now()
            self._save(chat)
            return chat

    def delete(self, chat_id: str) -> None:
        with self.lock:  # an append in flight must not recreate the file afterwards
            self.get(chat_id)
            self._path(chat_id).unlink()

    def list(self) -> list[dict]:
        out = []
        for path in self.root.glob("*.json"):
            try:
                chat = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            summary = {k: v for k, v in chat.items() if k != "messages"}
            summary["message_count"] = len(chat.get("messages", []))
            out.append(summary)
        return sorted(out, key=lambda c: (c["updated_at"], c["created_at"]), reverse=True)
