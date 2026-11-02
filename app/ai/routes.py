"""HTTP API for AI provider settings and transcript chats."""
import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from app import config
from app.ai.chats import ChatStore
from app.ai.prompting import build_system_prompt, estimate_tokens
from app.ai.providers import ProviderError, chat_history, list_models, stream_reply
from app.ai.settings import AISettings
from app.assemble import group_paragraphs
from app.storage import MeetingStore

log = logging.getLogger(__name__)
Provider = Literal["anthropic", "openai", "gemini"]
INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"


class ProviderIn(BaseModel):
    name: str
    provider: Provider
    api_key: str
    model: str


class ProviderPatch(BaseModel):
    name: str | None = None
    model: str | None = None
    api_key: str | None = None


class DefaultIn(BaseModel):
    id: str


class ModelsIn(BaseModel):
    provider: Provider | None = None
    api_key: str | None = None
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


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def ai_router(meetings: MeetingStore, settings: AISettings, chats: ChatStore) -> APIRouter:
    router = APIRouter()

    def public_provider(entry: dict) -> dict:
        return next(p for p in settings.public()["providers"] if p["id"] == entry["id"])

    def get_chat(chat_id: str) -> dict:
        try:
            return chats.get(chat_id)
        except KeyError:
            raise HTTPException(404, "Không tìm thấy cuộc trò chuyện") from None

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
            raise HTTPException(404, "Không tìm thấy cấu hình AI") from None
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
            raise HTTPException(404, "Không tìm thấy cấu hình AI") from None
        return settings.public()

    @router.post("/api/ai/models")
    def models(body: ModelsIn) -> dict:
        if body.provider_id:
            try:
                entry = settings.get(body.provider_id)
            except KeyError:
                raise HTTPException(404, "Không tìm thấy cấu hình AI") from None
            provider, key = entry["provider"], (body.api_key or "").strip() or entry["api_key"]
        else:
            provider, key = body.provider, (body.api_key or "").strip()
        if not provider or not key:
            raise HTTPException(422, "Cần chọn nhà cung cấp và nhập API key")
        try:
            return {"models": list_models(provider, key)}
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from None

    # ---------- chats ----------

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
            raise HTTPException(422, "Tên cuộc trò chuyện không được trống")
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
            raise HTTPException(422, "Câu hỏi không được trống")
        try:
            cfg = settings.get(chat["provider_id"]) if chat["provider_id"] else settings.default()
        except KeyError:
            cfg = settings.default()
        if not cfg:
            raise HTTPException(400, "Chưa cấu hình AI. Mở Cài đặt AI để thêm API key.")

        _, usable = scope(chat["meeting_ids"])
        system, labels = build_system_prompt(usable)
        history = chat_history(chat["messages"]) + [{"role": "user", "content": question}]
        chats.append(chat_id, {"role": "user", "content": question})
        # Labels are saved per answer so citations keep pointing at the right meeting after scope edits.
        meta = {"provider": cfg["name"], "model": cfg["model"], "labels": labels}

        def generate():
            parts: list[str] = []
            saved = False
            try:
                for delta in stream_reply(cfg, system, history):
                    parts.append(delta)
                    yield _line({"delta": delta})
                chats.append(chat_id, {"role": "assistant", "content": "".join(parts), **meta})
                saved = True
                yield _line({"done": True})
            except ProviderError as exc:
                chats.append(chat_id, {"role": "assistant", "content": "".join(parts), "error": str(exc), **meta})
                saved = True
                yield _line({"error": str(exc)})
            finally:
                if not saved:  # client went away mid-answer: keep what arrived
                    chats.append(chat_id, {"role": "assistant", "content": "".join(parts),
                                           "error": "Đã dừng giữa chừng", **meta})

        return StreamingResponse(generate(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
