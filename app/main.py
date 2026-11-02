"""FastAPI app: JSON API + static pages. Run with `uvicorn --factory app.main:build_app`."""
import logging
import re
from contextlib import asynccontextmanager
from datetime import date as Date
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from app import config
from app.assemble import group_paragraphs, speaker_label
from app.storage import Meeting, MeetingStore
from app.worker import ACTIVE, RealEngine, Worker, update_meeting

STATIC = Path(__file__).parent / "static"
MAX_SPEAKERS = 20


class MeetingPatch(BaseModel):
    title: str | None = None
    date: str | None = None
    speakers: dict[str, str] | None = None


def _check_date(value: str) -> str:
    try:
        return Date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, "Ngày phải có dạng YYYY-MM-DD") from None


def _check_speakers(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise HTTPException(422, "Số người nói phải là số") from None
    if not 1 <= n <= MAX_SPEAKERS:
        raise HTTPException(422, f"Số người nói phải từ 1 đến {MAX_SPEAKERS}")
    return n


LOCAL_HOSTS = ("localhost", "127.0.0.1")


def create_app(store: MeetingStore, worker, lifespan=None, allowed_hosts=LOCAL_HOSTS) -> FastAPI:
    app = FastAPI(title="Meeting Transcriber", lifespan=lifespan)
    # Blocks DNS-rebinding pages from reading transcripts through the browser.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def get_or_404(meeting_id: str) -> Meeting:
        try:
            return store.get(meeting_id)
        except KeyError:
            raise HTTPException(404, "Không tìm thấy cuộc họp") from None

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/m/{meeting_id}", include_in_schema=False)
    def meeting_page(meeting_id: str):
        get_or_404(meeting_id)
        return FileResponse(STATIC / "meeting.html")

    @app.get("/api/meetings")
    def list_meetings() -> list[dict]:
        return [m.to_dict() for m in store.list()]

    @app.post("/api/meetings", status_code=201)
    async def create_meeting(file: UploadFile = File(...), title: str = Form(""),
                             date: str = Form(...), keywords: str = Form(""),
                             diarize: bool = Form(True), num_speakers: str = Form("")) -> dict:
        date = _check_date(date)
        speakers = _check_speakers(num_speakers)
        name = file.filename or "audio"
        title = title.strip() or Path(name).stem
        suffix = re.sub(r"[^a-z0-9.]", "", Path(name).suffix.lower()) or ".bin"
        meeting = store.create(title=title, date=date, keywords=keywords.strip(), diarize=diarize,
                               num_speakers=speakers, original_name=name)
        try:
            with open(store.dir(meeting.id) / f"upload{suffix}", "wb") as out:
                while chunk := await file.read(1 << 20):
                    out.write(chunk)
        except Exception:
            store.delete(meeting.id)
            raise
        worker.enqueue(meeting.id)
        return store.get(meeting.id).to_dict()

    @app.get("/api/meetings/{meeting_id}")
    def get_meeting(meeting_id: str) -> dict:
        meeting = get_or_404(meeting_id)
        paragraphs = []
        if meeting.status == "done":
            utterances = store.read_json(meeting_id, "segments.json")
            for p in group_paragraphs(utterances, config.PARAGRAPH_MAX_GAP, config.PARAGRAPH_MAX_LEN):
                paragraphs.append({"start": p["start"], "speaker": p["speaker"],
                                   "label": speaker_label(p["speaker"], meeting.speakers),
                                   "text": p["text"]})
        return {"meeting": meeting.to_dict(), "paragraphs": paragraphs}

    @app.patch("/api/meetings/{meeting_id}")
    def patch_meeting(meeting_id: str, patch: MeetingPatch) -> dict:
        meeting = get_or_404(meeting_id)
        changes = {}
        if patch.title is not None:
            if not patch.title.strip():
                raise HTTPException(422, "Tiêu đề không được trống")
            changes["title"] = patch.title.strip()
        if patch.date is not None:
            changes["date"] = _check_date(patch.date)
        if patch.speakers is not None:
            changes["speakers"] = {k: v.strip() for k, v in patch.speakers.items() if v.strip()}
        return update_meeting(store, meeting_id, **changes).to_dict()

    @app.post("/api/meetings/{meeting_id}/retry")
    def retry(meeting_id: str) -> dict:
        if get_or_404(meeting_id).status != "error":
            raise HTTPException(409, "Chỉ chạy lại được cuộc họp bị lỗi")
        worker.enqueue(meeting_id, "full")
        return store.get(meeting_id).to_dict()

    @app.post("/api/meetings/{meeting_id}/rediarize")
    def rediarize(meeting_id: str, num_speakers: int | None = Body(None, embed=True)) -> dict:
        if get_or_404(meeting_id).status != "done":
            raise HTTPException(409, "Chỉ tách lại được khi cuộc họp đã xử lý xong")
        store.update(meeting_id, num_speakers=_check_speakers(num_speakers))
        worker.enqueue(meeting_id, "rediarize")
        return store.get(meeting_id).to_dict()

    @app.delete("/api/meetings/{meeting_id}", status_code=204)
    def delete(meeting_id: str) -> Response:
        if get_or_404(meeting_id).status in ACTIVE:
            raise HTTPException(409, "Đang xử lý, chưa xóa được")
        store.delete(meeting_id)
        return Response(status_code=204)

    @app.get("/api/meetings/{meeting_id}/audio")
    def audio(meeting_id: str):
        path = store.dir(get_or_404(meeting_id).id) / "audio.m4a"
        if not path.exists():
            raise HTTPException(404, "Chưa có audio")
        return FileResponse(path, media_type="audio/mp4")

    @app.get("/api/meetings/{meeting_id}/transcript.md")
    def transcript(meeting_id: str):
        path = store.dir(get_or_404(meeting_id).id) / "transcript.md"
        if not path.exists():
            raise HTTPException(404, "Chưa có bản ghi")
        return FileResponse(path, media_type="text/markdown; charset=utf-8",
                            filename=f"{meeting_id}.md")

    return app


def build_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = MeetingStore(config.MEETINGS_DIR)
    worker = Worker(store, RealEngine(), config.WHISPER_MODEL)

    @asynccontextmanager
    async def lifespan(_app):
        worker.start()
        yield

    return create_app(store, worker, lifespan=lifespan)
