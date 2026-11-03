"""Runs the real models on 60 s of the meeting recording. `uv run pytest -m slow`."""
import shutil
import subprocess

import numpy as np
import pytest

from app import config

WAV = config.ROOT / "cuoc_hop_20261102_143735.wav"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not WAV.exists() or not config.VAD_MODEL.exists(),
                       reason="needs the meeting recording and ./setup_models.sh"),
]


@pytest.fixture(scope="module")
def samples():
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "300", "-t", "60", "-i", str(WAV),
           "-f", "f32le", "-ac", "1", "-ar", "16000", "-"]
    return np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, np.float32)


def test_transcribe_excerpt(samples):
    from app.asr import transcribe
    progress = []
    segs, language = transcribe(samples, config.WHISPER_MODEL, "Odoo, AI", progress.append, "vi")
    assert language == "vi"
    assert len(segs) >= 3
    assert all(0 <= s["start"] <= s["end"] <= 60.5 for s in segs)
    assert all(s["text"].strip() for s in segs)
    assert progress and progress[-1] == pytest.approx(1.0)



def test_release_model_frees_gpu_memory(samples):
    import mlx.core as mx
    from app.asr import release_model, transcribe
    transcribe(samples[:16000 * 10], config.WHISPER_MODEL, "", lambda p: None, "vi")
    assert mx.get_active_memory() > 1e9
    release_model()
    assert mx.get_active_memory() < 3e8


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="needs ffmpeg")
def test_youtube_download_real_video(tmp_path):
    from app.audio import probe
    from app.youtube import download_audio, fetch_info
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", 19 s, public since 2005
    info = fetch_info(url)
    assert info["title"] == "Me at the zoo" and info["date"] == "2005-04-24"
    progress = []
    path = download_audio(url, tmp_path, progress.append)
    assert path.name.startswith("upload.") and probe(path)["duration"] == pytest.approx(19, abs=1.5)


def test_auto_language_detects_vietnamese(samples):
    from app.asr import transcribe
    segs, language = transcribe(samples[:16000 * 30], config.WHISPER_MODEL, "", lambda p: None, "auto")
    assert language == "vi" and segs
