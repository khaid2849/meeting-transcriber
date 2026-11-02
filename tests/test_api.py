import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import MeetingStore

UTTS = [{"start": 5.0, "end": 8.0, "speaker": "S1", "text": "Xin chào"},
        {"start": 9.0, "end": 11.0, "speaker": "S2", "text": "Chào anh"}]


class FakeWorker:
    def __init__(self, store):
        self.store, self.calls = store, []

    def enqueue(self, meeting_id, mode="full"):
        self.calls.append((meeting_id, mode))
        self.store.update(meeting_id, status="queued", error=None)


@pytest.fixture
def store(tmp_path):
    return MeetingStore(tmp_path)


@pytest.fixture
def worker(store):
    return FakeWorker(store)


@pytest.fixture
def client(store, worker):
    return TestClient(create_app(store, worker, allowed_hosts=("testserver", "localhost", "127.0.0.1")))


def upload(client, **form):
    data = {"title": "Hạ tầng AI", "date": "2026-11-02", "keywords": "Odoo", "diarize": "true",
            "num_speakers": ""}
    data.update(form)
    return client.post("/api/meetings", data=data,
                       files={"file": ("cuoc hop.wav", b"RIFF" + b"\0" * 3000, "audio/wav")})


@pytest.fixture
def done_meeting(store):
    m = store.create(title="Xong", date="2026-11-02", keywords="", diarize=True, num_speakers=None,
                     original_name="a.wav")
    store.write_json(m.id, "segments.json", UTTS)
    (store.dir(m.id) / "audio.m4a").write_bytes(bytes(range(256)) * 4)
    (store.dir(m.id) / "transcript.md").write_text("# Xong\n", encoding="utf-8")
    store.update(m.id, status="done", progress=1.0)
    return m.id


def test_upload_creates_meeting_and_enqueues(client, store, worker):
    r = upload(client)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == "2026-11-02-ha-tang-ai" and body["diarize"] is True
    assert body["original_name"] == "cuoc hop.wav"
    assert (store.dir(body["id"]) / "upload.wav").stat().st_size == 3004
    assert worker.calls == [(body["id"], "full")]


def test_upload_blank_num_speakers_is_none(client):
    assert upload(client).json()["num_speakers"] is None
    assert upload(client, num_speakers="3").json()["num_speakers"] == 3


def test_upload_rejects_bad_num_speakers(client):
    assert upload(client, num_speakers="0").status_code == 422


def test_upload_diarize_false(client):
    assert upload(client, diarize="false").json()["diarize"] is False


def test_list_meetings(client, done_meeting):
    assert [m["id"] for m in client.get("/api/meetings").json()] == [done_meeting]


def test_get_unknown_404(client):
    assert client.get("/api/meetings/2026-01-01-khong-co").status_code == 404
    assert client.get("/api/meetings/..%2Fetc").status_code == 404


def test_get_done_meeting_returns_paragraphs(client, done_meeting, store):
    store.update(done_meeting, speakers={"S1": "Anh Tuấn"})
    body = client.get(f"/api/meetings/{done_meeting}").json()
    assert body["meeting"]["status"] == "done"
    assert [(p["speaker"], p["label"], p["text"]) for p in body["paragraphs"]] == [
        ("S1", "Anh Tuấn", "Xin chào"), ("S2", "Người 2", "Chào anh")]


def test_get_processing_meeting_has_no_paragraphs(client):
    mid = upload(client).json()["id"]
    assert client.get(f"/api/meetings/{mid}").json()["paragraphs"] == []


def test_patch_speakers_rebuilds_transcript(client, done_meeting, store):
    r = client.patch(f"/api/meetings/{done_meeting}",
                     json={"title": "Tên mới", "speakers": {"S1": "Anh Tuấn"}})
    assert r.status_code == 200 and r.json()["title"] == "Tên mới"
    md = (store.dir(done_meeting) / "transcript.md").read_text(encoding="utf-8")
    assert md.startswith("# Tên mới") and "Anh Tuấn:" in md


def test_patch_rejects_bad_date(client, done_meeting):
    assert client.patch(f"/api/meetings/{done_meeting}", json={"date": "hôm nay"}).status_code == 422


def test_retry_only_from_error_409(client, done_meeting, store, worker):
    assert client.post(f"/api/meetings/{done_meeting}/retry").status_code == 409
    store.update(done_meeting, status="error", error="x")
    assert client.post(f"/api/meetings/{done_meeting}/retry").status_code == 200
    assert worker.calls[-1] == (done_meeting, "full")


def test_rediarize_only_from_done(client, done_meeting, store, worker):
    r = client.post(f"/api/meetings/{done_meeting}/rediarize", json={"num_speakers": 3})
    assert r.status_code == 200 and store.get(done_meeting).num_speakers == 3
    assert worker.calls[-1] == (done_meeting, "rediarize")
    store.update(done_meeting, status="transcribing")
    assert client.post(f"/api/meetings/{done_meeting}/rediarize", json={}).status_code == 409


def test_delete_blocked_while_processing_409(client, done_meeting, store):
    store.update(done_meeting, status="transcribing")
    assert client.delete(f"/api/meetings/{done_meeting}").status_code == 409
    store.update(done_meeting, status="done")
    assert client.delete(f"/api/meetings/{done_meeting}").status_code == 204
    assert client.get(f"/api/meetings/{done_meeting}").status_code == 404


def test_transcript_download_headers(client, done_meeting):
    r = client.get(f"/api/meetings/{done_meeting}/transcript.md")
    assert r.status_code == 200 and r.text == "# Xong\n"
    assert r.headers["content-type"].startswith("text/markdown")
    assert f'filename="{done_meeting}.md"' in r.headers["content-disposition"]


def test_audio_supports_range(client, done_meeting):
    r = client.get(f"/api/meetings/{done_meeting}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def test_pages_served(client, done_meeting):
    assert client.get("/").status_code == 200
    assert client.get(f"/m/{done_meeting}").status_code == 200


def test_rejects_foreign_host_header(client):
    r = client.get("/api/meetings", headers={"Host": "evil.example"})
    assert r.status_code == 400


def test_allows_localhost_host_header(client):
    assert client.get("/api/meetings", headers={"Host": "localhost:8765"}).status_code == 200
