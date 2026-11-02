"""AI provider configurations, stored on this machine only (file mode 600)."""
import json
import threading
import uuid
from pathlib import Path

from app.storage import write_text_atomic

PROVIDERS = ("anthropic", "openai", "gemini")


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) < 12:
        return "…"
    return f"{key[:3]}…{key[-4:]}"


class AISettings:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"providers": [], "default_id": None}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.path, json.dumps(data, ensure_ascii=False, indent=2))  # mkstemp → 0600

    def public(self) -> dict:
        """Settings safe to send to the browser: API keys replaced by a short hint."""
        data = self._load()
        return {
            "providers": [{"id": p["id"], "name": p["name"], "provider": p["provider"],
                           "model": p["model"], "key_hint": mask_key(p["api_key"])} for p in data["providers"]],
            "default_id": data["default_id"],
        }

    def get(self, provider_id: str) -> dict:
        for p in self._load()["providers"]:
            if p["id"] == provider_id:
                return p
        raise KeyError(provider_id)

    def default(self) -> dict | None:
        data = self._load()
        return next((p for p in data["providers"] if p["id"] == data["default_id"]), None)

    def add(self, *, name: str, provider: str, api_key: str, model: str) -> dict:
        entry = {"id": uuid.uuid4().hex[:12], "name": name.strip(), "provider": provider,
                 "api_key": api_key.strip(), "model": model.strip()}
        _validate(entry)
        with self.lock:
            data = self._load()
            data["providers"].append(entry)
            data["default_id"] = data["default_id"] or entry["id"]
            self._save(data)
        return entry

    def update(self, provider_id: str, *, name: str | None = None, model: str | None = None,
               api_key: str | None = None) -> dict:
        with self.lock:
            data = self._load()
            entry = next((p for p in data["providers"] if p["id"] == provider_id), None)
            if entry is None:
                raise KeyError(provider_id)
            if name is not None:
                entry["name"] = name.strip()
            if model is not None:
                entry["model"] = model.strip()
            if api_key and api_key.strip():  # blank keeps the stored key
                entry["api_key"] = api_key.strip()
            _validate(entry)
            self._save(data)
            return entry

    def delete(self, provider_id: str) -> None:
        with self.lock:
            data = self._load()
            data["providers"] = [p for p in data["providers"] if p["id"] != provider_id]
            if data["default_id"] == provider_id:
                data["default_id"] = data["providers"][0]["id"] if data["providers"] else None
            self._save(data)

    def set_default(self, provider_id: str) -> None:
        with self.lock:
            self.get(provider_id)
            data = self._load()
            data["default_id"] = provider_id
            self._save(data)


def _validate(entry: dict) -> None:
    if entry["provider"] not in PROVIDERS:
        raise ValueError(f"Nhà cung cấp không hỗ trợ: {entry['provider']}")
    for field, label in (("name", "Tên"), ("api_key", "API key"), ("model", "Model")):
        if not entry[field]:
            raise ValueError(f"{label} không được trống")
