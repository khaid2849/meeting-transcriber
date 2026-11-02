import numpy as np
import pytest

from app.audio import AudioError
from app.storage import MeetingStore
from app.worker import Worker, rebuild_transcript

SEGMENTS = [
    {"start": 5.0, "end": 8.0, "text": " Hôm nay bàn hạ tầng",
     "words": [{"start": 5.0, "end": 6.0, "word": " Hôm"}, {"start": 6.0, "end": 7.0, "word": " nay"},
               {"start": 7.0, "end": 7.5, "word": " bàn"}, {"start": 7.5, "end": 8.0, "word": " hạ tầng"}]},
    {"start": 9.0, "end": 11.0, "text": " Bên mình dùng GPU",
     "words": [{"start": 9.0, "end": 11.0, "word": " Bên mình dùng GPU"}]},
]
TURNS = [(4.0, 8.5, 0), (8.5, 12.0, 1)]


class FakeEngine:
    def __init__(self, has_video=False, probe_error=None, diarize_error=None):
        self.has_video, self.probe_error, self.diarize_error = has_video, probe_error, diarize_error
        self.calls = []

    def probe(self, path):
        self.calls.append("probe")
        if self.probe_error:
            raise self.probe_error
        return {"duration": 12.0, "has_video": self.has_video}

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
        return [dict(s) for s in SEGMENTS]

    def diarize(self, samples, num_speakers, on_progress):
        self.calls.append(("diarize", num_speakers))
        if self.diarize_error:
            raise self.diarize_error
        on_progress(1.0)
        return list(TURNS)


@pytest.fixture
def store(tmp_path):
    return MeetingStore(tmp_path)


def new_meeting(store, ext="wav", diarize=True, title="Họp"):
    m = store.create(title=title, date="2026-11-02", keywords="Odoo", diarize=diarize,
                     num_speakers=None, original_name=f"x.{ext}")
    (store.dir(m.id) / f"upload.{ext}").write_bytes(b"raw")
    return m


def files(store, m):
    return sorted(p.name for p in store.dir(m.id).iterdir())


def test_full_job_writes_all_files_and_done(store):
    m = new_meeting(store)
    Worker(store, FakeEngine(), "model-x").run_job(m.id, "full")
    got = store.get(m.id)
    assert (got.status, got.progress, got.model, got.duration, got.error) == ("done", 1.0, "model-x", 12.0, None)
    assert {"audio.m4a", "whisper.json", "segments.json", "transcript.md"} <= set(files(store, m))
    assert store.read_json(m.id, "whisper.json")["model"] == "model-x"
    md = (store.dir(m.id) / "transcript.md").read_text(encoding="utf-8")
    assert "**[00:00:05] Người 1:** Hôm nay bàn hạ tầng" in md
    assert "**[00:00:09] Người 2:** Bên mình dùng GPU" in md


def test_audio_upload_kept_as_original(store):
    m = new_meeting(store, "wav")
    Worker(store, FakeEngine(has_video=False), "m").run_job(m.id, "full")
    names = files(store, m)
    assert "original.wav" in names and not any(n.startswith("upload.") for n in names)


def test_video_upload_deleted(store):
    m = new_meeting(store, "mp4")
    Worker(store, FakeEngine(has_video=True), "m").run_job(m.id, "full")
    names = files(store, m)
    assert not any(n.startswith(("upload.", "original.")) for n in names)


def test_bad_media_sets_error_message(store):
    m = new_meeting(store)
    Worker(store, FakeEngine(probe_error=AudioError("Invalid data found")), "m").run_job(m.id, "full")
    got = store.get(m.id)
    assert got.status == "error" and "Invalid data found" in got.error


def test_diarize_failure_finishes_with_warning(store):
    m = new_meeting(store)
    Worker(store, FakeEngine(diarize_error=RuntimeError("boom")), "m").run_job(m.id, "full")
    got = store.get(m.id)
    assert got.status == "done" and "boom" in got.warning
    md = (store.dir(m.id) / "transcript.md").read_text(encoding="utf-8")
    assert "Người 1" not in md and "**[00:00:05]** Hôm nay bàn hạ tầng" in md


def test_diarize_disabled_skips_diarize(store):
    m = new_meeting(store, diarize=False)
    engine = FakeEngine()
    Worker(store, engine, "m").run_job(m.id, "full")
    assert not any(isinstance(c, tuple) for c in engine.calls)
    assert store.get(m.id).status == "done"


def test_retry_after_conversion_skips_extract(store):
    m = new_meeting(store)
    engine = FakeEngine()
    w = Worker(store, engine, "m")
    w.run_job(m.id, "full")
    engine.calls.clear()
    w.run_job(m.id, "full")
    assert "extract" not in engine.calls and "transcribe" in engine.calls


def test_rediarize_reuses_whisper_and_resets_names(store):
    m = new_meeting(store)
    engine = FakeEngine()
    w = Worker(store, engine, "m")
    w.run_job(m.id, "full")
    store.update(m.id, speakers={"S1": "A"}, num_speakers=3)
    engine.calls.clear()
    w.run_job(m.id, "rediarize")
    assert "transcribe" not in engine.calls and ("diarize", 3) in engine.calls
    got = store.get(m.id)
    assert got.speakers == {} and got.status == "done" and got.diarize is True


def test_rebuild_transcript_uses_names(store):
    m = new_meeting(store)
    Worker(store, FakeEngine(), "m").run_job(m.id, "full")
    before = (store.dir(m.id) / "segments.json").read_bytes()
    store.update(m.id, speakers={"S1": "Anh Tuấn"})
    rebuild_transcript(store, m.id)
    md = (store.dir(m.id) / "transcript.md").read_text(encoding="utf-8")
    assert "**[00:00:05] Anh Tuấn:**" in md
    assert (store.dir(m.id) / "segments.json").read_bytes() == before


def test_recover_marks_interrupted_and_requeues_queued(store):
    busy = new_meeting(store, title="Đang chạy")
    store.update(busy.id, status="transcribing", progress=0.4)
    waiting = new_meeting(store, title="Đang chờ")
    w = Worker(store, FakeEngine(), "m")
    w.recover()
    got = store.get(busy.id)
    assert got.status == "error" and got.error
    assert list(w.queue.queue) == [(waiting.id, "full")]


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
    w.run_job(m.id, "full")
    assert ("transcribing", 0.5) in seen
    assert seen[-1] == ("done", 1.0)


def test_enqueue_resets_error_state(store):
    m = new_meeting(store)
    store.update(m.id, status="error", error="x")
    w = Worker(store, FakeEngine(), "m")
    w.enqueue(m.id)
    got = store.get(m.id)
    assert (got.status, got.error) == ("queued", None)


class HallucinatingEngine(FakeEngine):
    def transcribe(self, samples, model, keywords, on_progress):
        on_progress(1.0)
        outro = {"start": 11.0, "end": 11.1, "text": " Cảm ơn các bạn đã theo dõi và hẹn gặp lại.", "words": []}
        return [dict(s) for s in SEGMENTS] + [outro]


def test_whisper_json_keeps_raw_output_and_transcript_is_cleaned(store):
    m = new_meeting(store)
    Worker(store, HallucinatingEngine(), "m").run_job(m.id, "full")
    assert len(store.read_json(m.id, "whisper.json")["segments"]) == 3
    md = (store.dir(m.id) / "transcript.md").read_text(encoding="utf-8")
    assert "theo dõi" not in md and "Bên mình dùng GPU" in md


def test_worker_loop_survives_unexpected_error(store):
    import threading
    a, b = new_meeting(store, title="A"), new_meeting(store, title="B")
    w = Worker(store, FakeEngine(), "m")
    real_run, done = w.run_job, threading.Event()

    def flaky(meeting_id, mode):
        if meeting_id == a.id:
            raise OSError("disk full")
        real_run(meeting_id, mode)
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
    assert [mid for mid, _ in w.queue.queue] == [good.id]


def test_concurrent_renames_leave_transcript_consistent(store):
    import threading
    m = new_meeting(store)
    Worker(store, FakeEngine(), "m").run_job(m.id, "full")
    errors = []
    from app.worker import update_meeting

    def worker(i):
        try:
            for j in range(15):
                update_meeting(store, m.id, speakers={"S1": f"Tên {i}-{j}"})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    final = store.get(m.id).speakers["S1"]
    assert f"**[00:00:05] {final}:**" in (store.dir(m.id) / "transcript.md").read_text(encoding="utf-8")
