"""Background job runner: one meeting at a time, from upload to transcript.md."""
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Protocol

import numpy as np

from app.assemble import render_markdown, to_utterances
from app.filters import clean_segments
from app.messages import msg
from app.storage import Meeting, MeetingStore, write_text_atomic

log = logging.getLogger(__name__)
# "diarizing" only appears in meeting.json files written before speaker separation was removed.
ACTIVE = ("downloading", "converting", "transcribing", "diarizing")


class Engine(Protocol):
    def probe(self, path: Path) -> dict: ...
    def extract_audio(self, src: Path, dst: Path) -> None: ...
    def load_samples(self, path: Path) -> np.ndarray: ...
    def transcribe(self, samples, model: str, keywords: str, on_progress,
                   language: str) -> tuple[list[dict], str | None]: ...
    def release(self) -> None: ...
    def download(self, url: str, folder: Path, on_progress) -> Path: ...


class RealEngine:
    def probe(self, path):
        from app.audio import probe
        return probe(path)

    def extract_audio(self, src, dst):
        from app.audio import extract_audio
        extract_audio(src, dst)

    def load_samples(self, path):
        from app.audio import load_samples
        return load_samples(path)

    def transcribe(self, samples, model, keywords, on_progress, language):
        from app.asr import transcribe
        return transcribe(samples, model, keywords, on_progress, language)

    def release(self):
        from app.asr import release_model
        release_model()

    def download(self, url, folder, on_progress):
        from app.youtube import download_audio
        return download_audio(url, folder, on_progress)


def rebuild_transcript(store: MeetingStore, meeting_id: str) -> None:
    with store.lock:
        meeting = store.get(meeting_id)
        utterances = store.read_json(meeting_id, "segments.json")
        write_text_atomic(store.dir(meeting_id) / "transcript.md", render_markdown(meeting, utterances))


def update_meeting(store: MeetingStore, meeting_id: str, **changes) -> Meeting:
    """Apply user edits and re-render transcript.md atomically with respect to other edits."""
    with store.lock:
        meeting = store.update(meeting_id, **changes)
        if meeting.status == "done":
            rebuild_transcript(store, meeting_id)
        return meeting


class Worker:
    def __init__(self, store: MeetingStore, engine: Engine, model: str):
        self.store, self.engine, self.model = store, engine, model
        self.queue: queue.Queue[str] = queue.Queue()
        self.progress_interval = 2.0

    def start(self) -> None:
        self.recover()
        threading.Thread(target=self._loop, name="transcriber", daemon=True).start()

    def recover(self) -> None:
        for m in reversed(self.store.list()):  # oldest first; unreadable meetings are skipped
            if m.status in ACTIVE:
                self.store.update(m.id, status="error",
                                  error=msg("job.interrupted"))
            elif m.status == "queued":
                self.queue.put(m.id)

    def enqueue(self, meeting_id: str) -> None:
        self.store.update(meeting_id, status="queued", progress=0.0, error=None)
        self.queue.put(meeting_id)

    def _loop(self) -> None:
        while True:
            meeting_id = self.queue.get()
            try:
                self.run_job(meeting_id)
            except Exception:  # noqa: BLE001 — never let one job kill the worker thread
                log.exception("worker crashed on %s", meeting_id)
            if self.queue.empty():
                try:
                    self.engine.release()
                except Exception:  # noqa: BLE001
                    log.exception("could not release the model")

    def run_job(self, meeting_id: str) -> None:
        try:
            self.store.get(meeting_id)
        except KeyError:
            return  # deleted while queued
        try:
            self._full(meeting_id)
        except Exception as exc:  # noqa: BLE001 — any failure must surface in the UI
            log.exception("job %s failed", meeting_id)
            try:
                self.store.update(meeting_id, status="error", error=str(exc) or type(exc).__name__)
            except KeyError:
                pass

    def _stage(self, meeting_id: str, status: str):
        self.store.update(meeting_id, status=status, progress=0.0)
        last = [time.monotonic()]

        def on_progress(p: float) -> None:
            now = time.monotonic()
            if now - last[0] >= self.progress_interval:
                last[0] = now
                self.store.update(meeting_id, progress=round(float(p), 3))
        return on_progress

    def _full(self, meeting_id: str) -> None:
        folder = self.store.dir(meeting_id)
        audio = folder / "audio.m4a"
        upload = next(folder.glob("upload.*"), None)
        downloaded = False
        if upload is None and not audio.exists():
            source_url = self.store.get(meeting_id).source_url
            if source_url:
                on_progress = self._stage(meeting_id, "downloading")
                upload = self.engine.download(source_url, folder, on_progress)
                downloaded = True
        if upload is not None:
            self._stage(meeting_id, "converting")
            info = self.engine.probe(upload)
            self.engine.extract_audio(upload, audio)
            # A browser recording (WebM) has no duration in its header; the converted file does.
            duration = info["duration"] or self.engine.probe(audio)["duration"]
            self.store.update(meeting_id, duration=duration)
            if info["has_video"] or downloaded:  # the link stays in meeting.json; audio.m4a is enough
                upload.unlink()
            else:
                upload.rename(folder / f"original{upload.suffix}")
        elif not audio.exists():
            raise RuntimeError(msg("job.no_audio"))

        meeting = self.store.get(meeting_id)
        on_progress = self._stage(meeting_id, "transcribing")
        samples = self.engine.load_samples(audio)
        segments, used = self.engine.transcribe(samples, self.model, meeting.keywords, on_progress,
                                                meeting.language)
        self.store.write_json(meeting_id, "whisper.json",
                              {"model": self.model, "language": used, "segments": segments})
        self.store.update(meeting_id, model=self.model,
                          detected_language=used if meeting.language == "auto" else None)
        self._finish(meeting_id, segments)

    def _finish(self, meeting_id: str, segments: list[dict]) -> None:
        def log_drop(seg: dict, reason: str) -> None:
            log.info("%s: dropped %s segment at %.1fs: %r", meeting_id, reason, seg["start"], seg["text"])

        utterances = to_utterances(clean_segments(segments, on_drop=log_drop))
        self.store.write_json(meeting_id, "segments.json", utterances)
        rebuild_transcript(self.store, meeting_id)
        self.store.update(meeting_id, status="done", progress=1.0, error=None)
