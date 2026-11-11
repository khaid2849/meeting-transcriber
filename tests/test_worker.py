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

    def transcribe(self, samples, model, keywords, on_progress, language):
        self.calls.append("transcribe")
        self.language = language
        on_progress(0.5)
        on_progress(1.0)
        return [dict(s) for s in self.segments], ("en" if language == "auto" else language)

    def release(self):
        self.calls.append("release")


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


def run_in_thread(store, engine, n_jobs, fail_first=False):
    """Start the worker on queued meetings and wait until n_jobs have run."""
    w = Worker(store, engine, "m")
    real_run, seen, finished = w.run_job, [], threading.Event()

    def counted(meeting_id):
        try:
            if fail_first and not seen:
                raise OSError("boom")
            real_run(meeting_id)
        finally:
            seen.append(meeting_id)
            if len(seen) == n_jobs:
                finished.set()

    w.run_job = counted
    w.start()
    assert finished.wait(5)
    for _ in range(50):  # release runs right after the last job returns
        if engine.calls and engine.calls[-1] == "release":
            break
        threading.Event().wait(0.02)
    return engine.calls


def test_releases_model_once_when_queue_drains(store):
    new_meeting(store, title="A"), new_meeting(store, title="B")
    calls = run_in_thread(store, FakeEngine(), 2)
    assert calls.count("release") == 1 and calls[-1] == "release"
    assert calls.count("transcribe") == 2


def test_releases_model_after_failed_job(store):
    new_meeting(store)
    calls = run_in_thread(store, FakeEngine(), 1, fail_first=True)
    assert calls == ["release"]


class YouTubeEngine(FakeEngine):
    def __init__(self, download_error=None, **kw):
        super().__init__(**kw)
        self.download_error = download_error

    def download(self, url, folder, on_progress):
        self.calls.append(("download", url))
        if self.download_error:
            raise self.download_error
        on_progress(0.5)
        path = folder / "upload.webm"
        path.write_bytes(b"opus")
        return path


def youtube_meeting(store):
    return store.create(title="Video", date="2026-11-03", keywords="", original_name="YouTube",
                        source_url="https://www.youtube.com/watch?v=abc123")


def test_youtube_job_downloads_then_transcribes(store):
    m = youtube_meeting(store)
    seen = []
    orig = store.save
    store.save = lambda meeting: (seen.append(meeting.status), orig(meeting))
    engine = YouTubeEngine()
    w = Worker(store, engine, "m")
    w.progress_interval = 0
    w.run_job(m.id)
    assert ("download", "https://www.youtube.com/watch?v=abc123") in engine.calls
    assert "downloading" in seen and store.get(m.id).status == "done"
    names = files(store, m)
    assert "transcript.md" in names and not any(n.startswith(("upload.", "original.")) for n in names)


def test_youtube_download_error_is_shown(store):
    from app.youtube import YouTubeError
    m = youtube_meeting(store)
    Worker(store, YouTubeEngine(download_error=YouTubeError("Video đang ở chế độ riêng tư")), "m").run_job(m.id)
    got = store.get(m.id)
    assert got.status == "error" and "riêng tư" in got.error


def test_recover_marks_interrupted_download(store):
    m = youtube_meeting(store)
    store.update(m.id, status="downloading")
    Worker(store, YouTubeEngine(), "m").recover()
    assert store.get(m.id).status == "error"


def test_spoken_language_is_passed_to_whisper(store):
    m = store.create(title="Talk", date="2026-11-03", keywords="", original_name="a.wav", language="en")
    (store.dir(m.id) / "upload.wav").write_bytes(b"raw")
    engine = FakeEngine()
    Worker(store, engine, "m").run_job(m.id)
    assert engine.language == "en" and store.get(m.id).detected_language is None


def test_auto_language_records_what_was_detected(store):
    m = store.create(title="Talk", date="2026-11-03", keywords="", original_name="a.wav", language="auto")
    (store.dir(m.id) / "upload.wav").write_bytes(b"raw")
    Worker(store, FakeEngine(), "m").run_job(m.id)
    got = store.get(m.id)
    assert (got.language, got.detected_language) == ("auto", "en")


def test_language_defaults_to_vietnamese(store):
    assert new_meeting(store).language == "vi"


def test_recording_without_header_duration_uses_converted_audio(store):
    class NoDurationEngine(FakeEngine):
        def probe(self, path):
            self.calls.append("probe")
            return {"duration": 0.0 if path.name.startswith("upload") else 75.0, "has_video": False}

    m = new_meeting(store, ext="webm")
    Worker(store, NoDurationEngine(), "model-x").run_job(m.id)
    assert store.get(m.id).duration == 75.0
    assert "original.webm" in files(store, m)
