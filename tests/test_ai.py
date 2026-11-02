import os
import stat

import pytest

from app.ai.chats import ChatStore
from app.ai.prompting import build_system_prompt, estimate_tokens
from app.ai.settings import AISettings, mask_key


# ---------- settings ----------

def test_mask_key():
    assert mask_key("sk-ant-api03-abcdefghijkl1234") == "sk-…1234"
    assert mask_key("short") == "…"
    assert mask_key("") == ""


def test_add_list_never_exposes_key(tmp_path):
    s = AISettings(tmp_path / "ai.json")
    p = s.add(name="Claude", provider="anthropic", api_key="sk-ant-secret-9999", model="claude-opus-5-5")
    public = s.public()
    assert public["default_id"] == p["id"]  # first one becomes default
    assert public["providers"] == [{"id": p["id"], "name": "Claude", "provider": "anthropic",
                                    "model": "claude-opus-5-5", "key_hint": "sk-…9999"}]
    assert "secret" not in str(public)


def test_settings_file_is_private_and_persists(tmp_path):
    path = tmp_path / "ai.json"
    s = AISettings(path)
    p = s.add(name="GPT", provider="openai", api_key="sk-openai-1234", model="gpt-x")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert AISettings(path).get(p["id"])["api_key"] == "sk-openai-1234"


def test_update_keeps_key_when_blank(tmp_path):
    s = AISettings(tmp_path / "ai.json")
    p = s.add(name="G", provider="gemini", api_key="AIza-old-1111", model="gemini-a")
    s.update(p["id"], name="Gemini", model="gemini-b", api_key="")
    got = s.get(p["id"])
    assert (got["name"], got["model"], got["api_key"]) == ("Gemini", "gemini-b", "AIza-old-1111")
    s.update(p["id"], api_key="AIza-new-2222")
    assert s.get(p["id"])["api_key"] == "AIza-new-2222"


def test_rejects_unknown_provider_and_missing_fields(tmp_path):
    s = AISettings(tmp_path / "ai.json")
    with pytest.raises(ValueError):
        s.add(name="X", provider="mistral", api_key="k", model="m")
    with pytest.raises(ValueError):
        s.add(name="X", provider="openai", api_key="", model="m")
    with pytest.raises(ValueError):
        s.add(name="", provider="openai", api_key="k", model="m")


def test_delete_moves_default(tmp_path):
    s = AISettings(tmp_path / "ai.json")
    a = s.add(name="A", provider="openai", api_key="k1", model="m")
    b = s.add(name="B", provider="openai", api_key="k2", model="m")
    s.set_default(b["id"])
    assert s.public()["default_id"] == b["id"]
    s.delete(b["id"])
    assert s.public()["default_id"] == a["id"]
    s.delete(a["id"])
    assert s.public() == {"providers": [], "default_id": None}


def test_get_unknown_raises_keyerror(tmp_path):
    with pytest.raises(KeyError):
        AISettings(tmp_path / "ai.json").get("nope")


# ---------- chats ----------

def test_chat_create_append_and_list(tmp_path):
    c = ChatStore(tmp_path)
    chat = c.create(meeting_ids=["2026-11-02-a"], provider_id="p1")
    assert chat["title"] == "Cuộc trò chuyện mới" and chat["messages"] == []
    c.append(chat["id"], {"role": "user", "content": "Tóm tắt cuộc họp giúp tôi về hạ tầng GPU và chi phí"})
    got = c.get(chat["id"])
    assert got["title"] == "Tóm tắt cuộc họp giúp tôi về hạ tầng GPU và chi phí"
    assert got["messages"][0]["role"] == "user" and got["messages"][0]["created_at"]
    listing = c.list()
    assert [x["id"] for x in listing] == [chat["id"]]
    assert listing[0]["message_count"] == 1 and "messages" not in listing[0]


def test_chat_title_truncated_and_kept_after_first_question(tmp_path):
    c = ChatStore(tmp_path)
    chat = c.create(meeting_ids=[], provider_id=None)
    c.append(chat["id"], {"role": "user", "content": "a" * 200})
    c.append(chat["id"], {"role": "user", "content": "câu thứ hai"})
    title = c.get(chat["id"])["title"]
    assert len(title) <= 61 and title.endswith("…")


def test_chat_update_delete_and_traversal(tmp_path):
    c = ChatStore(tmp_path)
    chat = c.create(meeting_ids=["m1"], provider_id=None)
    c.update(chat["id"], title="Đổi tên", meeting_ids=["m1", "m2"], provider_id="p2")
    got = c.get(chat["id"])
    assert (got["title"], got["meeting_ids"], got["provider_id"]) == ("Đổi tên", ["m1", "m2"], "p2")
    with pytest.raises(KeyError):
        c.get("../../etc/passwd")
    c.delete(chat["id"])
    with pytest.raises(KeyError):
        c.get(chat["id"])


def test_chat_list_newest_first(tmp_path):
    c = ChatStore(tmp_path)
    a = c.create(meeting_ids=[], provider_id=None)
    b = c.create(meeting_ids=[], provider_id=None)
    c.append(a["id"], {"role": "user", "content": "x"})
    assert [x["id"] for x in c.list()] == [a["id"], b["id"]]


# ---------- prompting ----------

MEETINGS = [
    {"id": "2026-11-02-ha-tang", "title": "Hạ tầng AI", "date": "2026-11-02",
     "paragraphs": [{"start": 5, "text": "Nên dựng server một card."}, {"start": 701.4, "text": "Chọn card inference."}]},
    {"id": "2026-10-28-thau", "title": "Chấm thầu", "date": "2026-10-28",
     "paragraphs": [{"start": 3725, "text": "Tiêu chí chấm điểm."}]},
]


def test_system_prompt_labels_meetings_and_timestamps():
    prompt, labels = build_system_prompt(MEETINGS)
    assert labels == {"M1": "2026-11-02-ha-tang", "M2": "2026-10-28-thau"}
    assert '<meeting id="M1" title="Hạ tầng AI" date="2026-11-02">' in prompt
    assert "[00:11:41] Chọn card inference." in prompt
    assert "[01:02:05] Tiêu chí chấm điểm." in prompt
    assert "[M1 00:11:41]" in prompt  # citation format is explained
    assert "tiếng Việt" in prompt


def test_system_prompt_escapes_meeting_title_quotes():
    prompt, _ = build_system_prompt([{**MEETINGS[0], "title": 'Họp "gấp" <A>'}])
    assert 'title="Họp &quot;gấp&quot; &lt;A&gt;"' in prompt


def test_system_prompt_without_meetings_says_so():
    prompt, labels = build_system_prompt([])
    assert labels == {} and "Không có bản ghi" in prompt


def test_estimate_tokens_roughly_chars_over_three():
    assert estimate_tokens("a" * 300) == 100
    assert estimate_tokens("") == 0

