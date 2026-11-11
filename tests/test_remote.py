import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import remote as remote_mod
from app.main import create_app
from app.remote import RemoteError, RemoteStorage, offload, parse_range
from app.storage import MeetingStore
from app.worker import Worker
from tests.test_api import FakeWorker
from tests.test_worker import FakeEngine

AUDIO = bytes(range(256)) * 4


class FakeResponse:
    def __init__(self, data):
        self.data, self.closed = io.BytesIO(data), False

    def stream(self, amount):
        while chunk := self.data.read(amount):
            yield chunk

    def close(self):
        self.closed = True

    def release_conn(self):
        pass


class FakeMinio:
    """The slice of minio.Minio that RemoteStorage uses, backed by a dict."""

    def __init__(self, buckets=("rec",)):
        self.buckets, self.objects, self.down, self.truncate = set(buckets), {}, False, False

    def _up(self):
        if self.down:
            raise ConnectionError("storage is off")

    def bucket_exists(self, *, bucket_name):
        self._up()
        return bucket_name in self.buckets

    def fput_object(self, *, bucket_name, object_name, file_path, num_parallel_uploads=3):
        self._up()
        data = open(file_path, "rb").read()
        self.objects[object_name] = data[:-1] if self.truncate else data

    def stat_object(self, *, bucket_name, object_name):
        self._up()
        return SimpleNamespace(size=len(self.objects[object_name]))

    def get_object(self, *, bucket_name, object_name, offset=0, length=0):
        self._up()
        data = self.objects[object_name]
        return FakeResponse(data[offset:offset + length] if length else data[offset:])

    def fget_object(self, *, bucket_name, object_name, file_path):
        self._up()
        open(file_path, "wb").write(self.objects[object_name])

    def list_objects(self, *, bucket_name, prefix, recursive):
        self._up()
        return [SimpleNamespace(object_name=k) for k in self.objects if k.startswith(prefix)]

    def remove_object(self, *, bucket_name, object_name):
        self._up()
        del self.objects[object_name]


@pytest.fixture
def store(tmp_path):
    return MeetingStore(tmp_path)


@pytest.fixture
def minio():
    return FakeMinio()


@pytest.fixture
def remote(minio):
    return RemoteStorage(minio, "rec", prefix="/audio/")


def done_meeting(store, title="Xong"):
    m = store.create(title=title, date="2026-11-02", keywords="", original_name="a.webm")
    store.write_json(m.id, "segments.json", [{"start": 5.0, "end": 8.0, "text": "Xin chào"}])
    (store.dir(m.id) / "audio.m4a").write_bytes(AUDIO)
    (store.dir(m.id) / "original.webm").write_bytes(b"raw-recording")
    (store.dir(m.id) / "transcript.md").write_text("# Xong\n", encoding="utf-8")
    store.update(m.id, status="done", progress=1.0)
    return m.id


def names(store, meeting_id):
    return sorted(p.name for p in store.dir(meeting_id).iterdir())


def test_offload_moves_audio_and_keeps_transcript(store, remote, minio):
    mid = done_meeting(store)
    assert offload(store, remote, mid) == len(AUDIO) + len(b"raw-recording")
    assert minio.objects == {f"audio/{mid}/audio.m4a": AUDIO, f"audio/{mid}/original.webm": b"raw-recording"}
    assert names(store, mid) == ["meeting.json", "segments.json", "transcript.md"]
    assert store.get(mid).remote_files == {"audio.m4a": len(AUDIO), "original.webm": 13}
    assert offload(store, remote, mid) == 0  # nothing left to move


def test_offload_skips_unfinished_recordings(store, remote, minio):
    mid = done_meeting(store)
    store.update(mid, status="error")
    assert offload(store, remote, mid) == 0 and not minio.objects
    assert "audio.m4a" in names(store, mid)


def test_local_file_survives_a_failed_or_short_upload(store, remote, minio):
    mid = done_meeting(store)
    minio.truncate = True
    with pytest.raises(RemoteError):
        offload(store, remote, mid)
    minio.truncate, minio.down = False, True
    with pytest.raises(ConnectionError):
        offload(store, remote, mid)
    assert {"audio.m4a", "original.webm"} <= set(names(store, mid))
    assert store.get(mid).remote_files == {}


def test_delete_only_touches_that_recording(store, remote, minio):
    first, second = done_meeting(store, "Họp"), done_meeting(store, "Họp")  # ids "…-hop" and "…-hop-2"
    offload(store, remote, first)
    offload(store, remote, second)
    remote.delete(first)
    assert sorted(minio.objects) == [f"audio/{second}/audio.m4a", f"audio/{second}/original.webm"]


def test_check_reports_a_missing_bucket(minio):
    with pytest.raises(RemoteError):
        RemoteStorage(minio, "nope").check()


@pytest.mark.parametrize("header,expected", [
    (None, None), ("bytes=0-99", (0, 99)), ("bytes=100-", (100, 1023)), ("bytes=-24", (1000, 1023)),
    ("bytes=1000-5000", (1000, 1023)), ("bytes=0-0,5-9", None), ("items=0-5", None),
])
def test_parse_range(header, expected):
    assert parse_range(header, 1024) == expected


@pytest.mark.parametrize("header", ["bytes=1024-", "bytes=5-2", "bytes=-0"])
def test_parse_range_unsatisfiable(header):
    with pytest.raises(ValueError):
        parse_range(header, 1024)


def test_load_without_config_file_is_off(tmp_path):
    assert remote_mod.load(tmp_path / "remote_storage.json") is None


def test_load_reports_missing_settings(tmp_path):
    path = tmp_path / "remote_storage.json"
    path.write_text('{"endpoint": "http://10.0.0.5:9000", "bucket": "rec"}', encoding="utf-8")
    with pytest.raises(RemoteError, match="access_key, secret_key"):
        remote_mod.load(path)


def test_load_builds_a_client(tmp_path):
    path = tmp_path / "remote_storage.json"
    path.write_text('{"endpoint": "10.0.0.5:9000", "bucket": "rec", "access_key": "a", "secret_key": "s",'
                    ' "prefix": "audio"}', encoding="utf-8")
    got = remote_mod.load(path)
    assert (got.bucket, got.prefix) == ("rec", "audio")


# ---------- worker ----------

def new_upload(store):
    m = store.create(title="Họp", date="2026-11-02", keywords="", original_name="x.webm")
    (store.dir(m.id) / "upload.webm").write_bytes(b"raw")
    return m.id


def test_worker_offloads_after_a_finished_job(store, remote, minio):
    mid = new_upload(store)
    Worker(store, FakeEngine(), "model-x", remote=remote).run_job(mid)
    assert store.get(mid).status == "done"
    assert set(store.get(mid).remote_files) == {"audio.m4a", "original.webm"}
    assert not (store.dir(mid) / "audio.m4a").exists() and (store.dir(mid) / "transcript.md").exists()


def test_worker_keeps_audio_local_when_storage_is_down(store, remote, minio):
    minio.down = True
    mid = new_upload(store)
    Worker(store, FakeEngine(), "model-x", remote=remote).run_job(mid)
    got = store.get(mid)
    assert (got.status, got.error, got.remote_files) == ("done", None, {})
    assert (store.dir(mid) / "audio.m4a").exists()


def test_worker_fetches_audio_back_to_process_again(store, remote, minio):
    mid = new_upload(store)
    worker = Worker(store, FakeEngine(), "model-x", remote=remote)
    worker.run_job(mid)
    engine = FakeEngine()
    Worker(store, engine, "model-x", remote=remote).run_job(mid)
    assert engine.calls == ["load", "transcribe"] and store.get(mid).status == "done"
    assert "audio.m4a" in store.get(mid).remote_files and not (store.dir(mid) / "audio.m4a").exists()


# ---------- API ----------

@pytest.fixture
def client(store, remote):
    return TestClient(create_app(store, FakeWorker(store), remote=remote,
                                 allowed_hosts=("testserver", "localhost", "127.0.0.1")))


def test_audio_is_relayed_from_remote_storage(client, store, remote):
    mid = done_meeting(store)
    offload(store, remote, mid)
    full = client.get(f"/api/meetings/{mid}/audio")
    assert full.status_code == 200 and full.content == AUDIO
    assert full.headers["content-type"] == "audio/mp4" and full.headers["accept-ranges"] == "bytes"
    part = client.get(f"/api/meetings/{mid}/audio", headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == AUDIO[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(AUDIO)}"
    tail = client.get(f"/api/meetings/{mid}/audio", headers={"Range": "bytes=1000-"})
    assert tail.status_code == 206 and tail.content == AUDIO[1000:]
    assert client.get(f"/api/meetings/{mid}/audio", headers={"Range": "bytes=5000-"}).status_code == 416


def test_audio_502_when_storage_is_down(client, store, remote, minio):
    mid = done_meeting(store)
    offload(store, remote, mid)
    minio.down = True
    r = client.get(f"/api/meetings/{mid}/audio")
    assert r.status_code == 502 and r.json()["detail"] == "@err.remote_audio"


def test_delete_removes_remote_audio(client, store, remote, minio):
    mid = done_meeting(store)
    offload(store, remote, mid)
    assert client.delete(f"/api/meetings/{mid}").status_code == 204
    assert not minio.objects and client.get(f"/api/meetings/{mid}").status_code == 404


def test_delete_is_refused_while_storage_is_down(client, store, remote, minio):
    mid = done_meeting(store)
    offload(store, remote, mid)
    minio.down = True
    r = client.delete(f"/api/meetings/{mid}")
    assert r.status_code == 502 and r.json()["detail"] == "@err.remote_delete"
    assert client.get(f"/api/meetings/{mid}").status_code == 200
