import json

import pytest
from fastapi.testclient import TestClient

from app.ai import routes
from app.ai.chats import ChatStore
from app.ai.providers import ProviderError
from app.ai.settings import AISettings
from app.main import create_app
from app.storage import MeetingStore


class NoWorker:
    def enqueue(self, meeting_id):
        pass


@pytest.fixture
def stores(tmp_path):
    meetings = MeetingStore(tmp_path / "meetings")
    m = meetings.create(title="Hạ tầng AI", date="2026-11-02", keywords="", original_name="a.wav")
    meetings.write_json(m.id, "segments.json", [{"start": 701.0, "end": 705.0, "text": "Chọn card inference."}])
    meetings.update(m.id, status="done")
    pending = meetings.create(title="Đang xử lý", date="2026-11-03", keywords="", original_name="b.wav")
    return meetings, AISettings(tmp_path / "data" / "ai.json"), ChatStore(tmp_path / "data" / "chats"), m.id, pending.id


@pytest.fixture
def client(stores):
    meetings, settings, chats, _, _ = stores
    app = create_app(meetings, NoWorker(), allowed_hosts=("testserver",), ai_settings=settings, chats=chats)
    return TestClient(app)


def add_provider(client, **kw):
    body = {"name": "Claude", "provider": "anthropic", "api_key": "sk-ant-secret-key-9999", "model": "claude-opus-5-5"}
    body.update(kw)
    return client.post("/api/ai/providers", json=body)


def ndjson(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


# ---------- providers ----------

def test_provider_crud_never_returns_key(client):
    r = add_provider(client)
    assert r.status_code == 201 and r.json()["key_hint"] == "sk-…9999"
    pid = r.json()["id"]
    listing = client.get("/api/ai/providers").json()
    assert listing["default_id"] == pid and "secret" not in json.dumps(listing)

    r = client.patch(f"/api/ai/providers/{pid}", json={"name": "Claude Opus", "api_key": ""})
    assert r.status_code == 200 and r.json()["key_hint"] == "sk-…9999" and r.json()["name"] == "Claude Opus"

    second = add_provider(client, name="GPT", provider="openai", api_key="sk-openai-xxxx-1111", model="gpt-5").json()
    assert client.post("/api/ai/default", json={"id": second["id"]}).status_code == 200
    assert client.get("/api/ai/providers").json()["default_id"] == second["id"]

    assert client.delete(f"/api/ai/providers/{second['id']}").status_code == 204
    assert client.get("/api/ai/providers").json()["default_id"] == pid


def test_provider_validation(client):
    assert add_provider(client, provider="mistral").status_code == 422
    assert add_provider(client, api_key="  ").status_code == 422
    assert client.patch("/api/ai/providers/nope", json={"name": "x"}).status_code == 404


def test_list_models_with_new_or_stored_key(client, monkeypatch):
    calls = []
    monkeypatch.setattr(routes, "list_models", lambda provider, key: calls.append((provider, key)) or ["m-a", "m-b"])
    r = client.post("/api/ai/models", json={"provider": "openai", "api_key": "sk-new"})
    assert r.json() == {"models": ["m-a", "m-b"]}
    pid = add_provider(client).json()["id"]
    client.post("/api/ai/models", json={"provider_id": pid})
    assert calls == [("openai", "sk-new"), ("anthropic", "sk-ant-secret-key-9999")]


def test_list_models_error_is_readable(client, monkeypatch):
    def boom(provider, key):
        raise ProviderError("API key không hợp lệ")
    monkeypatch.setattr(routes, "list_models", boom)
    r = client.post("/api/ai/models", json={"provider": "gemini", "api_key": "bad"})
    assert r.status_code == 400 and r.json()["detail"] == "API key không hợp lệ"


# ---------- chats ----------

def test_create_chat_uses_default_provider_and_describes_scope(client, stores):
    _, _, _, done_id, pending_id = stores
    pid = add_provider(client).json()["id"]
    chat = client.post("/api/chats", json={"meeting_ids": [done_id, pending_id, "2026-01-01-da-xoa"]}).json()
    assert chat["provider_id"] == pid
    full = client.get(f"/api/chats/{chat['id']}").json()
    assert [(m["id"], m["label"], m["usable"]) for m in full["meetings"]] == [
        (done_id, "M1", True), (pending_id, None, False), ("2026-01-01-da-xoa", None, False)]
    assert full["meetings"][2]["title"] is None
    assert full["labels"] == {"M1": done_id}
    assert full["estimated_tokens"] > 0


def test_send_message_streams_and_saves(client, stores, monkeypatch):
    _, _, chats, done_id, _ = stores
    add_provider(client)
    seen = {}

    def fake_stream(cfg, system, history):
        seen.update(cfg=cfg, system=system, history=history)
        yield "Nên chọn "
        yield "card inference [M1 00:11:41]."

    monkeypatch.setattr(routes, "stream_reply", fake_stream)
    chat_id = client.post("/api/chats", json={"meeting_ids": [done_id]}).json()["id"]
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "Nên chọn card gì?"})
    assert r.status_code == 200
    assert ndjson(r) == [{"delta": "Nên chọn "}, {"delta": "card inference [M1 00:11:41]."}, {"done": True}]
    assert "[00:11:41] Chọn card inference." in seen["system"]
    assert seen["history"] == [{"role": "user", "content": "Nên chọn card gì?"}]
    assert seen["cfg"]["api_key"] == "sk-ant-secret-key-9999"
    saved = chats.get(chat_id)["messages"]
    assert [(m["role"], m["content"]) for m in saved] == [
        ("user", "Nên chọn card gì?"), ("assistant", "Nên chọn card inference [M1 00:11:41].")]
    assert saved[1]["provider"] == "Claude" and saved[1]["model"] == "claude-opus-5-5"
    assert saved[1]["labels"] == {"M1": done_id}
    assert chats.get(chat_id)["title"] == "Nên chọn card gì?"


def test_follow_up_sends_previous_turns(client, stores, monkeypatch):
    _, _, _, done_id, _ = stores
    add_provider(client)
    histories = []

    def fake_stream(cfg, system, history):
        histories.append(history)
        yield "ok"

    monkeypatch.setattr(routes, "stream_reply", fake_stream)
    chat_id = client.post("/api/chats", json={"meeting_ids": [done_id]}).json()["id"]
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "Câu 1"})
    client.post(f"/api/chats/{chat_id}/messages", json={"content": "Câu 2"})
    assert histories[1] == [{"role": "user", "content": "Câu 1"}, {"role": "assistant", "content": "ok"},
                            {"role": "user", "content": "Câu 2"}]


def test_provider_error_mid_stream_is_saved_with_partial_text(client, stores, monkeypatch):
    _, _, chats, done_id, _ = stores
    add_provider(client)

    def failing(cfg, system, history):
        yield "Một phần"
        raise ProviderError("Bị giới hạn tốc độ")

    monkeypatch.setattr(routes, "stream_reply", failing)
    chat_id = client.post("/api/chats", json={"meeting_ids": [done_id]}).json()["id"]
    lines = ndjson(client.post(f"/api/chats/{chat_id}/messages", json={"content": "Hỏi"}))
    assert lines == [{"delta": "Một phần"}, {"error": "Bị giới hạn tốc độ"}]
    last = chats.get(chat_id)["messages"][-1]
    assert (last["role"], last["content"], last["error"]) == ("assistant", "Một phần", "Bị giới hạn tốc độ")


def test_send_without_any_provider_is_rejected(client, stores):
    _, _, _, done_id, _ = stores
    chat_id = client.post("/api/chats", json={"meeting_ids": [done_id]}).json()["id"]
    r = client.post(f"/api/chats/{chat_id}/messages", json={"content": "Hỏi"})
    assert r.status_code == 400 and "cấu hình" in r.json()["detail"]


def test_send_rejects_empty_question(client, stores):
    _, _, _, done_id, _ = stores
    add_provider(client)
    chat_id = client.post("/api/chats", json={"meeting_ids": [done_id]}).json()["id"]
    assert client.post(f"/api/chats/{chat_id}/messages", json={"content": "   "}).status_code == 422


def test_chat_patch_list_delete(client, stores):
    _, _, _, done_id, _ = stores
    chat_id = client.post("/api/chats", json={"meeting_ids": []}).json()["id"]
    r = client.patch(f"/api/chats/{chat_id}", json={"title": "GPU", "meeting_ids": [done_id]})
    assert r.status_code == 200 and r.json()["meeting_ids"] == [done_id]
    assert [c["title"] for c in client.get("/api/chats").json()] == ["GPU"]
    assert client.delete(f"/api/chats/{chat_id}").status_code == 204
    assert client.get(f"/api/chats/{chat_id}").status_code == 404


def test_chat_url_serves_app_shell(client):
    chat_id = client.post("/api/chats", json={"meeting_ids": []}).json()["id"]
    assert client.get(f"/c/{chat_id}").text == client.get("/").text
    assert client.get("/c/000000000000").status_code == 404
