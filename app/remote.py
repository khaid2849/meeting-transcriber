"""Optional: keep the audio of finished recordings on an S3-compatible server (MinIO) instead of this disk.

Enabled by data/remote_storage.json (see remote_storage.example.json). Transcripts always stay local.

    uv run python -m app.remote check   # test the connection; touches no recording
    uv run python -m app.remote push    # move the audio of every finished recording
"""
import json
import logging
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

from app import config
from app.storage import MeetingStore

log = logging.getLogger(__name__)
CONFIG_FILE = config.DATA_DIR / "remote_storage.json"
CHUNK = 1 << 16
UPLOAD_TIMEOUT = 1800  # seconds per uploaded part


class RemoteError(Exception):
    pass


class RemoteStorage:
    """Objects live at <prefix>/<meeting id>/<file name> in one bucket."""

    def __init__(self, client, bucket: str, prefix: str = "", uploader=None):
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip("/")
        self.uploader = uploader or client  # a client with timeouts sized for slow uploads

    def _key(self, meeting_id: str, name: str = "") -> str:
        return "/".join(part for part in (self.prefix, meeting_id, name) if part)

    def check(self) -> None:
        if not self.client.bucket_exists(bucket_name=self.bucket):
            raise RemoteError(f"Bucket '{self.bucket}' chưa tồn tại trên máy lưu trữ.")

    def put(self, meeting_id: str, name: str, path: Path) -> int:
        """Upload and confirm the stored size, so the caller can safely delete the local file."""
        key = self._key(meeting_id, name)
        # One part at a time: parallel parts split a slow uplink and each then times out.
        self.uploader.fput_object(bucket_name=self.bucket, object_name=key, file_path=str(path),
                                  num_parallel_uploads=1)
        size = self.client.stat_object(bucket_name=self.bucket, object_name=key).size
        if size != path.stat().st_size:
            raise RemoteError(f"{key}: kích thước trên máy lưu trữ ({size}) khác file gốc ({path.stat().st_size}).")
        return size

    def open(self, meeting_id: str, name: str, offset: int = 0, length: int = 0) -> Iterator[bytes]:
        """Stream `length` bytes from `offset` (0 = to the end). Connects now, so errors surface here."""
        response = self.client.get_object(bucket_name=self.bucket, object_name=self._key(meeting_id, name),
                                          offset=offset, length=length)

        def chunks() -> Iterator[bytes]:
            try:
                yield from response.stream(CHUNK)
            finally:
                response.close()
                response.release_conn()
        return chunks()

    def fetch(self, meeting_id: str, name: str, dst: Path) -> None:
        self.client.fget_object(bucket_name=self.bucket, object_name=self._key(meeting_id, name), file_path=str(dst))

    def delete(self, meeting_id: str) -> None:
        # The trailing slash keeps "…-hop" from also matching "…-hop-2".
        for obj in list(self.client.list_objects(bucket_name=self.bucket, prefix=self._key(meeting_id) + "/",
                                                 recursive=True)):
            self.client.remove_object(bucket_name=self.bucket, object_name=obj.object_name)


def load(path: Path = CONFIG_FILE) -> RemoteStorage | None:
    """The configured storage, or None when the config file does not exist."""
    if not path.exists():
        return None
    import urllib3
    from minio import Minio

    cfg = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in ("endpoint", "bucket", "access_key", "secret_key") if not cfg.get(k)]
    if missing:
        raise RemoteError(f"{path.name} thiếu: {', '.join(missing)}")
    endpoint = cfg["endpoint"] if "://" in cfg["endpoint"] else f"http://{cfg['endpoint']}"
    url = urlparse(endpoint)

    def client(read_timeout: float):
        # Short connect timeout: when the storage machine is off, fail fast instead of hanging.
        http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=read_timeout),
                                   retries=urllib3.Retry(total=2, backoff_factor=0.2))
        return Minio(url.netloc, access_key=cfg["access_key"], secret_key=cfg["secret_key"],
                     secure=url.scheme == "https", http_client=http)

    # The timeout covers sending a whole 5 MB part, so uploads over a slow link (VPN, Wi-Fi) need far
    # longer than playback does.
    return RemoteStorage(client(60), cfg["bucket"], cfg.get("prefix", ""), uploader=client(UPLOAD_TIMEOUT))


def offload(store: MeetingStore, remote: RemoteStorage, meeting_id: str) -> int:
    """Move a finished recording's audio files to the remote storage. Returns the bytes freed."""
    if store.get(meeting_id).status != "done":
        return 0
    folder = store.dir(meeting_id)
    freed = 0
    for path in [folder / "audio.m4a", *sorted(folder.glob("original.*"))]:
        if not path.is_file():
            continue
        size = remote.put(meeting_id, path.name, path)
        with store.lock:  # record it before deleting, so the audio is never unreachable
            store.update(meeting_id, remote_files={**store.get(meeting_id).remote_files, path.name: size})
        path.unlink()
        freed += size
    return freed


def restore(store: MeetingStore, remote: RemoteStorage, meeting_id: str, name: str) -> None:
    """Bring one file back to the local folder (needed to process a recording again)."""
    remote.fetch(meeting_id, name, store.dir(meeting_id) / name)
    with store.lock:
        files = dict(store.get(meeting_id).remote_files)
        files.pop(name, None)
        store.update(meeting_id, remote_files=files)


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """First and last byte of a single-range request; None = whole file. ValueError = unsatisfiable."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not match or not (match[1] or match[2]):
        return None
    if not match[1]:  # "bytes=-N": the last N bytes
        start, end = max(0, size - int(match[2])), size - 1
    else:
        start, end = int(match[1]), min(int(match[2]) if match[2] else size - 1, size - 1)
    if start >= size or start > end:
        raise ValueError(header)
    return start, end


def _round_trip(remote: RemoteStorage) -> None:
    probe_id, payload = "connection-check", b"meeting-transcriber"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe.bin"
        src.write_bytes(payload)
        remote.put(probe_id, src.name, src)
        try:
            if b"".join(remote.open(probe_id, src.name)) != payload:
                raise RemoteError("Đọc lại file thử không khớp nội dung đã ghi.")
        finally:
            remote.delete(probe_id)


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command not in ("check", "push"):
        print(__doc__)
        return 2
    try:
        remote = load()
        if remote is None:
            print(f"Chưa cấu hình: tạo {CONFIG_FILE} theo mẫu remote_storage.example.json.")
            return 1
        remote.check()
        _round_trip(remote)
    except Exception as exc:  # noqa: BLE001 — any failure is a "cannot use this storage" answer
        print(f"Không dùng được máy lưu trữ: {exc}")
        return 1
    print(f"Kết nối tốt: ghi, đọc và xóa được trong bucket '{remote.bucket}'.")
    if command == "check":
        return 0

    store = MeetingStore(config.MEETINGS_DIR)
    total = failed = 0
    for meeting in store.list():
        before = sum(meeting.remote_files.values())
        error = None
        try:
            offload(store, remote, meeting.id)
        except Exception as exc:  # noqa: BLE001 — keep going; what was not moved stays local
            failed += 1
            error = exc
        freed = sum(store.get(meeting.id).remote_files.values()) - before  # files moved before any error count
        total += freed
        if freed:
            print(f"  đã chuyển  {meeting.id}  ({freed / 1048576:.1f} MB)", flush=True)
        if error:
            print(f"  LỖI  {meeting.id}: {error}", flush=True)
    print(f"Xong: giải phóng {total / 1048576:.1f} MB trên máy này." + (f" {failed} bản ghi lỗi." if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
