"""Streaming chat and model listing for Claude, OpenAI and Gemini behind one interface."""
from collections.abc import Callable, Iterator

# Models that accept Anthropic's server-side refusal fallback ("default" routing).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_OUTPUT = 64000

OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
OPENAI_EXCLUDE = ("audio", "realtime", "tts", "transcribe", "image", "embedding", "search",
                  "instruct", "moderation", "dall-e", "whisper")


class ProviderError(Exception):
    """A provider call failed; the message is already user-facing (Vietnamese)."""


def chat_history(messages: list[dict]) -> list[dict]:
    """Saved chat messages → plain role/content turns, skipping failed or empty answers."""
    return [{"role": m["role"], "content": m["content"]} for m in messages
            if m["role"] in ("user", "assistant") and m.get("content") and not m.get("error")]


def anthropic_request(model: str, system: str, history: list[dict], max_tokens: int) -> dict:
    req = {
        "model": model,
        "max_tokens": max_tokens,
        # The transcripts dominate the prompt; caching them makes follow-up questions cheap.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": history,
    }
    if model in FALLBACK_MODELS:
        req["betas"] = [FALLBACK_BETA]
        req["fallbacks"] = "default"
    return req


def openai_messages(system: str, history: list[dict]) -> list[dict]:
    return [{"role": "system", "content": system}] + history


def gemini_contents(history: list[dict]):
    from google.genai import types
    return [types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=m["content"])])
            for m in history]


def keep_chat_models(provider: str, ids: list[str]) -> list[str]:
    if provider == "openai":
        keep = [i for i in ids if i.startswith(OPENAI_PREFIXES) and not any(x in i for x in OPENAI_EXCLUDE)]
    elif provider == "gemini":
        keep = [i for i in ids if "gemini" in i and not any(x in i for x in ("embedding", "imagen", "tts", "image"))]
    else:
        keep = [i for i in ids if i.startswith("claude")]
    return sorted(set(keep))


def friendly_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    detail = getattr(exc, "message", None) or str(exc)
    if status in (401, 403):
        return "API key không hợp lệ hoặc không có quyền dùng model này."
    if status == 404:
        return "Model không tồn tại hoặc tài khoản không dùng được model này."
    if status == 429:
        return "Bị giới hạn tốc độ hoặc đã hết hạn mức của nhà cung cấp. Thử lại sau."
    if isinstance(status, int) and status >= 500:
        return f"Nhà cung cấp đang lỗi ({status}). Thử lại sau."
    name = type(exc).__name__
    if isinstance(exc, (ConnectionError, TimeoutError)) or "Connection" in name or "Timeout" in name:
        return "Không kết nối được tới nhà cung cấp AI."
    return f"Lỗi từ nhà cung cấp AI: {detail}"


# ---------- Claude ----------

def _anthropic_stream(cfg: dict, system: str, history: list[dict]) -> Iterator[str]:
    import anthropic

    client = anthropic.Anthropic(api_key=cfg["api_key"])
    try:
        max_tokens = min(MAX_OUTPUT, client.models.retrieve(cfg["model"]).max_tokens or MAX_OUTPUT)
    except anthropic.APIStatusError as exc:
        raise ProviderError(friendly_error(exc)) from exc
    req = anthropic_request(cfg["model"], system, history, max_tokens)
    api = client.beta.messages if "betas" in req else client.messages
    with api.stream(**req) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        raise ProviderError("Claude từ chối trả lời yêu cầu này.")
    if final.stop_reason == "max_tokens":
        yield "\n\n_(Câu trả lời bị cắt vì quá dài.)_"


def _anthropic_models(api_key: str) -> list[str]:
    import anthropic
    return [m.id for m in anthropic.Anthropic(api_key=api_key).models.list()]


# ---------- OpenAI ----------

def _openai_stream(cfg: dict, system: str, history: list[dict]) -> Iterator[str]:
    import openai

    client = openai.OpenAI(api_key=cfg["api_key"])
    stream = client.chat.completions.create(model=cfg["model"], messages=openai_messages(system, history), stream=True)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def _openai_models(api_key: str) -> list[str]:
    import openai
    return [m.id for m in openai.OpenAI(api_key=api_key).models.list()]


# ---------- Gemini ----------

def _gemini_stream(cfg: dict, system: str, history: list[dict]) -> Iterator[str]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=cfg["api_key"])
    stream = client.models.generate_content_stream(
        model=cfg["model"], contents=gemini_contents(history),
        config=types.GenerateContentConfig(system_instruction=system))
    for chunk in stream:
        if chunk.text:
            yield chunk.text


def _gemini_models(api_key: str) -> list[str]:
    from google import genai
    return [m.name.removeprefix("models/") for m in genai.Client(api_key=api_key).models.list()
            if "generateContent" in (m.supported_actions or [])]


STREAMERS: dict[str, Callable[[dict, str, list[dict]], Iterator[str]]] = {
    "anthropic": _anthropic_stream, "openai": _openai_stream, "gemini": _gemini_stream}
LISTERS: dict[str, Callable[[str], list[str]]] = {
    "anthropic": _anthropic_models, "openai": _openai_models, "gemini": _gemini_models}


def stream_reply(cfg: dict, system: str, history: list[dict]) -> Iterator[str]:
    """Yield answer text chunks; every failure surfaces as ProviderError."""
    try:
        yield from STREAMERS[cfg["provider"]](cfg, system, history)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 — SDKs raise many types; the user needs one readable message
        raise ProviderError(friendly_error(exc)) from exc


def list_models(provider: str, api_key: str) -> list[str]:
    try:
        return keep_chat_models(provider, LISTERS[provider](api_key))
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(friendly_error(exc)) from exc
