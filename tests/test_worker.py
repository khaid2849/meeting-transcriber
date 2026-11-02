import threading

import numpy as np
import pytest

from app.audio import AudioError
from app.storage import MeetingStore
from app.worker import Worker, rebuild_transcript, update_meeting

SEGMENTS = [
    {"start": 5.0, "end": 8.0, "text": " Hôm nay bàn hạ tầng", "words": []},
    {"start": 30.0, "end": 33.0, "text": " Bên mình dùng GPU", "words": []},
]


class FakeEngine:
    def __init__(self, has_video=False, probe_error=None, segments=SEGMENTS):
        self.has_video, self.probe_error, self.segments = has_video, probe_error, segments
        self.calls = []

    def probe(self, path):
        self.calls.append("probe")
        if self.probe_error:
            raise self.probe_error
        return {"duration": 40.0, "has_video": self.has_video}

    def extract_audio(self, src, dst):
        self.calls.append("extract")
        dst.write_bytes(b"m4a")

    def load_samples(self, path):
        self.calls.append("load")
        return np.zeros(16000, dtype=np.float32)

    def transcribe(self, samples, model, keywords, on_progress):
        self.calls.append("transcribe")
        on_progress(0.5)
        on_progress(1.0)
        return [dict(s) for s in self.segments]


@pytest.fixture
def store(tmp_path):
    return MeetingStore(tmp_path)


def new_meeting(store, ext="wav", title="Họp"):
    m = store.create(title=title, date="2026-11-02", keywords="Odoo", original_name=f"x.{ext}")
    (store.dir(m.id) / f"upload.{ext}").write_bytes(b"raw")
    return m


def files(store, m):
    return sorted(p.name for p in store.dir(m.id).iterdir())


def transcript(store, m):
    return (store.dir(m.id) / "transcript.md").read_text(encoding="utf-8")


def test_full_job_writes_all_files_and_done(store):
    m = new_meeting(store)
    Worker(store, FakeEngine(), "model-x").run_job(m.id)
    got = store.get(m.id)
    assert (got.status, got.progress, got.model, got.duration, got.error) == ("done", 1.0, "model-x", 40.0, None)
    assert {"audio.m4a", "whisper.json", "segments.json", "transcript.md"} <= set(files(store, m))
    assert store.read_json(m.id, "whisper.json")["model"] == "model-x"
    assert "**[00:00:05]** Hôm nay bàn hạ tầng" in transcript(store, m)
    assert "**[00:00:30]** Bên mình dùng GPU" in transcript(store, m)


def test_audio_upload_kept_as_original(store):
    m = new_meeting(store, "wav")
    Worker(store, FakeEngine(has_video=False), "m").run_job(m.id)
    names = files(store, m)
    assert "original.wav" in names and not any(n.startswith("upload.") for n in names)


def test_video_upload_deleted(store):
    m = new_meeting(store, "mp4")
    Worker(store, FakeEngine(has_video=True), "m").run_job(m.id)
    assert not any(n.startswith(("upload.", "original.")) for n in files(store, m))


def test_bad_media_sets_error_message(store):
    m = new_meeting(store)
    Worker(store, FakeEngine(probe_error=AudioError("Invalid data found")), "m").run_job(m.id)
    got = store.get(m.id)
    assert got.status == "error" and "Invalid data found" in got.error


def test_retry_after_conversion_skips_extract(store):
    m = new_meeting(store)
    engine = FakeEngine()
    w = Worker(store, engine, "m")
    w.run_job(m.id)
    engine.calls.clear()
    w.run_job(m.id)
    assert "extract" not in engine.calls and "transcribe" in engine.calls


def test_whisper_json_keeps_raw_output_and_transcript_is_cleaned(store):
    outro = {"start": 34.0, "end": 34.1, "text": " Cảm ơn các bạn đã theo dõi và hẹn gặp lại.", "words": []}
    m = new_meeting(store)
    Worker(store, FakeEngine(segments=SEGMENTS + [outro]), "m").run_job(m.id)
    assert len(store.read_json(m.id, "whisper.json")["segments"]) == 3
    md = transcript(store, m)
    assert "theo dõi" not in md and "Bên mình dùng GPU" in md


def test_rebuild_transcript_uses_new_title(store):
    m = new_meeting(store)
    Worker(store, FakeEngine(), "m").run_job(m.id)
    before = (store.dir(m.id) / "segments.json").read_bytes()
    store.update(m.id, title="Tên mới")
    rebuild_transcript(store, m.id)
    assert transcript(store, m).startswith("# Tên mới\n")
    assert (store.dir(m.id) / "segments.json").read_bytes() == before


def test_recover_marks_interrupted_and_requeues_queued(store):
    busy = new_meeting(store, title="Đang chạy")
    store.update(busy.id, status="transcribing", progress=0.4)
    waiting = new_meeting(store, title="Đang chờ")
    w = Worker(store, FakeEngine(), "m")
    w.recover()
    got = store.get(busy.id)
    assert got.status == "error" and got.error
    assert list(w.queue.queue) == [waiting.id]


def test_recover_marks_legacy_diarizing_status_as_interrupted(store):
    m = new_meeting(store)
    store.update(m.id, status="diarizing")
    Worker(store, FakeEngine(), "m").recover()
    assert store.get(m.id).status == "error"


def test_progress_updates_are_saved(store):
    m = new_meeting(store)
    seen = []
    orig = store.save

    def spy(meeting):
        seen.append((meeting.status, meeting.progress))
        orig(meeting)

    store.save = spy
    w = Worker(store, FakeEngine(), "m")
    w.progress_interval = 0
    w.run_job(m.id)
    assert ("transcribing", 0.5) in seen
    assert seen[-1] == ("done", 1.0)


def test_enqueue_resets_error_state(store):
    m = new_meeting(store)
    store.update(m.id, status="error", error="x")
    Worker(store, FakeEngine(), "m").enqueue(m.id)
    got = store.get(m.id)
    assert (got.status, got.error) == ("queued", None)


def test_worker_loop_survives_unexpected_error(store):
    a, b = new_meeting(store, title="A"), new_meeting(store, title="B")
    w = Worker(store, FakeEngine(), "m")
    real_run, done = w.run_job, threading.Event()

    def flaky(meeting_id):
        if meeting_id == a.id:
            raise OSError("disk full")
        real_run(meeting_id)
        done.set()

    w.run_job = flaky
    w.start()  # recover() queues both
    assert done.wait(5)
    assert store.get(b.id).status == "done"


def test_recover_skips_corrupt_meeting_json(store):
    good = new_meeting(store)
    bad = new_meeting(store, title="Hỏng")
    (store.dir(bad.id) / "meeting.json").write_text("{không phải json", encoding="utf-8")
    w = Worker(store, FakeEngine(), "m")
    w.recover()
    assert list(w.queue.queue) == [good.id]


def test_concurrent_title_edits_leave_transcript_consistent(store):
    m = new_meeting(store)
    Worker(store, FakeEngine(), "m").run_job(m.id)
    errors = []

    def edit(i):
        try:
            for j in range(15):
                update_meeting(store, m.id, title=f"Tên {i}-{j}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=edit, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert transcript(store, m).startswith(f"# {store.get(m.id).title}\n")
