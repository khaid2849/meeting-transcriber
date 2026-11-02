import subprocess

import numpy as np
import pytest

from app.audio import AudioError, extract_audio, load_samples, probe


def ff(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture
def tone(tmp_path):
    path = tmp_path / "tone.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ar", "48000", str(path))
    return path


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    ff("-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
       "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest",
       "-c:v", "libx264", "-c:a", "aac", str(path))
    return path


def test_probe_audio_only(tone):
    info = probe(tone)
    assert info["has_video"] is False
    assert info["duration"] == pytest.approx(2.0, abs=0.05)


def test_probe_video_detected(video):
    assert probe(video)["has_video"] is True


def test_probe_garbage_raises_audio_error(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_text("không phải audio")
    with pytest.raises(AudioError) as exc:
        probe(bad)
    assert str(exc.value)


def test_extract_and_load_16k(tone, tmp_path):
    dst = tmp_path / "audio.m4a"
    extract_audio(tone, dst)
    samples = load_samples(dst)
    assert samples.dtype == np.float32
    assert abs(len(samples) - 32000) < 1600


def test_extract_from_video_drops_picture(video, tmp_path):
    dst = tmp_path / "audio.m4a"
    extract_audio(video, dst)
    assert probe(dst)["has_video"] is False
