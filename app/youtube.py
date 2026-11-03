"""YouTube links: validate, read metadata and download the audio track with yt-dlp."""
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
VIDEO_ID = re.compile(r"^[\w-]{6,}$")


class YouTubeError(Exception):
    """A YouTube link could not be used; the message is user-facing (Vietnamese)."""


def is_youtube_url(url: str) -> bool:
    """True for a link to a single YouTube video (not a channel or playlist)."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    if host == "youtu.be":
        return len(parts) == 1 and bool(VIDEO_ID.match(parts[0]))
    if host not in YOUTUBE_HOSTS:
        return False
    if parts == ["watch"]:
        return bool(VIDEO_ID.match(parse_qs(parsed.query).get("v", [""])[0]))
    return len(parts) == 2 and parts[0] in ("live", "shorts", "embed") and bool(VIDEO_ID.match(parts[1]))


def info_summary(info: dict) -> dict:
    if info.get("_type") == "playlist":
        raise YouTubeError("Đây là playlist. Hãy dán link của từng video.")
    if info.get("is_live"):
        raise YouTubeError("Video đang phát trực tiếp. Hãy đợi buổi phát kết thúc rồi thử lại.")
    raw = info.get("upload_date") or ""
    date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if re.fullmatch(r"\d{8}", raw) else None
    return {"title": info.get("title") or "Video YouTube", "date": date,
            "duration": info.get("duration"), "url": info.get("webpage_url")}


def friendly_error(message: str) -> str:
    text = str(message)
    lower = text.lower()
    if "private video" in lower:
        return "Video đang ở chế độ riêng tư nên app không tải được."
    if "confirm your age" in lower or "age-restricted" in lower:
        return "Video bị giới hạn độ tuổi (cần đăng nhập YouTube) nên app không tải được."
    if "not a bot" in lower:
        return "YouTube đang chặn tải tự động từ máy này. Thử lại sau ít phút."
    if "members-only" in lower or "join this channel" in lower:
        return "Video chỉ dành cho hội viên của kênh."
    if "unavailable" in lower or "has been removed" in lower or "does not exist" in lower:
        return "Video không tồn tại hoặc đã bị gỡ."
    if "urlopen error" in lower or "timed out" in lower or "nodename" in lower or "connection" in lower:
        return "Không kết nối được tới YouTube."
    detail = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*[\w-]+:\s*)?", "", text).strip()
    return f"Không tải được video: {detail}"


def _ydl(extra: dict):
    import yt_dlp
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True, **extra})


def fetch_info(url: str) -> dict:
    """Metadata without downloading: {title, date (YYYY-MM-DD or None), duration, url}."""
    from yt_dlp.utils import DownloadError

    try:
        with _ydl({"skip_download": True}) as ydl:
            return info_summary(ydl.extract_info(url.strip(), download=False))
    except DownloadError as exc:
        raise YouTubeError(friendly_error(exc.msg if hasattr(exc, "msg") else str(exc))) from exc


def download_audio(url: str, folder: Path, on_progress: Callable[[float], None]) -> Path:
    """Download the best audio-only stream (or the smallest fallback) as folder/upload.<ext>."""
    from yt_dlp.utils import DownloadError

    def hook(d: dict) -> None:
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d.get("status") == "downloading" and total:
            on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    try:
        with _ydl({"format": "bestaudio/best", "outtmpl": str(folder / "upload.%(ext)s"),
                   "progress_hooks": [hook]}) as ydl:
            ydl.download([url.strip()])
    except DownloadError as exc:
        raise YouTubeError(friendly_error(exc.msg if hasattr(exc, "msg") else str(exc))) from exc
    files = [p for p in folder.glob("upload.*") if not p.name.endswith((".part", ".ytdl"))]
    if not files:
        raise YouTubeError("Tải xong nhưng không thấy file âm thanh.")
    return files[0]
