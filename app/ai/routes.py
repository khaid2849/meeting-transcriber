"""HTTP API for AI provider settings and transcript chats."""
import json
import logging
import threading
from pathlib import Path
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException
from starlette.concurrency import iterate_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from app import config
from app.ai.chats import ChatStore
from app.ai.prompting import build_system_prompt, estimate_tokens
from app.ai.providers import ProviderError, chat_history, list_models, stream_reply
from app.ai.settings import AISettings
from app.assemble import group_paragraphs
from app.messages import msg
from app.storage import MeetingStore

log = logging.getLogger(__name__)
Provider = Literal["anthropic", "openai", "gemini", "openai_compat"]
INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"


class ProviderIn(BaseModel):
    name: str
    provider: Provider
    api_key: str = ""
    model: str
    base_url: str | None = None


class ProviderPatch(BaseModel):
    name: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None


class DefaultIn(BaseModel):
    id: str


class ModelsIn(BaseModel):
    provider: Provider | None = None
    api_key: str | None = None
    base_url: str | None = None
    provider_id: str | None = None


class ChatIn(BaseModel):
    meeting_ids: list[str] = []
    provider_id: str | None = None


class ChatPatch(BaseModel):
    title: str | None = None
    meeting_ids: list[str] | None = None
    provider_id: str | None = None


class MessageIn(BaseModel):
    content: str
    lang: Literal["vi", "en"] = "vi"  # language of the answer, follows the UI


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


async def close_on_exit(gen):
    """Stream a sync generator and close it as soon as the client goes away.

    Starlette abandons a sync iterator on disconnect without closing it, which would keep the
    provider request (and its billing) running and delay saving the partial answer.
    """
    try:
        async for item in iterate_in_threadpool(gen):
            yield item
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await anyio.to_thread.run_sync(gen.close)
            except ValueError:  # generator still executing in its thread
                log.warning("could not close an answer stream")


def ai_router(meetings: MeetingStore, settings: AISettings, chats: ChatStore) -> APIRouter:
    router = APIRouter()
    streaming: set[str] = set()  # chats with an answer in flight
    streaming_lock = threading.Lock()

    def public_provider(entry: dict) -> dict:
        return next(p for p in settings.public()["providers"] if p["id"] == entry["id"])

    def get_chat(chat_id: str) -> dict:
        try:
            return chats.get(chat_id)
        except KeyError:
            raise HTTPException(404, msg("chat.not_found")) from None

    def scope(meeting_ids: list[str]) -> tuple[list[dict], list[dict]]:
        """Return (meeting info for the UI, transcripts usable as context)."""
        info, usable = [], []
        for mid in meeting_ids:
            try:
                m = meetings.get(mid)
            except KeyError:
                info.append({"id": mid, "title": None, "date": None, "label": None, "usable": False})
                continue
            entry = {"id": m.id, "title": m.title, "date": m.date, "label": None, "usable": m.status == "done"}
            if entry["usable"]:
                entry["label"] = f"M{len(usable) + 1}"
                utterances = meetings.read_json(m.id, "segments.json")
                usable.append({"id": m.id, "title": m.title, "date": m.date,
                               "paragraphs": group_paragraphs(utterances, config.PARAGRAPH_MAX_GAP,
                                                              config.PARAGRAPH_MAX_LEN)})
            info.append(entry)
        return info, usable

    # ---------- provider settings ----------

    @router.get("/api/ai/providers")
    def get_providers() -> dict:
        return settings.public()

    @router.post("/api/ai/providers", status_code=201)
    def add_provider(body: ProviderIn) -> dict:
        try:
            entry = settings.add(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return public_provider(entry)

    @router.patch("/api/ai/providers/{provider_id}")
    def patch_provider(provider_id: str, body: ProviderPatch) -> dict:
        try:
            entry = settings.update(provider_id, **body.model_dump())
        except KeyError:
            raise HTTPException(404, msg("ai.config_not_found")) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return public_provider(entry)

    @router.delete("/api/ai/providers/{provider_id}", status_code=204)
    def delete_provider(provider_id: str) -> Response:
        settings.delete(provider_id)
        return Response(status_code=204)

    @router.post("/api/ai/default")
    def set_default(body: DefaultIn) -> dict:
        try:
            settings.set_default(body.id)
        except KeyError:
            raise HTTPException(404, msg("ai.config_not_found")) from None
        return settings.public()

    @router.post("/api/ai/models")
    def models(body: ModelsIn) -> dict:
        if body.provider_id:
            try:
                entry = settings.get(body.provider_id)
            except KeyError:
                raise HTTPException(404, msg("ai.config_not_found")) from None
            provider, key = entry["provider"], (body.api_key or "").strip() or entry["api_key"]
            base_url = (body.base_url or "").strip().rstrip("/") or entry.get("base_url")
        else:
            provider, key = body.provider, (body.api_key or "").strip()
            base_url = (body.base_url or "").strip().rstrip("/") or None
        if provider == "openai_compat":
            if not base_url:
                raise HTTPException(422, msg("ai.need_base_url"))
        elif not provider or not key:
            raise HTTPException(422, msg("ai.need_key"))
        try:
            return {"models": list_models(provider, key, base_url)}
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from None

    # ---------- chats ----------

    @router.get("/c", include_in_schema=False)
    def chats_page():
        return FileResponse(INDEX)

    @router.get("/c/{chat_id}", include_in_schema=False)
    def chat_page(chat_id: str):
        get_chat(chat_id)
        return FileResponse(INDEX)

    @router.get("/api/chats")
    def list_chats() -> list[dict]:
        return chats.list()

    @router.post("/api/chats", status_code=201)
    def create_chat(body: ChatIn) -> dict:
        default = settings.default()
        provider_id = body.provider_id or (default["id"] if default else None)
        return chats.create(meeting_ids=body.meeting_ids, provider_id=provider_id)

    @router.get("/api/chats/{chat_id}")
    def get_chat_detail(chat_id: str) -> dict:
        chat = get_chat(chat_id)
        info, usable = scope(chat["meeting_ids"])
        system, labels = build_system_prompt(usable)
        return {**chat, "meetings": info, "labels": labels, "estimated_tokens": estimate_tokens(system)}

    @router.patch("/api/chats/{chat_id}")
    def patch_chat(chat_id: str, body: ChatPatch) -> dict:
        get_chat(chat_id)
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if "title" in changes and not changes["title"].strip():
            raise HTTPException(422, msg("chat.title_empty"))
        return chats.update(chat_id, **changes)

    @router.delete("/api/chats/{chat_id}", status_code=204)
    def delete_chat(chat_id: str) -> Response:
        get_chat(chat_id)
        chats.delete(chat_id)
        return Response(status_code=204)

    @router.post("/api/chats/{chat_id}/messages")
    def send_message(chat_id: str, body: MessageIn):
        chat = get_chat(chat_id)
        question = body.content.strip()
        if not question:
            raise HTTPException(422, msg("chat.question_empty"))
        try:
            cfg = settings.get(chat["provider_id"]) if chat["provider_id"] else settings.default()
        except KeyError:
            cfg = settings.default()
        if not cfg:
            raise HTTPException(400, msg("ai.not_configured"))

        busy = msg("chat.busy")
        if chat_id in streaming:
            raise HTTPException(409, busy)
        _, usable = scope(chat["meeting_ids"])
        system, labels = build_system_prompt(usable, body.lang)
        history = chat_history(chat["messages"]) + [{"role": "user", "content": question}]
        # Labels are saved per answer so citations keep pointing at the right meeting after scope edits.
        meta = {"provider": cfg["name"], "model": cfg["model"], "labels": labels}

        def save(error: str | None = None) -> bool:
            message = {"role": "assistant", "content": "".join(parts), **meta}
            if error:
                message["error"] = error
            try:
                chats.append(chat_id, message)
                return True
            except KeyError:  # chat deleted while the answer was streaming
                return False

        parts: list[str] = []

        def generate():
            # The guard is taken here, not in the endpoint: a stream that never starts holds nothing.
            with streaming_lock:
                taken = chat_id in streaming
                if not taken:
                    streaming.add(chat_id)
            if taken:
                yield _line({"error": busy})
                return
            saved = False
            try:
                try:
                    chats.append(chat_id, {"role": "user", "content": question})
                except KeyError:
                    saved = True
                    yield _line({"error": msg("chat.deleted")})
                    return
                for delta in stream_reply(cfg, system, history):
                    parts.append(delta)
                    yield _line({"delta": delta})
                if not "".join(parts).strip():
                    raise ProviderError(msg("ai.empty_answer"))
                saved = True
                yield _line({"done": True} if save() else {"error": msg("chat.deleted")})
            except ProviderError as exc:
                saved = True
                yield _line({"error": str(exc)} if save(str(exc)) else {"error": msg("chat.deleted")})
            finally:
                if not saved:  # client went away mid-answer: keep what arrived
                    save(msg("chat.stopped"))
                streaming.discard(chat_id)

        return StreamingResponse(close_on_exit(generate()), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
