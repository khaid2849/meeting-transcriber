from app.ai import providers
from app.ai.providers import (anthropic_request, chat_history, friendly_error, gemini_contents,
                              keep_chat_models, openai_messages)

SYSTEM = "Bạn là trợ lý."
HISTORY = [{"role": "user", "content": "Tóm tắt"}, {"role": "assistant", "content": "Đây là tóm tắt"},
           {"role": "user", "content": "Còn gì nữa?"}]


def test_chat_history_drops_errors_empty_and_system_entries():
    raw = [{"role": "user", "content": "a", "created_at": "x"},
           {"role": "assistant", "content": "", "error": "boom"},
           {"role": "assistant", "content": "partial", "error": "cut"},
           {"role": "user", "content": "b"},
           {"role": "assistant", "content": "ok", "provider": "Claude"}]
    assert chat_history(raw) == [{"role": "user", "content": "a"}, {"role": "user", "content": "b"},
                                 {"role": "assistant", "content": "ok"}]


def test_anthropic_request_caches_transcripts_and_enables_fallback_on_new_models():
    req = anthropic_request("claude-opus-5-5", SYSTEM, HISTORY, max_tokens=64000)
    assert req["system"] == [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
    assert req["messages"] == HISTORY and req["max_tokens"] == 64000
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in req and "temperature" not in req


def test_anthropic_request_no_fallback_on_other_models():
    req = anthropic_request("claude-haiku-4-5", SYSTEM, HISTORY, max_tokens=8000)
    assert "fallbacks" not in req and "betas" not in req


def test_openai_messages_prepend_system():
    assert openai_messages(SYSTEM, HISTORY) == [{"role": "system", "content": SYSTEM}] + HISTORY


def test_gemini_contents_map_assistant_to_model():
    contents = gemini_contents(HISTORY)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].text == "Đây là tóm tắt"


def test_keep_chat_models_filters_non_chat_ids():
    ids = ["gpt-5.1", "text-embedding-3-large", "gpt-4o-audio-preview", "o3", "dall-e-3",
           "gpt-image-1", "whisper-1", "tts-1", "gpt-4o-realtime", "chatgpt-4o-latest", "o4-mini",
           "gpt-5-pro", "gpt-5-codex", "o3-pro"]
    assert keep_chat_models("openai", ids) == ["chatgpt-4o-latest", "gpt-5.1", "o3", "o4-mini"]
    assert keep_chat_models("gemini", ["gemini-3-pro", "embedding-001", "imagen-4"]) == ["gemini-3-pro"]
    assert keep_chat_models("anthropic", ["claude-opus-5-5", "claude-haiku-5-5"]) == ["claude-haiku-5-5", "claude-opus-5-5"]


class _Err(Exception):
    def __init__(self, status, msg="raw"):
        super().__init__(msg)
        self.status_code = status


def test_friendly_error_maps_status_codes():
    assert friendly_error(_Err(401)) == "@ai.auth"
    assert friendly_error(_Err(429)) == "@ai.rate_limit"
    assert friendly_error(_Err(404)) == "@ai.model_not_found"
    assert friendly_error(_Err(503)) == '@ai.server:{"status": 503}'
    assert friendly_error(_Err(400, "raw detail")) == '@ai.other:{"detail": "raw detail"}'
    assert friendly_error(ConnectionError("x")) == "@ai.network"


def test_stream_reply_dispatches_by_provider(monkeypatch):
    seen = {}

    def fake(provider_name):
        def run(cfg, system, history):
            seen["call"] = (provider_name, cfg["model"], system, history)
            yield "xin "
            yield "chào"
        return run

    monkeypatch.setitem(providers.STREAMERS, "openai", fake("openai"))
    out = list(providers.stream_reply({"provider": "openai", "model": "gpt-x", "api_key": "k"}, SYSTEM, HISTORY))
    assert out == ["xin ", "chào"] and seen["call"][0:2] == ("openai", "gpt-x")


def test_openai_client_kwargs_for_compatible_providers():
    from app.ai.providers import openai_client_kwargs
    assert openai_client_kwargs({"provider": "openai", "api_key": "sk"}) == {"api_key": "sk"}
    assert openai_client_kwargs({"provider": "openai_compat", "api_key": "", "base_url": "http://localhost:11434/v1"}) == {
        "api_key": "not-needed", "base_url": "http://localhost:11434/v1"}


def test_keep_chat_models_compatible_keeps_everything():
    assert keep_chat_models("openai_compat", ["qwen-max", "deepseek-chat", "qwen-max"]) == ["deepseek-chat", "qwen-max"]


def test_compatible_provider_streams_through_openai_path(monkeypatch):
    seen = []
    monkeypatch.setitem(providers.STREAMERS, "openai_compat", lambda cfg, s, h: iter(seen.append(cfg) or ["ok"]))
    cfg = {"provider": "openai_compat", "model": "qwen-max", "api_key": "", "base_url": "http://x/v1"}
    assert list(providers.stream_reply(cfg, "s", [])) == ["ok"] and seen == [cfg]
