import pytest

from app.youtube import YouTubeError, friendly_error, info_summary, is_youtube_url


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "https://youtube.com/watch?v=jNQXAC9IVRw&t=30s",
    "https://m.youtube.com/watch?v=jNQXAC9IVRw",
    "https://youtu.be/jNQXAC9IVRw?si=abc",
    "https://www.youtube.com/live/jNQXAC9IVRw",
    "https://www.youtube.com/shorts/jNQXAC9IVRw",
    "  https://www.youtube.com/watch?v=jNQXAC9IVRw  ",
])
def test_accepts_youtube_video_links(url):
    assert is_youtube_url(url)


@pytest.mark.parametrize("url", [
    "https://vimeo.com/123", "http://localhost:8765/", "file:///etc/passwd", "youtube.com/watch?v=x",
    "https://www.youtube.com.evil.example/watch?v=x", "https://evil.example/?u=https://youtu.be/x",
    "https://www.youtube.com/playlist?list=PL123", "https://www.youtube.com/@kenh", "",
])
def test_rejects_other_links(url):
    assert not is_youtube_url(url)


def test_info_summary_maps_metadata():
    info = {"title": "Họp quý 3", "upload_date": "20261028", "duration": 3725, "is_live": False,
            "webpage_url": "https://www.youtube.com/watch?v=abc"}
    assert info_summary(info) == {"title": "Họp quý 3", "date": "2026-10-28", "duration": 3725,
                                  "url": "https://www.youtube.com/watch?v=abc"}


def test_info_summary_rejects_live_and_playlists():
    with pytest.raises(YouTubeError):
        info_summary({"title": "x", "is_live": True})
    with pytest.raises(YouTubeError):
        info_summary({"_type": "playlist", "title": "x"})


@pytest.mark.parametrize("raw,expected", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "@yt.private"),
    ("ERROR: [youtube] abc: Video unavailable", "@yt.unavailable"),
    ("ERROR: [youtube] abc: Sign in to confirm your age", "@yt.age"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot", "@yt.bot"),
    ("ERROR: unable to download webpage: <urlopen error [Errno 8] nodename nor servname provided>", "@yt.network"),
    ("ERROR: something new", '@yt.other:{"detail": "something new"}'),
])
def test_friendly_error(raw, expected):
    assert friendly_error(raw) == expected
